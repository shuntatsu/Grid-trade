import datetime as dt
from decimal import Decimal

import pytest

from grid_trade.calibration.contracts import CalibrationObservation
from grid_trade.calibration.engine import CalibrationEngineConfig
from grid_trade.calibration.execution_cost import ExecutionCostConfig
from grid_trade.calibration.funding import FundingCalibrationConfig
from grid_trade.calibration.intensity import IntensityCalibrationConfig
from grid_trade.calibration.microstructure_contracts import TopOfBookObservation
from grid_trade.calibration.microstructure_engine import MicrostructureCalibrationConfig
from grid_trade.calibration.order_flow import OfiImpactConfig
from grid_trade.calibration.trend import TrendCalibrationConfig
from grid_trade.calibration.universal_engine import (
    UniversalCalibrationConfig,
    UniversalCalibrationState,
    update_universal_calibration,
)
from grid_trade.calibration.volatility import RobustVolatilityConfig


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
                3, 20, Decimal("0.5"), Decimal("1.5"), 21, Decimal("0.1")
            ),
            ofi_impact=OfiImpactConfig(8, 2, Decimal("0.01"), Decimal("0.01"), Decimal("2")),
            execution_cost=ExecutionCostConfig(
                8, 2, Decimal("0.75"), Decimal("0.0002"), Decimal("0.003")
            ),
            min_microstructure_quality=Decimal("0"),
        ),
    )


def test_universal_engine_rejects_mid_disagreement_at_same_market_timestamp() -> None:
    timestamp = dt.datetime(2026, 8, 10, 5, tzinfo=dt.UTC)
    observation = CalibrationObservation(
        timestamp=timestamp,
        source_id="fixture",
        instrument_id="GENERIC-PERP",
        mid=Decimal("100"),
        funding_rate=Decimal("0.0001"),
    )
    book = TopOfBookObservation(
        timestamp=timestamp,
        source_id="fixture",
        instrument_id="GENERIC-PERP",
        best_bid=Decimal("100"),
        bid_size=Decimal("5"),
        best_ask=Decimal("102"),
        ask_size=Decimal("5"),
    )

    with pytest.raises(ValueError, match="mid"):
        update_universal_calibration(
            UniversalCalibrationState(),
            observation=observation,
            book=book,
            intensity_buckets=(),
            markouts=(),
            new_ofi_impact_samples=(),
            maker_fee_rate=Decimal("0"),
            tick_size=Decimal("0.01"),
            config=_config(),
        )
