from datetime import UTC, datetime
from decimal import Decimal

import pytest

from grid_trade.domain.market import MarketSnapshot
from grid_trade.strategy.adaptive_grid import (
    AdaptiveGridPolicyConfig,
    AdaptiveStage,
    initialize_adaptive_grid,
)
from grid_trade.strategy.adaptive_ladder import AdaptiveLadderConfig, build_adaptive_ladder
from grid_trade.strategy.adaptive_signals import AdaptiveSignals
from grid_trade.strategy.conditional_short import ShortOverlayConfig
from grid_trade.strategy.de_risk import DeRiskConfig
from grid_trade.strategy.dynamic_center import DynamicCenterConfig
from grid_trade.strategy.fixed_grid import build_fixed_long_grid
from grid_trade.strategy.funding_bias import FundingBiasConfig
from grid_trade.strategy.grid_geometry import FixedLongGridConfig, ladder_economic_signature
from grid_trade.strategy.inventory_target import InventoryTargetConfig
from grid_trade.strategy.order_book_reference import OrderBookReferenceConfig
from grid_trade.strategy.volatility_spacing import VolatilitySpacingConfig

_NOW = datetime(2026, 8, 10, tzinfo=UTC)


def _snapshot(instrument_id: str) -> MarketSnapshot:
    return MarketSnapshot(
        timestamp=_NOW,
        best_bid=Decimal("99"),
        best_ask=Decimal("101"),
        realized_volatility=Decimal("0.001"),
        position_quantity=Decimal("0"),
        source_id="fixture:instrument-bound-ladder",
        instrument_id=instrument_id,
    )


def test_explicit_instrument_namespaces_adaptive_orders() -> None:
    config = AdaptiveLadderConfig(
        levels=2,
        spacing_bps=10,
        order_quantity=Decimal("0.01"),
        tick_size=Decimal("0.01"),
        max_abs_inventory=Decimal("1"),
        instrument_id="BTC-PERP",
    )

    orders = build_adaptive_ladder(
        reference=Decimal("100"),
        position=Decimal("0"),
        target=Decimal("0.5"),
        bid_scale=Decimal("1"),
        ask_scale=Decimal("1"),
        config=config,
        generation=3,
        stage="adaptive",
    )

    assert orders
    assert all(order.instrument_id == "BTC-PERP" for order in orders)
    assert all(order.client_order_id.startswith("BTC-PERP:") for order in orders)


def test_fixed_grid_rejects_snapshot_from_another_instrument() -> None:
    config = FixedLongGridConfig(
        levels=2,
        spacing_bps=10,
        order_quantity=Decimal("0.01"),
        tick_size=Decimal("0.01"),
        instrument_id="BTC-PERP",
    )

    with pytest.raises(ValueError, match="fixed-grid snapshot/config instrument mismatch"):
        build_fixed_long_grid(_snapshot("ETH-PERP"), config, generation=0)


def test_adaptive_state_is_bound_to_snapshot_instrument() -> None:
    ladder = AdaptiveLadderConfig(
        levels=2,
        spacing_bps=10,
        order_quantity=Decimal("0.01"),
        tick_size=Decimal("0.01"),
        max_abs_inventory=Decimal("1"),
        instrument_id="BTC-PERP",
    )
    config = AdaptiveGridPolicyConfig(
        center=DynamicCenterConfig(Decimal("10"), Decimal("20")),
        spacing=VolatilitySpacingConfig(
            min_spacing_bps=Decimal("5"),
            max_spacing_bps=Decimal("100"),
            volatility_multiplier=Decimal("1"),
            execution_cost_floor_bps=Decimal("5"),
        ),
        ladder=ladder,
        inventory=InventoryTargetConfig(
            base_long_target=Decimal("0.5"),
            max_abs_target=Decimal("1"),
            reservation_skew_bps=Decimal("10"),
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
    )
    signals = AdaptiveSignals(
        trend_score=Decimal("0"),
        funding_rate=Decimal("0"),
        order_book_imbalance=Decimal("0"),
        microprice=Decimal("100"),
    )

    state, orders = initialize_adaptive_grid(_snapshot("BTC-PERP"), signals, config)

    assert state.instrument_id == "BTC-PERP"
    assert all(order.instrument_id == "BTC-PERP" for order in orders)


def test_economic_signature_distinguishes_instrument_identity() -> None:
    btc = build_adaptive_ladder(
        reference=Decimal("100"),
        position=Decimal("0"),
        target=Decimal("0.5"),
        bid_scale=Decimal("1"),
        ask_scale=Decimal("1"),
        config=AdaptiveLadderConfig(
            1,
            10,
            Decimal("0.01"),
            Decimal("0.01"),
            Decimal("1"),
            instrument_id="BTC-PERP",
        ),
        generation=0,
        stage="adaptive",
    )
    eth = build_adaptive_ladder(
        reference=Decimal("100"),
        position=Decimal("0"),
        target=Decimal("0.5"),
        bid_scale=Decimal("1"),
        ask_scale=Decimal("1"),
        config=AdaptiveLadderConfig(
            1,
            10,
            Decimal("0.01"),
            Decimal("0.01"),
            Decimal("1"),
            instrument_id="ETH-PERP",
        ),
        generation=0,
        stage="adaptive",
    )

    assert ladder_economic_signature(btc) != ladder_economic_signature(eth)
