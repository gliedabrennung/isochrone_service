import json

import pytest

from app.core.errors import ProblemError

ENDPOINT = "/api/v1/isochrone"
ORIGIN = {"lat": 43.238949, "lon": 76.889709}


def payload(**overrides):
    body = {**ORIGIN, "contours": [10, 20, 30], "mode": "pedestrian"}
    body.update(overrides)
    return body


def test_health_is_independent_of_dependencies(api):
    response = api.client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["X-Request-Id"]


def test_ready_reports_every_component(api):
    response = api.client.get("/api/v1/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in ("ok", "degraded")
    assert set(body["components"]) == {"engine", "cache", "water_layer", "dataset"}
    assert body["components"]["engine"] == "ok"


def test_ready_returns_503_while_the_engine_is_unavailable(api):
    api.engine.error = RuntimeError("tiles are still being built")
    response = api.client.get("/api/v1/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
    assert response.json()["components"]["engine"] == "unavailable"


def test_ready_stays_200_when_only_the_cache_is_down(api):
    api.cache.enabled = False
    response = api.client.get("/api/v1/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["components"]["engine"] == "ok"
    assert body["components"]["cache"] == "unavailable"
    assert "кэш" in body["detail"].lower()


def test_meta_describes_coverage_limits_and_defaults(api):
    body = api.client.get("/api/v1/meta").json()
    assert body["engine"] == "valhalla"
    assert body["modes"] == ["pedestrian", "bicycle", "auto"]
    assert body["coverage"]["bbox"] == [76.6, 43.05, 77.2, 43.45]
    assert body["coverage"]["geometry"]["type"] == "Polygon"
    assert body["limits"]["max_contours"] == 4
    assert body["limits"]["min_contour_minutes"] == 5
    assert body["limits"]["max_contour_minutes"] == 60
    assert body["osm_data_timestamp"] == "2026-05-01T00:00:00Z"
    assert body["data_version"] == "testfixture0001"


def test_metrics_are_exposed_in_prometheus_format(api):
    api.client.post(ENDPOINT, json=payload())
    response = api.client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "isochrone_requests_total" in response.text
    assert "isochrone_request_duration_seconds" in response.text
    assert "isochrone_cache_misses_total" in response.text


def test_successful_response_shape(api):
    response = api.client.post(ENDPOINT, json=payload())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/geo+json")
    assert response.headers["X-Cache"] == "MISS"
    assert response.headers["X-Request-Id"]

    body = response.json()
    assert body["type"] == "FeatureCollection"
    assert [feature["properties"]["contour_minutes"] for feature in body["features"]] == [
        30,
        20,
        10,
    ]

    metadata = body["metadata"]
    assert metadata["cache"] == "miss"
    assert metadata["mode"] == "pedestrian"
    assert metadata["contours"] == [10, 20, 30]
    assert metadata["engine"] == "valhalla"
    assert metadata["engine_version"] == "3.5.1"
    assert metadata["osm_data_timestamp"] == "2026-05-01T00:00:00Z"
    assert metadata["origin"] == ORIGIN
    assert metadata["snap_distance_m"] == pytest.approx(12.0, abs=1.0)
    assert metadata["duration_ms"] >= 0
    assert metadata["denoise"] == 0.20
    assert metadata["generalize"] == 100


def test_key_metadata_is_duplicated_in_every_feature(api):
    body = api.client.post(ENDPOINT, json=payload()).json()
    for feature in body["features"]:
        properties = feature["properties"]
        assert properties["mode"] == "pedestrian"
        assert properties["engine"] == "valhalla"
        assert properties["engine_version"] == "3.5.1"
        assert properties["osm_data_timestamp"] == "2026-05-01T00:00:00Z"
        assert properties["request_id"] == body["metadata"]["request_id"]
        assert properties["cache"] == "miss"
        assert properties["color"].startswith("#")
        assert properties["fill_opacity"] == 0.25
        assert properties["area_km2"] > 0
        assert properties["perimeter_km"] > 0


def test_colors_are_stable_between_requests(api):
    first = api.client.post(ENDPOINT, json=payload()).json()
    api.cache.store.clear()
    second = api.client.post(ENDPOINT, json=payload(mode="bicycle")).json()
    assert [feature["properties"]["color"] for feature in first["features"]] == [
        feature["properties"]["color"] for feature in second["features"]
    ]
    assert first["features"][0]["properties"]["color"] == "#2b83ba"


def test_coordinates_are_trimmed_to_six_decimals(api):
    body = api.client.post(ENDPOINT, json=payload()).json()
    for feature in body["features"]:
        coordinates = feature["geometry"]["coordinates"]
        polygons = coordinates if feature["geometry"]["type"] == "MultiPolygon" else [coordinates]
        for polygon in polygons:
            for ring in polygon:
                for lon, lat in ring:
                    assert lon == round(lon, 6)
                    assert lat == round(lat, 6)


def test_engine_is_called_once_for_every_contour(api):
    api.client.post(ENDPOINT, json=payload())
    assert len(api.engine.calls) == 1
    assert api.engine.calls[0]["contours"] == [{"time": 10}, {"time": 20}, {"time": 30}]
    assert api.engine.calls[0]["polygons"] is True
    assert api.engine.calls[0]["show_locations"] is True


def test_cache_hit_on_the_second_identical_request(api):
    first = api.client.post(ENDPOINT, json=payload())
    second = api.client.post(ENDPOINT, json=payload())

    assert first.headers["X-Cache"] == "MISS"
    assert second.headers["X-Cache"] == "HIT"
    assert second.json()["metadata"]["cache"] == "hit"
    assert len(api.engine.calls) == 1


def test_nearby_coordinates_share_a_cache_entry(api):
    first = api.client.post(ENDPOINT, json=payload(lat=43.23891, lon=76.88971))
    second = api.client.post(ENDPOINT, json=payload(lat=43.23894, lon=76.88974))
    assert first.headers["X-Cache"] == "MISS"
    assert second.headers["X-Cache"] == "HIT"


def test_service_keeps_working_without_the_cache(api):
    api.cache.enabled = False
    first = api.client.post(ENDPOINT, json=payload())
    second = api.client.post(ENDPOINT, json=payload())
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.headers["X-Cache"] == "MISS"


def test_get_variant_is_equivalent(api):
    response = api.client.get(
        ENDPOINT,
        params={"lat": ORIGIN["lat"], "lon": ORIGIN["lon"], "contours": "10,20,30", "mode": "auto"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["metadata"]["mode"] == "auto"
    assert body["metadata"]["contours"] == [10, 20, 30]


def test_get_variant_supports_rings_and_water_flags(api):
    response = api.client.get(
        ENDPOINT,
        params={
            "lat": ORIGIN["lat"],
            "lon": ORIGIN["lon"],
            "contours": "10,20",
            "mode": "pedestrian",
            "rings": "true",
            "exclude_water": "false",
        },
    )
    assert response.status_code == 200
    assert response.json()["metadata"]["rings"] is True
    assert response.json()["metadata"]["exclude_water"] is False


def test_get_variant_rejects_a_malformed_contour_list(api):
    response = api.client.get(
        ENDPOINT,
        params={"lat": ORIGIN["lat"], "lon": ORIGIN["lon"], "contours": "10;20", "mode": "auto"},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_get_variant_reports_out_of_range_contours(api):
    response = api.client.get(
        ENDPOINT,
        params={"lat": ORIGIN["lat"], "lon": ORIGIN["lon"], "contours": "61", "mode": "auto"},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("contours", [[4], [61], [10, 10], [5, 10, 15, 20, 30], []])
def test_invalid_contours_are_rejected_with_400(api, contours):
    response = api.client.post(ENDPOINT, json=payload(contours=contours))
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert response.headers["content-type"].startswith("application/problem+json")
    assert body["instance"] == ENDPOINT
    assert body["request_id"] == response.headers["X-Request-Id"]


@pytest.mark.parametrize("contours", [[5], [60]])
def test_boundary_contours_are_accepted(api, contours):
    assert api.client.post(ENDPOINT, json=payload(contours=contours)).status_code == 200


def test_malformed_json_is_reported_separately(api):
    response = api.client.post(
        ENDPOINT, content="{not json", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "MALFORMED_JSON"


def test_oversized_body_is_rejected(api):
    response = api.client.post(
        ENDPOINT,
        content=json.dumps({**payload(), "padding": "x" * 20000}),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_point_outside_coverage_returns_422(api):
    response = api.client.post(ENDPOINT, json=payload(lat=51.1605, lon=71.4704))
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "POINT_OUT_OF_COVERAGE"
    assert "вне покрытия" in body["detail"]


def test_point_that_snaps_too_far_returns_422(api):
    api.engine.snap_offset_m = 1840.0
    response = api.client.post(ENDPOINT, json=payload())
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "POINT_NOT_ROUTABLE"
    assert body["snap_distance_m"] == pytest.approx(1840.0, abs=5.0)


def test_engine_unavailable_is_propagated_as_503(api):
    api.engine.error = ProblemError("ENGINE_UNAVAILABLE", "tiles are still being built")
    response = api.client.post(ENDPOINT, json=payload())
    assert response.status_code == 503
    assert response.json()["code"] == "ENGINE_UNAVAILABLE"


async def test_isochrone_returns_503_without_calling_an_engine_that_is_not_ready(api):
    api.engine.error = ConnectionRefusedError("connection refused")
    await api.monitor.refresh()
    api.engine.calls.clear()

    response = api.client.post(ENDPOINT, json=payload())

    assert response.status_code == 503
    assert response.json()["code"] == "ENGINE_UNAVAILABLE"
    assert api.engine.calls == []


def test_engine_timeout_is_propagated_as_504(api):
    api.engine.error = ProblemError("ENGINE_TIMEOUT", "too slow")
    response = api.client.post(ENDPOINT, json=payload())
    assert response.status_code == 504
    assert response.json()["code"] == "ENGINE_TIMEOUT"


def test_unhandled_engine_failure_does_not_leak_a_stack_trace(api):
    api.engine.error = RuntimeError("boom /srv/app/services/valhalla.py line 42")
    response = api.client.post(ENDPOINT, json=payload())
    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "INTERNAL_ERROR"
    assert "Traceback" not in response.text
    assert "/srv/app" not in response.text
    assert body["request_id"]


def test_incoming_request_id_is_echoed(api):
    response = api.client.post(ENDPOINT, json=payload(), headers={"X-Request-Id": "trace-42"})
    assert response.headers["X-Request-Id"] == "trace-42"
    assert response.json()["metadata"]["request_id"] == "trace-42"


def test_unknown_path_returns_problem_json(api):
    response = api.client.get("/api/v1/nope")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "NOT_FOUND"


def test_openapi_documents_every_endpoint_and_error(api):
    schema = api.client.get("/api/v1/openapi.json").json()
    assert set(schema["paths"]) >= {
        "/api/v1/isochrone",
        "/api/v1/health",
        "/api/v1/ready",
        "/api/v1/meta",
        "/metrics",
    }
    operation = schema["paths"]["/api/v1/isochrone"]["post"]
    assert set(operation["responses"]) >= {"200", "400", "422", "429", "500", "503", "504"}
    assert "application/geo+json" in operation["responses"]["200"]["content"]
    assert "application/problem+json" in operation["responses"]["422"]["content"]
    assert "ProblemDetail" in schema["components"]["schemas"]


def test_swagger_ui_is_served(api):
    response = api.client.get("/api/v1/docs")
    assert response.status_code == 200
    assert "swagger-ui" in response.text.lower()
