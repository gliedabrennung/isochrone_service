import asyncio
import contextlib
import time
from enum import StrEnum
from typing import Any

import httpx
from shapely.geometry import shape

from app.config import TILE_BUILD_ESTIMATE_MINUTES
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
_POINT_TYPES = ("Point", "MultiPoint")


class ValhallaClient:
    def __init__(
        self,
        base_url: str,
        timeout_s: float,
        connect_timeout_s: float = 1.0,
        max_connections: int = 32,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout_s, connect=connect_timeout_s),
            limits=httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=max_connections,
                keepalive_expiry=60.0,
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

    async def warmup(self, connections: int) -> int:
        if connections <= 0:
            return 0
        results = await asyncio.gather(
            *(self.status() for _ in range(connections)), return_exceptions=True
        )
        established = sum(1 for result in results if not isinstance(result, BaseException))
        logger.info("engine_pool_warmed", requested=connections, established=established)
        return established

    async def version(self) -> str:
        if self._version:
            return self._version
        try:
            await self.status()
        except Exception:
            return "unknown"
        return self._version or "unknown"

    async def _post_isochrone(self, payload: dict[str, Any]) -> httpx.Response:
        try:
            return await self._client.post("/isochrone", json=payload)
        except (httpx.ConnectTimeout, httpx.ConnectError) as exc:
            logger.warning("engine_connect_retry", error_type=type(exc).__name__, error=str(exc))
            return await self._client.post("/isochrone", json=payload)

    async def isochrone(self, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
        started = time.perf_counter()
        try:
            response = await self._post_isochrone(payload)
        except httpx.TimeoutException as exc:
            logger.warning(
                "engine_timeout",
                timeout_type=type(exc).__name__,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
            )
            raise ProblemError(
                "ENGINE_TIMEOUT",
                "Роутинг-движок не ответил в отведённое время. "
                "Уменьшите число или величину контуров.",
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning(
                "engine_transport_error",
                error_type=type(exc).__name__,
                error=str(exc),
                elapsed_ms=int((time.perf_counter() - started) * 1000),
            )
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


class EngineState(StrEnum):
    ready = "ready"
    preparing = "preparing"
    unavailable = "unavailable"


class EngineMonitor:
    def __init__(
        self,
        client: ValhallaClient,
        poll_interval_s: float,
        profile: str = "almaty",
        failure_threshold: int = 3,
    ) -> None:
        self.client = client
        self._poll_interval_s = poll_interval_s
        self._profile = profile
        self.failure_threshold = max(failure_threshold, 1)
        self._failures = 0
        self._state = EngineState.preparing
        self._error: str | None = None
        self._started_at = time.monotonic()
        self._task: asyncio.Task | None = None

    @property
    def state(self) -> EngineState:
        return self._state

    @property
    def ready(self) -> bool:
        return self._state is EngineState.ready

    @property
    def error(self) -> str | None:
        return self._error

    async def refresh(self) -> EngineState:
        previous = self._state
        try:
            await self.client.status()
            self._failures = 0
            self._state = EngineState.ready
            self._error = None
        except Exception as exc:
            self._failures += 1
            self._error = f"{type(exc).__name__}: {exc}"
            if previous is not EngineState.preparing and self._failures >= self.failure_threshold:
                self._state = EngineState.unavailable

        if self._state is not previous:
            logger.warning(
                "engine_state_changed",
                previous=previous.value,
                current=self._state.value,
                failures=self._failures,
                error=self._error,
            )
        return self._state

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="engine-monitor")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._task
        self._task = None

    async def _loop(self) -> None:
        while True:
            await self.refresh()
            await asyncio.sleep(self._poll_interval_s)

    def unavailable_detail(self) -> str:
        if self._state is EngineState.preparing:
            estimate = TILE_BUILD_ESTIMATE_MINUTES.get(self._profile, 30)
            elapsed = int((time.monotonic() - self._started_at) / 60)
            remaining = max(estimate - elapsed, 1)
            return (
                f"Идёт первичная подготовка данных и сборка тайлов Valhalla для профиля "
                f"'{self._profile}'. Прошло примерно {elapsed} мин, ориентировочно осталось "
                f"~{remaining} мин. Прогресс: docker compose logs -f valhalla. "
                "Сервис не сломан, расчёт станет доступен после готовности движка."
            )
        return (
            "Роутинг-движок не отвечает, расчёт временно невозможен. "
            f"Последняя ошибка: {self._error or 'нет данных'}. "
            "Проверьте состояние контейнера: docker compose ps valhalla."
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

        if geometry_type in _POINT_TYPES:
            if properties.get("type") == "snapped":
                coordinates = geometry.get("coordinates") or []
                if geometry_type == "MultiPoint":
                    coordinates = coordinates[0] if coordinates else []
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
