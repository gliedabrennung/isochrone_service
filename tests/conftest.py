import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

TESTS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
API_DIR = TESTS_DIR.parent / "api"

sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(TESTS_DIR))

os.environ.setdefault("DATA_DIR", str(FIXTURES_DIR / "data"))
os.environ.setdefault("VALHALLA_URL", "http://valhalla.test:8002")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6399/0")
os.environ.setdefault("RATE_LIMIT", "5000/second")
os.environ.setdefault("RATE_LIMIT_BURST", "10000")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("OSM_PROFILE", "almaty")
os.environ.setdefault("ENGINE_POLL_INTERVAL_S", "3600")

from support import ORIGIN_LAT, ORIGIN_LON, FakeCache, StubEngine  # noqa: E402


@dataclass
class ApiHarness:
    client: Any
    engine: StubEngine
    cache: FakeCache
    monitor: Any
    app: Any


@pytest.fixture(scope="session")
def _test_client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        yield client, app


@pytest.fixture
def api(_test_client) -> ApiHarness:
    client, app = _test_client
    engine = StubEngine()
    cache = FakeCache()

    app.state.engine = engine
    app.state.cache = cache

    monitor = app.state.engine_monitor
    monitor.client = engine
    monitor.failure_threshold = 1
    asyncio.run(monitor.refresh())

    service = app.state.isochrone_service
    service.engine = engine
    service.cache = cache
    service.monitor = monitor

    return ApiHarness(client=client, engine=engine, cache=cache, monitor=monitor, app=app)


@pytest.fixture
def control_points() -> dict[str, Any]:
    with (FIXTURES_DIR / "control_points.json").open(encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture
def origin() -> tuple[float, float]:
    return ORIGIN_LAT, ORIGIN_LON
