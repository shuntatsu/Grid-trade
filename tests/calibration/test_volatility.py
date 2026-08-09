import datetime as dt
from decimal import Decimal, localcontext

import pytest

from grid_trade.calibration import CalibrationObservation
from grid_trade.calibration.volatility import (
    RobustVolatilityConfig,
    RobustVolatilityState,
    VolatilityEstimate,
    update_robust_volatility,
)


def _observation(index: int, price: str) -> CalibrationObservation:
    return CalibrationObservation(
        timestamp=dt.datetime(2026, 8, 9, tzinfo=dt.UTC) + dt.timedelta(minutes=index),
        source_id="fixture",
        instrument_id="AAA-PERP",
        mid=Decimal(price),
        funding_rate=None,
    )


def _run(
    prices: list[str],
    *,
    min_samples: int = 3,
    window: int = 4,
) -> tuple[RobustVolatilityState, VolatilityEstimate]:
    state = RobustVolatilityState()
    estimate = None
    config = RobustVolatilityConfig(
        window=window,
        min_samples=min_samples,
        mad_scale=Decimal("1.4826"),
    )
    for index, price in enumerate(prices):
        state, estimate = update_robust_volatility(state, _observation(index, price), config)
    assert estimate is not None
    return state, estimate


def test_volatility_is_scale_invariant_to_price_level() -> None:
    _, a = _run(["100", "101", "100", "102", "101"])
    _, b = _run(["1000", "1010", "1000", "1020", "1010"])

    assert a.ready is True
    assert a.scale == b.scale


def test_volatility_is_independent_of_ambient_decimal_precision() -> None:
    with localcontext() as context:
        context.prec = 10
        _, low_precision = _run(["100", "101.37", "99.81", "102.44", "101.03"])
    with localcontext() as context:
        context.prec = 50
        _, high_precision = _run(["100", "101.37", "99.81", "102.44", "101.03"])

    assert low_precision == high_precision


def test_volatility_is_not_ready_before_min_samples() -> None:
    _, estimate = _run(["100", "101"], min_samples=3)

    assert estimate.ready is False
    assert estimate.scale is None
    assert estimate.sample_count == 1


def test_flat_prices_produce_zero_scale_after_warmup() -> None:
    _, estimate = _run(["100", "100", "100", "100"])

    assert estimate.ready is True
    assert estimate.scale == Decimal(0)


def test_state_truncates_to_window_plus_one_prices() -> None:
    state, estimate = _run(["100", "101", "102", "103", "104", "105"], window=3)

    assert state.prices == (Decimal("102"), Decimal("103"), Decimal("104"), Decimal("105"))
    assert estimate.sample_count == 3


@pytest.mark.parametrize(
    "config",
    [
        RobustVolatilityConfig(window=2, min_samples=1, mad_scale=Decimal("1")),
    ],
)
def test_valid_config_is_immutable(config: RobustVolatilityConfig) -> None:
    assert config.window == 2


def test_invalid_config_fails_closed() -> None:
    with pytest.raises(ValueError, match="window"):
        RobustVolatilityConfig(window=0, min_samples=1, mad_scale=Decimal("1"))
    with pytest.raises(ValueError, match="min_samples"):
        RobustVolatilityConfig(window=3, min_samples=4, mad_scale=Decimal("1"))
    with pytest.raises(ValueError, match="mad_scale"):
        RobustVolatilityConfig(window=3, min_samples=2, mad_scale=Decimal("0"))
