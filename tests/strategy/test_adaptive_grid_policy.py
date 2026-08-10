from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.orders import OrderSide
from grid_trade.strategy.adaptive_grid import (
    AdaptiveGridPolicyConfig,
    decide_adaptive_grid,
    initialize_adaptive_grid,
)
from grid_trade.strategy.adaptive_ladder import AdaptiveLadderConfig
from grid_trade.strategy.adaptive_signals import AdaptiveSignals
from grid_trade.strategy.conditional_short import ShortOverlayConfig, ShortPhase
from grid_trade.strategy.de_risk import DeRiskConfig, DeRiskRegime
from grid_trade.strategy.dynamic_center import DynamicCenterConfig
from grid_trade.strategy.funding_bias import FundingBiasConfig
from grid_trade.strategy.inventory_target import InventoryTargetConfig
from grid_trade.strategy.order_book_reference import OrderBookReferenceConfig
from grid_trade.strategy.volatility_spacing import VolatilitySpacingConfig


def _snapshot(
    *,
    second: int = 0,
    mid: str = "100",
    volatility: str = "0.005",
    position: str = "0",
) -> MarketSnapshot:
    value = Decimal(mid)
    return MarketSnapshot(
        timestamp=datetime(2026, 8, 9, 12, 0, tzinfo=UTC) + timedelta(seconds=second),
        best_bid=value - Decimal("0.01"),
        best_ask=value + Decimal("0.01"),
        realized_volatility=Decimal(volatility),
        position_quantity=Decimal(position),
        source_id=f"fixture:adaptive:{second}",
    )


def _config(*, tick_size: Decimal = Decimal("0.01")) -> AdaptiveGridPolicyConfig:
    return AdaptiveGridPolicyConfig(
        center=DynamicCenterConfig(
            reanchor_threshold_bps=Decimal("20"),
            max_step_bps=Decimal("50"),
        ),
        spacing=VolatilitySpacingConfig(
            min_spacing_bps=Decimal("40"),
            max_spacing_bps=Decimal("200"),
            volatility_multiplier=Decimal("1"),
            execution_cost_floor_bps=Decimal("30"),
        ),
        ladder=AdaptiveLadderConfig(
            levels=3,
            spacing_bps=50,
            order_quantity=Decimal("0.02"),
            tick_size=tick_size,
            max_abs_inventory=Decimal("0.10"),
        ),
        inventory=InventoryTargetConfig(
            base_long_target=Decimal("0.05"),
            max_abs_target=Decimal("0.10"),
            reservation_skew_bps=Decimal("20"),
            side_skew_strength=Decimal("1"),
        ),
        de_risk=DeRiskConfig(
            warning_trend_threshold=Decimal("-0.25"),
            severe_trend_threshold=Decimal("-0.60"),
            warning_target_fraction=Decimal("0.50"),
            severe_target_fraction=Decimal("0"),
        ),
        short=ShortOverlayConfig(
            entry_trend_threshold=Decimal("-0.60"),
            max_short_target=Decimal("0.08"),
        ),
        funding=FundingBiasConfig(
            funding_scale=Decimal("0.001"),
            max_abs_target=Decimal("0.10"),
            max_target_shift_fraction=Decimal("0.50"),
        ),
        order_book=OrderBookReferenceConfig(
            microprice_weight=Decimal("0.50"),
            imbalance_shift_bps=Decimal("10"),
        ),
    )


def _signals(
    *,
    trend: str = "0",
    funding: str = "0",
    imbalance: str = "0",
    microprice: str | None = None,
) -> AdaptiveSignals:
    return AdaptiveSignals(
        trend_score=Decimal(trend),
        funding_rate=Decimal(funding),
        order_book_imbalance=Decimal(imbalance),
        microprice=None if microprice is None else Decimal(microprice),
    )


def test_neutral_state_is_long_biased_and_inventory_skewed() -> None:
    state, ladder = initialize_adaptive_grid(_snapshot(), _signals(), _config())

    assert state.target == Decimal("0.05")
    assert state.reference > state.center
    assert state.spacing_bps == 50
    assert state.generation == 0
    assert ladder
    assert all(order.side is OrderSide.BUY for order in ladder)
    assert all(not order.reduce_only for order in ladder)


def test_strong_bear_with_long_position_flattens_before_short() -> None:
    initial_state, _ = initialize_adaptive_grid(_snapshot(position="0.05"), _signals(), _config())
    decision, state, ladder = decide_adaptive_grid(
        _snapshot(second=1, position="0.05"),
        _signals(trend="-0.90"),
        initial_state,
        _config(),
    )

    assert decision.de_risk.regime is DeRiskRegime.SEVERE
    assert decision.short.phase is ShortPhase.FLATTEN_LONG
    assert decision.short.requested_target < 0
    assert state.target == Decimal(0)
    assert ladder
    assert all(order.side is OrderSide.SELL and order.reduce_only for order in ladder)


def test_strong_bear_from_flat_can_activate_short_overlay() -> None:
    initial_state, _ = initialize_adaptive_grid(_snapshot(), _signals(), _config())
    decision, state, ladder = decide_adaptive_grid(
        _snapshot(second=1),
        _signals(trend="-0.90"),
        initial_state,
        _config(),
    )

    assert decision.short.phase is ShortPhase.SHORT
    assert state.target < 0
    assert ladder
    assert all(order.side is OrderSide.SELL for order in ladder)
    assert all(not order.reduce_only for order in ladder)


def test_positive_funding_can_strengthen_existing_bearish_short_bias() -> None:
    initial_state, _ = initialize_adaptive_grid(_snapshot(), _signals(), _config())
    decision, state, _ = decide_adaptive_grid(
        _snapshot(second=1),
        _signals(trend="-0.90", funding="0.001"),
        initial_state,
        _config(),
    )

    assert decision.short.effective_target < 0
    assert decision.funding.target_shift < 0
    assert state.target == Decimal("-0.10")


def test_order_book_signal_moves_reference_but_not_hard_inventory_target() -> None:
    initial_state, _ = initialize_adaptive_grid(_snapshot(), _signals(), _config())
    decision, state, _ = decide_adaptive_grid(
        _snapshot(second=1),
        _signals(imbalance="0.5", microprice="100.2"),
        initial_state,
        _config(),
    )

    assert decision.order_book.microprice_used
    assert state.reference > decision.inventory_reference
    assert state.target == Decimal("0.05")


def test_tick_equivalent_signal_change_preserves_generation_and_queue() -> None:
    config = _config(tick_size=Decimal("1"))
    initial_state, initial_ladder = initialize_adaptive_grid(_snapshot(), _signals(), config)
    _, next_state, next_ladder = decide_adaptive_grid(
        _snapshot(second=1),
        _signals(imbalance="0.0001"),
        initial_state,
        config,
    )

    assert next_state.generation == initial_state.generation
    assert next_ladder == initial_ladder


def test_runtime_config_change_compares_against_previous_working_ladder() -> None:
    previous_config = _config()
    initial_state, initial_ladder = initialize_adaptive_grid(
        _snapshot(),
        _signals(),
        previous_config,
    )
    next_config = replace(
        previous_config,
        ladder=replace(previous_config.ladder, order_quantity=Decimal("0.01")),
    )

    decision, next_state, next_ladder = decide_adaptive_grid(
        _snapshot(second=1),
        _signals(),
        initial_state,
        next_config,
        previous_config=previous_config,
    )

    assert decision.economic_ladder_changed is True
    assert next_state.generation == initial_state.generation + 1
    assert initial_ladder != next_ladder
    assert all(order.quantity <= Decimal("0.01") for order in next_ladder)
