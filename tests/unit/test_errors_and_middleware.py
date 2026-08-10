import asyncio
import time

import pytest

from app.core.errors import ERROR_CATALOG, ProblemError, problem_payload
from app.core.ids import new_request_id, sanitize_request_id
from app.core.middleware import RateLimitMiddleware
from app.services.cache import CacheService


def test_problem_error_uses_the_catalog_defaults():
    error = ProblemError("POINT_NOT_ROUTABLE", "too far")
    assert error.status == 422
    assert error.title == "Point is not routable"
    assert error.type_uri.endswith("/point-not-routable")


def test_problem_error_allows_overrides():
    error = ProblemError("INTERNAL_ERROR", "boom", status=503, title="Custom", headers={"A": "B"})
    assert (error.status, error.title, error.headers) == (503, "Custom", {"A": "B"})


def test_unknown_code_falls_back_to_internal_error():
    error = ProblemError("NO_SUCH_CODE", "boom")
    assert error.status == 500


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("VALIDATION_ERROR", 400),
        ("MALFORMED_JSON", 400),
        ("POINT_OUT_OF_COVERAGE", 422),
        ("POINT_NOT_ROUTABLE", 422),
        ("EMPTY_RESULT", 422),
        ("RATE_LIMIT_EXCEEDED", 429),
        ("INTERNAL_ERROR", 500),
        ("ENGINE_UNAVAILABLE", 503),
        ("ENGINE_TIMEOUT", 504),
    ],
)
def test_error_catalog_matches_the_specification(code, status):
    assert ERROR_CATALOG[code][0] == status


def test_problem_payload_has_every_rfc7807_member():
    payload = problem_payload("EMPTY_RESULT", "empty", "/api/v1/isochrone")
    assert set(payload) >= {"type", "title", "status", "detail", "instance", "request_id", "code"}
    assert payload["code"] == "EMPTY_RESULT"
    assert payload["instance"] == "/api/v1/isochrone"


def test_request_ids_are_unique_and_sortable():
    first = new_request_id()
    second = new_request_id()
    assert len(first) == 26
    assert first != second


def test_incoming_request_id_is_reused_when_safe():
    assert sanitize_request_id("abc-123_XYZ.1") == "abc-123_XYZ.1"


@pytest.mark.parametrize("raw", [None, "", "with space", "x" * 200, "inject\nheader"])
def test_unsafe_request_ids_are_replaced(raw):
    assert sanitize_request_id(raw) != raw


def test_token_bucket_allows_the_burst_then_throttles():
    limiter = RateLimitMiddleware(app=None, rate_per_second=10, burst=20)
    now = 1000.0
    allowed = sum(1 for _ in range(20) if limiter._allow("1.2.3.4", now)[0])
    assert allowed == 20

    ok, retry_after = limiter._allow("1.2.3.4", now)
    assert ok is False
    assert retry_after >= 1


def test_token_bucket_refills_over_time():
    limiter = RateLimitMiddleware(app=None, rate_per_second=10, burst=10)
    now = 500.0
    for _ in range(10):
        limiter._allow("5.6.7.8", now)
    assert limiter._allow("5.6.7.8", now)[0] is False
    assert limiter._allow("5.6.7.8", now + 1.0)[0] is True


def test_token_bucket_is_per_client():
    limiter = RateLimitMiddleware(app=None, rate_per_second=1, burst=1)
    now = 10.0
    assert limiter._allow("10.0.0.1", now)[0] is True
    assert limiter._allow("10.0.0.2", now)[0] is True
    assert limiter._allow("10.0.0.1", now)[0] is False


def test_token_bucket_evicts_old_clients():
    limiter = RateLimitMiddleware(app=None, rate_per_second=100, burst=100, max_tracked_clients=3)
    for index in range(10):
        limiter._allow(f"10.0.0.{index}", 1.0)
    assert len(limiter._buckets) <= 3


def test_rate_limiting_can_be_disabled():
    limiter = RateLimitMiddleware(app=None, rate_per_second=0, burst=1)
    assert all(limiter._allow("1.1.1.1", 1.0)[0] for _ in range(100))


async def test_cache_degrades_without_a_server():
    cache = CacheService(
        "redis://127.0.0.1:6399/0",
        ttl_seconds=60,
        connect_timeout_ms=50,
        breaker_threshold=1,
    )
    await cache.connect()
    assert cache.enabled is False
    assert await cache.get("missing") is None
    await cache.set("missing", {"a": 1})
    assert await cache.ping() is False
    assert cache.healthy is False
    await cache.aclose()


class FlakyRedis:
    def __init__(self) -> None:
        self.fail = True
        self.calls = 0
        self.block: asyncio.Event | None = None

    async def ping(self) -> bool:
        self.calls += 1
        if self.fail:
            raise ConnectionError("connection refused")
        return True

    async def get(self, key: str) -> None:
        self.calls += 1
        if self.block is not None:
            await self.block.wait()
        if self.fail:
            raise ConnectionError("connection refused")
        return None

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.calls += 1
        if self.fail:
            raise ConnectionError("connection refused")

    async def aclose(self) -> None:
        return None


def _breaker_cache() -> tuple[CacheService, FlakyRedis]:
    cache = CacheService(
        "redis://127.0.0.1:6399/0",
        ttl_seconds=60,
        breaker_threshold=3,
        breaker_cooldown_s=30.0,
    )
    client = FlakyRedis()
    cache._client = client
    return cache, client


async def test_cache_breaker_opens_after_the_threshold_is_reached():
    cache, client = _breaker_cache()

    for _ in range(2):
        assert await cache.get("key") is None
    assert cache.breaker_open is False
    assert cache.enabled is True

    assert await cache.get("key") is None
    assert cache.breaker_open is True
    assert cache.enabled is False

    calls_before = client.calls
    assert await cache.get("key") is None
    await cache.set("key", {"a": 1})
    assert client.calls == calls_before


async def test_cache_breaker_admits_a_single_probe_after_the_cooldown():
    cache, client = _breaker_cache()
    for _ in range(3):
        await cache.get("key")
    assert cache.breaker_open is True

    cache._open_until = time.monotonic() - 1
    client.block = asyncio.Event()

    probe = asyncio.create_task(cache.get("key"))
    await asyncio.sleep(0)
    calls_in_flight = client.calls

    assert await cache.get("key") is None
    await cache.set("key", {"a": 1})
    assert client.calls == calls_in_flight

    client.block.set()
    assert await probe is None
    assert cache.breaker_open is True


async def test_cache_breaker_reopens_for_a_new_probe_after_a_failed_one():
    cache, client = _breaker_cache()
    for _ in range(3):
        await cache.get("key")

    cache._open_until = time.monotonic() - 1
    calls_before = client.calls
    assert await cache.get("key") is None
    assert client.calls == calls_before + 1
    assert cache.breaker_open is True

    cache._open_until = time.monotonic() - 1
    assert await cache.get("key") is None
    assert client.calls == calls_before + 2


async def test_cache_breaker_closes_after_a_successful_probe():
    cache, client = _breaker_cache()
    for _ in range(3):
        await cache.get("key")
    assert cache.enabled is False

    cache._open_until = 0.0
    client.fail = False

    assert await cache.get("key") is None
    assert cache.healthy is True
    assert cache.breaker_open is False
    assert cache.enabled is True
