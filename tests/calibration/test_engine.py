import datetime as dt
from decimal import Decimal

import pytest

from grid_trade.calibration import CalibrationObservation, CalibrationReadiness
from grid_trade.calibration.engine import (
    CalibrationEngineConfig,
    CalibrationEngineState,
    CalibrationUpdate,
    update_calibration_engine,
)
from grid_trade.calibration.funding import FundingCalibrationConfig
from grid_trade.calibration.trend import TrendCalibrationConfig
from grid_trade.calibration.volatility import RobustVolatilityConfig


def _config() -> CalibrationEngineConfig:
    return CalibrationEngineConfig(
        volatility=RobustVolatilityConfig(
            window=4,
            min_samples=3,
            mad_scale=Decimal("1.4826"),
        ),
        trend=TrendCalibrationConfig(
            horizon=3,
            transform_gain=Decimal("1"),
            min_volatility_scale=Decimal("0.000001"),
            max_abs_z=Decimal("8"),
        ),
        funding=FundingCalibrationConfig(
            window=5,
            min_samples=3,
            mad_scale=Decimal("1"),
            clip_z=Decimal("4"),
        ),
    )


def _observations(
    instrument_id: str,
    *,
    scale: Decimal = Decimal("1"),
    funding_values: tuple[str | None, ...] = ("0.001", "0.002", "0.0015", "0.003", "0.0025"),
) -> tuple[CalibrationObservation, ...]:
    prices = ("100", "101", "100", "102", "103")
    base = dt.datetime(2026, 8, 9, 12, 0, tzinfo=dt.UTC)
    return tuple(
        CalibrationObservation(
            timestamp=base + dt.timedelta(minutes=index),
            source_id="fixture",
            instrument_id=instrument_id,
            mid=Decimal(price) * scale,
            funding_rate=None if funding is None else Decimal(funding),
        )
        for index, (price, funding) in enumerate(zip(prices, funding_values, strict=True))
    )


def _run(observations: tuple[CalibrationObservation, ...]) -> CalibrationUpdate:
    state = CalibrationEngineState()
    update = None
    for observation in observations:
        update = update_calibration_engine(state, observation, _config())
        state = update.next_state
    assert update is not None
    return update


def test_engine_becomes_ready_after_warmup() -> None:
    update = _run(_observations("AAA-PERP"))

    assert update.market_state.readiness is CalibrationReadiness.READY
    assert update.market_state.volatility_status.ready is True
    assert update.market_state.trend_status.ready is True
    assert update.market_state.volatility_scale is not None
    assert update.market_state.trend_score is not None
    assert update.next_state.generation == 5


def test_symbol_name_does_not_change_numeric_output() -> None:
    a = _run(_observations("AAA-PERP"))
    b = _run(_observations("BTCUSDT-PERP"))

    assert a.market_state.volatility_scale == b.market_state.volatility_scale
    assert a.market_state.trend_score == b.market_state.trend_score
    assert a.market_state.funding_score == b.market_state.funding_score
    assert a.market_state.instrument_id != b.market_state.instrument_id


def test_price_scale_does_not_change_normalized_market_state() -> None:
    a = _run(_observations("AAA-PERP", scale=Decimal("1")))
    b = _run(_observations("AAA-PERP", scale=Decimal("10")))

    assert a.market_state.volatility_scale == b.market_state.volatility_scale
    assert a.market_state.trend_score == b.market_state.trend_score


def test_microstructure_values_are_never_fabricated() -> None:
    state = _run(_observations("AAA-PERP")).market_state

    assert state.quote_distance_scale is None
    assert state.execution_cost_floor is None
    assert state.order_book_score is None
    assert state.estimated_microprice_displacement is None
    assert state.microstructure_status.ready is False


def test_missing_funding_does_not_block_foundation_readiness() -> None:
    update = _run(_observations("AAA-PERP", funding_values=(None, None, None, None, None)))

    assert update.market_state.readiness is CalibrationReadiness.READY
    assert update.market_state.funding_score is None
    assert update.market_state.funding_status.ready is False


def test_degenerate_funding_does_not_block_foundation_readiness() -> None:
    update = _run(
        _observations(
            "AAA-PERP",
            funding_values=("0.001", "0.001", "0.001", "0.001", "0.001"),
        )
    )

    assert update.market_state.readiness is CalibrationReadiness.READY
    assert update.market_state.funding_score is None
    assert update.market_state.funding_status.ready is False
    assert update.market_state.funding_status.reason == "degenerate"


def test_equal_or_out_of_order_timestamp_fails_closed() -> None:
    first = _observations("AAA-PERP")[0]
    initial = CalibrationEngineState()
    accepted = update_calibration_engine(initial, first, _config())

    with pytest.raises(ValueError, match="strictly newer"):
        update_calibration_engine(accepted.next_state, first, _config())


def test_identity_cannot_change_inside_one_engine_state() -> None:
    observations = _observations("AAA-PERP")
    accepted = update_calibration_engine(CalibrationEngineState(), observations[0], _config())
    changed_identity = CalibrationObservation(
        timestamp=observations[1].timestamp,
        source_id="fixture",
        instrument_id="BBB-PERP",
        mid=observations[1].mid,
        funding_rate=observations[1].funding_rate,
    )

    with pytest.raises(ValueError, match="instrument_id"):
        update_calibration_engine(accepted.next_state, changed_identity, _config())


def test_identical_ordered_observations_are_deterministic() -> None:
    observations = _observations("AAA-PERP")

    assert _run(observations) == _run(observations)
