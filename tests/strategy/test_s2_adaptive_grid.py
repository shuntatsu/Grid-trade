from datetime import UTC, datetime
from decimal import Decimal

from grid_trade.domain.market import MarketSnapshot
from grid_trade.strategy.dynamic_center import DynamicCenterConfig
from grid_trade.strategy.grid_geometry import FixedLongGridConfig
from grid_trade.strategy.s2_adaptive_grid import (
    S2GridState,
    decide_s2_grid,
    initialize_s2_grid,
)
from grid_trade.strategy.volatility_spacing import VolatilitySpacingConfig

_NOW = datetime(2026, 8, 9, 10, 50, tzinfo=UTC)


def _snapshot(*, mid: str = "100", vol: str = "0.002") -> MarketSnapshot:
    value = Decimal(mid)
    return MarketSnapshot(
        timestamp=_NOW,
        best_bid=value - Decimal("0.01"),
        best_ask=value + Decimal("0.01"),
        realized_volatility=Decimal(vol),
        position_quantity=Decimal("0"),
        source_id="fixture:s2-grid",
    )


def _grid_config(*, levels: int = 3, spacing_bps: int = 20, tick: str = "0.1") -> FixedLongGridConfig:
    return FixedLongGridConfig(
        levels=levels,
        spacing_bps=spacing_bps,
        order_quantity=Decimal("0.01"),
        tick_size=Decimal(tick),
    )


def _center_config() -> DynamicCenterConfig:
    return DynamicCenterConfig(
        reanchor_threshold_bps=Decimal("25"),
        max_step_bps=Decimal("50"),
    )


def _spacing_config() -> VolatilitySpacingConfig:
    return VolatilitySpacingConfig(
        min_spacing_bps=Decimal("10"),
        max_spacing_bps=Decimal("100"),
        volatility_multiplier=Decimal("0.5"),
        execution_cost_floor_bps=Decimal("12"),
    )


def test_initial_state_uses_causal_volatility_spacing() -> None:
    state = initialize_s2_grid(
        _snapshot(vol="0.006"),
        _grid_config(),
        _spacing_config(),
    )

    assert state.center == Decimal("100.00")
    assert state.spacing_bps == 30
    assert state.generation == 0


def test_spacing_only_change_advances_generation_once() -> None:
    state = S2GridState(center=Decimal("100"), spacing_bps=20, generation=4)
    decision, candidate_state, ladder = decide_s2_grid(
        _snapshot(mid="100", vol="0.006"),
        state,
        _center_config(),
        _grid_config(),
        _spacing_config(),
    )

    assert decision.center_threshold_crossed is False
    assert decision.spacing_changed is True
    assert decision.economic_ladder_changed is True
    assert candidate_state == S2GridState(
        center=Decimal("100"),
        spacing_bps=30,
        generation=5,
    )
    assert all(order.generation == 5 for order in ladder)


def test_center_and_spacing_change_still_advance_generation_once() -> None:
    state = S2GridState(center=Decimal("100"), spacing_bps=20, generation=7)
    decision, candidate_state, ladder = decide_s2_grid(
        _snapshot(mid="101", vol="0.006"),
        state,
        DynamicCenterConfig(Decimal("1"), Decimal("50")),
        _grid_config(),
        _spacing_config(),
    )

    assert decision.center_threshold_crossed is True
    assert decision.spacing_changed is True
    assert candidate_state.center == Decimal("100.500")
    assert candidate_state.spacing_bps == 30
    assert candidate_state.generation == 8
    assert all(order.generation == 8 for order in ladder)


def test_tick_equivalent_spacing_candidate_keeps_entire_previous_state() -> None:
    state = S2GridState(center=Decimal("100"), spacing_bps=20, generation=3)
    decision, candidate_state, ladder = decide_s2_grid(
        _snapshot(mid="100", vol="0.0042"),
        state,
        _center_config(),
        _grid_config(levels=1, tick="1"),
        _spacing_config(),
    )

    assert decision.spacing_changed is True
    assert decision.economic_ladder_changed is False
    assert candidate_state == state
    assert ladder[0].generation == 3
    assert ladder[0].price == Decimal("99")


def test_low_volatility_narrows_spacing_to_cost_floor() -> None:
    state = S2GridState(center=Decimal("100"), spacing_bps=50, generation=1)
    _, candidate_state, _ = decide_s2_grid(
        _snapshot(mid="100", vol="0.0001"),
        state,
        _center_config(),
        _grid_config(spacing_bps=50),
        _spacing_config(),
    )

    assert candidate_state.spacing_bps == 12
    assert candidate_state.generation == 2
