# isochrone-service

![Demo: isochrones from a point in Almaty, 20/30/45/60 min rings, Sayran lake cut out](docs/img.png)

Self-hosted isochrone (reachability) service built on OpenStreetMap data and the Valhalla routing engine. It answers "where can I get from here in N minutes" on foot, by bike or by car and returns a valid GeoJSON FeatureCollection. No paid external APIs; a MapLibre GL JS demo page is included.

## Requirements

Docker 24+ with Compose v2, 4 vCPU, 8 GB RAM, 12 GB free disk (`almaty` profile), internet access on first run.

## Quick start

```bash
git clone https://github.com/gliedabrennung/isochrone_service.git
cd isochrone_service
cp .env.example .env
docker compose up -d
```

- Demo page: <http://localhost:8080>
- Swagger UI: <http://localhost:8080/api/v1/docs>

The first run downloads the OSM extract (~211 MB) and builds routing tiles (about a minute for `almaty`). Until the engine is ready, `/api/v1/ready` and `/api/v1/isochrone` return `503 ENGINE_UNAVAILABLE`. Check readiness:

```bash
curl -s localhost:8080/api/v1/ready
```

Restarts (`docker compose down && up`) do not rebuild tiles.

## API

Base path `/api/v1`. OpenAPI schema: `docs/openapi.yaml`.

| Endpoint | Description |
|---|---|
| `POST /isochrone` | Compute isochrones (`GET` with query params also works) |
| `GET /health`, `GET /ready` | Liveness / readiness |
| `GET /meta` | Coverage, engine and data version |
| `GET /metrics` (root path) | Prometheus metrics |

```bash
curl -s -X POST localhost:8080/api/v1/isochrone \
  -H 'Content-Type: application/json' \
  -d '{
        "lat": 43.238949,
        "lon": 76.889709,
        "contours": [10, 20, 30],
        "mode": "pedestrian",
        "options": {"rings": false, "exclude_water": true}
      }'
```

- `mode`: `pedestrian` | `bicycle` | `auto`
- `contours`: 1–4 values, 5–60 minutes
- Response: `application/geo+json`, one Feature per contour (largest first) with area, perimeter and color; request metadata under `metadata`; `X-Request-Id` and `X-Cache` headers.

Errors follow RFC 7807 (`application/problem+json`) with a `code` field: `VALIDATION_ERROR`, `MALFORMED_JSON` (400); `POINT_OUT_OF_COVERAGE`, `POINT_NOT_ROUTABLE`, `EMPTY_RESULT` (422); `RATE_LIMIT_EXCEEDED` (429); `INTERNAL_ERROR` (500); `ENGINE_UNAVAILABLE` (503); `ENGINE_TIMEOUT` (504).

## Configuration

All variables are documented in `.env.example`. The main ones:

| Variable | Default | Description |
|---|---|---|
| `OSM_PROFILE` | `almaty` | `almaty` \| `almaty-region` \| `kazakhstan` |
| `OSM_EXTRACT_URL` | — | Custom PBF URL (overrides the profile source) |
| `COVERAGE_BBOX` | — | Custom coverage bbox, `W,S,E,N` |
| `RATE_LIMIT` / `RATE_LIMIT_BURST` | `10/second` / `20` | Per-IP rate limit |
| `CACHE_TTL_SECONDS` | `604800` | Cache TTL (7 days) |
| `ENGINE_TIMEOUT_S` | `10` | Engine response timeout |
| `BASEMAP_TILE_URL` | OSM tiles | Demo page basemap |
| `WEB_PORT` | `8080` | The only port published outside |

## Commands

```bash
make up                 # production profile
make dev                # hot reload, ports exposed
make down               # stop, keep data
make clean              # stop and delete volumes
make update-data        # re-download OSM data, rebuild tiles (after changing OSM_PROFILE too)
make smoke              # quick end-to-end check
make lint               # ruff
make test-unit          # unit tests (coverage >= 70%)
make test-integration   # needs a running stack
make test-contract      # OpenAPI fuzzing (schemathesis)
make load-test          # k6, 10 RPS x 5 min
```

To change the region, set `OSM_PROFILE` in `.env` (or `OSM_EXTRACT_URL` + `COVERAGE_BBOX` for a custom area) and run `make update-data`. OSM data is not refreshed automatically; `make update-data` does it manually.

## Architecture

```
browser → nginx (web, :8080) → FastAPI (api) → Valhalla 3.5.1
                                    ├── Redis (optional cache)
                                    └── water.geojson (in-memory index)
data-prep (one-shot job): PBF download, bbox cut, water layer, metadata
```

- Only `web` is published; `api`, `valhalla` and `redis` stay inside the compose network.
- Redis is optional: if it goes down, the service keeps working without cache (`/ready` reports `degraded`).
- Water bodies and buffered rivers/canals are subtracted from the polygons; contours are nested and valid by construction.
- Data version (profile, bbox, OSM date) is part of the cache key, so updated data invalidates the cache automatically.

## Troubleshooting

- **Everything returns 503 after start:** tiles are still building, see `docker compose logs -f valhalla`.
- **`valhalla` restarts (OOM):** use the `almaty` profile, lower `VALHALLA_SERVER_THREADS`, give Docker more RAM.
- **Gray map:** `curl localhost:8080/config.js` — `basemapTileUrl` must contain `{z}`, `{x}`, `{y}`.
- **Port 8080 busy:** change `WEB_PORT` in `.env`.

## License and attribution

Data © OpenStreetMap contributors ([ODbL](https://opendatacommons.org/licenses/odbl/1-0/)). Valhalla: MIT. MapLibre GL JS: BSD-3-Clause. Osmium Tool: GPL-3.0. GDAL/OGR: MIT-style. Shapely/pyproj: BSD.# isochrone-service
