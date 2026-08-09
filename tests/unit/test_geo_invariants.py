import json
from pathlib import Path

import pytest
from shapely.geometry import Point, shape
from shapely.ops import unary_union

ENDPOINT = "/api/v1/isochrone"
ORIGIN = {"lat": 43.238949, "lon": 76.889709}
CONTOURS = [10, 20, 30]
NESTING_TOLERANCE = 0.01

pytestmark = pytest.mark.geo


def request_isochrone(api, **overrides):
    body = {**ORIGIN, "contours": CONTOURS, "mode": "pedestrian"}
    body.update(overrides)
    response = api.client.post(ENDPOINT, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def geometries(payload):
    return {
        feature["properties"]["contour_minutes"]: shape(feature["geometry"])
        for feature in payload["features"]
    }


def areas(payload):
    return {
        feature["properties"]["contour_minutes"]: feature["properties"]["area_km2"]
        for feature in payload["features"]
    }


@pytest.fixture
def lake():
    path = Path(__file__).resolve().parents[1] / "fixtures" / "data" / "water.geojson"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return unary_union([shape(feature["geometry"]) for feature in payload["features"]])


@pytest.mark.parametrize("mode", ["pedestrian", "bicycle", "auto"])
@pytest.mark.parametrize("rings", [False, True])
def test_geo_01_every_returned_geometry_is_valid(api, mode, rings):
    payload = request_isochrone(api, mode=mode, options={"rings": rings})
    for feature in payload["features"]:
        geometry = shape(feature["geometry"])
        assert geometry.is_valid
        assert not geometry.is_empty
        assert geometry.geom_type in ("Polygon", "MultiPolygon")


@pytest.mark.parametrize("mode", ["pedestrian", "bicycle", "auto"])
def test_geo_02_area_grows_monotonically_with_time(api, mode):
    payload = request_isochrone(api, mode=mode)
    by_minutes = areas(payload)
    assert by_minutes[10] < by_minutes[20] < by_minutes[30]


@pytest.mark.parametrize("mode", ["pedestrian", "bicycle", "auto"])
def test_geo_03_smaller_contours_are_contained_in_larger_ones(api, mode):
    shapes = geometries(request_isochrone(api, mode=mode))
    for smaller, larger in ((10, 20), (20, 30), (10, 30)):
        outside = shapes[smaller].difference(shapes[larger]).area
        assert outside <= shapes[smaller].area * NESTING_TOLERANCE


@pytest.mark.parametrize("mode", ["pedestrian", "bicycle", "auto"])
def test_geo_04_snapped_origin_lies_inside_the_smallest_contour(api, mode):
    payload = request_isochrone(api, mode=mode)
    snapped = payload["metadata"]["snapped_origin"]
    point = Point(snapped["lon"], snapped["lat"])
    assert geometries(payload)[10].contains(point)


@pytest.mark.parametrize("mode", ["pedestrian", "bicycle", "auto"])
def test_geo_05_rings_do_not_overlap(api, mode):
    shapes = geometries(request_isochrone(api, mode=mode, options={"rings": True}))
    minutes = sorted(shapes)
    for left in range(len(minutes)):
        for right in range(left + 1, len(minutes)):
            overlap = shapes[minutes[left]].intersection(shapes[minutes[right]])
            assert overlap.area == pytest.approx(0.0, abs=1e-12)


def test_geo_05_rings_partition_the_full_isochrone(api):
    plain = geometries(request_isochrone(api, options={"rings": False}))
    api.cache.store.clear()
    rings = geometries(request_isochrone(api, options={"rings": True}))
    union = unary_union(list(rings.values()))
    assert union.area == pytest.approx(plain[30].area, rel=1e-6)


def test_geo_06_auto_reaches_further_than_bicycle_and_pedestrian(api):
    pedestrian = areas(request_isochrone(api, mode="pedestrian"))[30]
    bicycle = areas(request_isochrone(api, mode="bicycle"))[30]
    auto = areas(request_isochrone(api, mode="auto"))[30]
    assert auto > bicycle > pedestrian


def test_geo_07_water_is_cut_out_of_the_result(api, lake):
    payload = request_isochrone(api, contours=[30], mode="pedestrian")
    for geometry in geometries(payload).values():
        assert geometry.intersection(lake).area == pytest.approx(0.0, abs=1e-12)


def test_geo_07_water_is_kept_when_exclusion_is_disabled(api, lake):
    payload = request_isochrone(api, contours=[30], options={"exclude_water": False})
    overlap = sum(geometry.intersection(lake).area for geometry in geometries(payload).values())
    assert overlap > 0


def test_geo_07_water_removal_reduces_the_reported_area(api):
    with_water = areas(request_isochrone(api, contours=[30], options={"exclude_water": False}))[30]
    without_water = areas(request_isochrone(api, contours=[30], options={"exclude_water": True}))[
        30
    ]
    assert without_water < with_water


def test_geo_08_cache_hit_returns_identical_geometry(api):
    first = api.client.post(ENDPOINT, json={**ORIGIN, "contours": CONTOURS, "mode": "auto"})
    second = api.client.post(ENDPOINT, json={**ORIGIN, "contours": CONTOURS, "mode": "auto"})

    assert first.headers["X-Cache"] == "MISS"
    assert second.headers["X-Cache"] == "HIT"

    first_features = first.json()["features"]
    second_features = second.json()["features"]
    assert [feature["geometry"] for feature in first_features] == [
        feature["geometry"] for feature in second_features
    ]
    assert [feature["properties"]["area_km2"] for feature in first_features] == [
        feature["properties"]["area_km2"] for feature in second_features
    ]
