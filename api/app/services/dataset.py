import json
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.core.logging import get_logger

logger = get_logger("app.dataset")

UNKNOWN_TIMESTAMP = "unknown"


@dataclass(frozen=True)
class DatasetMeta:
    profile: str
    bbox: tuple[float, float, float, float]
    osm_data_timestamp: str
    data_version: str
    prepared_at: str | None
    source_url: str | None
    water_parts: int
    complete: bool


def load_dataset_meta(settings: Settings) -> DatasetMeta:
    path: Path = settings.meta_path
    fallback = DatasetMeta(
        profile=settings.osm_profile,
        bbox=settings.fallback_bbox,
        osm_data_timestamp=UNKNOWN_TIMESTAMP,
        data_version="unknown",
        prepared_at=None,
        source_url=None,
        water_parts=0,
        complete=False,
    )

    if not path.exists():
        logger.warning("dataset_meta_missing", path=str(path))
        return fallback

    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        logger.warning("dataset_meta_unreadable", path=str(path), error=str(exc))
        return fallback

    bbox_values = payload.get("bbox") or []
    if len(bbox_values) == 4:
        bbox = (
            float(bbox_values[0]),
            float(bbox_values[1]),
            float(bbox_values[2]),
            float(bbox_values[3]),
        )
    else:
        bbox = settings.fallback_bbox

    if settings.coverage_bbox.strip():
        bbox = settings.fallback_bbox

    meta = DatasetMeta(
        profile=payload.get("profile") or settings.osm_profile,
        bbox=bbox,
        osm_data_timestamp=payload.get("osm_data_timestamp") or UNKNOWN_TIMESTAMP,
        data_version=payload.get("data_version") or "unknown",
        prepared_at=payload.get("prepared_at"),
        source_url=payload.get("source_url"),
        water_parts=int(payload.get("water_parts") or 0),
        complete=True,
    )
    logger.info(
        "dataset_meta_loaded",
        profile=meta.profile,
        bbox=list(meta.bbox),
        osm_data_timestamp=meta.osm_data_timestamp,
        data_version=meta.data_version,
    )
    return meta
