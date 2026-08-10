import datetime as dt
from decimal import Decimal

from grid_trade.application.calibrated_adaptive import (
    CalibratedAdaptiveMetaConfig,
    VenueGridConstraints,
    initialize_calibrated_adaptive_grid,
    prepare_calibrated_adaptive_inputs,
)
from grid_trade.calibration import (
    CalibratedMarketState,
    CalibrationComponentStatus,
    CalibrationReadiness,
)
from grid_trade.domain.market import MarketSnapshot
from grid_trade.risk.sizing import InventoryCapacity
from grid_trade.strategy.adaptive_grid import AdaptiveStage
from grid_trade.strategy.features import AdaptiveFeatures
from grid_trade.strategy.target_profile import DirectionalTargetProfileConfig


def _status(ready: bool, reason: str = "ready") -> CalibrationComponentStatus:
    return CalibrationComponentStatus(ready=ready, sample_count=20 if ready else 0, reason=reason)


def _market() -> CalibratedMarketState:
    return CalibratedMarketState(
        timestamp=dt.datetime(2026, 8, 10, 1, tzinfo=dt.UTC),
        source_id="fixture",
        instrument_id="AAA-PERP",
        readiness=CalibrationReadiness.READY,
        volatility_scale=Decimal("0.001"),
        trend_score=Decimal("-0.2"),
        funding_score=None,
        quote_distance_scale=Decimal("0.0015"),
        execution_cost_floor=Decimal("0.0008"),
        order_book_score=Decimal("0.3"),
        estimated_microprice_displacement=Decimal("0.0005"),
        volatility_status=_status(True),
        trend_status=_status(True),
        funding_status=_status(False, "unavailable"),
        microstructure_status=_status(True),
    )


def _snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        timestamp=dt.datetime(2026, 8, 10, 1, tzinfo=dt.UTC),
        best_bid=Decimal("99"),
        best_ask=Decimal("101"),
        realized_volatility=Decimal("0"),
        position_quantity=Decimal("0"),
        source_id="fixture",
    )


def _capacity() -> InventoryCapacity:
    return InventoryCapacity(
        q_notional=Decimal("1"),
        q_margin=Decimal("0.8"),
        q_volatility=Decimal("0.7"),
        q_venue=Decimal("0.12"),
        q_max=Decimal("0.12"),
        binding_constraint="venue",
    )


def _meta() -> CalibratedAdaptiveMetaConfig:
    return CalibratedAdaptiveMetaConfig(
        stage=AdaptiveStage.S7_ORDER_BOOK,
        levels=3,
        base_long_fraction=Decimal("0.5"),
        level_quantity_fraction=Decimal("0.1"),
        max_short_fraction=Decimal("0.5"),
        center_reanchor_vol_units=Decimal("0.5"),
        center_max_step_vol_units=Decimal("1"),
        min_spacing_vol_units=Decimal("0.5"),
        max_spacing_vol_units=Decimal("4"),
        spacing_volatility_multiplier=Decimal("1"),
        intensity_spacing_multiplier=Decimal("1"),
        execution_cost_multiplier=Decimal("1.5"),
        reservation_skew_vol_units=Decimal("1"),
        side_skew_strength=Decimal("0.5"),
        warning_trend_threshold=Decimal("-0.25"),
        severe_trend_threshold=Decimal("-0.6"),
        warning_target_fraction=Decimal("0.5"),
        severe_target_fraction=Decimal("0"),
        short_entry_trend_threshold=Decimal("-0.6"),
        funding_max_target_shift_fraction=Decimal("0.25"),
        order_book_microprice_weight=Decimal("0.5"),
        order_book_shift_vol_units=Decimal("1"),
    )


def test_readiness_depends_on_active_features_not_stage_ordinal() -> None:
    features = AdaptiveFeatures(
        inventory_control=True,
        partial_derisk=True,
        conditional_reversal=True,
        funding_bias=False,
        order_book_reference=True,
    )

    result = prepare_calibrated_adaptive_inputs(
        snapshot=_snapshot(),
        calibrated=_market(),
        capacity=_capacity(),
        meta=_meta(),
        venue=VenueGridConstraints(Decimal("0.01"), Decimal("0.01")),
        features=features,
    )

    assert result.inputs is not None
    assert result.inputs.policy_config.active_features == features
    assert result.inputs.signals.funding_rate == Decimal("0")


def test_calibrated_preparation_accepts_short_biased_profile() -> None:
    features = AdaptiveFeatures(
        inventory_control=True,
        partial_derisk=False,
        conditional_reversal=False,
        funding_bias=False,
        order_book_reference=False,
    )
    profile = DirectionalTargetProfileConfig(
        baseline_target_fraction=Decimal("-0.5"),
        allow_opposite=False,
        opposite_entry_aligned_trend_threshold=Decimal("-0.6"),
        max_opposite_target_fraction=Decimal("0"),
    )

    result = prepare_calibrated_adaptive_inputs(
        snapshot=_snapshot(),
        calibrated=_market(),
        capacity=_capacity(),
        meta=_meta(),
        venue=VenueGridConstraints(Decimal("0.01"), Decimal("0.01")),
        features=features,
        target_profile=profile,
    )

    assert result.inputs is not None
    state, ladder = initialize_calibrated_adaptive_grid(result.inputs)
    assert state.policy_state.target == Decimal("-0.060")
    assert ladder
    assert all(order.side.value == "sell" and not order.reduce_only for order in ladder)
