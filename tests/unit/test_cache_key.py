import pytest

from app.services.cache import KEY_PREFIX, make_cache_key

BASE = {
    "lat": 43.238949,
    "lon": 76.889709,
    "mode": "pedestrian",
    "contours": [10, 20, 30],
    "denoise": 0.2,
    "generalize": 100,
    "rings": False,
    "exclude_water": True,
    "data_version": "abc123",
    "coord_precision": 4,
}


def key(**overrides):
    payload = dict(BASE)
    payload.update(overrides)
    return make_cache_key(**payload)


def test_key_is_prefixed_and_deterministic():
    first = key()
    assert first.startswith(f"{KEY_PREFIX}:")
    assert first == key()


def test_contour_order_does_not_change_the_key():
    assert key(contours=[30, 10, 20]) == key(contours=[10, 20, 30])


def test_coordinates_are_rounded_to_the_configured_precision():
    assert key(lat=43.2389491) == key(lat=43.23894)
    assert key(lat=43.2389) != key(lat=43.2390)


def test_lower_precision_widens_the_bucket():
    assert key(lat=43.2381, coord_precision=2) == key(lat=43.2389, coord_precision=2)


@pytest.mark.parametrize(
    "override",
    [
        {"mode": "auto"},
        {"contours": [10, 20]},
        {"denoise": 0.3},
        {"generalize": 150},
        {"rings": True},
        {"exclude_water": False},
        {"data_version": "def456"},
        {"departure_time": "2026-08-09T09:30"},
    ],
)
def test_every_parameter_participates_in_the_key(override):
    assert key(**override) != key()


def test_data_version_invalidates_the_cache_on_osm_update():
    before = key(data_version="osm-2026-05-01")
    after = key(data_version="osm-2026-06-01")
    assert before != after
