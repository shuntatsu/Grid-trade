import datetime as dt
from decimal import Decimal

import pytest

from grid_trade.calibration.contracts import CalibrationObservation
from grid_trade.calibration.engine import (
    CalibrationEngineConfig,
    CalibrationEngineState,
    update_calibration_engine,
)
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
from grid_trade.calibration.sampling import SamplingSpec
from grid_trade.calibration.trend import TrendCalibrationConfig
from grid_trade.calibration.universal_engine import (
    UniversalCalibrationConfig,
    UniversalCalibrationState,
    update_universal_calibration,
)
from grid_trade.calibration.volatility import RobustVolatilityConfig

_BASE = dt.datetime(2026, 8, 10, tzinfo=dt.UTC)


def _sampling() -> SamplingSpec:
    return SamplingSpec(
        observation_interval_ms=1_000,
        interval_tolerance_ms=50,
        volatility_window_ms=4_000,
        trend_horizon_ms=2_000,
        markout_horizon_ms=5_000,
        ofi_horizon_ms=5_000,
    )


def _foundation() -> CalibrationEngineConfig:
    return CalibrationEngineConfig(
        volatility=RobustVolatilityConfig(4, 2, Decimal("1.4826")),
        trend=TrendCalibrationConfig(2, Decimal("1"), Decimal("0.000001"), Decimal("5")),
        funding=FundingCalibrationConfig(4, 2, Decimal("1.4826"), Decimal("3")),
        sampling=_sampling(),
    )


def _observation(offset_ms: int) -> CalibrationObservation:
    return CalibrationObservation(
        timestamp=_BASE + dt.timedelta(milliseconds=offset_ms),
        source_id="fixture:sampling",
        instrument_id="BTC-PERP",
        mid=Decimal("100"),
        funding_rate=Decimal("0.0001"),
    )


def _microstructure() -> MicrostructureCalibrationConfig:
    return MicrostructureCalibrationConfig(
        intensity=IntensityCalibrationConfig(
            3,
            20,
            Decimal("0.5"),
            Decimal("1.5"),
            21,
            Decimal("0.1"),
        ),
        ofi_impact=OfiImpactConfig(8, 2, Decimal("0.01"), Decimal("0.01"), Decimal("2")),
        execution_cost=ExecutionCostConfig(
            8,
            2,
            Decimal("0.75"),
            Decimal("0.0002"),
            Decimal("0.003"),
        ),
        min_microstructure_quality=Decimal("0"),
    )


def _book(offset_ms: int) -> TopOfBookObservation:
    return TopOfBookObservation(
        timestamp=_BASE + dt.timedelta(milliseconds=offset_ms),
        source_id="fixture:sampling",
        instrument_id="BTC-PERP",
        best_bid=Decimal("99"),
        bid_size=Decimal("5"),
        best_ask=Decimal("101"),
        ask_size=Decimal("5"),
    )


def _buckets() -> tuple[IntensityBucket, ...]:
    return tuple(
        IntensityBucket(Decimal(distance), Decimal("100"), arrivals)
        for distance, arrivals in ((0, 1000), (1, 368), (2, 135), (3, 50))
    )


def test_sampling_spec_binds_count_windows_to_elapsed_time() -> None:
    spec = _sampling()

    spec.validate_engine_counts(volatility_window=4, trend_horizon=2)

    with pytest.raises(ValueError, match="volatility window"):
        spec.validate_engine_counts(volatility_window=5, trend_horizon=2)
    with pytest.raises(ValueError, match="trend horizon"):
        spec.validate_engine_counts(volatility_window=4, trend_horizon=3)


def test_sampling_spec_rejects_off_cadence_observation() -> None:
    spec = _sampling()

    spec.validate_observation_delta(_BASE, _BASE + dt.timedelta(milliseconds=1_040))

    with pytest.raises(ValueError, match="observation cadence"):
        spec.validate_observation_delta(_BASE, _BASE + dt.timedelta(milliseconds=1_100))


def test_sampling_spec_validates_matured_label_horizons() -> None:
    spec = _sampling()
    valid_markout = MaturedMarkout(
        _BASE,
        _BASE + dt.timedelta(milliseconds=5_020),
        MarkoutSide.BUY,
        Decimal("100"),
        Decimal("99.9"),
    )
    valid_ofi = OfiImpactSample(
        _BASE,
        _BASE + dt.timedelta(milliseconds=4_980),
        Decimal("1"),
        Decimal("0.001"),
    )

    spec.validate_markout(valid_markout)
    spec.validate_ofi_sample(valid_ofi)

    invalid_markout = MaturedMarkout(
        _BASE,
        _BASE + dt.timedelta(milliseconds=5_100),
        MarkoutSide.BUY,
        Decimal("100"),
        Decimal("99.9"),
    )
    with pytest.raises(ValueError, match="markout horizon"):
        spec.validate_markout(invalid_markout)


def test_calibration_engine_enforces_configured_observation_cadence() -> None:
    config = _foundation()
    first = update_calibration_engine(CalibrationEngineState(), _observation(0), config)

    update_calibration_engine(first.next_state, _observation(1_040), config)

    with pytest.raises(ValueError, match="observation cadence"):
        update_calibration_engine(first.next_state, _observation(1_100), config)


def test_universal_engine_rejects_wrong_matured_label_horizon() -> None:
    config = UniversalCalibrationConfig(
        foundation=_foundation(),
        microstructure=_microstructure(),
    )
    invalid_markout = MaturedMarkout(
        _BASE,
        _BASE + dt.timedelta(milliseconds=6_000),
        MarkoutSide.BUY,
        Decimal("100"),
        Decimal("99.9"),
    )

    with pytest.raises(ValueError, match="markout horizon"):
        update_universal_calibration(
            UniversalCalibrationState(),
            observation=_observation(0),
            book=_book(0),
            intensity_buckets=_buckets(),
            markouts=(invalid_markout,),
            new_ofi_impact_samples=(),
            maker_fee_rate=Decimal("0.0001"),
            tick_size=Decimal("0.01"),
            config=config,
        )
