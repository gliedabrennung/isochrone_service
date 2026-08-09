import contextlib
import hashlib
import json
import time
from typing import Any

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.core.logging import get_logger
from app.core.metrics import CACHE_ERRORS

logger = get_logger("app.cache")

KEY_PREFIX = "isochrone:v1"
FAILURE_BACKOFF_S = 5.0


def make_cache_key(
    *,
    lat: float,
    lon: float,
    mode: str,
    contours: list[int],
    denoise: float,
    generalize: int,
    rings: bool,
    exclude_water: bool,
    data_version: str,
    coord_precision: int,
    departure_time: str | None = None,
) -> str:
    payload = [
        round(lat, coord_precision),
        round(lon, coord_precision),
        mode,
        sorted(contours),
        round(float(denoise), 4),
        int(generalize),
        bool(rings),
        bool(exclude_water),
        data_version,
        departure_time,
    ]
    digest = hashlib.sha1(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"{KEY_PREFIX}:{digest}"


class CacheService:
    def __init__(self, url: str, ttl_seconds: int, timeout_s: float = 0.5) -> None:
        self.url = url
        self.ttl_seconds = ttl_seconds
        self._timeout_s = timeout_s
        self._client: redis.Redis | None = None
        self._skip_until = 0.0

    async def connect(self) -> None:
        try:
            self._client = redis.from_url(
                self.url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=self._timeout_s,
                socket_timeout=self._timeout_s,
                health_check_interval=30,
            )
            await self._client.ping()
            logger.info("cache_connected", url=self.url)
        except (RedisError, OSError, ValueError) as exc:
            logger.warning("cache_unavailable_at_startup", url=self.url, error=str(exc))
            self._degrade("connect")

    async def aclose(self) -> None:
        if self._client is not None:
            with contextlib.suppress(RedisError, OSError):
                await self._client.aclose()
            self._client = None

    @property
    def enabled(self) -> bool:
        return self._client is not None and time.monotonic() >= self._skip_until

    def _degrade(self, operation: str) -> None:
        CACHE_ERRORS.labels(operation=operation).inc()
        self._skip_until = time.monotonic() + FAILURE_BACKOFF_S

    async def ping(self) -> float | None:
        if self._client is None:
            return None
        started = time.perf_counter()
        try:
            await self._client.ping()
        except (RedisError, OSError):
            return None
        return round((time.perf_counter() - started) * 1000, 2)

    async def get(self, key: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        try:
            raw = await self._client.get(key)
        except (RedisError, OSError) as exc:
            logger.warning("cache_get_failed", error=str(exc))
            self._degrade("get")
            return None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            logger.warning("cache_payload_corrupted", key=key)
            return None

    async def set(self, key: str, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        try:
            await self._client.set(
                key,
                json.dumps(payload, separators=(",", ":")),
                ex=self.ttl_seconds,
            )
        except (RedisError, OSError) as exc:
            logger.warning("cache_set_failed", error=str(exc))
            self._degrade("set")
