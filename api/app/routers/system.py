from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app import __version__
from app.config import ENGINE_NAME, SMOOTHING_DEFAULTS, get_settings
from app.core.metrics import render_metrics
from app.schemas import (
    ComponentState,
    HealthResponse,
    MetaCoverage,
    MetaLimits,
    MetaResponse,
    ReadyResponse,
    TravelMode,
)
from app.services.geometry import bbox_polygon
from app.services.valhalla import EngineState

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
        "Ответ на вопрос «можно ли направлять сюда трафик». Проверяются движок, кэш, "
        "слой воды и метаданные покрытия.\n\n"
        "Ответ 503 возвращается только при недоступности движка: без него расчёт невозможен. "
        "Недоступность кэша, слоя воды или метаданных переводит сервис в состояние degraded "
        "с кодом 200 — сервис отдаёт корректные ответы, теряя только скорость "
        "(п. 6.4 и 10.3 ТЗ). Система мониторинга различает ok и degraded по телу ответа, "
        "оркестратор — по HTTP-коду."
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
    monitor = getattr(request.app.state, "engine_monitor", None)
    cache = getattr(request.app.state, "cache", None)
    water = getattr(request.app.state, "water", None)
    meta = getattr(request.app.state, "dataset_meta", None)

    components: dict[str, ComponentState] = {}
    notes: list[str] = []

    engine_ok = False
    if monitor is None:
        components["engine"] = ComponentState.unavailable
        notes.append("Клиент движка не инициализирован.")
    else:
        engine_ok = await monitor.refresh() is EngineState.ready
        components["engine"] = ComponentState.ok if engine_ok else ComponentState.unavailable
        if not engine_ok:
            notes.append(monitor.unavailable_detail())

    if cache is not None and await cache.ping():
        components["cache"] = ComponentState.ok
    else:
        components["cache"] = ComponentState.unavailable
        notes.append("Кэш недоступен, запросы обрабатываются без кэширования.")

    if water is not None and water.available:
        components["water_layer"] = ComponentState.ok
    else:
        components["water_layer"] = ComponentState.degraded
        notes.append("Слой воды не загружен, вырезание водоёмов отключено.")

    if meta is not None and meta.complete:
        components["dataset"] = ComponentState.ok
    else:
        components["dataset"] = ComponentState.degraded
        notes.append("Метаданные покрытия из data/meta.json недоступны.")

    if not engine_ok:
        status = "unavailable"
        http_status = 503
    elif all(state is ComponentState.ok for state in components.values()):
        status = "ok"
        http_status = 200
    else:
        status = "degraded"
        http_status = 200

    payload = ReadyResponse(
        status=status,
        components=components,
        detail=" ".join(notes) if notes else None,
    )
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
