import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.valhalla import ValhallaClient
from support import StubEngine


@pytest.fixture(autouse=True)
def stubbed_engine(monkeypatch):
    stub = StubEngine()

    monkeypatch.setattr(ValhallaClient, "isochrone", lambda self, payload: stub.isochrone(payload))
    monkeypatch.setattr(ValhallaClient, "status", lambda self: stub.status())
    monkeypatch.setattr(ValhallaClient, "version", lambda self: stub.version())
    monkeypatch.setattr(ValhallaClient, "cached_version", property(lambda self: stub.version_value))
    return stub
