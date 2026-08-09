from datetime import UTC, datetime
from decimal import Decimal

from grid_trade.domain.market import MarketSnapshot
from grid_trade.strategy.dynamic_center import (
    CenterDecisionReason,
    DynamicCenterConfig,
    DynamicCenterState,
    decide_dynamic_center,
)
from grid_trade.strategy.fixed_grid import FixedLongGridConfig

_NOW = datetime(2026, 8, 9, 9, 0, tzinfo=UTC)


def _snapshot(*, mid: str) -> MarketSnapshot:
    value = Decimal(mid)
    return MarketSnapshot(
        timestamp=_NOW,
        best_bid=value - Decimal("0.01"),
        best_ask=value + Decimal("0.01"),
        realized_volatility=Decimal("0.01"),
        position_quantity=Decimal("0"),
        source_id="fixture:s1-ladder",
    )


def _grid_config(*, tick_size: str = "0.1") -> FixedLongGridConfig:
    return FixedLongGridConfig(
        levels=3,
        spacing_bps=100,
        order_quantity=Decimal("0.01"),
        tick_size=Decimal(tick_size),
    )


def test_within_threshold_preserves_center_generation_and_ladder() -> None:
    decision, ladder = decide_dynamic_center(
        _snapshot(mid="100.20"),
        DynamicCenterState(center=Decimal("100"), generation=4),
        DynamicCenterConfig(Decimal("25"), Decimal("50")),
        _grid_config(),
    )

    assert decision.reason is CenterDecisionReason.WITHIN_THRESHOLD
    assert decision.effective_center == Decimal("100")
    assert decision.effective_generation == 4
    assert decision.reanchored is False
    assert decision.economic_ladder_changed is False
    assert all(order.generation == 4 for order in ladder)


def test_tick_equivalent_candidate_preserves_generation_and_center() -> None:
    decision, ladder = decide_dynamic_center(
        _snapshot(mid="100.05"),
        DynamicCenterState(center=Decimal("100"), generation=4),
        DynamicCenterConfig(Decimal("1"), Decimal("10")),
        _grid_config(tick_size="1"),
    )

    assert decision.proposed_center == Decimal("100.05")
    assert decision.reason is CenterDecisionReason.NO_EFFECTIVE_LADDER_CHANGE
    assert decision.effective_center == Decimal("100")
    assert decision.effective_generation == 4
    assert decision.reanchored is False
    assert decision.economic_ladder_changed is False
    assert [order.price for order in ladder] == [Decimal("99"), Decimal("98"), Decimal("97")]


def test_effective_price_change_increments_generation_once() -> None:
    decision, ladder = decide_dynamic_center(
        _snapshot(mid="101"),
        DynamicCenterState(center=Decimal("100"), generation=4),
        DynamicCenterConfig(Decimal("1"), Decimal("50")),
        _grid_config(),
    )

    assert decision.proposed_center == Decimal("100.5")
    assert decision.reason is CenterDecisionReason.BOUNDED_REANCHOR
    assert decision.effective_center == Decimal("100.5")
    assert decision.effective_generation == 5
    assert decision.reanchored is True
    assert decision.economic_ladder_changed is True
    assert all(order.generation == 5 for order in ladder)
    assert all(order.client_order_id.startswith("s1:g5:") for order in ladder)


def test_repeated_large_moves_advance_one_generation_per_effective_decision() -> None:
    config = DynamicCenterConfig(Decimal("1"), Decimal("50"))
    grid_config = _grid_config()
    state = DynamicCenterState(center=Decimal("100"), generation=0)

    first, _ = decide_dynamic_center(_snapshot(mid="102"), state, config, grid_config)
    second, _ = decide_dynamic_center(
        _snapshot(mid="102"),
        DynamicCenterState(first.effective_center, first.effective_generation),
        config,
        grid_config,
    )

    assert first.effective_center == Decimal("100.5")
    assert first.effective_generation == 1
    assert second.effective_center == Decimal("101.0025")
    assert second.effective_generation == 2
