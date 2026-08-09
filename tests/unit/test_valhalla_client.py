import asyncio

import httpx
import pytest
import respx

from app.core.errors import ProblemError
from app.services.valhalla import (
    EngineMonitor,
    EngineState,
    ValhallaClient,
    build_isochrone_payload,
    parse_isochrone_response,
)

BASE_URL = "http://valhalla.test:8002"


@pytest.fixture
async def client():
    engine = ValhallaClient(BASE_URL, timeout_s=2.0)
    yield engine
    await engine.aclose()


def test_payload_matches_the_engine_contract():
    payload = build_isochrone_payload(
        lat=43.238949,
        lon=76.889709,
        costing="pedestrian",
        contours=[10, 20, 30],
        denoise=0.2,
        generalize=100,
        request_id="REQ1",
    )
    assert payload == {
        "locations": [{"lat": 43.238949, "lon": 76.889709}],
        "costing": "pedestrian",
        "contours": [{"time": 10}, {"time": 20}, {"time": 30}],
        "polygons": True,
        "denoise": 0.2,
        "generalize": 100,
        "show_locations": True,
        "id": "REQ1",
    }


def test_payload_carries_departure_time_when_requested():
    payload = build_isochrone_payload(
        lat=1.0,
        lon=2.0,
        costing="auto",
        contours=[10],
        denoise=0.2,
        generalize=100,
        request_id="REQ2",
        departure_time="2026-08-09T09:30",
    )
    assert payload["date_time"] == {"type": 1, "value": "2026-08-09T09:30"}


def test_parse_response_extracts_polygons_and_snapped_point():
    payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"contour": 20.0, "metric": "time"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]],
                },
            },
            {
                "type": "Feature",
                "properties": {"contour": 10.0, "metric": "time"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
                },
            },
            {
                "type": "Feature",
                "properties": {"type": "input"},
                "geometry": {"type": "Point", "coordinates": [76.88, 43.23]},
            },
            {
                "type": "Feature",
                "properties": {"type": "snapped"},
                "geometry": {"type": "Point", "coordinates": [76.881, 43.231]},
            },
        ],
    }
    polygons, snapped = parse_isochrone_response(payload, [10, 20])
    assert sorted(polygons) == [10, 20]
    assert polygons[20].area > polygons[10].area
    assert snapped == (43.231, 76.881)


def test_parse_response_ignores_unknown_contours_and_geometries():
    payload = {
        "features": [
            {
                "properties": {"contour": 99.0},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]],
                },
            },
            {"properties": {}, "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}},
        ]
    }
    polygons, snapped = parse_isochrone_response(payload, [10])
    assert polygons == {}
    assert snapped is None


async def test_status_caches_the_engine_version(client):
    with respx.mock:
        respx.get(f"{BASE_URL}/status").mock(
            return_value=httpx.Response(200, json={"version": "3.5.1"})
        )
        assert (await client.status())["version"] == "3.5.1"
    assert client.cached_version == "3.5.1"
    assert await client.version() == "3.5.1"


async def test_version_degrades_to_unknown_when_the_engine_is_down(client):
    with respx.mock:
        respx.get(f"{BASE_URL}/status").mock(side_effect=httpx.ConnectError("refused"))
        assert await client.version() == "unknown"


async def test_isochrone_returns_payload_and_duration(client):
    with respx.mock:
        respx.post(f"{BASE_URL}/isochrone").mock(
            return_value=httpx.Response(200, json={"type": "FeatureCollection", "features": []})
        )
        payload, engine_ms = await client.isochrone({"locations": []})
    assert payload["type"] == "FeatureCollection"
    assert engine_ms >= 0


async def test_timeout_maps_to_engine_timeout(client):
    with respx.mock:
        respx.post(f"{BASE_URL}/isochrone").mock(side_effect=httpx.ReadTimeout("slow"))
        with pytest.raises(ProblemError) as info:
            await client.isochrone({})
    assert info.value.code == "ENGINE_TIMEOUT"
    assert info.value.status == 504


async def test_connection_error_maps_to_engine_unavailable(client):
    with respx.mock:
        respx.post(f"{BASE_URL}/isochrone").mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(ProblemError) as info:
            await client.isochrone({})
    assert info.value.code == "ENGINE_UNAVAILABLE"
    assert info.value.status == 503


async def test_engine_5xx_maps_to_engine_unavailable(client):
    with respx.mock:
        respx.post(f"{BASE_URL}/isochrone").mock(return_value=httpx.Response(500, text="boom"))
        with pytest.raises(ProblemError) as info:
            await client.isochrone({})
    assert info.value.code == "ENGINE_UNAVAILABLE"


async def test_no_suitable_edges_maps_to_point_not_routable(client):
    with respx.mock:
        respx.post(f"{BASE_URL}/isochrone").mock(
            return_value=httpx.Response(
                400,
                json={
                    "error_code": 171,
                    "error": "No suitable edges near location",
                    "status_code": 400,
                },
            )
        )
        with pytest.raises(ProblemError) as info:
            await client.isochrone({})
    assert info.value.code == "POINT_NOT_ROUTABLE"
    assert info.value.status == 422
    assert info.value.extra["engine_error_code"] == 171


async def test_unknown_engine_error_maps_to_validation_error(client):
    with respx.mock:
        respx.post(f"{BASE_URL}/isochrone").mock(
            return_value=httpx.Response(400, json={"error_code": 999, "error": "Nope"})
        )
        with pytest.raises(ProblemError) as info:
            await client.isochrone({})
    assert info.value.code == "VALIDATION_ERROR"


async def test_unreadable_engine_body_maps_to_engine_unavailable(client):
    with respx.mock:
        respx.post(f"{BASE_URL}/isochrone").mock(
            return_value=httpx.Response(200, text="<html>not json</html>")
        )
        with pytest.raises(ProblemError) as info:
            await client.isochrone({})
    assert info.value.code == "ENGINE_UNAVAILABLE"


class FlakyEngine:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    async def status(self) -> dict[str, str]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return {"version": "3.5.1"}


async def test_monitor_starts_in_preparing_until_the_engine_answers():
    engine = FlakyEngine(ConnectionRefusedError("connection refused"))
    monitor = EngineMonitor(engine, poll_interval_s=60)

    assert monitor.state is EngineState.preparing
    assert await monitor.refresh() is EngineState.preparing
    assert monitor.ready is False

    detail = monitor.unavailable_detail()
    assert "сборка тайлов" in detail
    assert "мин" in detail


async def test_monitor_reports_a_failure_only_after_the_engine_was_ready():
    engine = FlakyEngine()
    monitor = EngineMonitor(engine, poll_interval_s=60)

    assert await monitor.refresh() is EngineState.ready
    assert monitor.ready is True

    engine.error = ConnectionRefusedError("connection refused")
    assert await monitor.refresh() is EngineState.unavailable

    detail = monitor.unavailable_detail()
    assert "сборка тайлов" not in detail
    assert "ConnectionRefusedError" in monitor.error


async def test_monitor_recovers_and_stops_its_background_task():
    engine = FlakyEngine(ConnectionRefusedError("connection refused"))
    monitor = EngineMonitor(engine, poll_interval_s=0.01)
    monitor.start()
    await asyncio.sleep(0.03)
    assert engine.calls >= 1

    engine.error = None
    await asyncio.sleep(0.03)
    assert monitor.ready is True

    await monitor.stop()
    calls_after_stop = engine.calls
    await asyncio.sleep(0.03)
    assert engine.calls == calls_after_stop
