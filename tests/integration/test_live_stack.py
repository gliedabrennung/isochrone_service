import json
import os
from pathlib import Path

import httpx
import pytest
from shapely.geometry import Point, shape

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8080")
API = f"{BASE_URL}/api/v1"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 and start the stack with `make up`",
    ),
]


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=API, timeout=30.0) as session:
        yield session


@pytest.fixture(scope="module")
def control_points():
    payload = json.loads((FIXTURES / "control_points.json").read_text(encoding="utf-8"))
    return {point["id"]: point for point in payload["points"]}


def isochrone(client, **body):
    payload = {"lat": 43.238949, "lon": 76.889709, "contours": [10, 20, 30], "mode": "pedestrian"}
    payload.update(body)
    return client.post("/isochrone", json=payload)


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_ready(client):
    response = client.get("/ready")
    assert response.status_code == 200, response.text
    assert response.json()["components"]["engine"] == "ok"


def test_meta_reports_real_osm_timestamp(client):
    body = client.get("/meta").json()
    assert body["osm_data_timestamp"] != "unknown"
    assert body["engine_version"].startswith("3.")
    assert body["water_parts"] > 0


@pytest.mark.parametrize("mode", ["pedestrian", "bicycle", "auto"])
def test_every_mode_returns_polygons(client, mode):
    response = isochrone(client, mode=mode)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/geo+json")

    body = response.json()
    assert [feature["properties"]["contour_minutes"] for feature in body["features"]] == [
        30,
        20,
        10,
    ]
    for feature in body["features"]:
        geometry = shape(feature["geometry"])
        assert geometry.is_valid
        assert feature["properties"]["area_km2"] > 0


def test_areas_are_monotonic_and_nested(client):
    body = isochrone(client).json()
    shapes = {
        feature["properties"]["contour_minutes"]: shape(feature["geometry"])
        for feature in body["features"]
    }
    assert shapes[10].area < shapes[20].area < shapes[30].area
    for smaller, larger in ((10, 20), (20, 30)):
        assert shapes[smaller].difference(shapes[larger]).area <= shapes[smaller].area * 0.01


def test_snapped_origin_is_inside_the_smallest_contour(client):
    body = isochrone(client).json()
    snapped = body["metadata"]["snapped_origin"]
    smallest = min(body["features"], key=lambda feature: feature["properties"]["contour_minutes"])
    assert shape(smallest["geometry"]).contains(Point(snapped["lon"], snapped["lat"]))


def test_auto_covers_more_than_bicycle_and_pedestrian(client):
    def largest_area(mode):
        body = isochrone(client, mode=mode, contours=[15]).json()
        return body["features"][0]["properties"]["area_km2"]

    assert largest_area("auto") > largest_area("bicycle") > largest_area("pedestrian")


def test_cache_hit_is_fast_and_identical(client):
    first = isochrone(client, contours=[12, 24])
    second = isochrone(client, contours=[12, 24])
    assert first.headers["X-Cache"] == "MISS"
    assert second.headers["X-Cache"] == "HIT"
    assert second.json()["metadata"]["duration_ms"] <= 200
    assert [feature["geometry"] for feature in first.json()["features"]] == [
        feature["geometry"] for feature in second.json()["features"]
    ]


def test_rings_do_not_overlap(client):
    body = isochrone(client, options={"rings": True}).json()
    shapes = [shape(feature["geometry"]) for feature in body["features"]]
    for left in range(len(shapes)):
        for right in range(left + 1, len(shapes)):
            assert shapes[left].intersection(shapes[right]).area == pytest.approx(0.0, abs=1e-9)


def test_sayran_lake_is_cut_out(client, control_points):
    point = control_points[2]
    body = isochrone(client, lat=point["lat"], lon=point["lon"], contours=[20]).json()
    geometry = shape(body["features"][0]["geometry"])
    lake = shape(
        json.loads((FIXTURES / "data" / "water.geojson").read_text(encoding="utf-8"))["features"][
            0
        ]["geometry"]
    )
    assert geometry.intersection(lake).area <= lake.area * 0.05


def test_point_outside_coverage(client, control_points):
    point = control_points[7]
    response = isochrone(client, lat=point["lat"], lon=point["lon"])
    assert response.status_code == 422
    assert response.json()["code"] == "POINT_OUT_OF_COVERAGE"
    assert response.headers["content-type"].startswith("application/problem+json")


@pytest.mark.parametrize("contours", [[4], [61], [10, 10]])
def test_invalid_parameters(client, contours):
    response = isochrone(client, contours=contours)
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_metrics_endpoint(client):
    response = httpx.get(f"{BASE_URL}/metrics", timeout=10.0)
    assert response.status_code == 200
    assert "isochrone_requests_total" in response.text
