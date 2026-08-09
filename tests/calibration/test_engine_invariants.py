import datetime as dt
from dataclasses import replace
from decimal import Decimal

import pytest

from grid_trade.calibration import CalibrationObservation
from grid_trade.calibration.engine import (
    CalibrationEngineConfig,
    CalibrationEngineState,
    update_calibration_engine,
)
from grid_trade.calibration.funding import FundingCalibrationConfig
from grid_trade.calibration.trend import TrendCalibrationConfig
from grid_trade.calibration.volatility import RobustVolatilityConfig, RobustVolatilityState


def _config() -> CalibrationEngineConfig:
    return CalibrationEngineConfig(
        volatility=RobustVolatilityConfig(4, 3, Decimal("1.4826")),
        trend=TrendCalibrationConfig(3, Decimal("1"), Decimal("0.000001"), Decimal("8")),
        funding=FundingCalibrationConfig(5, 3, Decimal("1"), Decimal("4")),
    )


def _observation(index: int, price: str = "100") -> CalibrationObservation:
    return CalibrationObservation(
        timestamp=dt.datetime(2026, 8, 9, 12, 0, tzinfo=dt.UTC)
        + dt.timedelta(minutes=index),
        source_id="fixture",
        instrument_id="AAA-PERP",
        mid=Decimal(price),
        funding_rate=Decimal("0.001"),
    )


def test_engine_binds_meta_parameters_on_first_update() -> None:
    config = _config()
    update = update_calibration_engine(CalibrationEngineState(), _observation(0), config)

    assert update.next_state.config == config


def test_engine_rejects_meta_parameter_change_midstream() -> None:
    config = _config()
    first = update_calibration_engine(CalibrationEngineState(), _observation(0), config)
    changed = replace(
        config,
        trend=replace(config.trend, transform_gain=Decimal("1.5")),
    )

    with pytest.raises(ValueError, match="config"):
        update_calibration_engine(first.next_state, _observation(1, "101"), changed)


def test_restored_state_rejects_divergent_price_histories() -> None:
    config = _config()

    with pytest.raises(ValueError, match="price history"):
        CalibrationEngineState(
            prices=(Decimal("100"), Decimal("101"), Decimal("102")),
            volatility_state=RobustVolatilityState(
                prices=(Decimal("99"), Decimal("101"), Decimal("102"))
            ),
            generation=3,
            last_timestamp=dt.datetime(2026, 8, 9, 12, 2, tzinfo=dt.UTC),
            source_id="fixture",
            instrument_id="AAA-PERP",
            config=config,
        )


def test_restored_state_generation_covers_retained_history() -> None:
    config = _config()

    with pytest.raises(ValueError, match="generation"):
        CalibrationEngineState(
            prices=(Decimal("100"), Decimal("101"), Decimal("102")),
            volatility_state=RobustVolatilityState(
                prices=(Decimal("100"), Decimal("101"), Decimal("102"))
            ),
            generation=2,
            last_timestamp=dt.datetime(2026, 8, 9, 12, 2, tzinfo=dt.UTC),
            source_id="fixture",
            instrument_id="AAA-PERP",
            config=config,
        )
