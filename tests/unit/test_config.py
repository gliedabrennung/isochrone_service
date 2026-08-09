import pytest
from pydantic import ValidationError

from app.config import (
    CONTOUR_PALETTE,
    PROFILE_BBOX,
    Settings,
    contour_bucket,
    contour_color,
    smoothing_defaults,
)


@pytest.mark.parametrize(
    ("max_contour", "expected"),
    [
        (5, (0.15, 50)),
        (15, (0.15, 50)),
        (16, (0.20, 100)),
        (30, (0.20, 100)),
        (31, (0.25, 150)),
        (45, (0.25, 150)),
        (46, (0.30, 200)),
        (60, (0.30, 200)),
        (90, (0.30, 200)),
    ],
)
def test_smoothing_defaults_follow_the_specification_table(max_contour, expected):
    assert smoothing_defaults(max_contour) == expected


@pytest.mark.parametrize(
    ("max_contour", "expected"),
    [(10, "<=15"), (15, "<=15"), (20, "16-30"), (40, "31-45"), (60, "46-60")],
)
def test_contour_bucket(max_contour, expected):
    assert contour_bucket(max_contour) == expected


def test_contour_color_is_stable_and_wraps_around():
    assert contour_color(0) == CONTOUR_PALETTE[0] == "#2b83ba"
    assert contour_color(3) == CONTOUR_PALETTE[3]
    assert contour_color(4) == CONTOUR_PALETTE[0]


def test_rate_limit_parsing():
    assert Settings(rate_limit="10/second").rate_limit_per_second == pytest.approx(10.0)
    assert Settings(rate_limit="120/minute").rate_limit_per_second == pytest.approx(2.0)
    assert Settings(rate_limit="7").rate_limit_per_second == pytest.approx(7.0)


def test_rate_limit_rejects_unknown_period():
    settings = Settings(rate_limit="10/fortnight")
    with pytest.raises(ValueError):
        _ = settings.rate_limit_per_second


def test_cors_origin_list():
    assert Settings(cors_origins="*").cors_origin_list == ["*"]
    assert Settings(cors_origins="").cors_origin_list == ["*"]
    assert Settings(cors_origins="https://a.kz, https://b.kz").cors_origin_list == [
        "https://a.kz",
        "https://b.kz",
    ]


def test_fallback_bbox_uses_profile_by_default():
    assert Settings(osm_profile="almaty").fallback_bbox == PROFILE_BBOX["almaty"]


def test_fallback_bbox_can_be_overridden():
    settings = Settings(coverage_bbox="1,2,3,4")
    assert settings.fallback_bbox == (1.0, 2.0, 3.0, 4.0)


def test_fallback_bbox_rejects_malformed_value():
    settings = Settings(coverage_bbox="1,2,3")
    with pytest.raises(ValueError):
        _ = settings.fallback_bbox


def test_unknown_profile_is_rejected():
    with pytest.raises(ValidationError):
        Settings(osm_profile="mars")
