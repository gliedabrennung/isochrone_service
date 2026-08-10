import time
from typing import Any

import orjson
import structlog
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.config import contour_bucket
from app.core.errors import PROBLEM_MEDIA_TYPE, ProblemError
from app.core.logging import request_id_var
from app.core.metrics import REQUEST_DURATION, REQUESTS_TOTAL
from app.schemas import (
    CONTOURS_QUERY_PATTERN,
    DEPARTURE_TIME_PATTERN,
    IsochroneFeatureCollection,
    IsochroneRequest,
)
from app.services.isochrone import IsochroneService

GEOJSON_MEDIA_TYPE = "application/geo+json"
PROBLEM_SCHEMA_REF = "#/components/schemas/ProblemDetail"

router = APIRouter(tags=["isochrone"])


class GeoJSONResponse(JSONResponse):
    media_type = GEOJSON_MEDIA_TYPE

    def render(self, content: Any) -> bytes:
        return orjson.dumps(content, option=orjson.OPT_SERIALIZE_NUMPY)


def _problem_doc(description: str) -> dict:
    return {
        "description": description,
        "content": {PROBLEM_MEDIA_TYPE: {"schema": {"$ref": PROBLEM_SCHEMA_REF}}},
    }


ERROR_RESPONSES = {
    400: _problem_doc("VALIDATION_ERROR или MALFORMED_JSON: нарушены правила валидации запроса."),
    422: _problem_doc(
        "POINT_OUT_OF_COVERAGE, POINT_NOT_ROUTABLE или EMPTY_RESULT: "
        "запрос корректен, но расчёт для этой точки невозможен."
    ),
    429: _problem_doc(
        "RATE_LIMIT_EXCEEDED: превышен лимит запросов на IP, см. заголовок Retry-After."
    ),
    500: _problem_doc(
        "INTERNAL_ERROR: необработанное исключение, подробности — в логах по request_id."
    ),
    503: _problem_doc("ENGINE_UNAVAILABLE: движок недоступен или не завершил сборку тайлов."),
    504: _problem_doc("ENGINE_TIMEOUT: движок не уложился в ENGINE_TIMEOUT_S."),
}

SUCCESS_RESPONSE = {
    200: {
        "description": (
            "GeoJSON FeatureCollection: один Feature на каждый временной порог, "
            "от большего времени к меньшему. Заголовок X-Cache содержит HIT либо MISS."
        )
    }
}


def get_service(request: Request) -> IsochroneService:
    service = getattr(request.app.state, "isochrone_service", None)
    if service is None:
        raise ProblemError(
            "ENGINE_UNAVAILABLE",
            "Сервис ещё не завершил инициализацию. Повторите запрос через несколько секунд.",
        )
    return service


async def _handle(request: Request, payload: IsochroneRequest) -> GeoJSONResponse:
    started = time.perf_counter()
    service = get_service(request)

    structlog.contextvars.bind_contextvars(
        mode=payload.mode.value,
        contours=payload.contours,
        lat=payload.lat,
        lon=payload.lon,
        rings=payload.options.rings,
    )

    bucket = contour_bucket(payload.max_contour)
    status_code = 200
    try:
        result = await service.compute(payload, request_id_var.get(), started)
    except ProblemError as exc:
        status_code = exc.status
        REQUESTS_TOTAL.labels(mode=payload.mode.value, status=str(status_code)).inc()
        REQUEST_DURATION.labels(
            mode=payload.mode.value, max_contour_bucket=bucket, cache="error"
        ).observe(time.perf_counter() - started)
        raise

    cache_state = "hit" if result.cache_hit else "miss"
    structlog.contextvars.bind_contextvars(
        cache_hit=result.cache_hit,
        engine_ms=result.engine_ms,
        snap_distance_m=result.snap_distance_m,
    )

    REQUESTS_TOTAL.labels(mode=payload.mode.value, status=str(status_code)).inc()
    REQUEST_DURATION.labels(
        mode=payload.mode.value, max_contour_bucket=bucket, cache=cache_state
    ).observe(time.perf_counter() - started)

    return GeoJSONResponse(
        content=result.payload,
        headers={"X-Cache": cache_state.upper(), "Cache-Control": "no-store"},
    )


@router.post(
    "/isochrone",
    summary="Рассчитать изохроны для точки",
    description=(
        "Строит зоны досягаемости для одной точки старта и одного или нескольких временных "
        "порогов за один вызов движка. Возвращает GeoJSON FeatureCollection: один Feature на "
        "каждый порог, в порядке от наибольшего времени к наименьшему.\n\n"
        "Пост-обработка: починка геометрии, вырезание водоёмов, опциональные кольца, "
        "геодезический расчёт площади и периметра."
    ),
    response_model=IsochroneFeatureCollection,
    response_class=GeoJSONResponse,
    responses={**SUCCESS_RESPONSE, **ERROR_RESPONSES},
)
async def post_isochrone(request: Request, payload: IsochroneRequest) -> GeoJSONResponse:
    return await _handle(request, payload)


@router.get(
    "/isochrone",
    summary="Рассчитать изохроны (query-вариант)",
    description=(
        "Функционально эквивалентен POST /isochrone, параметры передаются в query-строке. "
        "Предназначен для отладки и шаринга ссылок."
    ),
    response_model=IsochroneFeatureCollection,
    response_class=GeoJSONResponse,
    responses={**SUCCESS_RESPONSE, **ERROR_RESPONSES},
)
async def get_isochrone(
    request: Request,
    lat: float = Query(description="Широта точки старта, WGS84.", examples=[43.238949]),
    lon: float = Query(description="Долгота точки старта, WGS84.", examples=[76.889709]),
    contours: str = Query(
        pattern=CONTOURS_QUERY_PATTERN,
        description="Временные пороги в минутах через запятую, например 10,20,30.",
        examples=["10,20,30"],
    ),
    mode: str = Query(
        default="pedestrian",
        description="Способ передвижения: pedestrian | bicycle | auto.",
        examples=["pedestrian"],
    ),
    rings: bool | None = Query(default=None, description="Возвращать контуры как кольца."),
    exclude_water: bool | None = Query(default=None, description="Вырезать водные объекты."),
    denoise: float | None = Query(default=None, description="Параметр denoise движка, 0.0…1.0."),
    generalize: int | None = Query(
        default=None, description="Параметр generalize движка, 0…500 м."
    ),
    departure_time: str | None = Query(
        default=None,
        pattern=DEPARTURE_TIME_PATTERN,
        description="Время отправления, YYYY-MM-DDTHH:MM (резервный параметр).",
    ),
) -> GeoJSONResponse:
    try:
        parsed_contours = [int(item.strip()) for item in contours.split(",") if item.strip()]
    except ValueError as exc:
        raise ProblemError(
            "VALIDATION_ERROR",
            "contours: список целых чисел через запятую, например 10,20,30.",
        ) from exc

    options: dict[str, object] = {}
    if rings is not None:
        options["rings"] = rings
    if exclude_water is not None:
        options["exclude_water"] = exclude_water
    if denoise is not None:
        options["denoise"] = denoise
    if generalize is not None:
        options["generalize"] = generalize
    if departure_time:
        options["departure_time"] = departure_time

    try:
        payload = IsochroneRequest.model_validate(
            {
                "lat": lat,
                "lon": lon,
                "contours": parsed_contours,
                "mode": mode,
                "options": options,
            }
        )
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(item) for item in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise ProblemError("VALIDATION_ERROR", details or "invalid query parameters") from exc

    return await _handle(request, payload)
