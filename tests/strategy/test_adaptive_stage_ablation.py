from datetime import UTC, datetime
from decimal import Decimal

from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.orders import OrderSide, PassiveOrderIntent
from grid_trade.strategy.adaptive_grid import (
    AdaptiveGridDecision,
    AdaptiveGridPolicyConfig,
    AdaptiveGridState,
    AdaptiveStage,
    decide_adaptive_grid,
    initialize_adaptive_grid,
)
from grid_trade.strategy.adaptive_ladder import AdaptiveLadderConfig
from grid_trade.strategy.adaptive_signals import AdaptiveSignals
from grid_trade.strategy.conditional_short import ShortOverlayConfig
from grid_trade.strategy.de_risk import DeRiskConfig
from grid_trade.strategy.dynamic_center import DynamicCenterConfig
from grid_trade.strategy.funding_bias import FundingBiasConfig
from grid_trade.strategy.inventory_target import InventoryTargetConfig
from grid_trade.strategy.order_book_reference import OrderBookReferenceConfig
from grid_trade.strategy.volatility_spacing import VolatilitySpacingConfig


def _snapshot(position: str = "0") -> MarketSnapshot:
    return MarketSnapshot(
        timestamp=datetime(2026, 8, 9, 14, 0, tzinfo=UTC),
        best_bid=Decimal("99.99"),
        best_ask=Decimal("100.01"),
        realized_volatility=Decimal("0.005"),
        position_quantity=Decimal(position),
        source_id="fixture:ablation",
    )


def _signals() -> AdaptiveSignals:
    return AdaptiveSignals(
        trend_score=Decimal("-0.90"),
        funding_rate=Decimal("0.001"),
        order_book_imbalance=Decimal("0.5"),
        microprice=Decimal("100.2"),
    )


def _config(stage: AdaptiveStage) -> AdaptiveGridPolicyConfig:
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
            tick_size=Decimal("0.01"),
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
            severe_target_fraction=Decimal(0),
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
        stage=stage,
    )


def _decision(
    stage: AdaptiveStage,
) -> tuple[AdaptiveGridDecision, AdaptiveGridState, tuple[PassiveOrderIntent, ...]]:
    initial, _ = initialize_adaptive_grid(_snapshot(), AdaptiveSignals.neutral(), _config(stage))
    return decide_adaptive_grid(_snapshot(), _signals(), initial, _config(stage))


def test_s3_keeps_long_inventory_target_without_later_features() -> None:
    decision, state, ladder = _decision(AdaptiveStage.S3_INVENTORY)
    assert not decision.de_risk_applied
    assert not decision.short_applied
    assert not decision.funding_applied
    assert not decision.order_book_applied
    assert state.target == Decimal("0.05")
    assert all(order.side is OrderSide.BUY for order in ladder)


def test_s4_can_derisk_but_cannot_open_short() -> None:
    decision, state, ladder = _decision(AdaptiveStage.S4_DERISK)
    assert decision.de_risk_applied
    assert not decision.short_applied
    assert state.target == Decimal(0)
    assert ladder == ()


def test_s5_adds_conditional_short_but_not_funding_or_orderbook() -> None:
    decision, state, ladder = _decision(AdaptiveStage.S5_SHORT)
    assert decision.short_applied
    assert not decision.funding_applied
    assert not decision.order_book_applied
    assert state.target == Decimal("-0.06")
    assert all(order.side is OrderSide.SELL for order in ladder)


def test_s6_adds_funding_bias_but_keeps_orderbook_disabled() -> None:
    decision, state, _ = _decision(AdaptiveStage.S6_FUNDING)
    assert decision.funding_applied
    assert not decision.order_book_applied
    assert state.target == Decimal("-0.10")
    assert state.reference == decision.inventory_reference


def test_s7_adds_orderbook_reference_adjustment() -> None:
    decision, state, _ = _decision(AdaptiveStage.S7_ORDER_BOOK)
    assert decision.order_book_applied
    assert state.reference > decision.inventory_reference
