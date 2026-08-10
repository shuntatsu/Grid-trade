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
    UniversalCalibrationUpdate,
    update_universal_calibration,
)
from grid_trade.calibration.volatility import RobustVolatilityConfig


def _time(minute: int) -> dt.datetime:
    return dt.datetime(2026, 8, 10, 0, minute, tzinfo=dt.UTC)


def _config() -> UniversalCalibrationConfig:
    robust_scale = Decimal("1.4826")
    return UniversalCalibrationConfig(
        foundation=CalibrationEngineConfig(
            volatility=RobustVolatilityConfig(3, 2, robust_scale),
            trend=TrendCalibrationConfig(2, Decimal("1"), Decimal("0.000001"), Decimal("5")),
            funding=FundingCalibrationConfig(3, 2, robust_scale, Decimal("3")),
        ),
        microstructure=MicrostructureCalibrationConfig(
            intensity=IntensityCalibrationConfig(
                3,
                20,
                Decimal("0.5"),
                Decimal("1.5"),
                21,
                Decimal("0.1"),
            ),
            ofi_impact=OfiImpactConfig(
                8,
                2,
                Decimal("0.01"),
                Decimal("0.01"),
                Decimal("2"),
            ),
            execution_cost=ExecutionCostConfig(
                8,
                2,
                Decimal("0.75"),
                Decimal("0.0002"),
                Decimal("0.003"),
            ),
            min_microstructure_quality=Decimal("0"),
        ),
    )


def _observation(
    minute: int, *, instrument: str = "AAA-PERP", scale: str = "1"
) -> CalibrationObservation:
    p = Decimal(scale)
    mid = Decimal(100 + minute - 4) * p
    return CalibrationObservation(
        timestamp=_time(minute),
        source_id="fixture",
        instrument_id=instrument,
        mid=mid,
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
    mid = Decimal(100 + minute - 4) * p
    return TopOfBookObservation(
        timestamp=_time(minute),
        source_id="fixture",
        instrument_id=instrument,
        best_bid=mid - p,
        bid_size=Decimal(bid_size) * q,
        best_ask=mid + p,
        ask_size=Decimal(ask_size) * q,
    )


def _buckets() -> tuple[IntensityBucket, ...]:
    return tuple(
        IntensityBucket(Decimal(distance), Decimal("100"), arrivals)
        for distance, arrivals in ((0, 1000), (1, 368), (2, 135), (3, 50))
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
) -> UniversalCalibrationUpdate:
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


def _run_ready(
    *,
    instrument: str = "AAA-PERP",
    price_scale: str = "1",
    size_scale: str = "1",
) -> UniversalCalibrationUpdate:
    state = UniversalCalibrationState()
    for minute, sizes in ((4, ("5", "5")), (5, ("8", "4")), (6, ("9", "3"))):
        update = _update(
            state,
            minute,
            instrument=instrument,
            price_scale=price_scale,
            size_scale=size_scale,
            bid_size=sizes[0],
            ask_size=sizes[1],
        )
        state = update.next_state
    return update


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
    update = _run_ready()
    market = update.market_state

    assert market.volatility_status.ready is True
    assert market.trend_status.ready is True
    assert market.microstructure_status.ready is True
    assert market.quote_distance_scale is not None
    assert market.execution_cost_floor is not None
    assert market.order_book_score is not None
    assert market.estimated_microprice_displacement is not None
    assert update.next_state.foundation_state.generation == 3
    assert update.next_state.microstructure_state.generation == 3


def test_universal_engine_symbol_rename_changes_metadata_only() -> None:
    left = _run_ready(instrument="AAA-PERP").market_state
    right = _run_ready(instrument="BBB-PERP").market_state

    assert (left.instrument_id, right.instrument_id) == ("AAA-PERP", "BBB-PERP")
    assert left.volatility_scale == right.volatility_scale
    assert left.trend_score == right.trend_score
    assert left.funding_score == right.funding_score
    assert left.quote_distance_scale == right.quote_distance_scale
    assert left.execution_cost_floor == right.execution_cost_floor
    assert left.order_book_score == right.order_book_score
    assert left.estimated_microprice_displacement == right.estimated_microprice_displacement


def test_universal_engine_common_price_and_size_scaling_preserves_relative_outputs() -> None:
    left = _run_ready().market_state
    right = _run_ready(price_scale="100", size_scale="100").market_state

    assert left.volatility_scale == right.volatility_scale
    assert left.trend_score == right.trend_score
    assert left.funding_score == right.funding_score
    assert left.quote_distance_scale == right.quote_distance_scale
    assert left.execution_cost_floor == right.execution_cost_floor
    assert left.order_book_score == right.order_book_score
    assert left.estimated_microprice_displacement == right.estimated_microprice_displacement
