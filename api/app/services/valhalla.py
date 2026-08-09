import time
from typing import Any

import httpx
from shapely.geometry import shape

from app.core.errors import ProblemError
from app.core.logging import get_logger

logger = get_logger("app.valhalla")

ENGINE_ERROR_MAP: dict[int, str] = {
    130: "VALIDATION_ERROR",
    131: "VALIDATION_ERROR",
    140: "VALIDATION_ERROR",
    141: "VALIDATION_ERROR",
    142: "VALIDATION_ERROR",
    143: "VALIDATION_ERROR",
    144: "VALIDATION_ERROR",
    145: "VALIDATION_ERROR",
    154: "POINT_NOT_ROUTABLE",
    170: "POINT_NOT_ROUTABLE",
    171: "POINT_NOT_ROUTABLE",
    199: "VALIDATION_ERROR",
}

_POLYGON_TYPES = ("Polygon", "MultiPolygon")


class ValhallaClient:
    def __init__(self, base_url: str, timeout_s: float, max_connections: int = 32) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout_s, connect=min(timeout_s, 5.0)),
            limits=httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=max_connections // 2 or 1,
            ),
            headers={"User-Agent": "isochrone-service/1.0"},
        )
        self._version: str | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def cached_version(self) -> str | None:
        return self._version

    async def status(self) -> dict[str, Any]:
        response = await self._client.get("/status", timeout=3.0)
        response.raise_for_status()
        payload = response.json()
        version = payload.get("version")
        if version:
            self._version = version
        return payload

    async def version(self) -> str:
        if self._version:
            return self._version
        try:
            await self.status()
        except Exception:
            return "unknown"
        return self._version or "unknown"

    async def isochrone(self, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
        started = time.perf_counter()
        try:
            response = await self._client.post("/isochrone", json=payload)
        except httpx.TimeoutException as exc:
            raise ProblemError(
                "ENGINE_TIMEOUT",
                "Роутинг-движок не ответил в отведённое время. "
                "Уменьшите число или величину контуров.",
            ) from exc
        except httpx.HTTPError as exc:
            raise ProblemError(
                "ENGINE_UNAVAILABLE",
                "Роутинг-движок недоступен. Возможно, ещё не завершена первичная сборка тайлов.",
            ) from exc

        engine_ms = int((time.perf_counter() - started) * 1000)

        if response.status_code >= 500:
            raise ProblemError(
                "ENGINE_UNAVAILABLE",
                "Роутинг-движок вернул внутреннюю ошибку.",
            )
        if response.status_code >= 400:
            self._raise_engine_error(response)

        try:
            return response.json(), engine_ms
        except ValueError as exc:
            raise ProblemError(
                "ENGINE_UNAVAILABLE", "Роутинг-движок вернул нечитаемый ответ."
            ) from exc

    def _raise_engine_error(self, response: httpx.Response) -> None:
        try:
            body = response.json()
        except ValueError:
            body = {}
        engine_code = body.get("error_code")
        engine_message = body.get("error") or "Роутинг-движок отклонил запрос."
        code = ENGINE_ERROR_MAP.get(engine_code, "VALIDATION_ERROR")
        logger.warning(
            "engine_rejected_request",
            engine_status=response.status_code,
            engine_code=engine_code,
            engine_message=engine_message,
        )
        raise ProblemError(
            code,
            f"{engine_message} (код движка {engine_code}).",
            extra={"engine_error_code": engine_code},
        )


def build_isochrone_payload(
    *,
    lat: float,
    lon: float,
    costing: str,
    contours: list[int],
    denoise: float,
    generalize: int,
    request_id: str,
    departure_time: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "locations": [{"lat": lat, "lon": lon}],
        "costing": costing,
        "contours": [{"time": minutes} for minutes in contours],
        "polygons": True,
        "denoise": denoise,
        "generalize": generalize,
        "show_locations": True,
        "id": request_id,
    }
    if departure_time:
        payload["date_time"] = {"type": 1, "value": departure_time}
    return payload


def parse_isochrone_response(
    payload: dict[str, Any], contours: list[int]
) -> tuple[dict[int, Any], tuple[float, float] | None]:
    features = payload.get("features") or []
    polygons: dict[int, Any] = {}
    snapped: tuple[float, float] | None = None

    for feature in features:
        geometry = feature.get("geometry") or {}
        properties = feature.get("properties") or {}
        geometry_type = geometry.get("type")

        if geometry_type == "Point":
            if properties.get("type") == "snapped":
                coordinates = geometry.get("coordinates") or []
                if len(coordinates) >= 2:
                    snapped = (float(coordinates[1]), float(coordinates[0]))
            continue

        if geometry_type not in _POLYGON_TYPES:
            continue

        contour_value = properties.get("contour")
        if contour_value is None:
            continue
        minutes = _match_contour(float(contour_value), contours)
        if minutes is None:
            continue

        parsed = shape(geometry)
        if minutes in polygons:
            polygons[minutes] = polygons[minutes].union(parsed)
        else:
            polygons[minutes] = parsed

    return polygons, snapped


def _match_contour(value: float, contours: list[int]) -> int | None:
    for minutes in contours:
        if abs(value - minutes) < 0.51:
            return minutes
    return None
