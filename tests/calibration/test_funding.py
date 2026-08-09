from decimal import Decimal, localcontext

import pytest

from grid_trade.calibration.funding import (
    FundingCalibrationConfig,
    FundingCalibrationState,
    FundingEstimate,
    update_funding_calibration,
)


def _config(*, window: int = 8, min_samples: int = 5) -> FundingCalibrationConfig:
    return FundingCalibrationConfig(
        window=window,
        min_samples=min_samples,
        mad_scale=Decimal("1"),
        clip_z=Decimal("4"),
    )


def _run(values: list[str], *, config: FundingCalibrationConfig | None = None) -> FundingEstimate:
    state = FundingCalibrationState()
    estimate = FundingEstimate.unavailable()
    effective_config = config or _config()
    for value in values:
        state, estimate = update_funding_calibration(state, Decimal(value), effective_config)
    return estimate


def test_constant_funding_is_explicitly_degenerate() -> None:
    result = _run(["0.0001"] * 8)

    assert result.degenerate is True
    assert result.ready is False
    assert result.score is None
    assert result.z_score is None


def test_funding_score_is_location_and_scale_normalized() -> None:
    a = _run(["0", "1", "2", "3", "4"])
    b = _run(["10", "12", "14", "16", "18"])

    assert a.ready is True
    assert b.ready is True
    assert a.z_score == b.z_score
    assert a.score == b.score


def test_funding_is_independent_of_ambient_decimal_precision() -> None:
    values = ["0.00011", "0.00027", "0.00019", "0.00043", "0.00031"]
    config = FundingCalibrationConfig(
        window=5,
        min_samples=5,
        mad_scale=Decimal("1.4826"),
        clip_z=Decimal("4"),
    )
    with localcontext() as context:
        context.prec = 10
        low_precision = _run(values, config=config)
    with localcontext() as context:
        context.prec = 50
        high_precision = _run(values, config=config)

    assert low_precision == high_precision


def test_score_is_clipped_and_bounded() -> None:
    result = _run(["0", "1", "2", "3", "100"])

    assert result.z_score == Decimal("4")
    assert result.score == Decimal("1")


def test_missing_funding_leaves_rolling_state_unchanged() -> None:
    state = FundingCalibrationState(values=(Decimal("0.1"), Decimal("0.2")))

    next_state, estimate = update_funding_calibration(state, None, _config())

    assert next_state == state
    assert estimate.ready is False
    assert estimate.score is None
    assert estimate.degenerate is False


def test_window_is_bounded() -> None:
    config = _config(window=3, min_samples=2)
    state = FundingCalibrationState()
    estimate = FundingEstimate.unavailable()
    for value in ["1", "2", "3", "4", "5"]:
        state, estimate = update_funding_calibration(state, Decimal(value), config)

    assert state.values == (Decimal("3"), Decimal("4"), Decimal("5"))
    assert estimate.ready is True


def test_invalid_config_fails_closed() -> None:
    with pytest.raises(ValueError, match="window"):
        FundingCalibrationConfig(0, 1, Decimal("1"), Decimal("4"))
    with pytest.raises(ValueError, match="min_samples"):
        FundingCalibrationConfig(4, 5, Decimal("1"), Decimal("4"))
    with pytest.raises(ValueError, match="mad_scale"):
        FundingCalibrationConfig(4, 2, Decimal("0"), Decimal("4"))
    with pytest.raises(ValueError, match="clip_z"):
        FundingCalibrationConfig(4, 2, Decimal("1"), Decimal("0"))
