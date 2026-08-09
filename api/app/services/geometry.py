import json
from pathlib import Path
from typing import Any

import shapely
from pyproj import Geod
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.strtree import STRtree
from shapely.validation import make_valid

from app.core.logging import get_logger

logger = get_logger("app.geometry")

GEOD = Geod(ellps="WGS84")
EMPTY = Polygon()


def repair(geometry: BaseGeometry) -> BaseGeometry:
    if geometry.is_empty:
        return geometry
    if geometry.is_valid:
        return geometry
    buffered = geometry.buffer(0)
    if buffered.is_valid and not buffered.is_empty:
        return buffered
    return make_valid(geometry)


def polygonal(geometry: BaseGeometry) -> BaseGeometry:
    if geometry.is_empty:
        return EMPTY
    if geometry.geom_type == "Polygon":
        return geometry
    if geometry.geom_type == "MultiPolygon":
        return geometry
    if geometry.geom_type == "GeometryCollection":
        parts = [part for part in geometry.geoms if part.geom_type in ("Polygon", "MultiPolygon")]
        if not parts:
            return EMPTY
        return repair(unary_union(parts))
    return EMPTY


def normalize(geometry: BaseGeometry) -> BaseGeometry:
    return polygonal(repair(geometry))


def round_geometry(geometry: BaseGeometry, precision: int) -> BaseGeometry:
    if geometry.is_empty:
        return geometry
    grid_size = 10.0**-precision
    try:
        snapped = shapely.set_precision(geometry, grid_size)
    except Exception:
        return geometry
    snapped = polygonal(snapped)
    if snapped.is_empty or not snapped.is_valid:
        return geometry
    return snapped


def enforce_nesting(ordered: list[tuple[int, BaseGeometry]]) -> list[tuple[int, BaseGeometry]]:
    result: list[tuple[int, BaseGeometry]] = []
    accumulated: BaseGeometry | None = None
    for minutes, geometry in ordered:
        current = normalize(geometry)
        if accumulated is not None and not accumulated.is_empty:
            current = normalize(current.union(accumulated)) if not current.is_empty else accumulated
        accumulated = current
        result.append((minutes, current))
    return result


def build_rings(ordered: list[tuple[int, BaseGeometry]]) -> list[tuple[int, BaseGeometry]]:
    rings: list[tuple[int, BaseGeometry]] = []
    previous: BaseGeometry | None = None
    for minutes, geometry in ordered:
        if previous is None or previous.is_empty:
            ring = geometry
        else:
            ring = polygonal(geometry.difference(previous))
        rings.append((minutes, ring))
        previous = geometry
    return rings


def area_perimeter_km(geometry: BaseGeometry) -> tuple[float, float]:
    if geometry.is_empty:
        return 0.0, 0.0
    area_m2, perimeter_m = GEOD.geometry_area_perimeter(geometry)
    return round(abs(area_m2) / 1_000_000, 2), round(abs(perimeter_m) / 1000, 2)


def geodesic_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    _, _, distance = GEOD.inv(lon1, lat1, lon2, lat2)
    return abs(distance)


def bbox_polygon(bbox: tuple[float, float, float, float]) -> dict[str, Any]:
    west, south, east, north = bbox
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [west, south],
                [east, south],
                [east, north],
                [west, north],
                [west, south],
            ]
        ],
    }


def point_in_bbox(lat: float, lon: float, bbox: tuple[float, float, float, float]) -> bool:
    west, south, east, north = bbox
    return west <= lon <= east and south <= lat <= north


class WaterIndex:
    def __init__(self, parts: list[BaseGeometry]) -> None:
        self._parts = parts
        self._tree = STRtree(parts) if parts else None

    @property
    def part_count(self) -> int:
        return len(self._parts)

    @property
    def available(self) -> bool:
        return self._tree is not None

    @classmethod
    def empty(cls) -> "WaterIndex":
        return cls([])

    @classmethod
    def from_file(cls, path: Path) -> "WaterIndex":
        if not path.exists():
            logger.warning("water_layer_missing", path=str(path))
            return cls.empty()
        try:
            with path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError) as exc:
            logger.warning("water_layer_unreadable", path=str(path), error=str(exc))
            return cls.empty()

        parts: list[BaseGeometry] = []
        for feature in payload.get("features") or []:
            geometry = feature.get("geometry")
            if not geometry:
                continue
            parsed = normalize(shape(geometry))
            if parsed.is_empty:
                continue
            if isinstance(parsed, MultiPolygon):
                parts.extend(parsed.geoms)
            else:
                parts.append(parsed)

        logger.info("water_layer_loaded", path=str(path), parts=len(parts))
        return cls(parts)

    def subtract(self, geometry: BaseGeometry) -> BaseGeometry:
        if self._tree is None or geometry.is_empty:
            return geometry
        candidates = self._tree.query(geometry, predicate="intersects")
        if len(candidates) == 0:
            return geometry
        water = unary_union([self._parts[index] for index in candidates])
        return normalize(geometry.difference(water))
