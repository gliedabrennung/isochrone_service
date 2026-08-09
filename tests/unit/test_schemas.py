import pytest
from pydantic import ValidationError

from app.schemas import IsochroneOptions, IsochroneRequest, TravelMode


def build(**overrides):
    payload = {"lat": 43.238949, "lon": 76.889709, "contours": [10, 20], "mode": "pedestrian"}
    payload.update(overrides)
    return IsochroneRequest.model_validate(payload)


def test_contours_are_sorted_by_the_server():
    assert build(contours=[30, 10, 20]).contours == [10, 20, 30]


def test_max_contour_property():
    assert build(contours=[10, 45]).max_contour == 45


def test_duplicate_contours_are_rejected():
    with pytest.raises(ValidationError, match="duplicates"):
        build(contours=[10, 10])


@pytest.mark.parametrize("contours", [[4], [61], [5, 61], [0]])
def test_contours_outside_the_allowed_range_are_rejected(contours):
    with pytest.raises(ValidationError):
        build(contours=contours)


@pytest.mark.parametrize("contours", [[5], [60], [5, 60], [5, 10, 15, 20]])
def test_boundary_contours_are_accepted(contours):
    assert build(contours=contours).contours == sorted(contours)


def test_too_many_contours_are_rejected():
    with pytest.raises(ValidationError):
        build(contours=[5, 10, 15, 20, 30])


def test_empty_contours_are_rejected():
    with pytest.raises(ValidationError):
        build(contours=[])


@pytest.mark.parametrize(("lat", "lon"), [(91.0, 0.0), (-91.0, 0.0), (0.0, 181.0), (0.0, -181.0)])
def test_coordinates_outside_wgs84_are_rejected(lat, lon):
    with pytest.raises(ValidationError):
        build(lat=lat, lon=lon)


def test_unknown_mode_is_rejected():
    with pytest.raises(ValidationError):
        build(mode="teleport")


def test_all_three_modes_are_supported():
    for mode in ("pedestrian", "bicycle", "auto"):
        assert build(mode=mode).mode == TravelMode(mode)


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        build(colour="red")

    with pytest.raises(ValidationError):
        build(options={"unknown": 1})


def test_option_defaults():
    options = IsochroneOptions()
    assert options.denoise is None
    assert options.generalize is None
    assert options.rings is False
    assert options.exclude_water is True
    assert options.departure_time is None


@pytest.mark.parametrize("denoise", [-0.1, 1.1])
def test_denoise_bounds(denoise):
    with pytest.raises(ValidationError):
        IsochroneOptions(denoise=denoise)


@pytest.mark.parametrize("generalize", [-1, 501])
def test_generalize_bounds(generalize):
    with pytest.raises(ValidationError):
        IsochroneOptions(generalize=generalize)


def test_departure_time_format_is_validated():
    assert IsochroneOptions(departure_time="2026-08-09T09:30").departure_time == "2026-08-09T09:30"
    with pytest.raises(ValidationError):
        IsochroneOptions(departure_time="09:30 09.08.2026")
