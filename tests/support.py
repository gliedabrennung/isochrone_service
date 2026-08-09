import json
import math
from dataclasses import dataclass, field
from typing import Any

ORIGIN_LAT = 43.238949
ORIGIN_LON = 76.889709
SPEED_KMH = {"pedestrian": 5.0, "bicycle": 15.0, "auto": 40.0}
LAKE_CENTER = (43.2440, 76.8680)


def circle_coordinates(
    lat: float, lon: float, radius_km: float, points: int = 72
) -> list[list[float]]:
    delta_lat = radius_km / 111.32
    delta_lon = radius_km / (111.32 * max(math.cos(math.radians(lat)), 1e-6))
    ring = []
    for index in range(points):
        angle = 2 * math.pi * index / points
        ring.append(
            [
                round(lon + delta_lon * math.cos(angle), 8),
                round(lat + delta_lat * math.sin(angle), 8),
            ]
        )
    ring.append(ring[0])
    return ring


def build_engine_response(
    lat: float,
    lon: float,
    contours: list[int],
    costing: str,
    snap_offset_m: float = 12.0,
) -> dict[str, Any]:
    speed = SPEED_KMH.get(costing, 5.0)
    features: list[dict[str, Any]] = []

    for minutes in sorted(contours, reverse=True):
        radius_km = speed * minutes / 60.0
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [circle_coordinates(lat, lon, radius_km)],
                },
                "properties": {
                    "contour": float(minutes),
                    "metric": "time",
                    "color": "ff0000",
                    "opacity": 0.33,
                },
            }
        )

    snapped_lat = lat + snap_offset_m / 111_320.0
    features.append(
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"type": "input", "location_index": 0},
        }
    )
    features.append(
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, snapped_lat]},
            "properties": {"type": "snapped", "location_index": 0},
        }
    )

    return {"type": "FeatureCollection", "features": features}


@dataclass
class StubEngine:
    version_value: str = "3.5.1"
    snap_offset_m: float = 12.0
    engine_ms: int = 42
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    @property
    def cached_version(self) -> str:
        return self.version_value

    async def status(self) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        return {"version": self.version_value, "tileset_last_modified": 1748736000}

    async def version(self) -> str:
        return self.version_value

    async def isochrone(self, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
        self.calls.append(payload)
        if self.error is not None:
            raise self.error
        location = payload["locations"][0]
        contours = [item["time"] for item in payload["contours"]]
        response = build_engine_response(
            location["lat"],
            location["lon"],
            contours,
            payload["costing"],
            snap_offset_m=self.snap_offset_m,
        )
        return response, self.engine_ms

    async def aclose(self) -> None:
        return None


class FakeCache:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.reads = 0
        self.writes = 0
        self.enabled = True

    async def connect(self) -> None:
        return None

    async def aclose(self) -> None:
        return None

    async def ping(self) -> bool:
        return self.enabled

    async def get(self, key: str) -> dict[str, Any] | None:
        self.reads += 1
        if not self.enabled:
            return None
        raw = self.store.get(key)
        return json.loads(raw) if raw else None

    async def set(self, key: str, payload: dict[str, Any]) -> None:
        self.writes += 1
        if self.enabled:
            self.store[key] = json.dumps(payload)
