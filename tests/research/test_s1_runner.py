from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.risk import RiskLimits
from grid_trade.evidence.events import PnLBreakdown
from grid_trade.research.s1_runner import run_s1_comparison
from grid_trade.strategy.dynamic_center import DynamicCenterConfig
from grid_trade.strategy.fixed_grid import FixedLongGridConfig

pytestmark = pytest.mark.research

_START = datetime(2026, 8, 9, 9, 0, tzinfo=UTC)


def _snapshot(index: int, mid: str, *, position: str = "0") -> MarketSnapshot:
    value = Decimal(mid)
    return MarketSnapshot(
        timestamp=_START + timedelta(seconds=index),
        best_bid=value - Decimal("0.01"),
        best_ask=value + Decimal("0.01"),
        realized_volatility=Decimal("0.01"),
        position_quantity=Decimal(position),
        source_id=f"fixture:s1:{index}",
    )


def _snapshots() -> tuple[MarketSnapshot, ...]:
    return (
        _snapshot(0, "100.00"),
        _snapshot(1, "100.20"),
        _snapshot(2, "100.26"),
        _snapshot(3, "100.27"),
        _snapshot(4, "101.50"),
    )


def _grid_config() -> FixedLongGridConfig:
    return FixedLongGridConfig(
        levels=3,
        spacing_bps=100,
        order_quantity=Decimal("0.01"),
        tick_size=Decimal("0.1"),
    )


def _center_config() -> DynamicCenterConfig:
    return DynamicCenterConfig(
        reanchor_threshold_bps=Decimal("25"),
        max_step_bps=Decimal("50"),
    )


def _limits() -> RiskLimits:
    return RiskLimits(
        max_abs_position=Decimal("1"),
        max_drawdown_fraction=Decimal("0.10"),
        max_data_age_ms=1_000,
        max_open_orders=10,
    )


def test_s1_tracks_center_without_turning_s0_into_dynamic_baseline() -> None:
    result = run_s1_comparison(
        run_id="s1-controlled",
        snapshots=_snapshots(),
        grid_config=_grid_config(),
        center_config=_center_config(),
        risk_limits=_limits(),
    )

    assert result.s0_center_path == (
        Decimal("100.00"),
        Decimal("100.00"),
        Decimal("100.00"),
        Decimal("100.00"),
        Decimal("100.00"),
    )
    assert result.s1_center_path == (
        Decimal("100.00"),
        Decimal("100.00"),
        Decimal("100.2600"),
        Decimal("100.2600"),
        Decimal("100.761300"),
    )
    assert result.s1_reanchor_count == 2
    assert result.s1_generation_count == 2
    assert result.cancel_count == 6
    assert result.submit_count == 9
    assert result.queue_reset_count == 2
    assert result.risk_rejection_count == 0
    assert result.risk_reasons_seen == ()
    assert result.s1_mean_abs_center_error_bps < result.s0_mean_abs_center_error_bps


def test_risk_rejection_is_explicit_and_does_not_count_as_reanchor_queue_reset() -> None:
    result = run_s1_comparison(
        run_id="s1-risk-rejection",
        snapshots=(
            _snapshot(0, "100.00"),
            _snapshot(1, "101.00", position="0.98"),
        ),
        grid_config=_grid_config(),
        center_config=DynamicCenterConfig(Decimal("1"), Decimal("50")),
        risk_limits=_limits(),
    )

    assert result.s1_center_path == (Decimal("100.00"), Decimal("100.00"))
    assert result.s1_generation_count == 0
    assert result.s1_reanchor_count == 0
    assert result.queue_reset_count == 0
    assert result.cancel_count == 3
    assert result.submit_count == 3
    assert result.risk_rejection_count == 1
    assert result.risk_reasons_seen == ("max_position",)


def test_s1_controlled_runner_is_exactly_deterministic_and_no_go() -> None:
    first = run_s1_comparison(
        run_id="s1-controlled",
        snapshots=_snapshots(),
        grid_config=_grid_config(),
        center_config=_center_config(),
        risk_limits=_limits(),
    )
    second = run_s1_comparison(
        run_id="s1-controlled",
        snapshots=_snapshots(),
        grid_config=_grid_config(),
        center_config=_center_config(),
        risk_limits=_limits(),
    )

    assert first == second
    assert first.deterministic is True
    assert first.production_authorized is False
    assert first.alpha_validated is False
    assert first.execution_scope == "policy_reconciliation_only"
    assert first.ending_inventory == Decimal("0")
    assert first.pnl == PnLBreakdown(
        realized_grid=Decimal("0"),
        directional_mark=Decimal("0"),
        fees=Decimal("0"),
        funding=Decimal("0"),
        emergency_execution=Decimal("0"),
    )
    assert len(first.evidence_digest) == 64


def test_empty_snapshot_sequence_is_rejected() -> None:
    with pytest.raises(ValueError, match="snapshots"):
        run_s1_comparison(
            run_id="s1-empty",
            snapshots=(),
            grid_config=_grid_config(),
            center_config=_center_config(),
            risk_limits=_limits(),
        )
