import time

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app import __version__
from app.config import ENGINE_NAME, SMOOTHING_DEFAULTS, get_settings
from app.core.metrics import render_metrics
from app.schemas import (
    ComponentHealth,
    HealthResponse,
    MetaCoverage,
    MetaLimits,
    MetaResponse,
    ReadyResponse,
    TravelMode,
)
from app.services.geometry import bbox_polygon

router = APIRouter(tags=["system"])
metrics_router = APIRouter(tags=["system"])

PROBLEM_SCHEMA_REF = "#/components/schemas/ProblemDetail"
READY_SCHEMA_REF = "#/components/schemas/ReadyResponse"


@router.get(
    "/health",
    summary="Liveness",
    description="Проверка живости процесса. К зависимостям не обращается.",
    response_model=HealthResponse,
)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)


@router.get(
    "/ready",
    summary="Readiness",
    description=(
        "Проверка готовности обслуживать запросы. Обращается к Valhalla, Redis и слою воды.\n\n"
        "Ответ 503 возвращается только при недоступности движка: без него расчёт невозможен. "
        "Недоступность Redis или слоя воды переводит сервис в состояние degraded, "
        "но не приводит к отказу (см. п. 10.3 ТЗ)."
    ),
    response_model=ReadyResponse,
    responses={
        200: {"description": "Сервис готов (status = ok или degraded)."},
        503: {
            "description": "Движок недоступен, status = unavailable.",
            "content": {"application/json": {"schema": {"$ref": READY_SCHEMA_REF}}},
        },
    },
)
async def ready(request: Request) -> Response:
    engine = getattr(request.app.state, "engine", None)
    cache = getattr(request.app.state, "cache", None)
    water = getattr(request.app.state, "water", None)
    meta = getattr(request.app.state, "dataset_meta", None)

    components: dict[str, ComponentHealth] = {}

    engine_ok = False
    if engine is None:
        components["valhalla"] = ComponentHealth(
            status="unavailable", detail="client is not initialized"
        )
    else:
        started = time.perf_counter()
        try:
            status_payload = await engine.status()
            engine_ok = True
            components["valhalla"] = ComponentHealth(
                status="ok",
                detail=f"version {status_payload.get('version', 'unknown')}",
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        except Exception as exc:
            components["valhalla"] = ComponentHealth(
                status="unavailable",
                detail=f"{type(exc).__name__}: сборка тайлов не завершена либо движок не отвечает",
            )

    if cache is None:
        components["redis"] = ComponentHealth(status="degraded", detail="cache is disabled")
    else:
        latency = await cache.ping()
        if latency is None:
            components["redis"] = ComponentHealth(
                status="degraded", detail="кэш недоступен, сервис работает без кэша"
            )
        else:
            components["redis"] = ComponentHealth(status="ok", latency_ms=latency)

    if water is None or not water.available:
        components["water_layer"] = ComponentHealth(
            status="degraded", detail="слой воды не загружен, вырезание водоёмов отключено"
        )
    else:
        components["water_layer"] = ComponentHealth(status="ok", detail=f"{water.part_count} parts")

    if meta is None or not meta.complete:
        components["dataset"] = ComponentHealth(
            status="degraded", detail="metadata из data/meta.json недоступна"
        )
    else:
        components["dataset"] = ComponentHealth(
            status="ok", detail=f"profile {meta.profile}, osm {meta.osm_data_timestamp}"
        )

    if not engine_ok:
        status = "unavailable"
        http_status = 503
    elif any(component.status != "ok" for component in components.values()):
        status = "degraded"
        http_status = 200
    else:
        status = "ok"
        http_status = 200

    payload = ReadyResponse(status=status, components=components)
    return JSONResponse(status_code=http_status, content=payload.model_dump())


@router.get(
    "/meta",
    summary="Покрытие, версия данных и лимиты",
    description=(
        "Описание покрытия (bbox и его GeoJSON-границы), даты среза данных OSM, версии "
        "движка, доступных режимов передвижения, действующих лимитов и значений по умолчанию."
    ),
    response_model=MetaResponse,
)
async def meta(request: Request) -> MetaResponse:
    settings = get_settings()
    dataset = request.app.state.dataset_meta
    water = getattr(request.app.state, "water", None)
    engine = getattr(request.app.state, "engine", None)

    engine_version = await engine.version() if engine is not None else "unknown"

    return MetaResponse(
        service="isochrone-service",
        version=__version__,
        engine=ENGINE_NAME,
        engine_version=engine_version,
        modes=[TravelMode.pedestrian, TravelMode.bicycle, TravelMode.auto],
        osm_data_timestamp=dataset.osm_data_timestamp,
        data_version=dataset.data_version,
        coverage=MetaCoverage(
            profile=dataset.profile,
            bbox=list(dataset.bbox),
            geometry=bbox_polygon(dataset.bbox),
        ),
        limits=MetaLimits(
            max_contours=settings.max_contours,
            min_contour_minutes=settings.min_contour_minutes,
            max_contour_minutes=settings.max_contour_minutes,
            max_snap_distance_m=settings.max_snap_distance_m,
            max_body_size=settings.max_body_size,
            rate_limit=settings.rate_limit,
            engine_timeout_s=settings.engine_timeout_s,
        ),
        defaults={
            "rings": settings.rings_default,
            "exclude_water": settings.exclude_water_default,
            "smoothing": [
                {"max_contour_minutes": threshold, "denoise": denoise, "generalize": generalize}
                for threshold, denoise, generalize in SMOOTHING_DEFAULTS
            ],
        },
        water_parts=water.part_count if water is not None else 0,
    )


@metrics_router.get(
    "/metrics",
    summary="Метрики Prometheus",
    description="Экспорт метрик сервиса в формате Prometheus text exposition.",
    response_class=Response,
    responses={200: {"content": {"text/plain": {"schema": {"type": "string"}}}}},
)
async def metrics() -> Response:
    payload, content_type = render_metrics()
    return Response(content=payload, media_type=content_type)
