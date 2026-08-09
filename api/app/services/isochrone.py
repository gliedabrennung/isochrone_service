import time
from datetime import UTC, datetime
from typing import Any

from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

from app.config import (
    COORDINATE_PRECISION,
    COSTING_BY_MODE,
    ENGINE_NAME,
    FILL_OPACITY,
    MIN_MEANINGFUL_AREA_KM2,
    Settings,
    contour_color,
    smoothing_defaults,
)
from app.core.errors import ProblemError
from app.core.logging import get_logger
from app.core.metrics import CACHE_HITS, CACHE_MISSES, ENGINE_DURATION
from app.schemas import IsochroneRequest
from app.services.cache import CacheService, make_cache_key
from app.services.dataset import DatasetMeta
from app.services.geometry import (
    EMPTY,
    WaterIndex,
    area_perimeter_km,
    build_rings,
    enforce_nesting,
    geodesic_distance_m,
    normalize,
    point_in_bbox,
    round_geometry,
)
from app.services.valhalla import (
    ValhallaClient,
    build_isochrone_payload,
    parse_isochrone_response,
)

logger = get_logger("app.isochrone")


class IsochroneResult:
    __slots__ = ("cache_hit", "engine_ms", "payload", "snap_distance_m")

    def __init__(
        self,
        payload: dict[str, Any],
        cache_hit: bool,
        engine_ms: int,
        snap_distance_m: float | None,
    ) -> None:
        self.payload = payload
        self.cache_hit = cache_hit
        self.engine_ms = engine_ms
        self.snap_distance_m = snap_distance_m


class IsochroneService:
    def __init__(
        self,
        settings: Settings,
        engine: ValhallaClient,
        cache: CacheService,
        water: WaterIndex,
        meta: DatasetMeta,
    ) -> None:
        self.settings = settings
        self.engine = engine
        self.cache = cache
        self.water = water
        self.meta = meta

    def resolve_smoothing(self, request: IsochroneRequest) -> tuple[float, int]:
        denoise_default, generalize_default = smoothing_defaults(request.max_contour)
        denoise = (
            request.options.denoise if request.options.denoise is not None else denoise_default
        )
        generalize = (
            request.options.generalize
            if request.options.generalize is not None
            else generalize_default
        )
        return float(denoise), int(generalize)

    def check_coverage(self, lat: float, lon: float) -> None:
        if point_in_bbox(lat, lon, self.meta.bbox):
            return
        west, south, east, north = self.meta.bbox
        raise ProblemError(
            "POINT_OUT_OF_COVERAGE",
            (
                f"Точка ({lat:.6f}, {lon:.6f}) находится вне покрытия загруженных данных OSM. "
                f"Профиль '{self.meta.profile}', bbox {west}, {south}, {east}, {north}."
            ),
        )

    async def compute(
        self, request: IsochroneRequest, request_id: str, started: float
    ) -> IsochroneResult:
        self.check_coverage(request.lat, request.lon)
        denoise, generalize = self.resolve_smoothing(request)

        cache_key = make_cache_key(
            lat=request.lat,
            lon=request.lon,
            mode=request.mode.value,
            contours=request.contours,
            denoise=denoise,
            generalize=generalize,
            rings=request.options.rings,
            exclude_water=request.options.exclude_water,
            data_version=self.meta.data_version,
            coord_precision=self.settings.cache_coord_precision,
            departure_time=request.options.departure_time,
        )

        cached = await self.cache.get(cache_key)
        if cached is not None:
            CACHE_HITS.inc()
            payload = self._finalize(cached, request_id, started, cache_state="hit")
            return IsochroneResult(
                payload=payload,
                cache_hit=True,
                engine_ms=int(payload["metadata"].get("engine_ms") or 0),
                snap_distance_m=payload["metadata"].get("snap_distance_m"),
            )

        CACHE_MISSES.inc()
        engine_payload = build_isochrone_payload(
            lat=request.lat,
            lon=request.lon,
            costing=COSTING_BY_MODE[request.mode.value],
            contours=request.contours,
            denoise=denoise,
            generalize=generalize,
            request_id=request_id,
            departure_time=request.options.departure_time,
        )

        raw, engine_ms = await self.engine.isochrone(engine_payload)
        ENGINE_DURATION.labels(mode=request.mode.value).observe(engine_ms / 1000)

        polygons, snapped = parse_isochrone_response(raw, request.contours)
        snap_distance = self._check_snap(request, snapped)

        engine_version = await self.engine.version()
        features = self._build_features(request, polygons, request_id, engine_version)
        if not features:
            raise ProblemError(
                "EMPTY_RESULT",
                (
                    "Движок вернул пустую или вырожденную геометрию: площадь всех "
                    f"контуров меньше {MIN_MEANINGFUL_AREA_KM2} км². "
                    "Проверьте точку старта и режим передвижения."
                ),
            )

        stable = {
            "type": "FeatureCollection",
            "features": features,
            "metadata": {
                "origin": {"lat": request.lat, "lon": request.lon},
                "snapped_origin": (
                    {"lat": snapped[0], "lon": snapped[1]} if snapped is not None else None
                ),
                "snap_distance_m": snap_distance,
                "mode": request.mode.value,
                "contours": request.contours,
                "rings": request.options.rings,
                "exclude_water": request.options.exclude_water,
                "denoise": denoise,
                "generalize": generalize,
                "engine": ENGINE_NAME,
                "engine_version": engine_version,
                "osm_data_timestamp": self.meta.osm_data_timestamp,
                "data_version": self.meta.data_version,
                "engine_ms": engine_ms,
            },
        }

        await self.cache.set(cache_key, stable)
        payload = self._finalize(stable, request_id, started, cache_state="miss")
        return IsochroneResult(
            payload=payload,
            cache_hit=False,
            engine_ms=engine_ms,
            snap_distance_m=snap_distance,
        )

    def _check_snap(
        self, request: IsochroneRequest, snapped: tuple[float, float] | None
    ) -> float | None:
        if snapped is None:
            return None
        distance = round(geodesic_distance_m(request.lat, request.lon, snapped[0], snapped[1]), 1)
        if distance > self.settings.max_snap_distance_m:
            raise ProblemError(
                "POINT_NOT_ROUTABLE",
                (
                    f"Ближайший узел дорожного графа находится в {distance:.0f} м от точки, "
                    f"что превышает лимит {self.settings.max_snap_distance_m:.0f} м."
                ),
                extra={"snap_distance_m": distance},
            )
        return distance

    def _build_features(
        self,
        request: IsochroneRequest,
        polygons: dict[int, BaseGeometry],
        request_id: str,
        engine_version: str,
    ) -> list[dict[str, Any]]:
        ascending = [(minutes, polygons.get(minutes, EMPTY)) for minutes in request.contours]
        ascending = enforce_nesting(ascending)

        if request.options.exclude_water and self.water.available:
            ascending = [
                (minutes, self.water.subtract(geometry)) for minutes, geometry in ascending
            ]

        ascending = [
            (minutes, round_geometry(normalize(geometry), COORDINATE_PRECISION))
            for minutes, geometry in ascending
        ]

        if request.options.rings:
            ascending = build_rings(ascending)

        descending = list(reversed(ascending))
        computed_at = datetime.now(UTC).isoformat()

        features: list[dict[str, Any]] = []
        skipped: list[int] = []
        largest_area = 0.0

        for index, (minutes, geometry) in enumerate(descending):
            if geometry.is_empty:
                skipped.append(minutes)
                continue
            area_km2, perimeter_km = area_perimeter_km(geometry)
            largest_area = max(largest_area, area_km2)
            features.append(
                {
                    "type": "Feature",
                    "geometry": mapping(geometry),
                    "properties": {
                        "contour_minutes": minutes,
                        "mode": request.mode.value,
                        "color": contour_color(index),
                        "fill_opacity": FILL_OPACITY,
                        "area_km2": area_km2,
                        "perimeter_km": perimeter_km,
                        "rings": request.options.rings,
                        "engine": ENGINE_NAME,
                        "engine_version": engine_version,
                        "osm_data_timestamp": self.meta.osm_data_timestamp,
                        "computed_at": computed_at,
                        "request_id": request_id,
                        "cache": "miss",
                    },
                }
            )

        if skipped:
            logger.warning("contours_without_geometry", contours=skipped)

        if largest_area < MIN_MEANINGFUL_AREA_KM2:
            return []

        return features

    def _finalize(
        self,
        stable: dict[str, Any],
        request_id: str,
        started: float,
        *,
        cache_state: str,
    ) -> dict[str, Any]:
        computed_at = datetime.now(UTC).isoformat()
        features = []
        for feature in stable["features"]:
            properties = dict(feature["properties"])
            properties["request_id"] = request_id
            properties["cache"] = cache_state
            if cache_state == "hit":
                properties["computed_at"] = computed_at
            features.append({**feature, "properties": properties})

        metadata = dict(stable["metadata"])
        metadata["request_id"] = request_id
        metadata["computed_at"] = computed_at
        metadata["cache"] = cache_state
        metadata["duration_ms"] = int((time.perf_counter() - started) * 1000)
        metadata.setdefault("engine_ms", 0)

        return {"type": "FeatureCollection", "features": features, "metadata": metadata}
