#!/usr/bin/env bash
set -euo pipefail

PBF_FOR_WATER="${PBF_FOR_WATER:?PBF_FOR_WATER is required}"
OUT_WATER="${OUT_WATER:-/data/water.geojson}"
BBOX="${BBOX:-}"
WORK_DIR="${WORK_DIR:-$(mktemp -d)}"
WATER_MIN_AREA_M2="${WATER_MIN_AREA_M2:-5000}"
WATER_RIVER_WIDTH_M="${WATER_RIVER_WIDTH_M:-15}"
WATER_STREAM_WIDTH_M="${WATER_STREAM_WIDTH_M:-4}"
WATER_CANAL_WIDTH_M="${WATER_CANAL_WIDTH_M:-10}"
WATER_INCLUDE_CANALS="${WATER_INCLUDE_CANALS:-true}"

log() { printf '%s prepare-water %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }

FILTERS=(
  "w/natural=water" "r/natural=water"
  "w/waterway=riverbank" "r/waterway=riverbank"
  "w/landuse=reservoir,basin" "r/landuse=reservoir,basin"
  "w/waterway=river,stream"
)
if [ "$WATER_INCLUDE_CANALS" = "true" ]; then
  FILTERS+=("w/waterway=canal")
fi

log "filtering water features from $PBF_FOR_WATER"
osmium tags-filter "$PBF_FOR_WATER" \
  "${FILTERS[@]}" \
  -o "${WORK_DIR}/water.osm.pbf" --overwrite

export OSM_CONFIG_FILE="${OSM_CONFIG_FILE:-/usr/share/gdal/osmconf.ini}"

log "converting areal water objects to GeoJSON"
ogr2ogr -f GeoJSON -skipfailures -nlt PROMOTE_TO_MULTI \
  "${WORK_DIR}/water_areas.geojson" "${WORK_DIR}/water.osm.pbf" multipolygons

log "converting linear waterways to GeoJSON"
ogr2ogr -f GeoJSON -skipfailures \
  -where "waterway IS NOT NULL" \
  "${WORK_DIR}/water_lines.geojson" "${WORK_DIR}/water.osm.pbf" lines

log "building unified water layer"
python3 /opt/scripts/water_build.py \
  --areas "${WORK_DIR}/water_areas.geojson" \
  --lines "${WORK_DIR}/water_lines.geojson" \
  --out "$OUT_WATER" \
  --bbox "$BBOX" \
  --min-area-m2 "$WATER_MIN_AREA_M2" \
  --river-width-m "$WATER_RIVER_WIDTH_M" \
  --stream-width-m "$WATER_STREAM_WIDTH_M" \
  --canal-width-m "$WATER_CANAL_WIDTH_M"

rm -rf "${WORK_DIR}/water.osm.pbf" "${WORK_DIR}/water_areas.geojson" "${WORK_DIR}/water_lines.geojson"

log "water layer written to $OUT_WATER"
