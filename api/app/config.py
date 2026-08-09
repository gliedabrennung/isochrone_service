from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENGINE_NAME = "valhalla"
API_PREFIX = "/api/v1"
ERROR_TYPE_BASE = "https://isochrone.local/errors"

PROFILE_BBOX: dict[str, tuple[float, float, float, float]] = {
    "almaty": (76.60, 43.05, 77.20, 43.45),
    "almaty-region": (75.80, 42.60, 78.60, 44.60),
    "kazakhstan": (46.49, 40.56, 87.32, 55.45),
}

SMOOTHING_DEFAULTS: tuple[tuple[int, float, int], ...] = (
    (15, 0.15, 50),
    (30, 0.20, 100),
    (45, 0.25, 150),
    (60, 0.30, 200),
)

CONTOUR_PALETTE: tuple[str, ...] = ("#2b83ba", "#abdda4", "#fdae61", "#d7191c")
FILL_OPACITY = 0.25
COORDINATE_PRECISION = 6
MIN_MEANINGFUL_AREA_KM2 = 0.001

COSTING_BY_MODE = {
    "pedestrian": "pedestrian",
    "bicycle": "bicycle",
    "auto": "auto",
}


def smoothing_defaults(max_contour_minutes: int) -> tuple[float, int]:
    for threshold, denoise, generalize in SMOOTHING_DEFAULTS:
        if max_contour_minutes <= threshold:
            return denoise, generalize
    return SMOOTHING_DEFAULTS[-1][1], SMOOTHING_DEFAULTS[-1][2]


def contour_color(index: int) -> str:
    return CONTOUR_PALETTE[index % len(CONTOUR_PALETTE)]


def contour_bucket(max_contour_minutes: int) -> str:
    if max_contour_minutes <= 15:
        return "<=15"
    if max_contour_minutes <= 30:
        return "16-30"
    if max_contour_minutes <= 45:
        return "31-45"
    return "46-60"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    osm_profile: str = "almaty"
    coverage_bbox: str = ""
    data_dir: Path = Path("/data")

    valhalla_url: str = "http://valhalla:8002"
    engine_timeout_s: float = 10.0

    redis_url: str = "redis://redis:6379/0"
    cache_ttl_seconds: int = 604800
    cache_coord_precision: int = 4

    max_contours: int = Field(default=4, ge=1, le=8)
    max_contour_minutes: int = Field(default=60, ge=1, le=120)
    min_contour_minutes: int = Field(default=5, ge=1, le=120)
    max_snap_distance_m: float = Field(default=500.0, gt=0)
    max_body_size: int = Field(default=16384, gt=0)

    rate_limit: str = "10/second"
    rate_limit_burst: int = Field(default=20, ge=1)

    exclude_water_default: bool = True
    rings_default: bool = False

    cors_origins: str = "*"
    log_level: str = "INFO"

    @field_validator("osm_profile")
    @classmethod
    def _known_profile(cls, value: str) -> str:
        if value not in PROFILE_BBOX:
            raise ValueError(
                f"unknown OSM_PROFILE '{value}', expected one of {sorted(PROFILE_BBOX)}"
            )
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        raw = self.cors_origins.strip()
        if raw in ("", "*"):
            return ["*"]
        return [item.strip() for item in raw.split(",") if item.strip()]

    @property
    def fallback_bbox(self) -> tuple[float, float, float, float]:
        if self.coverage_bbox.strip():
            parts = [float(value) for value in self.coverage_bbox.split(",")]
            if len(parts) != 4:
                raise ValueError("COVERAGE_BBOX must be 'west,south,east,north'")
            return parts[0], parts[1], parts[2], parts[3]
        return PROFILE_BBOX[self.osm_profile]

    @property
    def rate_limit_per_second(self) -> float:
        raw = self.rate_limit.strip().lower()
        if "/" not in raw:
            return float(raw)
        amount, _, period = raw.partition("/")
        divisor = {"second": 1.0, "sec": 1.0, "s": 1.0, "minute": 60.0, "min": 60.0, "m": 60.0}
        if period not in divisor:
            raise ValueError(f"unsupported RATE_LIMIT period '{period}'")
        return float(amount) / divisor[period]

    @property
    def water_path(self) -> Path:
        return self.data_dir / "water.geojson"

    @property
    def meta_path(self) -> Path:
        return self.data_dir / "meta.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
