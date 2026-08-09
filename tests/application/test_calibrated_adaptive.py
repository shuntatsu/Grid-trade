import datetime as dt
from dataclasses import replace
from decimal import Decimal

import pytest

from grid_trade.application.calibrated_adaptive import (
    CalibratedAdaptiveMetaConfig,
    CalibratedAdaptivePreparation,
    VenueGridConstraints,
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


def _status(*, ready: bool, reason: str = "ready") -> CalibrationComponentStatus:
    return CalibrationComponentStatus(ready=ready, sample_count=20 if ready else 0, reason=reason)


def _market(
    *,
    instrument: str = "AAA-PERP",
    funding_ready: bool = True,
    micro_ready: bool = True,
    order_book_score: Decimal | None = Decimal("0.3"),
) -> CalibratedMarketState:
    return CalibratedMarketState(
        timestamp=dt.datetime(2026, 8, 10, 1, tzinfo=dt.UTC),
        source_id="fixture",
        instrument_id=instrument,
        readiness=CalibrationReadiness.READY,
        volatility_scale=Decimal("0.001"),
        trend_score=Decimal("-0.2"),
        funding_score=Decimal("0.4") if funding_ready else None,
        quote_distance_scale=Decimal("0.0015") if micro_ready else None,
        execution_cost_floor=Decimal("0.0008") if micro_ready else None,
        order_book_score=order_book_score if micro_ready else None,
        estimated_microprice_displacement=Decimal("0.0005") if micro_ready else None,
        volatility_status=_status(ready=True),
        trend_status=_status(ready=True),
        funding_status=_status(ready=funding_ready, reason="unavailable"),
        microstructure_status=_status(ready=micro_ready, reason="unavailable"),
    )


def _snapshot(*, price_scale: str = "1") -> MarketSnapshot:
    scale = Decimal(price_scale)
    return MarketSnapshot(
        timestamp=dt.datetime(2026, 8, 10, 1, tzinfo=dt.UTC),
        best_bid=Decimal("99") * scale,
        best_ask=Decimal("101") * scale,
        realized_volatility=Decimal("0.9"),
        position_quantity=Decimal("0"),
        source_id="fixture",
    )


def _capacity() -> InventoryCapacity:
    return InventoryCapacity(
        q_notional=Decimal("1"),
        q_margin=Decimal("0.8"),
        q_volatility=Decimal("0.7"),
        q_venue=Decimal("0.1234"),
        q_max=Decimal("0.1234"),
        binding_constraint="venue",
    )


def _meta(*, stage: AdaptiveStage = AdaptiveStage.S7_ORDER_BOOK) -> CalibratedAdaptiveMetaConfig:
    return CalibratedAdaptiveMetaConfig(
        stage=stage,
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


def _venue(*, price_scale: str = "1") -> VenueGridConstraints:
    return VenueGridConstraints(
        tick_size=Decimal("0.01") * Decimal(price_scale),
        quantity_step=Decimal("0.01"),
    )


def _prepare(
    *,
    calibrated: CalibratedMarketState | None = None,
    meta: CalibratedAdaptiveMetaConfig | None = None,
    price_scale: str = "1",
) -> CalibratedAdaptivePreparation:
    return prepare_calibrated_adaptive_inputs(
        snapshot=_snapshot(price_scale=price_scale),
        calibrated=calibrated or _market(),
        capacity=_capacity(),
        meta=meta or _meta(),
        venue=_venue(price_scale=price_scale),
    )


def test_preparation_fails_closed_without_ready_microstructure() -> None:
    result = _prepare(calibrated=_market(micro_ready=False))
    assert result.inputs is None
    assert result.reason == "microstructure_not_ready"


def test_preparation_derives_qmax_targets_and_spacing_from_relative_inputs() -> None:
    result = _prepare()
    assert result.inputs is not None
    inputs = result.inputs

    assert inputs.effective_q_max == Decimal("0.12")
    assert inputs.policy_config.ladder.max_abs_inventory == Decimal("0.12")
    assert inputs.policy_config.inventory.base_long_target == Decimal("0.06")
    assert inputs.policy_config.short.max_short_target == Decimal("0.06")
    assert inputs.policy_config.ladder.order_quantity == Decimal("0.01")
    assert inputs.policy_config.center.reanchor_threshold_bps == Decimal("5.0000")
    assert inputs.policy_config.center.max_step_bps == Decimal("10.000")
    assert inputs.policy_config.spacing.min_spacing_bps == Decimal("15.0000")
    assert inputs.policy_config.spacing.execution_cost_floor_bps == Decimal("12.00000")
    assert inputs.policy_config.spacing.max_spacing_bps == Decimal("40")
    assert inputs.policy_config.ladder.spacing_bps == 15
    assert inputs.policy_config.inventory.reservation_skew_bps == Decimal("10.000")
    assert inputs.policy_config.funding.funding_scale == Decimal("1")
    assert inputs.policy_config.order_book.imbalance_shift_bps == Decimal("10.000")
    assert inputs.snapshot.realized_volatility == Decimal("0.001")
    assert inputs.signals.funding_rate == Decimal("0.4")
    assert inputs.signals.order_book_imbalance == Decimal("0.3")
    assert inputs.signals.microprice == Decimal("100.0500")


def test_preparation_never_rounds_inventory_capacity_up() -> None:
    result = _prepare()
    assert result.inputs is not None
    assert result.inputs.effective_q_max <= _capacity().q_max


def test_symbol_identity_does_not_change_prepared_numeric_inputs() -> None:
    assert _prepare(calibrated=_market(instrument="AAA-PERP")) == _prepare(
        calibrated=_market(instrument="BBB-PERP")
    )


def test_common_price_scaling_preserves_normalized_strategy_inputs() -> None:
    base = _prepare()
    scaled = _prepare(price_scale="100")
    assert base.inputs is not None and scaled.inputs is not None
    assert base.inputs.effective_q_max == scaled.inputs.effective_q_max
    assert base.inputs.signals.trend_score == scaled.inputs.signals.trend_score
    assert base.inputs.signals.funding_rate == scaled.inputs.signals.funding_rate
    assert base.inputs.signals.order_book_imbalance == scaled.inputs.signals.order_book_imbalance
    assert base.inputs.signals.microprice is not None
    assert scaled.inputs.signals.microprice == base.inputs.signals.microprice * Decimal("100")
    assert base.inputs.policy_config.spacing == scaled.inputs.policy_config.spacing
    assert base.inputs.policy_config.inventory == scaled.inputs.policy_config.inventory
    assert scaled.inputs.snapshot.mid == base.inputs.snapshot.mid * Decimal("100")
    assert scaled.inputs.policy_config.ladder.tick_size == (
        base.inputs.policy_config.ladder.tick_size * Decimal("100")
    )


def test_s6_requires_ready_normalized_funding() -> None:
    result = _prepare(
        calibrated=_market(funding_ready=False),
        meta=_meta(stage=AdaptiveStage.S6_FUNDING),
    )
    assert result.inputs is None
    assert result.reason == "funding_not_ready"


def test_s7_requires_order_book_outputs() -> None:
    result = _prepare(calibrated=_market(order_book_score=None))
    assert result.inputs is None
    assert result.reason == "order_book_not_ready"


def test_invalid_economic_floor_above_max_spacing_fails_closed() -> None:
    market = replace(_market(), execution_cost_floor=Decimal("0.02"))
    with pytest.raises(ValueError, match="economic spacing floor"):
        _prepare(calibrated=market)
