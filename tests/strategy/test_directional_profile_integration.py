from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

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
from grid_trade.strategy.grid_geometry import ladder_economic_signature
from grid_trade.strategy.inventory_target import InventoryTargetConfig
from grid_trade.strategy.order_book_reference import OrderBookReferenceConfig
from grid_trade.strategy.target_profile import DirectionalTargetProfileConfig
from grid_trade.strategy.volatility_spacing import VolatilitySpacingConfig

_NOW = datetime(2026, 8, 10, tzinfo=UTC)
_FEATURES = AdaptiveFeatures(
    inventory_control=True,
    partial_derisk=True,
    conditional_reversal=True,
    funding_bias=False,
    order_book_reference=False,
)


def _snapshot(*, position: str) -> MarketSnapshot:
    return MarketSnapshot(
        timestamp=_NOW,
        best_bid=Decimal("99"),
        best_ask=Decimal("101"),
        realized_volatility=Decimal("0.001"),
        position_quantity=Decimal(position),
        source_id="fixture:directional-profile",
    )


def _signals(*, trend: str) -> AdaptiveSignals:
    return AdaptiveSignals(
        trend_score=Decimal(trend),
        funding_rate=Decimal("0"),
        order_book_imbalance=Decimal("0"),
        microprice=None,
    )


def _legacy_config() -> AdaptiveGridPolicyConfig:
    return AdaptiveGridPolicyConfig(
        center=DynamicCenterConfig(Decimal("10"), Decimal("20")),
        spacing=VolatilitySpacingConfig(
            min_spacing_bps=Decimal("5"),
            max_spacing_bps=Decimal("100"),
            volatility_multiplier=Decimal("1"),
            execution_cost_floor_bps=Decimal("5"),
        ),
        ladder=AdaptiveLadderConfig(
            levels=3,
            spacing_bps=10,
            order_quantity=Decimal("0.1"),
            tick_size=Decimal("0.01"),
            max_abs_inventory=Decimal("1"),
        ),
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
        short=ShortOverlayConfig(Decimal("-0.6"), Decimal("0.4")),
        funding=FundingBiasConfig(Decimal("1"), Decimal("1"), Decimal("0.25")),
        order_book=OrderBookReferenceConfig(Decimal("0.5"), Decimal("10")),
        stage=AdaptiveStage.S5_SHORT,
        features=_FEATURES,
    )


def _profile() -> DirectionalTargetProfileConfig:
    return DirectionalTargetProfileConfig(
        baseline_target_fraction=Decimal("0.5"),
        allow_opposite=True,
        opposite_entry_aligned_trend_threshold=Decimal("-0.6"),
        max_opposite_target_fraction=Decimal("0.4"),
    )


@pytest.mark.parametrize(
    ("trend", "position"),
    (("0", "0"), ("-0.4", "0"), ("-0.9", "0"), ("-0.9", "0.2")),
)
def test_explicit_long_profile_matches_legacy_target_pipeline(
    trend: str,
    position: str,
) -> None:
    legacy = _legacy_config()
    explicit = replace(legacy, target_profile=_profile())

    legacy_state, legacy_ladder = initialize_adaptive_grid(
        _snapshot(position=position),
        _signals(trend=trend),
        legacy,
    )
    explicit_state, explicit_ladder = initialize_adaptive_grid(
        _snapshot(position=position),
        _signals(trend=trend),
        explicit,
    )

    assert explicit_state.target == legacy_state.target
    assert explicit_state.bid_scale == legacy_state.bid_scale
    assert explicit_state.ask_scale == legacy_state.ask_scale
    assert explicit_state.reference == legacy_state.reference
    assert ladder_economic_signature(explicit_ladder) == ladder_economic_signature(legacy_ladder)
