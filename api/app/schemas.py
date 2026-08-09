from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import get_settings

_settings = get_settings()

MIN_MINUTES = _settings.min_contour_minutes
MAX_MINUTES = _settings.max_contour_minutes
MAX_CONTOURS = _settings.max_contours

DEPARTURE_TIME_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$"
CONTOURS_QUERY_PATTERN = r"^\d{1,3}(,\d{1,3})*$"

ContourMinutes = Annotated[
    int,
    Field(
        strict=True,
        ge=MIN_MINUTES,
        le=MAX_MINUTES,
        description=f"Временной порог контура в минутах, {MIN_MINUTES}…{MAX_MINUTES}",
    ),
]


class TravelMode(StrEnum):
    pedestrian = "pedestrian"
    bicycle = "bicycle"
    auto = "auto"


class CacheState(StrEnum):
    hit = "hit"
    miss = "miss"


class Coordinate(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"lat": 43.238949, "lon": 76.889709}})

    lat: float = Field(description="Широта, WGS84", examples=[43.238949])
    lon: float = Field(description="Долгота, WGS84", examples=[76.889709])


class IsochroneOptions(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "denoise": 0.2,
                "generalize": 100,
                "rings": False,
                "exclude_water": True,
            }
        },
    )

    denoise: float | None = Field(
        default=None,
        strict=True,
        ge=0.0,
        le=1.0,
        description=(
            "Подавление мелких изолированных фрагментов изохроны. Пусто — значение "
            "по умолчанию подбирается по максимальному контуру (см. README, п. 6.6 ТЗ)."
        ),
        examples=[0.2],
    )
    generalize: int | None = Field(
        default=None,
        strict=True,
        ge=0,
        le=500,
        description=(
            "Допуск упрощения контура в метрах (алгоритм Дугласа — Пекера). "
            "Пусто — значение по умолчанию подбирается по максимальному контуру."
        ),
        examples=[100],
    )
    rings: bool = Field(
        default=_settings.rings_default,
        strict=True,
        description=(
            "Возвращать контуры как непересекающиеся кольца: из большего контура "
            "вырезается меньший. Нужно для корректной заливки без наложения прозрачностей."
        ),
        examples=[False],
    )
    exclude_water: bool = Field(
        default=_settings.exclude_water_default,
        strict=True,
        description="Вырезать из полигонов площадные водоёмы и буферизованные водотоки.",
        examples=[True],
    )
    departure_time: str | None = Field(
        default=None,
        strict=True,
        pattern=DEPARTURE_TIME_PATTERN,
        description=(
            "Время отправления в локальном времени региона, формат YYYY-MM-DDTHH:MM. "
            "Резервный параметр (FR-21): передаётся в движок как date_time.type=1. "
            "Пробки и исторические скорости в текущей версии не учитываются."
        ),
        examples=["2026-08-09T09:30"],
    )


class IsochroneRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "lat": 43.238949,
                "lon": 76.889709,
                "contours": [10, 20, 30],
                "mode": "pedestrian",
                "options": {
                    "denoise": 0.2,
                    "generalize": 100,
                    "rings": False,
                    "exclude_water": True,
                },
            }
        },
    )

    lat: float = Field(
        strict=True,
        ge=-90.0,
        le=90.0,
        description="Широта точки старта в WGS84. Должна попадать в bbox покрытия данных.",
        examples=[43.238949],
    )
    lon: float = Field(
        strict=True,
        ge=-180.0,
        le=180.0,
        description="Долгота точки старта в WGS84. Должна попадать в bbox покрытия данных.",
        examples=[76.889709],
    )
    contours: Annotated[
        list[ContourMinutes],
        Field(
            strict=True,
            min_length=1,
            max_length=MAX_CONTOURS,
            description=(
                f"Временные пороги в минутах: от 1 до {MAX_CONTOURS} значений, "
                f"каждое в диапазоне {MIN_MINUTES}…{MAX_MINUTES}, без дубликатов. "
                "Сервер сортирует список по возрастанию."
            ),
            examples=[[10, 20, 30]],
        ),
    ]
    mode: TravelMode = Field(
        description=(
            "Способ передвижения: pedestrian — пешком, bicycle — велосипед, auto — автомобиль."
        ),
        examples=["pedestrian"],
    )
    options: IsochroneOptions = Field(
        default_factory=IsochroneOptions,
        description="Параметры геометрии и пост-обработки.",
    )

    @field_validator("contours")
    @classmethod
    def _unique_sorted(cls, value: list[int]) -> list[int]:
        if len(set(value)) != len(value):
            raise ValueError("contours must not contain duplicates")
        return sorted(value)

    @property
    def max_contour(self) -> int:
        return self.contours[-1]


class IsochroneFeatureProperties(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "contour_minutes": 30,
                "mode": "pedestrian",
                "color": "#2b83ba",
                "fill_opacity": 0.25,
                "area_km2": 18.42,
                "perimeter_km": 27.9,
                "rings": False,
                "engine": "valhalla",
                "engine_version": "3.5.1",
                "osm_data_timestamp": "2026-05-01T00:00:00Z",
                "computed_at": "2026-08-09T10:15:32+06:00",
                "request_id": "01J9X2K7Q4T8Z0ABCDEFGHJKMN",
                "cache": "miss",
            }
        }
    )

    contour_minutes: int = Field(description="Временной порог контура в минутах.", examples=[30])
    mode: TravelMode = Field(description="Способ передвижения.", examples=["pedestrian"])
    color: str = Field(
        description="Цвет контура из фиксированной палитры ColorBrewer.", examples=["#2b83ba"]
    )
    fill_opacity: float = Field(description="Рекомендуемая прозрачность заливки.", examples=[0.25])
    area_km2: float = Field(
        description="Площадь контура в км², геодезический расчёт.", examples=[18.42]
    )
    perimeter_km: float = Field(description="Периметр контура в км.", examples=[27.9])
    rings: bool = Field(description="Признак режима колец.", examples=[False])
    engine: str = Field(description="Роутинг-движок.", examples=["valhalla"])
    engine_version: str = Field(description="Версия движка.", examples=["3.5.1"])
    osm_data_timestamp: str = Field(
        description="Дата среза данных OSM.", examples=["2026-05-01T00:00:00Z"]
    )
    computed_at: str = Field(
        description="Момент расчёта, ISO 8601 с таймзоной.", examples=["2026-08-09T10:15:32+06:00"]
    )
    request_id: str = Field(
        description="Идентификатор запроса.", examples=["01J9X2K7Q4T8Z0ABCDEFGHJKMN"]
    )
    cache: CacheState = Field(description="Признак попадания в кэш.", examples=["miss"])


class IsochroneFeature(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [76.85, 43.22],
                            [76.92, 43.22],
                            [76.92, 43.26],
                            [76.85, 43.26],
                            [76.85, 43.22],
                        ]
                    ],
                },
                "properties": {"contour_minutes": 30, "mode": "pedestrian"},
            }
        }
    )

    type: Literal["Feature"] = Field(default="Feature", description="Тип объекта GeoJSON.")
    geometry: dict[str, Any] = Field(
        description=(
            "Геометрия RFC 7946: Polygon либо MultiPolygon. Порядок координат [lon, lat], "
            "точность 6 знаков после запятой."
        )
    )
    properties: IsochroneFeatureProperties = Field(description="Атрибуты контура.")


class IsochroneMetadata(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "request_id": "01J9X2K7Q4T8Z0ABCDEFGHJKMN",
                "origin": {"lat": 43.238949, "lon": 76.889709},
                "snapped_origin": {"lat": 43.238910, "lon": 76.889602},
                "snap_distance_m": 12.4,
                "mode": "pedestrian",
                "contours": [10, 20, 30],
                "rings": False,
                "exclude_water": True,
                "denoise": 0.2,
                "generalize": 100,
                "engine": "valhalla",
                "engine_version": "3.5.1",
                "osm_data_timestamp": "2026-05-01T00:00:00Z",
                "data_version": "b0f3c1a9",
                "computed_at": "2026-08-09T10:15:32+06:00",
                "duration_ms": 812,
                "engine_ms": 690,
                "cache": "miss",
            }
        }
    )

    request_id: str = Field(
        description="Идентификатор запроса, дублируется в заголовке X-Request-Id."
    )
    origin: Coordinate = Field(description="Координаты, переданные в запросе.")
    snapped_origin: Coordinate | None = Field(
        default=None, description="Точка дорожного графа, к которой движок привязал origin."
    )
    snap_distance_m: float | None = Field(
        default=None, description="Расстояние привязки к графу в метрах.", examples=[12.4]
    )
    mode: TravelMode = Field(description="Способ передвижения.")
    contours: list[int] = Field(description="Запрошенные контуры, отсортированы по возрастанию.")
    rings: bool = Field(description="Признак режима колец.")
    exclude_water: bool = Field(description="Признак вырезания воды.")
    denoise: float = Field(description="Фактически применённое значение denoise.")
    generalize: int = Field(description="Фактически применённое значение generalize, м.")
    engine: str = Field(description="Роутинг-движок.", examples=["valhalla"])
    engine_version: str = Field(description="Версия движка.", examples=["3.5.1"])
    osm_data_timestamp: str = Field(description="Дата среза данных OSM.")
    data_version: str = Field(description="Версия набора данных, входит в ключ кэша.")
    computed_at: str = Field(description="Момент расчёта, ISO 8601 с таймзоной.")
    duration_ms: int = Field(
        description="Полное время обработки запроса сервисом, мс.", examples=[812]
    )
    engine_ms: int = Field(description="Время ответа роутинг-движка, мс.", examples=[690])
    cache: CacheState = Field(description="Признак попадания в кэш.")


class IsochroneFeatureCollection(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Polygon", "coordinates": [[[76.85, 43.22]]]},
                        "properties": {"contour_minutes": 30, "area_km2": 18.42},
                    }
                ],
                "metadata": {"request_id": "01J9X2K7Q4T8Z0ABCDEFGHJKMN", "cache": "miss"},
            }
        }
    )

    type: Literal["FeatureCollection"] = Field(
        default="FeatureCollection", description="Тип коллекции GeoJSON."
    )
    features: list[IsochroneFeature] = Field(
        description="По одному Feature на каждый временной порог, от большего времени к меньшему."
    )
    metadata: IsochroneMetadata = Field(
        description=(
            "Foreign member верхнего уровня по RFC 7946. Клиенты, не знающие о нём, "
            "обязаны его игнорировать; ключевые поля продублированы в properties каждого Feature."
        )
    )


class ProblemDetail(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "type": "https://isochrone.local/errors/point-not-routable",
                "title": "Point is not routable",
                "status": 422,
                "detail": (
                    "Ближайший узел дорожного графа находится в 1840 м от точки, "
                    "что превышает лимит 500 м."
                ),
                "instance": "/api/v1/isochrone",
                "request_id": "01J9X2K7Q4T8Z0ABCDEFGHJKMN",
                "code": "POINT_NOT_ROUTABLE",
            }
        }
    )

    type: str = Field(description="URI типа ошибки, RFC 7807.")
    title: str = Field(description="Краткое человекочитаемое название проблемы.")
    status: int = Field(description="HTTP-код ответа.")
    detail: str = Field(description="Подробное описание проблемы для конкретного запроса.")
    instance: str = Field(description="Путь запроса, вызвавшего ошибку.")
    request_id: str = Field(description="Идентификатор запроса для поиска в логах.")
    code: str = Field(description="Машиночитаемый код ошибки.", examples=["POINT_NOT_ROUTABLE"])


class HealthResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"status": "ok", "version": "1.0.0"}})

    status: Literal["ok"] = Field(description="Признак живости процесса.", examples=["ok"])
    version: str = Field(description="Версия сервиса.", examples=["1.0.0"])


class ComponentState(StrEnum):
    ok = "ok"
    degraded = "degraded"
    unavailable = "unavailable"


class ReadyResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "degraded",
                "components": {
                    "engine": "ok",
                    "cache": "unavailable",
                    "water_layer": "ok",
                    "dataset": "ok",
                },
                "detail": "Кэш недоступен, запросы обрабатываются без кэширования.",
            }
        }
    )

    status: Literal["ok", "degraded", "unavailable"] = Field(
        description=(
            "Ответ на вопрос «можно ли направлять сюда трафик». "
            "ok — все компоненты в норме; degraded — движок доступен, что-то из "
            "необязательных компонентов нет; unavailable — движок недоступен, ответ 503. "
            "Система мониторинга различает ok и degraded по телу ответа, "
            "оркестратор — по HTTP-коду."
        ),
        examples=["ok"],
    )
    components: dict[str, ComponentState] = Field(
        description="Состояние каждого компонента: engine, cache, water_layer, dataset.",
        examples=[{"engine": "ok", "cache": "ok", "water_layer": "ok", "dataset": "ok"}],
    )
    detail: str | None = Field(
        default=None,
        description="Пояснение к состоянию, если оно отличается от ok.",
        examples=["Кэш недоступен, запросы обрабатываются без кэширования."],
    )


class MetaLimits(BaseModel):
    max_contours: int = Field(description="Максимум контуров в одном запросе.", examples=[4])
    min_contour_minutes: int = Field(description="Нижняя граница времени контура.", examples=[5])
    max_contour_minutes: int = Field(description="Верхняя граница времени контура.", examples=[60])
    max_snap_distance_m: float = Field(
        description="Максимальная дистанция привязки к дорожному графу.", examples=[500]
    )
    max_body_size: int = Field(
        description="Максимальный размер тела запроса, байт.", examples=[16384]
    )
    rate_limit: str = Field(description="Лимит запросов на IP.", examples=["10/second"])
    engine_timeout_s: float = Field(description="Таймаут обращения к движку, с.", examples=[10])


class MetaCoverage(BaseModel):
    profile: str = Field(description="Профиль покрытия данных.", examples=["almaty"])
    bbox: list[float] = Field(
        description="Границы покрытия в порядке [west, south, east, north].",
        examples=[[76.6, 43.05, 77.2, 43.45]],
    )
    geometry: dict[str, Any] = Field(description="Границы покрытия как GeoJSON Polygon.")


class MetaResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "service": "isochrone-service",
                "version": "1.0.0",
                "engine": "valhalla",
                "engine_version": "3.5.1",
                "modes": ["pedestrian", "bicycle", "auto"],
                "osm_data_timestamp": "2026-05-01T00:00:00Z",
                "data_version": "b0f3c1a9",
                "coverage": {"profile": "almaty", "bbox": [76.6, 43.05, 77.2, 43.45]},
                "limits": {"max_contours": 4},
                "defaults": {"rings": False, "exclude_water": True},
                "water_parts": 1421,
            }
        }
    )

    service: str = Field(description="Имя сервиса.", examples=["isochrone-service"])
    version: str = Field(description="Версия сервиса.", examples=["1.0.0"])
    engine: str = Field(description="Роутинг-движок.", examples=["valhalla"])
    engine_version: str = Field(description="Версия движка либо unknown, если он недоступен.")
    modes: list[TravelMode] = Field(description="Поддерживаемые способы передвижения.")
    osm_data_timestamp: str = Field(description="Дата среза данных OSM.")
    data_version: str = Field(description="Версия набора данных.")
    coverage: MetaCoverage = Field(description="Покрытие данных.")
    limits: MetaLimits = Field(description="Действующие лимиты.")
    defaults: dict[str, Any] = Field(description="Значения параметров по умолчанию.")
    water_parts: int = Field(description="Число полигонов в слое воды.", examples=[1421])
