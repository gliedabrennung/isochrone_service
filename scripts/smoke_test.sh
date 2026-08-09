#!/usr/bin/env bash
set -uo pipefail

BASE_URL="${BASE_URL:-http://localhost:8080}"
API="${BASE_URL}/api/v1"
LAT="${SMOKE_LAT:-43.238949}"
LON="${SMOKE_LON:-76.889709}"
READY_TIMEOUT_S="${READY_TIMEOUT_S:-60}"

PASSED=0
FAILED=0

green() { printf '\033[32m%s\033[0m\n' "$1"; }
red() { printf '\033[31m%s\033[0m\n' "$1"; }

pass() { PASSED=$((PASSED + 1)); green "  PASS  $1"; }
fail() { FAILED=$((FAILED + 1)); red "  FAIL  $1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; }

http_status() {
  curl -s -o /tmp/smoke_body.json -w '%{http_code}' "$@"
}

json_field() {
  python3 - "$@" <<'PY' 2>/dev/null
import json
import sys

with open("/tmp/smoke_body.json", encoding="utf-8") as handle:
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

echo "[1/6] liveness"
if [ "$(http_status "${API}/health")" = "200" ]; then
  pass "GET /health -> 200"
else
  fail "GET /health" "$(cat /tmp/smoke_body.json 2>/dev/null)"
fi

echo "[2/6] readiness"
if wait_for_ready; then
  pass "GET /ready -> 200"
else
  fail "GET /ready" "движок не поднялся за ${READY_TIMEOUT_S} с, проверьте: docker compose logs -f valhalla"
fi

echo "[3/6] metadata"
if [ "$(http_status "${API}/meta")" = "200" ]; then
  pass "GET /meta -> 200 (osm $(json_field osm_data_timestamp))"
else
  fail "GET /meta"
fi

echo "[4/6] расчёт по трём режимам"
for MODE in pedestrian bicycle auto; do
  STATUS=$(http_status -X POST "${API}/isochrone" \
    -H 'Content-Type: application/json' \
    -d "{\"lat\":${LAT},\"lon\":${LON},\"contours\":[10,20,30],\"mode\":\"${MODE}\"}")
  FEATURES=$(json_field features)
  if [ "$STATUS" = "200" ] && [ "$FEATURES" = "3" ]; then
    pass "POST /isochrone ${MODE} -> 200, 3 features, $(json_field metadata duration_ms) ms"
  else
    fail "POST /isochrone ${MODE}" "status=${STATUS} $(head -c 300 /tmp/smoke_body.json)"
  fi
done

echo "[5/6] негативный сценарий: точка вне покрытия"
STATUS=$(http_status -X POST "${API}/isochrone" \
  -H 'Content-Type: application/json' \
  -d '{"lat":51.1605,"lon":71.4704,"contours":[10],"mode":"auto"}')
CODE=$(json_field code)
if [ "$STATUS" = "422" ] && [ "$CODE" = "POINT_OUT_OF_COVERAGE" ]; then
  pass "POST /isochrone вне bbox -> 422 POINT_OUT_OF_COVERAGE"
else
  fail "POST /isochrone вне bbox" "status=${STATUS} code=${CODE}"
fi

echo "[6/6] негативный сценарий: невалидные параметры"
STATUS=$(http_status -X POST "${API}/isochrone" \
  -H 'Content-Type: application/json' \
  -d "{\"lat\":${LAT},\"lon\":${LON},\"contours\":[61],\"mode\":\"auto\"}")
CODE=$(json_field code)
if [ "$STATUS" = "400" ] && [ "$CODE" = "VALIDATION_ERROR" ]; then
  pass "POST /isochrone contours=[61] -> 400 VALIDATION_ERROR"
else
  fail "POST /isochrone contours=[61]" "status=${STATUS} code=${CODE}"
fi

echo
echo "passed: ${PASSED}, failed: ${FAILED}"

if [ "$FAILED" -gt 0 ]; then
  exit 1
fi
exit 0
