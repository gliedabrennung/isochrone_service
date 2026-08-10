#!/usr/bin/env bash
set -uo pipefail

BASE_URL="${BASE_URL:-http://localhost:8080}"
API="${BASE_URL}/api/v1"
LAT="${SMOKE_LAT:-43.238949}"
LON="${SMOKE_LON:-76.889709}"
UNROUTABLE_LAT="${SMOKE_UNROUTABLE_LAT:-43.06}"
UNROUTABLE_LON="${SMOKE_UNROUTABLE_LON:-77.05}"
READY_TIMEOUT_S="${READY_TIMEOUT_S:-60}"

PASSED=0
FAILED=0

BODY="$(mktemp -t smoke_body.XXXXXXXX.json)"
trap 'rm -f "$BODY"' EXIT

green() { printf '\033[32m%s\033[0m\n' "$1"; }
red() { printf '\033[31m%s\033[0m\n' "$1"; }

pass() { PASSED=$((PASSED + 1)); green "  PASS  $1"; }
fail() { FAILED=$((FAILED + 1)); red "  FAIL  $1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; }

http_status() {
  local status exit_code
  : > "$BODY"
  status="$(curl -s -o "$BODY" -w '%{http_code}' "$@")"
  exit_code=$?
  if [ "$exit_code" -ne 0 ]; then
    printf 'curl-error-%s\n' "$exit_code"
    return 0
  fi
  printf '%s\n' "$status"
}

json_field() {
  SMOKE_BODY="$BODY" python3 - "$@" <<'PY' 2>/dev/null
import json
import os
import sys

with open(os.environ["SMOKE_BODY"], encoding="utf-8") as handle:
    data = json.load(handle)
for key in sys.argv[1:]:
    data = data[int(key)] if key.lstrip("-").isdigit() else data[key]
print(len(data) if isinstance(data, (list, dict)) else data)
PY
}

wait_for_ready() {
  local waited=0
  while [ "$waited" -lt "$READY_TIMEOUT_S" ]; do
    if [ "$(http_status "${API}/ready")" = "200" ]; then
      return 0
    fi
    sleep 3
    waited=$((waited + 3))
  done
  return 1
}

echo "smoke test against ${BASE_URL}"

echo "[1/8] liveness"
if [ "$(http_status "${API}/health")" = "200" ]; then
  pass "GET /health -> 200"
else
  fail "GET /health" "$(cat "$BODY" 2>/dev/null)"
fi

echo "[2/8] readiness"
if wait_for_ready; then
  pass "GET /ready -> 200"
else
  fail "GET /ready" "движок не поднялся за ${READY_TIMEOUT_S} с, проверьте: docker compose logs -f valhalla"
fi

echo "[3/8] metadata"
if [ "$(http_status "${API}/meta")" = "200" ]; then
  pass "GET /meta -> 200 (osm $(json_field osm_data_timestamp))"
else
  fail "GET /meta"
fi

echo "[4/8] расчёт по трём режимам"
for MODE in pedestrian bicycle auto; do
  STATUS=$(http_status -X POST "${API}/isochrone" \
    -H 'Content-Type: application/json' \
    -d "{\"lat\":${LAT},\"lon\":${LON},\"contours\":[10,20,30],\"mode\":\"${MODE}\"}")
  FEATURES=$(json_field features)
  if [ "$STATUS" = "200" ] && [ "$FEATURES" = "3" ]; then
    pass "POST /isochrone ${MODE} -> 200, 3 features, $(json_field metadata duration_ms) ms"
  else
    fail "POST /isochrone ${MODE}" "status=${STATUS} $(head -c 300 "$BODY")"
  fi
done

echo "[5/8] негативный сценарий: точка вне покрытия"
STATUS=$(http_status -X POST "${API}/isochrone" \
  -H 'Content-Type: application/json' \
  -d '{"lat":51.1605,"lon":71.4704,"contours":[10],"mode":"auto"}')
CODE=$(json_field code)
if [ "$STATUS" = "422" ] && [ "$CODE" = "POINT_OUT_OF_COVERAGE" ]; then
  pass "POST /isochrone вне bbox -> 422 POINT_OUT_OF_COVERAGE"
else
  fail "POST /isochrone вне bbox" "status=${STATUS} code=${CODE}"
fi

echo "[6/8] негативный сценарий: нероутируемая точка"
STATUS=$(http_status -X POST "${API}/isochrone" \
  -H 'Content-Type: application/json' \
  -d "{\"lat\":${UNROUTABLE_LAT},\"lon\":${UNROUTABLE_LON},\"contours\":[10],\"mode\":\"pedestrian\"}")
CODE=$(json_field code)
if [ "$STATUS" = "422" ] && [ "$CODE" = "POINT_NOT_ROUTABLE" ]; then
  pass "POST /isochrone в горах -> 422 POINT_NOT_ROUTABLE, снап $(json_field snap_distance_m) м"
else
  fail "POST /isochrone в горах" "status=${STATUS} code=${CODE}"
fi

echo "[7/8] негативный сценарий: невалидные параметры"
STATUS=$(http_status -X POST "${API}/isochrone" \
  -H 'Content-Type: application/json' \
  -d "{\"lat\":${LAT},\"lon\":${LON},\"contours\":[61],\"mode\":\"auto\"}")
CODE=$(json_field code)
if [ "$STATUS" = "400" ] && [ "$CODE" = "VALIDATION_ERROR" ]; then
  pass "POST /isochrone contours=[61] -> 400 VALIDATION_ERROR"
else
  fail "POST /isochrone contours=[61]" "status=${STATUS} code=${CODE}"
fi

echo "[8/8] наружу опубликован только порт web (AC-18)"
COMPOSE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ "${SMOKE_SKIP_PORT_CHECK:-0}" = "1" ]; then
  echo "  SKIP  проверка портов отключена через SMOKE_SKIP_PORT_CHECK=1"
elif ! docker compose version >/dev/null 2>&1; then
  fail "проверка опубликованных портов" "docker compose недоступен; для запуска против удалённого стенда выставьте SMOKE_SKIP_PORT_CHECK=1"
else
  PUBLISHED=$(docker compose -f "${COMPOSE_ROOT}/docker-compose.yml" config --format json 2>/dev/null |
    python3 -c 'import json,sys; c=json.load(sys.stdin); print(" ".join(sorted(n for n, s in c["services"].items() if s.get("ports"))))')
  if [ "$PUBLISHED" = "web" ]; then
    pass "порты публикует только web"
  else
    fail "опубликованные порты" "ожидалось 'web', получено '${PUBLISHED}'"
  fi
fi

echo
echo "passed: ${PASSED}, failed: ${FAILED}"

if [ "$FAILED" -gt 0 ]; then
  exit 1
fi
exit 0
