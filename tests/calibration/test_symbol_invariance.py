import ast
import datetime as dt
from decimal import Decimal
from pathlib import Path

from grid_trade.calibration import CalibrationObservation
from grid_trade.calibration.engine import (
    CalibrationEngineConfig,
    CalibrationEngineState,
    CalibrationUpdate,
    update_calibration_engine,
)
from grid_trade.calibration.funding import FundingCalibrationConfig
from grid_trade.calibration.trend import TrendCalibrationConfig
from grid_trade.calibration.volatility import RobustVolatilityConfig

_CALIBRATION_SRC = Path("src/grid_trade/calibration")
_FORBIDDEN_SYMBOL_LITERALS = ("BTC", "ETH", "SOL")


def _config() -> CalibrationEngineConfig:
    return CalibrationEngineConfig(
        volatility=RobustVolatilityConfig(4, 3, Decimal("1.4826")),
        trend=TrendCalibrationConfig(3, Decimal("1"), Decimal("0.000001"), Decimal("8")),
        funding=FundingCalibrationConfig(5, 3, Decimal("1"), Decimal("4")),
    )


def _run(instrument_id: str, scale: Decimal) -> CalibrationUpdate:
    prices = ("100", "101", "100", "102", "103")
    funding = ("0.001", "0.002", "0.0015", "0.003", "0.0025")
    base = dt.datetime(2026, 8, 9, 12, 0, tzinfo=dt.UTC)
    state = CalibrationEngineState()
    update = None
    for index, (price, rate) in enumerate(zip(prices, funding, strict=True)):
        update = update_calibration_engine(
            state,
            CalibrationObservation(
                timestamp=base + dt.timedelta(minutes=index),
                source_id="fixture",
                instrument_id=instrument_id,
                mid=Decimal(price) * scale,
                funding_rate=Decimal(rate),
            ),
            _config(),
        )
        state = update.next_state
    assert update is not None
    return update


def test_changing_only_symbol_identity_does_not_change_numeric_output() -> None:
    generic = _run("AAA-PERP", Decimal("1"))
    named = _run("BTCUSDT-PERP", Decimal("1"))

    assert generic.market_state.volatility_scale == named.market_state.volatility_scale
    assert generic.market_state.trend_score == named.market_state.trend_score
    assert generic.market_state.funding_score == named.market_state.funding_score
    assert generic.next_state.prices == named.next_state.prices
    assert generic.market_state.instrument_id != named.market_state.instrument_id


def test_multiplying_all_prices_preserves_normalized_calibration() -> None:
    base = _run("AAA-PERP", Decimal("1"))
    scaled = _run("AAA-PERP", Decimal("100"))

    assert base.market_state.volatility_scale == scaled.market_state.volatility_scale
    assert base.market_state.trend_score == scaled.market_state.trend_score
    assert base.market_state.funding_score == scaled.market_state.funding_score


def test_production_calibration_contains_no_known_symbol_magic_branches() -> None:
    violations: list[str] = []
    for path in sorted(_CALIBRATION_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            upper = node.value.upper()
            if any(symbol in upper for symbol in _FORBIDDEN_SYMBOL_LITERALS):
                violations.append(f"{path}:{node.lineno}:{node.value}")

    assert violations == []
