import json
import math

import pytest
from shapely.geometry import MultiPolygon, Point, Polygon, box, mapping, shape

from app.services.geometry import (
    WaterIndex,
    area_perimeter_km,
    bbox_polygon,
    build_rings,
    enforce_nesting,
    geodesic_distance_m,
    normalize,
    point_in_bbox,
    polygonal,
    repair,
    round_geometry,
)

ALMATY_BBOX = (76.60, 43.05, 77.20, 43.45)


def circle(lat: float, lon: float, radius_km: float, points: int = 180) -> Polygon:
    delta_lat = radius_km / 111.32
    delta_lon = radius_km / (111.32 * math.cos(math.radians(lat)))
    ring = [
        (
            lon + delta_lon * math.cos(2 * math.pi * index / points),
            lat + delta_lat * math.sin(2 * math.pi * index / points),
        )
        for index in range(points)
    ]
    return Polygon(ring)


def test_repair_fixes_a_self_intersecting_bowtie():
    bowtie = Polygon([(0, 0), (2, 2), (2, 0), (0, 2), (0, 0)])
    assert not bowtie.is_valid
    fixed = repair(bowtie)
    assert fixed.is_valid


def test_repair_returns_valid_geometry_untouched():
    square = box(0, 0, 1, 1)
    assert repair(square) is square


def test_polygonal_keeps_only_areal_parts():
    collection = shape(
        {
            "type": "GeometryCollection",
            "geometries": [
                {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
                {"type": "LineString", "coordinates": [[0, 0], [5, 5]]},
                {"type": "Point", "coordinates": [3, 3]},
            ],
        }
    )
    result = polygonal(collection)
    assert result.geom_type in ("Polygon", "MultiPolygon")
    assert result.area == pytest.approx(1.0)


def test_polygonal_drops_non_areal_geometry():
    assert polygonal(Point(0, 0)).is_empty


def test_round_geometry_limits_coordinate_precision():
    polygon = Polygon([(0.1234567891, 0.9876543219), (1.5, 0), (1.5, 1.5), (0, 1.5)])
    rounded = round_geometry(polygon, 6)
    for x, y in rounded.exterior.coords:
        assert round(x, 6) == pytest.approx(x, abs=1e-12)
        assert round(y, 6) == pytest.approx(y, abs=1e-12)
    assert rounded.is_valid


def test_area_perimeter_of_a_known_circle():
    geometry = circle(43.238949, 76.889709, 2.5)
    area_km2, perimeter_km = area_perimeter_km(geometry)
    assert area_km2 == pytest.approx(math.pi * 2.5**2, rel=0.01)
    assert perimeter_km == pytest.approx(2 * math.pi * 2.5, rel=0.01)


def test_area_of_an_empty_geometry_is_zero():
    assert area_perimeter_km(Polygon()) == (0.0, 0.0)


def test_area_accounts_for_holes():
    outer = circle(43.238949, 76.889709, 2.0)
    inner = circle(43.238949, 76.889709, 1.0)
    ring = outer.difference(inner)
    area_km2, _ = area_perimeter_km(ring)
    assert area_km2 == pytest.approx(math.pi * (2.0**2 - 1.0**2), rel=0.01)


def test_geodesic_distance_matches_a_known_offset():
    distance = geodesic_distance_m(43.238949, 76.889709, 43.238949 + 1 / 111_320, 76.889709)
    assert distance == pytest.approx(1.0, abs=0.05)


def test_enforce_nesting_makes_contours_monotonic():
    small = box(0, 0, 2, 2)
    broken_large = box(0.5, 0.5, 3, 3)
    result = enforce_nesting([(10, small), (20, broken_large)])
    assert result[1][1].contains(result[0][1].buffer(-1e-9))
    assert result[1][1].area > result[0][1].area


def test_enforce_nesting_tolerates_missing_contours():
    result = enforce_nesting([(10, Polygon()), (20, box(0, 0, 1, 1))])
    assert result[0][1].is_empty
    assert result[1][1].area == pytest.approx(1.0)


def test_enforce_nesting_carries_the_previous_contour_forward():
    result = enforce_nesting([(10, box(0, 0, 1, 1)), (20, Polygon())])
    assert result[1][1].area == pytest.approx(1.0)


def test_build_rings_produces_disjoint_bands():
    ordered = [(10, box(0, 0, 1, 1)), (20, box(0, 0, 2, 2)), (30, box(0, 0, 3, 3))]
    rings = build_rings(ordered)
    assert [minutes for minutes, _ in rings] == [10, 20, 30]
    assert rings[0][1].area == pytest.approx(1.0)
    assert rings[1][1].area == pytest.approx(3.0)
    assert rings[2][1].area == pytest.approx(5.0)
    for left in range(len(rings)):
        for right in range(left + 1, len(rings)):
            assert rings[left][1].intersection(rings[right][1]).area == pytest.approx(0.0)


def test_point_in_bbox():
    assert point_in_bbox(43.238949, 76.889709, ALMATY_BBOX)
    assert not point_in_bbox(51.1605, 71.4704, ALMATY_BBOX)
    assert point_in_bbox(43.05, 76.60, ALMATY_BBOX)


def test_bbox_polygon_is_a_closed_ring():
    geometry = bbox_polygon(ALMATY_BBOX)
    assert geometry["type"] == "Polygon"
    ring = geometry["coordinates"][0]
    assert ring[0] == ring[-1]
    assert shape(geometry).is_valid


def test_water_index_subtracts_intersecting_polygons():
    lake = box(0.4, 0.4, 0.6, 0.6)
    index = WaterIndex([lake])
    result = index.subtract(box(0, 0, 1, 1))
    assert result.area == pytest.approx(1.0 - 0.04)
    assert result.intersection(lake).area == pytest.approx(0.0)


def test_water_index_returns_input_when_nothing_intersects():
    index = WaterIndex([box(10, 10, 11, 11)])
    original = box(0, 0, 1, 1)
    assert index.subtract(original).area == pytest.approx(original.area)


def test_empty_water_index_is_a_no_op():
    index = WaterIndex.empty()
    assert not index.available
    assert index.part_count == 0
    assert index.subtract(box(0, 0, 1, 1)).area == pytest.approx(1.0)


def test_water_index_loads_from_file(tmp_path):
    path = tmp_path / "water.geojson"
    path.write_text(
        '{"type":"FeatureCollection","features":[{"type":"Feature","properties":{},'
        '"geometry":{"type":"Polygon","coordinates":[[[0,0],[1,0],[1,1],[0,1],[0,0]]]}}]}',
        encoding="utf-8",
    )
    index = WaterIndex.from_file(path)
    assert index.available
    assert index.part_count == 1


def test_water_index_explodes_multipolygons(tmp_path):
    path = tmp_path / "water.geojson"
    multi = MultiPolygon([box(0, 0, 1, 1), box(3, 3, 4, 4)])
    payload = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {}, "geometry": mapping(multi)}],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    index = WaterIndex.from_file(path)
    assert index.part_count == 2


def test_water_index_degrades_when_the_file_is_missing(tmp_path):
    index = WaterIndex.from_file(tmp_path / "absent.geojson")
    assert not index.available


def test_water_index_degrades_on_broken_json(tmp_path):
    path = tmp_path / "water.geojson"
    path.write_text("{not json", encoding="utf-8")
    assert not WaterIndex.from_file(path).available


def test_normalize_returns_polygonal_geometry():
    bowtie = Polygon([(0, 0), (2, 2), (2, 0), (0, 2), (0, 0)])
    result = normalize(bowtie)
    assert result.is_valid
    assert result.geom_type in ("Polygon", "MultiPolygon")
