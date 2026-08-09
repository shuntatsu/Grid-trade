import datetime as dt
from decimal import Decimal

import pytest

from grid_trade.calibration.contracts import CalibrationObservation
from grid_trade.calibration.engine import CalibrationEngineConfig
from grid_trade.calibration.execution_cost import ExecutionCostConfig
from grid_trade.calibration.funding import FundingCalibrationConfig
from grid_trade.calibration.intensity import IntensityCalibrationConfig
from grid_trade.calibration.microstructure_contracts import (
    IntensityBucket,
    MarkoutSide,
    MaturedMarkout,
    OfiImpactSample,
    TopOfBookObservation,
)
from grid_trade.calibration.microstructure_engine import MicrostructureCalibrationConfig
from grid_trade.calibration.order_flow import OfiImpactConfig
from grid_trade.calibration.trend import TrendCalibrationConfig
from grid_trade.calibration.universal_engine import (
    UniversalCalibrationConfig,
    UniversalCalibrationState,
    update_universal_calibration,
)
from grid_trade.calibration.volatility import RobustVolatilityConfig


def _time(minute: int) -> dt.datetime:
    return dt.datetime(2026, 8, 10, 0, minute, tzinfo=dt.UTC)


def _config() -> UniversalCalibrationConfig:
    return UniversalCalibrationConfig(
        foundation=CalibrationEngineConfig(
            volatility=RobustVolatilityConfig(window=3, min_samples=2),
            trend=TrendCalibrationConfig(
                horizon=2,
                score_scale=Decimal("1"),
                volatility_floor=Decimal("0.000001"),
            ),
            funding=FundingCalibrationConfig(window=3, min_samples=2),
        ),
        microstructure=MicrostructureCalibrationConfig(
            intensity=IntensityCalibrationConfig(
                min_buckets=3,
                min_total_arrivals=20,
                k_min=Decimal("0.5"),
                k_max=Decimal("1.5"),
                k_steps=21,
                min_log_likelihood_improvement=Decimal("0.1"),
            ),
            ofi_impact=OfiImpactConfig(
                window=8,
                min_samples=2,
                min_abs_feature_energy=Decimal("0.01"),
                max_abs_beta=Decimal("0.01"),
                score_scale_vol_units=Decimal("2"),
            ),
            execution_cost=ExecutionCostConfig(
                markout_window=8,
                min_markout_samples=2,
                adverse_quantile=Decimal("0.75"),
                uncertainty_buffer=Decimal("0.0002"),
                fallback_adverse_cost=Decimal("0.003"),
            ),
            min_microstructure_quality=Decimal("0"),
        ),
    )


def _observation(
    minute: int, *, instrument: str = "AAA-PERP", scale: str = "1"
) -> CalibrationObservation:
    price_scale = Decimal(scale)
    return CalibrationObservation(
        timestamp=_time(minute),
        source_id="fixture",
        instrument_id=instrument,
        mid=Decimal("100") * price_scale,
        funding_rate=Decimal("0.0001") if minute % 2 == 0 else Decimal("0.0002"),
    )


def _book(
    minute: int,
    *,
    instrument: str = "AAA-PERP",
    price_scale: str = "1",
    size_scale: str = "1",
    bid_size: str = "5",
    ask_size: str = "5",
) -> TopOfBookObservation:
    p = Decimal(price_scale)
    q = Decimal(size_scale)
    return TopOfBookObservation(
        timestamp=_time(minute),
        source_id="fixture",
        instrument_id=instrument,
        best_bid=Decimal("99") * p,
        bid_size=Decimal(bid_size) * q,
        best_ask=Decimal("101") * p,
        ask_size=Decimal(ask_size) * q,
    )


def _buckets() -> tuple[IntensityBucket, ...]:
    return (
        IntensityBucket(Decimal("0"), Decimal("100"), 1000),
        IntensityBucket(Decimal("1"), Decimal("100"), 368),
        IntensityBucket(Decimal("2"), Decimal("100"), 135),
        IntensityBucket(Decimal("3"), Decimal("100"), 50),
    )


def _labels() -> tuple[OfiImpactSample, ...]:
    return (
        OfiImpactSample(_time(0), _time(2), Decimal("-1"), Decimal("-0.002")),
        OfiImpactSample(_time(1), _time(3), Decimal("1"), Decimal("0.002")),
    )


def _markouts(*, price_scale: str = "1") -> tuple[MaturedMarkout, ...]:
    p = Decimal(price_scale)
    return (
        MaturedMarkout(
            _time(0), _time(2), MarkoutSide.BUY, Decimal("100") * p, Decimal("99.9") * p
        ),
        MaturedMarkout(
            _time(1), _time(3), MarkoutSide.SELL, Decimal("100") * p, Decimal("100.2") * p
        ),
    )


def _update(
    state: UniversalCalibrationState,
    minute: int,
    *,
    instrument: str = "AAA-PERP",
    price_scale: str = "1",
    size_scale: str = "1",
    bid_size: str = "5",
    ask_size: str = "5",
):
    return update_universal_calibration(
        state,
        observation=_observation(minute, instrument=instrument, scale=price_scale),
        book=_book(
            minute,
            instrument=instrument,
            price_scale=price_scale,
            size_scale=size_scale,
            bid_size=bid_size,
            ask_size=ask_size,
        ),
        intensity_buckets=_buckets(),
        markouts=_markouts(price_scale=price_scale),
        new_ofi_impact_samples=_labels() if state.foundation_state.generation == 0 else (),
        maker_fee_rate=Decimal("0.0001"),
        tick_size=Decimal("0.01") * Decimal(price_scale),
        config=_config(),
    )


def test_universal_engine_rejects_foundation_microstructure_identity_mismatch() -> None:
    with pytest.raises(ValueError, match="identity"):
        update_universal_calibration(
            UniversalCalibrationState(),
            observation=_observation(4, instrument="AAA-PERP"),
            book=_book(4, instrument="BBB-PERP"),
            intensity_buckets=_buckets(),
            markouts=_markouts(),
            new_ofi_impact_samples=_labels(),
            maker_fee_rate=Decimal("0.0001"),
            tick_size=Decimal("0.01"),
            config=_config(),
        )


def test_universal_engine_composes_ready_microstructure_into_market_state() -> None:
    state = UniversalCalibrationState()
    for minute, sizes in ((4, ("5", "5")), (5, ("8", "4")), (6, ("9", "3"))):
        update = _update(state, minute, bid_size=sizes[0], ask_size=sizes[1])
        state = update.next_state

    market = update.market_state
    assert market.volatility_status.ready is True
    assert market.trend_status.ready is True
    assert market.microstructure_status.ready is True
    assert market.quote_distance_scale is not None
    assert market.execution_cost_floor is not None
    assert market.order_book_score is not None
    assert market.estimated_microprice_displacement is not None
    assert state.foundation_state.generation == state.microstructure_state.generation == 3


def test_universal_engine_symbol_rename_changes_metadata_only() -> None:
    aaa = UniversalCalibrationState()
    bbb = UniversalCalibrationState()
    for minute, sizes in ((4, ("5", "5")), (5, ("8", "4")), (6, ("9", "3"))):
        aaa_update = _update(
            aaa, minute, instrument="AAA-PERP", bid_size=sizes[0], ask_size=sizes[1]
        )
        bbb_update = _update(
            bbb, minute, instrument="BBB-PERP", bid_size=sizes[0], ask_size=sizes[1]
        )
        aaa = aaa_update.next_state
        bbb = bbb_update.next_state

    left = aaa_update.market_state
    right = bbb_update.market_state
    assert left.instrument_id == "AAA-PERP"
    assert right.instrument_id == "BBB-PERP"
    assert left.volatility_scale == right.volatility_scale
    assert left.trend_score == right.trend_score
    assert left.funding_score == right.funding_score
    assert left.quote_distance_scale == right.quote_distance_scale
    assert left.execution_cost_floor == right.execution_cost_floor
    assert left.order_book_score == right.order_book_score
    assert left.estimated_microprice_displacement == right.estimated_microprice_displacement


def test_universal_engine_common_price_and_size_scaling_preserves_relative_outputs() -> None:
    base = UniversalCalibrationState()
    scaled = UniversalCalibrationState()
    for minute, sizes in ((4, ("5", "5")), (5, ("8", "4")), (6, ("9", "3"))):
        base_update = _update(base, minute, bid_size=sizes[0], ask_size=sizes[1])
        scaled_update = _update(
            scaled,
            minute,
            price_scale="100",
            size_scale="100",
            bid_size=sizes[0],
            ask_size=sizes[1],
        )
        base = base_update.next_state
        scaled = scaled_update.next_state

    left = base_update.market_state
    right = scaled_update.market_state
    assert left.volatility_scale == right.volatility_scale
    assert left.trend_score == right.trend_score
    assert left.funding_score == right.funding_score
    assert left.quote_distance_scale == right.quote_distance_scale
    assert left.execution_cost_floor == right.execution_cost_floor
    assert left.order_book_score == right.order_book_score
    assert left.estimated_microprice_displacement == right.estimated_microprice_displacement
