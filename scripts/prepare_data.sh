#!/usr/bin/env bash
set -euo pipefail

OSM_PROFILE="${OSM_PROFILE:-almaty}"
OSM_EXTRACT_URL="${OSM_EXTRACT_URL:-}"
OSM_FORCE_REFRESH="${OSM_FORCE_REFRESH:-false}"
COVERAGE_BBOX="${COVERAGE_BBOX:-}"
CUSTOM_FILES="${CUSTOM_FILES:-/custom_files}"
SOURCE_DIR="${SOURCE_DIR:-/osm}"
DATA_DIR="${DATA_DIR:-/data}"
VALHALLA_UID="${VALHALLA_UID:-59999}"
VALHALLA_GID="${VALHALLA_GID:-59999}"
VALHALLA_WRITE_CONFIG="${VALHALLA_WRITE_CONFIG:-true}"
VALHALLA_MAX_DISTANCE_CONTOUR_KM="${VALHALLA_MAX_DISTANCE_CONTOUR_KM:-200}"
VALHALLA_MAX_TIME_CONTOUR_MIN="${VALHALLA_MAX_TIME_CONTOUR_MIN:-60}"
MAX_CONTOURS="${MAX_CONTOURS:-4}"
DEFAULT_SOURCE_URL="https://download.geofabrik.de/asia/kazakhstan-latest.osm.pbf"

log() { printf '%s data-prep %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }
fail() { log "ERROR: $*"; exit 1; }

profile_bbox() {
  case "$1" in
    almaty) echo "76.60,43.05,77.20,43.45" ;;
    almaty-region) echo "75.80,42.60,78.60,44.60" ;;
    kazakhstan) echo "" ;;
    *) fail "unknown OSM_PROFILE '$1' (expected: almaty | almaty-region | kazakhstan)" ;;
  esac
}

BBOX="${COVERAGE_BBOX:-$(profile_bbox "$OSM_PROFILE")}"
SOURCE_URL="${OSM_EXTRACT_URL:-$DEFAULT_SOURCE_URL}"
EXTRACT="${CUSTOM_FILES}/${OSM_PROFILE}.osm.pbf"
SOURCE="${SOURCE_DIR}/source.osm.pbf"
META="${DATA_DIR}/meta.json"
WATER="${DATA_DIR}/water.geojson"

mkdir -p "$CUSTOM_FILES" "$SOURCE_DIR" "$DATA_DIR"

if [ -z "$BBOX" ]; then
  SOURCE="$EXTRACT"
fi

signature() {
  printf '%s|%s|%s|%s|%s|%s|%s|%s|%s' \
    "$OSM_PROFILE" "$BBOX" "$SOURCE_URL" "$1" \
    "${WATER_MIN_AREA_M2:-5000}" "${WATER_RIVER_WIDTH_M:-15}" \
    "${WATER_STREAM_WIDTH_M:-4}" "${WATER_CANAL_WIDTH_M:-10}" \
    "${WATER_INCLUDE_CANALS:-true}" | sha1sum | cut -d' ' -f1
}

download_source() {
  if [ -s "$SOURCE" ] && [ "$OSM_FORCE_REFRESH" != "true" ]; then
    log "source PBF already present: $SOURCE ($(du -h "$SOURCE" | cut -f1))"
    return
  fi
  log "downloading $SOURCE_URL"
  curl --fail --location --retry 3 --retry-delay 5 --remote-time \
    --connect-timeout 30 --output "${SOURCE}.part" "$SOURCE_URL"
  mv "${SOURCE}.part" "$SOURCE"
  log "downloaded $(du -h "$SOURCE" | cut -f1)"
}

read_osm_timestamp() {
  local ts
  ts="$(osmium fileinfo --json "$1" 2>/dev/null \
    | jq -r '.header.option.osmosis_replication_timestamp // .header.option.timestamp // empty')"
  if [ -z "$ts" ]; then
    ts="$(date -u -r "$1" +%Y-%m-%dT%H:%M:%SZ)"
  fi
  echo "$ts"
}

cut_extract() {
  if [ -z "$BBOX" ]; then
    log "profile '$OSM_PROFILE' uses the source extract as is"
    return
  fi
  log "cutting bbox $BBOX from source"
  find "$CUSTOM_FILES" -maxdepth 1 -name '*.pbf' ! -name "$(basename "$EXTRACT")" -delete
  osmium extract --bbox "$BBOX" --strategy complete_ways --overwrite \
    --output "${EXTRACT}.part" "$SOURCE"
  mv "${EXTRACT}.part" "$EXTRACT"
  log "extract ready: $(du -h "$EXTRACT" | cut -f1)"
}

write_valhalla_config() {
  local config="${CUSTOM_FILES}/valhalla.json"
  if [ "$VALHALLA_WRITE_CONFIG" != "true" ] || [ -f "$config" ]; then
    return
  fi
  log "writing isochrone service limits to $config"
  jq -n \
    --argjson contours "$MAX_CONTOURS" \
    --argjson time "$VALHALLA_MAX_TIME_CONTOUR_MIN" \
    --argjson distance "$VALHALLA_MAX_DISTANCE_CONTOUR_KM" \
    '{service_limits: {isochrone: {max_contours: $contours, max_time_contour: $time, max_distance_contour: $distance, max_locations: 1}}}' \
    > "$config"
}

effective_bbox() {
  if [ -n "$BBOX" ]; then
    echo "$BBOX"
    return
  fi
  local header_box
  header_box="$(osmium fileinfo --json "$EXTRACT" 2>/dev/null \
    | jq -r '.header.boxes[0] // empty' 2>/dev/null || true)"
  if printf '%s' "$header_box" | grep -Eq '^-?[0-9.]+,-?[0-9.]+,-?[0-9.]+,-?[0-9.]+$'; then
    echo "$header_box"
  else
    echo "46.49,40.56,87.32,55.45"
  fi
}

write_meta() {
  local osm_ts="$1" sig="$2" bbox="$3" water_parts="$4"
  jq -n \
    --arg profile "$OSM_PROFILE" \
    --arg bbox "$bbox" \
    --arg osm_ts "$osm_ts" \
    --arg data_version "$sig" \
    --arg prepared_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --arg source_url "$SOURCE_URL" \
    --argjson water_parts "$water_parts" \
    '{profile: $profile,
      bbox: ($bbox | split(",") | map(tonumber)),
      osm_data_timestamp: $osm_ts,
      data_version: $data_version,
      prepared_at: $prepared_at,
      source_url: $source_url,
      water_parts: $water_parts}' > "${META}.part"
  mv "${META}.part" "$META"
}

fix_ownership() {
  if [ "$(id -u)" = "0" ]; then
    chown -R "${VALHALLA_UID}:${VALHALLA_GID}" "$CUSTOM_FILES" || true
    chmod -R a+rX "$DATA_DIR" || true
  fi
}

download_source
OSM_TIMESTAMP="$(read_osm_timestamp "$SOURCE")"
SIGNATURE="$(signature "$OSM_TIMESTAMP")"

if [ -f "$META" ] && [ -s "$EXTRACT" ] && [ -s "$WATER" ] \
   && [ "$(jq -r '.data_version // empty' "$META")" = "$SIGNATURE" ] \
   && [ "$OSM_FORCE_REFRESH" != "true" ]; then
  log "data already prepared (data_version=$SIGNATURE), nothing to do"
  fix_ownership
  exit 0
fi

log "preparing profile '$OSM_PROFILE' (osm_data_timestamp=$OSM_TIMESTAMP)"
cut_extract
write_valhalla_config

BBOX_EFFECTIVE="$(effective_bbox)"
PBF_FOR_WATER="$EXTRACT" \
OUT_WATER="$WATER" \
BBOX="$BBOX_EFFECTIVE" \
  /opt/scripts/prepare_water.sh

WATER_PARTS="$(jq '.features | length' "$WATER")"
write_meta "$OSM_TIMESTAMP" "$SIGNATURE" "$BBOX_EFFECTIVE" "$WATER_PARTS"
fix_ownership

log "done: extract=$EXTRACT water_parts=$WATER_PARTS data_version=$SIGNATURE"
