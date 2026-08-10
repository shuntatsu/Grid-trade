from datetime import UTC, datetime
from decimal import Decimal

from grid_trade.domain.market import MarketSnapshot
from grid_trade.strategy.adaptive_grid import (
    AdaptiveGridPolicyConfig,
    AdaptiveStage,
    initialize_adaptive_grid,
)
from grid_trade.strategy.adaptive_ladder import AdaptiveLadderConfig
from grid_trade.strategy.adaptive_signals import AdaptiveSignals
from grid_trade.strategy.conditional_short import ShortOverlayConfig
from grid_trade.strategy.de_risk import DeRiskConfig
from grid_trade.strategy.dynamic_center import DynamicCenterConfig
from grid_trade.strategy.features import AdaptiveFeatures
from grid_trade.strategy.funding_bias import FundingBiasConfig
from grid_trade.strategy.inventory_target import InventoryTargetConfig
from grid_trade.strategy.order_book_reference import OrderBookReferenceConfig
from grid_trade.strategy.volatility_spacing import VolatilitySpacingConfig

_NOW = datetime(2026, 8, 10, tzinfo=UTC)


def _snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        timestamp=_NOW,
        best_bid=Decimal("99"),
        best_ask=Decimal("101"),
        realized_volatility=Decimal("0.001"),
        position_quantity=Decimal("0.8"),
        source_id="fixture:adaptive-features",
    )


def _signals() -> AdaptiveSignals:
    return AdaptiveSignals(
        trend_score=Decimal("0"),
        funding_rate=Decimal("0.8"),
        order_book_imbalance=Decimal("0.5"),
        microprice=Decimal("100.2"),
    )


def _config(features: AdaptiveFeatures) -> AdaptiveGridPolicyConfig:
    return AdaptiveGridPolicyConfig(
        center=DynamicCenterConfig(Decimal("10"), Decimal("20")),
        spacing=VolatilitySpacingConfig(
            min_spacing_bps=Decimal("5"),
            max_spacing_bps=Decimal("100"),
            volatility_multiplier=Decimal("1"),
            execution_cost_floor_bps=Decimal("5"),
        ),
        ladder=AdaptiveLadderConfig(
            levels=2,
            spacing_bps=10,
            order_quantity=Decimal("0.05"),
            tick_size=Decimal("0.01"),
            max_abs_inventory=Decimal("1"),
        ),
        inventory=InventoryTargetConfig(
            base_long_target=Decimal("0.5"),
            max_abs_target=Decimal("1"),
            reservation_skew_bps=Decimal("20"),
            side_skew_strength=Decimal("0.5"),
        ),
        de_risk=DeRiskConfig(
            warning_trend_threshold=Decimal("-0.25"),
            severe_trend_threshold=Decimal("-0.6"),
            warning_target_fraction=Decimal("0.5"),
            severe_target_fraction=Decimal("0"),
        ),
        short=ShortOverlayConfig(Decimal("-0.6"), Decimal("0.5")),
        funding=FundingBiasConfig(Decimal("1"), Decimal("1"), Decimal("0.25")),
        order_book=OrderBookReferenceConfig(Decimal("0.5"), Decimal("10")),
        stage=AdaptiveStage.S7_ORDER_BOOK,
        features=features,
    )


def test_order_book_can_be_enabled_without_funding() -> None:
    features = AdaptiveFeatures(
        inventory_control=True,
        partial_derisk=False,
        conditional_reversal=False,
        funding_bias=False,
        order_book_reference=True,
    )

    state, _ = initialize_adaptive_grid(_snapshot(), _signals(), _config(features))

    assert state.target == Decimal("0.5")
    assert state.reference > Decimal("100")


def test_inventory_control_can_be_disabled_without_removing_target() -> None:
    features = AdaptiveFeatures(
        inventory_control=False,
        partial_derisk=False,
        conditional_reversal=False,
        funding_bias=False,
        order_book_reference=False,
    )

    state, _ = initialize_adaptive_grid(_snapshot(), _signals(), _config(features))

    assert state.target == Decimal("0.5")
    assert state.bid_scale == Decimal("1")
    assert state.ask_scale == Decimal("1")
    assert state.reference == Decimal("100")
