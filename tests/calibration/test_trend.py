from decimal import Decimal

import pytest

from grid_trade.calibration.trend import (
    TrendCalibrationConfig,
    estimate_normalized_trend,
)
from grid_trade.calibration.volatility import VolatilityEstimate


def _config() -> TrendCalibrationConfig:
    return TrendCalibrationConfig(
        horizon=3,
        transform_gain=Decimal("1"),
        min_volatility_scale=Decimal("0.000001"),
        max_abs_z=Decimal("8"),
    )


def _trend(prices: list[str], vol: str):
    return estimate_normalized_trend(
        tuple(Decimal(price) for price in prices),
        VolatilityEstimate(scale=Decimal(vol), sample_count=20, ready=True),
        _config(),
    )


def test_trend_is_invariant_to_multiplying_all_prices() -> None:
    a = _trend(["100", "101", "102", "103"], "0.01")
    b = _trend(["1000", "1010", "1020", "1030"], "0.01")

    assert a.ready is True
    assert a.z_score == b.z_score
    assert a.score == b.score


def test_trend_sign_tracks_direction() -> None:
    assert _trend(["100", "101", "102", "103"], "0.01").score > 0
    assert _trend(["103", "102", "101", "100"], "0.01").score < 0


def test_trend_is_bounded() -> None:
    result = _trend(["100", "100", "100", "10000"], "0.000001")

    assert result.z_score == Decimal("8")
    assert result.score is not None
    assert Decimal("-1") <= result.score <= Decimal("1")


def test_zero_volatility_uses_dimensionless_floor() -> None:
    result = _trend(["100", "100", "100", "101"], "0")

    assert result.ready is True
    assert result.score is not None
    assert result.score > 0


def test_insufficient_horizon_is_not_ready() -> None:
    result = estimate_normalized_trend(
        (Decimal("100"), Decimal("101"), Decimal("102")),
        VolatilityEstimate(scale=Decimal("0.01"), sample_count=20, ready=True),
        _config(),
    )

    assert result.ready is False
    assert result.z_score is None
    assert result.score is None


def test_unavailable_volatility_is_not_ready() -> None:
    result = estimate_normalized_trend(
        (Decimal("100"), Decimal("101"), Decimal("102"), Decimal("103")),
        VolatilityEstimate(scale=None, sample_count=1, ready=False),
        _config(),
    )

    assert result.ready is False


def test_invalid_config_fails_closed() -> None:
    with pytest.raises(ValueError, match="horizon"):
        TrendCalibrationConfig(0, Decimal("1"), Decimal("0.001"), Decimal("8"))
    with pytest.raises(ValueError, match="transform_gain"):
        TrendCalibrationConfig(3, Decimal("0"), Decimal("0.001"), Decimal("8"))
    with pytest.raises(ValueError, match="min_volatility_scale"):
        TrendCalibrationConfig(3, Decimal("1"), Decimal("0"), Decimal("8"))
    with pytest.raises(ValueError, match="max_abs_z"):
        TrendCalibrationConfig(3, Decimal("1"), Decimal("0.001"), Decimal("0"))
