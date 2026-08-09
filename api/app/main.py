import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.config import API_PREFIX, get_settings
from app.core.errors import (
    ProblemError,
    http_exception_handler,
    problem_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from app.core.logging import configure_logging, get_logger
from app.core.middleware import (
    BodySizeLimitMiddleware,
    RateLimitMiddleware,
    RequestContextMiddleware,
)
from app.routers import isochrone as isochrone_router
from app.routers import system as system_router
from app.schemas import ProblemDetail
from app.services.cache import CacheService
from app.services.dataset import load_dataset_meta
from app.services.geometry import WaterIndex
from app.services.isochrone import IsochroneService
from app.services.valhalla import ValhallaClient

logger = get_logger("app.main")

DESCRIPTION = """
Сервис расчёта зон досягаемости (изохрон) на открытых данных OpenStreetMap
и открытом роутинг-движке Valhalla.

**Что делает.** По координатам точки старта, способу передвижения и одному или
нескольким временным порогам строит полигоны достижимости и возвращает их в виде
GeoJSON FeatureCollection.

**Особенности контракта**

* Ответ соответствует RFC 7946: порядок координат `[lon, lat]`, точность 6 знаков.
* `metadata` — foreign member верхнего уровня; ключевые поля продублированы
  в `properties` каждого `Feature` для совместимости с ГИС-редакторами.
* Ошибки соответствуют RFC 7807 (`application/problem+json`).
* Каждый ответ содержит заголовок `X-Request-Id`, успешный ответ — ещё и `X-Cache`.
"""

TAGS_METADATA = [
    {"name": "isochrone", "description": "Расчёт зон досягаемости."},
    {
        "name": "system",
        "description": "Служебные эндпоинты: liveness, readiness, метаданные, метрики.",
    },
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)

    logger.info(
        "service_starting",
        version=__version__,
        profile=settings.osm_profile,
        valhalla_url=settings.valhalla_url,
        data_dir=str(settings.data_dir),
    )

    app.state.settings = settings
    app.state.dataset_meta = load_dataset_meta(settings)
    app.state.water = await asyncio.to_thread(WaterIndex.from_file, settings.water_path)

    app.state.cache = CacheService(settings.redis_url, settings.cache_ttl_seconds)
    await app.state.cache.connect()

    app.state.engine = ValhallaClient(settings.valhalla_url, settings.engine_timeout_s)
    app.state.isochrone_service = IsochroneService(
        settings=settings,
        engine=app.state.engine,
        cache=app.state.cache,
        water=app.state.water,
        meta=app.state.dataset_meta,
    )

    logger.info(
        "service_started",
        water_parts=app.state.water.part_count,
        data_version=app.state.dataset_meta.data_version,
        osm_data_timestamp=app.state.dataset_meta.osm_data_timestamp,
    )

    try:
        yield
    finally:
        logger.info("service_stopping")
        await app.state.engine.aclose()
        await app.state.cache.aclose()
        logger.info("service_stopped")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="isochrone-service",
        version=__version__,
        summary="API расчёта зон досягаемости на OpenStreetMap и Valhalla",
        description=DESCRIPTION,
        openapi_tags=TAGS_METADATA,
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=f"{API_PREFIX}/redoc",
        openapi_url=f"{API_PREFIX}/openapi.json",
        lifespan=lifespan,
        license_info={"name": "ODbL for OSM data, see README"},
    )

    app.add_middleware(BodySizeLimitMiddleware, max_body_size=settings.max_body_size)
    app.add_middleware(
        RateLimitMiddleware,
        rate_per_second=settings.rate_limit_per_second,
        burst=settings.rate_limit_burst,
    )
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Request-Id"],
        expose_headers=["X-Request-Id", "X-Cache", "Retry-After"],
        max_age=600,
    )

    app.add_exception_handler(ProblemError, problem_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    app.include_router(isochrone_router.router, prefix=API_PREFIX)
    app.include_router(system_router.router, prefix=API_PREFIX)
    app.include_router(system_router.metrics_router)

    def custom_openapi() -> dict:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            summary=app.summary,
            description=app.description,
            routes=app.routes,
            tags=TAGS_METADATA,
            license_info=app.license_info,
        )
        schema.setdefault("components", {}).setdefault("schemas", {})["ProblemDetail"] = (
            ProblemDetail.model_json_schema(ref_template="#/components/schemas/{model}")
        )
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi
    return app


app = create_app()
