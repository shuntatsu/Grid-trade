from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.risk import RiskLimits
from grid_trade.evidence.events import PnLBreakdown
from grid_trade.research.s2_runner import run_s2_comparison
from grid_trade.strategy.dynamic_center import DynamicCenterConfig
from grid_trade.strategy.grid_geometry import FixedLongGridConfig
from grid_trade.strategy.volatility_spacing import VolatilitySpacingConfig

pytestmark = pytest.mark.research

_START = datetime(2026, 8, 9, 11, 20, tzinfo=UTC)


def _snapshot(index: int, *, mid: str, vol: str, position: str = "0") -> MarketSnapshot:
    value = Decimal(mid)
    return MarketSnapshot(
        timestamp=_START + timedelta(seconds=index),
        best_bid=value - Decimal("0.01"),
        best_ask=value + Decimal("0.01"),
        realized_volatility=Decimal(vol),
        position_quantity=Decimal(position),
        source_id=f"fixture:s2:{index}",
    )


def _snapshots() -> tuple[MarketSnapshot, ...]:
    return (
        _snapshot(0, mid="100.00", vol="0.0024"),
        _snapshot(1, mid="100.10", vol="0.0024"),
        _snapshot(2, mid="100.30", vol="0.0060"),
        _snapshot(3, mid="100.30", vol="0.0060"),
        _snapshot(4, mid="100.80", vol="0.0024"),
    )


def _grid_config() -> FixedLongGridConfig:
    return FixedLongGridConfig(
        levels=3,
        spacing_bps=12,
        order_quantity=Decimal("0.01"),
        tick_size=Decimal("0.1"),
    )


def _center_config() -> DynamicCenterConfig:
    return DynamicCenterConfig(Decimal("25"), Decimal("50"))


def _spacing_config() -> VolatilitySpacingConfig:
    return VolatilitySpacingConfig(
        min_spacing_bps=Decimal("10"),
        max_spacing_bps=Decimal("100"),
        volatility_multiplier=Decimal("0.5"),
        execution_cost_floor_bps=Decimal("12"),
    )


def _limits() -> RiskLimits:
    return RiskLimits(
        max_abs_position=Decimal("1"),
        max_drawdown_fraction=Decimal("0.10"),
        max_data_age_ms=1_000,
        max_open_orders=10,
    )


def test_s2_widens_and_narrows_spacing_while_s1_baseline_stays_fixed() -> None:
    result = run_s2_comparison(
        run_id="s2-controlled",
        snapshots=_snapshots(),
        grid_config=_grid_config(),
        center_config=_center_config(),
        spacing_config=_spacing_config(),
        risk_limits=_limits(),
    )

    assert result.s1_spacing_path == (12, 12, 12, 12, 12)
    assert result.s2_spacing_path == (12, 12, 30, 30, 12)
    assert result.s2_spacing_change_count == 2
    assert result.s2_generation_count >= result.s2_spacing_change_count
    assert result.queue_reset_count == result.s2_generation_count
    assert result.risk_rejection_count == 0
    assert result.risk_reasons_seen == ()


def test_s2_center_and_spacing_change_share_one_generation() -> None:
    result = run_s2_comparison(
        run_id="s2-single-generation",
        snapshots=(
            _snapshot(0, mid="100.00", vol="0.0024"),
            _snapshot(1, mid="100.30", vol="0.0060"),
        ),
        grid_config=_grid_config(),
        center_config=_center_config(),
        spacing_config=_spacing_config(),
        risk_limits=_limits(),
    )

    assert result.s2_spacing_path == (12, 30)
    assert result.s2_generation_count == 1
    assert result.queue_reset_count == 1


def test_s2_risk_rejection_is_explicit_and_does_not_commit_candidate() -> None:
    result = run_s2_comparison(
        run_id="s2-risk-rejection",
        snapshots=(
            _snapshot(0, mid="100.00", vol="0.0024"),
            _snapshot(1, mid="101.00", vol="0.0060", position="0.98"),
        ),
        grid_config=_grid_config(),
        center_config=DynamicCenterConfig(Decimal("1"), Decimal("50")),
        spacing_config=_spacing_config(),
        risk_limits=_limits(),
    )

    assert result.s2_spacing_path == (12, 12)
    assert result.s2_generation_count == 0
    assert result.queue_reset_count == 0
    assert result.risk_rejection_count == 1
    assert "max_position" in result.risk_reasons_seen


def test_s2_runner_is_deterministic_zero_pnl_and_no_go() -> None:
    first = run_s2_comparison(
        run_id="s2-deterministic",
        snapshots=_snapshots(),
        grid_config=_grid_config(),
        center_config=_center_config(),
        spacing_config=_spacing_config(),
        risk_limits=_limits(),
    )
    second = run_s2_comparison(
        run_id="s2-deterministic",
        snapshots=_snapshots(),
        grid_config=_grid_config(),
        center_config=_center_config(),
        spacing_config=_spacing_config(),
        risk_limits=_limits(),
    )

    assert first == second
    assert first.deterministic is True
    assert first.production_authorized is False
    assert first.alpha_validated is False
    assert first.execution_scope == "policy_reconciliation_only"
    assert first.pnl == PnLBreakdown(
        realized_grid=Decimal("0"),
        directional_mark=Decimal("0"),
        fees=Decimal("0"),
        funding=Decimal("0"),
        emergency_execution=Decimal("0"),
    )
    assert len(first.evidence_digest) == 64
