import datetime as dt
from decimal import Decimal

import pytest

from grid_trade.application.calibrated_adaptive import (
    CalibratedAdaptiveInputs,
    CalibratedAdaptiveMetaConfig,
    VenueGridConstraints,
    initialize_calibrated_adaptive_grid,
    prepare_calibrated_adaptive_inputs,
)
from grid_trade.calibration.contracts import (
    CalibratedMarketState,
    CalibrationComponentStatus,
    CalibrationReadiness,
)
from grid_trade.domain.instrument import ContractType, InstrumentSpec
from grid_trade.domain.market import MarketSnapshot
from grid_trade.risk.sizing import InventoryCapacity
from grid_trade.strategy.adaptive_grid import AdaptiveStage
from grid_trade.strategy.adaptive_signals import AdaptiveSignals
from grid_trade.strategy.features import AdaptiveFeatures

_NOW = dt.datetime(2026, 8, 10, tzinfo=dt.UTC)


def _status(ready: bool, reason: str = "ready") -> CalibrationComponentStatus:
    return CalibrationComponentStatus(ready=ready, sample_count=20 if ready else 0, reason=reason)


def _instrument(instrument_id: str = "BTC-PERP", *, min_notional: str = "1") -> InstrumentSpec:
    return InstrumentSpec(
        instrument_id=instrument_id,
        contract_type=ContractType.LINEAR_PERPETUAL,
        contract_multiplier=Decimal("1"),
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
        min_notional=Decimal(min_notional),
        max_quantity=Decimal("10"),
        funding_interval_seconds=3_600,
    )


def _market(instrument_id: str = "BTC-PERP") -> CalibratedMarketState:
    return CalibratedMarketState(
        timestamp=_NOW,
        source_id="fixture:instrument-contract",
        instrument_id=instrument_id,
        readiness=CalibrationReadiness.READY,
        volatility_scale=Decimal("0.001"),
        trend_score=Decimal("0"),
        funding_score=Decimal("0"),
        quote_distance_scale=Decimal("0.0015"),
        execution_cost_floor=Decimal("0.0008"),
        order_book_score=Decimal("0"),
        estimated_microprice_displacement=Decimal("0"),
        volatility_status=_status(True),
        trend_status=_status(True),
        funding_status=_status(True),
        microstructure_status=_status(True),
    )


def _snapshot(instrument_id: str = "BTC-PERP") -> MarketSnapshot:
    return MarketSnapshot(
        timestamp=_NOW,
        best_bid=Decimal("99"),
        best_ask=Decimal("101"),
        realized_volatility=Decimal("0"),
        position_quantity=Decimal("0"),
        source_id="fixture:instrument-contract",
        instrument_id=instrument_id,
    )


def _capacity(q_max: str = "0.2", *, q_venue: str = "10") -> InventoryCapacity:
    value = Decimal(q_max)
    venue = Decimal(q_venue)
    binding_constraint = "venue" if value == venue else "notional"
    return InventoryCapacity(
        q_notional=value if binding_constraint == "notional" else Decimal("10"),
        q_margin=Decimal("10"),
        q_volatility=Decimal("10"),
        q_venue=venue,
        q_max=value,
        binding_constraint=binding_constraint,
    )


def _meta() -> CalibratedAdaptiveMetaConfig:
    return CalibratedAdaptiveMetaConfig(
        stage=AdaptiveStage.S3_INVENTORY,
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


def _features() -> AdaptiveFeatures:
    return AdaptiveFeatures(
        inventory_control=True,
        partial_derisk=False,
        conditional_reversal=False,
        funding_bias=False,
        order_book_reference=False,
    )


def test_venue_constraints_can_be_derived_from_instrument() -> None:
    spec = _instrument()

    assert VenueGridConstraints.from_instrument(spec) == VenueGridConstraints(
        tick_size=spec.tick_size,
        quantity_step=spec.quantity_step,
    )


def test_explicit_instrument_rejects_snapshot_mismatch() -> None:
    spec = _instrument()

    with pytest.raises(ValueError, match="instrument mismatch"):
        prepare_calibrated_adaptive_inputs(
            snapshot=_snapshot("ETH-PERP"),
            calibrated=_market("BTC-PERP"),
            capacity=_capacity(),
            meta=_meta(),
            venue=VenueGridConstraints.from_instrument(spec),
            features=_features(),
            instrument=spec,
        )


def test_minimum_notional_makes_tiny_capacity_not_executable() -> None:
    spec = _instrument(min_notional="5")

    result = prepare_calibrated_adaptive_inputs(
        snapshot=_snapshot(),
        calibrated=_market(),
        capacity=_capacity("0.02"),
        meta=_meta(),
        venue=VenueGridConstraints.from_instrument(spec),
        features=_features(),
        instrument=spec,
    )

    assert result.inputs is None
    assert result.reason == "inventory_capacity_not_executable"


def test_explicit_instrument_binds_and_validates_candidate_orders() -> None:
    spec = _instrument()
    result = prepare_calibrated_adaptive_inputs(
        snapshot=_snapshot(),
        calibrated=_market(),
        capacity=_capacity(),
        meta=_meta(),
        venue=VenueGridConstraints.from_instrument(spec),
        features=_features(),
        instrument=spec,
    )

    assert result.inputs is not None
    _, orders = initialize_calibrated_adaptive_grid(result.inputs)
    assert orders
    assert all(order.instrument_id == spec.instrument_id for order in orders)
    assert all(spec.is_executable(order.quantity, order.price) for order in orders)


def test_calibrated_inputs_reject_snapshot_and_policy_instrument_mismatch() -> None:
    prepared = prepare_calibrated_adaptive_inputs(
        snapshot=_snapshot(),
        calibrated=_market(),
        capacity=_capacity(),
        meta=_meta(),
        venue=VenueGridConstraints.from_instrument(_instrument()),
        features=_features(),
        instrument=_instrument(),
    )
    assert prepared.inputs is not None

    with pytest.raises(ValueError, match="calibrated inputs instrument mismatch"):
        CalibratedAdaptiveInputs(
            snapshot=_snapshot("ETH-PERP"),
            signals=AdaptiveSignals(
                trend_score=Decimal("0"),
                funding_rate=Decimal("0"),
                order_book_imbalance=Decimal("0"),
                microprice=None,
            ),
            policy_config=prepared.inputs.policy_config,
            effective_q_max=prepared.inputs.effective_q_max,
        )
