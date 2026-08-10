import contextlib
import hashlib
import json
import time
from typing import Any

import orjson
import redis.asyncio as redis
from redis.exceptions import RedisError

from app import __version__
from app.core.logging import get_logger
from app.core.metrics import CACHE_ERRORS

logger = get_logger("app.cache")

KEY_PREFIX = "isochrone:v1"


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
        __version__,
    ]
    digest = hashlib.sha1(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"{KEY_PREFIX}:{digest}"


class CacheService:
    def __init__(
        self,
        url: str,
        ttl_seconds: int,
        connect_timeout_ms: int = 150,
        breaker_threshold: int = 3,
        breaker_cooldown_s: float = 30.0,
    ) -> None:
        self.url = url
        self.ttl_seconds = ttl_seconds
        self._timeout_s = connect_timeout_ms / 1000
        self._breaker_threshold = max(breaker_threshold, 1)
        self._breaker_cooldown_s = breaker_cooldown_s
        self._client: redis.Redis | None = None
        self._failures = 0
        self._open_until = 0.0
        self._healthy = True
        self._probing = False

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
            logger.info("cache_connected", url=self.url, connect_timeout_ms=self._timeout_s * 1000)
        except (RedisError, OSError, ValueError) as exc:
            self._record_failure("connect", str(exc))

    async def aclose(self) -> None:
        if self._client is not None:
            with contextlib.suppress(RedisError, OSError):
                await self._client.aclose()
            self._client = None

    @property
    def enabled(self) -> bool:
        if self._client is None:
            return False
        return not self._open_until or time.monotonic() >= self._open_until

    def _acquire(self) -> bool:
        if self._client is None:
            return False
        if not self._open_until:
            return True
        if time.monotonic() < self._open_until:
            return False
        if self._probing:
            return False
        self._probing = True
        return True

    @property
    def healthy(self) -> bool:
        return self._client is not None and self._healthy

    @property
    def breaker_open(self) -> bool:
        return bool(self._open_until) and time.monotonic() < self._open_until

    def _record_failure(self, operation: str, error: str) -> None:
        CACHE_ERRORS.labels(operation=operation).inc()
        self._probing = False
        self._failures += 1
        if self._failures >= self._breaker_threshold:
            self._open_until = time.monotonic() + self._breaker_cooldown_s
        if self._healthy:
            self._healthy = False
            logger.warning(
                "cache_unavailable",
                url=self.url,
                operation=operation,
                error=error,
                failures=self._failures,
                cooldown_s=self._breaker_cooldown_s if self._open_until else 0,
            )

    def _record_success(self) -> None:
        self._probing = False
        self._failures = 0
        self._open_until = 0.0
        if not self._healthy:
            self._healthy = True
            logger.warning("cache_recovered", url=self.url)

    async def ping(self) -> bool:
        if self._client is None:
            return False
        try:
            await self._client.ping()
        except (RedisError, OSError) as exc:
            self._record_failure("ping", str(exc))
            return False
        self._record_success()
        return True

    async def get(self, key: str) -> dict[str, Any] | None:
        if not self._acquire():
            return None
        try:
            raw = await self._client.get(key)
        except (RedisError, OSError) as exc:
            self._record_failure("get", str(exc))
            return None
        self._record_success()
        if not raw:
            return None
        try:
            return orjson.loads(raw)
        except orjson.JSONDecodeError:
            logger.warning("cache_payload_corrupted", key=key)
            return None

    async def set(self, key: str, payload: dict[str, Any]) -> None:
        if not self._acquire():
            return
        try:
            await self._client.set(key, orjson.dumps(payload), ex=self.ttl_seconds)
        except (RedisError, OSError) as exc:
            self._record_failure("set", str(exc))
            return
        self._record_success()
