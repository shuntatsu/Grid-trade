from dataclasses import dataclass, replace
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from grid_trade.application.passive_policy import (
    PassivePolicyTransition,
    continue_passive_policy_reconciliation,
    transition_passive_policy,
)
from grid_trade.calibration.contracts import CalibratedMarketState
from grid_trade.domain.instrument import (
    LEGACY_UNSPECIFIED_INSTRUMENT,
    InstrumentSpec,
    require_explicit_instrument,
    require_instruments_compatible,
)
from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.numeric import deterministic_decimal_context
from grid_trade.domain.orders import PassiveOrderIntent, WorkingOrder
from grid_trade.domain.risk import RiskLimits, RiskState
from grid_trade.risk.sizing import InventoryCapacity
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
from grid_trade.strategy.features import AdaptiveFeatures
from grid_trade.strategy.funding_bias import FundingBiasConfig
from grid_trade.strategy.inventory_target import InventoryTargetConfig
from grid_trade.strategy.order_book_reference import OrderBookReferenceConfig
from grid_trade.strategy.target_profile import DirectionalTargetProfileConfig
from grid_trade.strategy.volatility_spacing import VolatilitySpacingConfig

_BASIS_POINTS = Decimal(10_000)
_ZERO = Decimal(0)
_ONE = Decimal(1)


def _require_finite(value: Decimal, *, field: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{field} must be a finite Decimal")


def _require_positive(value: Decimal, *, field: str) -> None:
    _require_finite(value, field=field)
    if value <= 0:
        raise ValueError(f"{field} must be positive")


def _require_fraction(value: Decimal, *, field: str, allow_zero: bool = True) -> None:
    _require_finite(value, field=field)
    lower_ok = value >= _ZERO if allow_zero else value > _ZERO
    if not lower_ok or value > _ONE:
        interval = "[0, 1]" if allow_zero else "(0, 1]"
        raise ValueError(f"{field} must be within {interval}")


def _floor_quantity(value: Decimal, step: Decimal) -> Decimal:
    with deterministic_decimal_context():
        return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


@dataclass(frozen=True, slots=True)
class VenueGridConstraints:
    tick_size: Decimal
    quantity_step: Decimal

    def __post_init__(self) -> None:
        _require_positive(self.tick_size, field="tick_size")
        _require_positive(self.quantity_step, field="quantity_step")

    @classmethod
    def from_instrument(cls, instrument: InstrumentSpec) -> "VenueGridConstraints":
        return cls(
            tick_size=instrument.tick_size,
            quantity_step=instrument.quantity_step,
        )

    def require_matches(self, instrument: InstrumentSpec) -> None:
        if self.tick_size != instrument.tick_size:
            raise ValueError("venue tick_size must match InstrumentSpec")
        if self.quantity_step != instrument.quantity_step:
            raise ValueError("venue quantity_step must match InstrumentSpec")


@dataclass(frozen=True, slots=True)
class CalibratedAdaptiveMetaConfig:
    stage: AdaptiveStage
    levels: int
    base_long_fraction: Decimal
    level_quantity_fraction: Decimal
    max_short_fraction: Decimal
    center_reanchor_vol_units: Decimal
    center_max_step_vol_units: Decimal
    min_spacing_vol_units: Decimal
    max_spacing_vol_units: Decimal
    spacing_volatility_multiplier: Decimal
    intensity_spacing_multiplier: Decimal
    execution_cost_multiplier: Decimal
    reservation_skew_vol_units: Decimal
    side_skew_strength: Decimal
    warning_trend_threshold: Decimal
    severe_trend_threshold: Decimal
    warning_target_fraction: Decimal
    severe_target_fraction: Decimal
    short_entry_trend_threshold: Decimal
    funding_max_target_shift_fraction: Decimal
    order_book_microprice_weight: Decimal
    order_book_shift_vol_units: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.stage, AdaptiveStage):
            raise ValueError("stage must be an AdaptiveStage")
        if not 1 <= self.levels <= 50:
            raise ValueError("levels must be within [1, 50]")
        _require_fraction(self.base_long_fraction, field="base_long_fraction")
        _require_fraction(
            self.level_quantity_fraction,
            field="level_quantity_fraction",
            allow_zero=False,
        )
        _require_fraction(
            self.max_short_fraction,
            field="max_short_fraction",
            allow_zero=False,
        )
        for field_name in (
            "center_reanchor_vol_units",
            "center_max_step_vol_units",
            "min_spacing_vol_units",
            "max_spacing_vol_units",
            "spacing_volatility_multiplier",
            "intensity_spacing_multiplier",
            "execution_cost_multiplier",
            "reservation_skew_vol_units",
            "order_book_shift_vol_units",
        ):
            _require_positive(getattr(self, field_name), field=field_name)
        if self.max_spacing_vol_units < self.min_spacing_vol_units:
            raise ValueError("max_spacing_vol_units must be at least min_spacing_vol_units")
        _require_fraction(self.side_skew_strength, field="side_skew_strength")
        _require_fraction(self.warning_target_fraction, field="warning_target_fraction")
        _require_fraction(self.severe_target_fraction, field="severe_target_fraction")
        _require_fraction(
            self.funding_max_target_shift_fraction,
            field="funding_max_target_shift_fraction",
        )
        _require_fraction(
            self.order_book_microprice_weight,
            field="order_book_microprice_weight",
        )
        for field_name in (
            "warning_trend_threshold",
            "severe_trend_threshold",
            "short_entry_trend_threshold",
        ):
            _require_finite(getattr(self, field_name), field=field_name)


@dataclass(frozen=True, slots=True)
class CalibratedAdaptiveInputs:
    snapshot: MarketSnapshot
    signals: AdaptiveSignals
    policy_config: AdaptiveGridPolicyConfig
    effective_q_max: Decimal

    def __post_init__(self) -> None:
        _require_positive(self.effective_q_max, field="effective_q_max")
        if self.policy_config.ladder.max_abs_inventory != self.effective_q_max:
            raise ValueError("policy inventory cap must equal effective_q_max")


@dataclass(frozen=True, slots=True)
class CalibratedAdaptivePreparation:
    inputs: CalibratedAdaptiveInputs | None
    reason: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("reason must be non-empty")
        if (self.inputs is not None) != (self.reason == "ready"):
            raise ValueError("ready reason must match prepared input availability")


@dataclass(frozen=True, slots=True)
class CalibratedAdaptiveState:
    policy_state: AdaptiveGridState
    applied_config: AdaptiveGridPolicyConfig


CalibratedAdaptiveTransition = PassivePolicyTransition[
    CalibratedAdaptiveState, AdaptiveGridDecision
]


def _require_matching_market_context(
    snapshot: MarketSnapshot,
    calibrated: CalibratedMarketState,
    instrument: InstrumentSpec | None,
) -> None:
    if snapshot.timestamp != calibrated.timestamp:
        raise ValueError("snapshot and calibrated timestamp must match")
    if snapshot.source_id != calibrated.source_id:
        raise ValueError("snapshot and calibrated source_id must match")
    if snapshot.instrument_id != LEGACY_UNSPECIFIED_INSTRUMENT:
        require_instruments_compatible(
            snapshot.instrument_id,
            calibrated.instrument_id,
            context="snapshot/calibration",
        )
    if instrument is not None:
        require_explicit_instrument(snapshot.instrument_id, context="calibrated strategy")
        require_instruments_compatible(
            snapshot.instrument_id,
            instrument.instrument_id,
            context="snapshot/spec",
        )
        require_instruments_compatible(
            calibrated.instrument_id,
            instrument.instrument_id,
            context="calibration/spec",
        )


def _readiness_reason(
    calibrated: CalibratedMarketState,
    features: AdaptiveFeatures,
) -> str | None:
    if (
        not calibrated.volatility_status.ready
        or not calibrated.trend_status.ready
        or calibrated.volatility_scale is None
        or calibrated.trend_score is None
    ):
        return "foundation_not_ready"
    if calibrated.volatility_scale <= 0:
        return "volatility_degenerate"
    if (
        not calibrated.microstructure_status.ready
        or calibrated.quote_distance_scale is None
        or calibrated.execution_cost_floor is None
    ):
        return "microstructure_not_ready"
    if features.funding_bias and (
        not calibrated.funding_status.ready or calibrated.funding_score is None
    ):
        return "funding_not_ready"
    if features.order_book_reference and (
        calibrated.order_book_score is None or calibrated.estimated_microprice_displacement is None
    ):
        return "order_book_not_ready"
    return None


def prepare_calibrated_adaptive_inputs(
    *,
    snapshot: MarketSnapshot,
    calibrated: CalibratedMarketState,
    capacity: InventoryCapacity,
    meta: CalibratedAdaptiveMetaConfig,
    venue: VenueGridConstraints,
    features: AdaptiveFeatures | None = None,
    target_profile: DirectionalTargetProfileConfig | None = None,
    instrument: InstrumentSpec | None = None,
) -> CalibratedAdaptivePreparation:
    _require_matching_market_context(snapshot, calibrated, instrument)
    if instrument is not None:
        venue.require_matches(instrument)
        if capacity.q_venue > instrument.max_quantity:
            raise ValueError("capacity venue quantity must not exceed InstrumentSpec max_quantity")
    active_features = features or AdaptiveFeatures.from_stage(meta.stage)
    unavailable_reason = _readiness_reason(calibrated, active_features)
    if unavailable_reason is not None:
        return CalibratedAdaptivePreparation(inputs=None, reason=unavailable_reason)

    volatility = calibrated.volatility_scale
    trend = calibrated.trend_score
    quote_distance = calibrated.quote_distance_scale
    execution_cost = calibrated.execution_cost_floor
    if volatility is None or trend is None or quote_distance is None or execution_cost is None:
        raise AssertionError("readiness validation must guarantee calibrated values")

    with deterministic_decimal_context():
        effective_q_max = (
            instrument.floor_quantity(capacity.q_max)
            if instrument is not None
            else _floor_quantity(capacity.q_max, venue.quantity_step)
        )
        if effective_q_max <= 0:
            return CalibratedAdaptivePreparation(
                inputs=None,
                reason="inventory_capacity_not_executable",
            )

        floor_quantity = (
            instrument.floor_quantity
            if instrument is not None
            else lambda value: _floor_quantity(value, venue.quantity_step)
        )
        base_long_target = floor_quantity(effective_q_max * meta.base_long_fraction)
        max_short_target = floor_quantity(effective_q_max * meta.max_short_fraction)
        order_quantity = floor_quantity(effective_q_max * meta.level_quantity_fraction)
        if max_short_target <= 0 or order_quantity <= 0:
            return CalibratedAdaptivePreparation(
                inputs=None,
                reason="inventory_capacity_not_executable",
            )
        if instrument is not None and not instrument.is_executable(order_quantity, snapshot.mid):
            return CalibratedAdaptivePreparation(
                inputs=None,
                reason="inventory_capacity_not_executable",
            )

        center_reanchor_bps = volatility * meta.center_reanchor_vol_units * _BASIS_POINTS
        center_max_step_bps = volatility * meta.center_max_step_vol_units * _BASIS_POINTS
        volatility_min_relative = volatility * meta.min_spacing_vol_units
        intensity_relative = quote_distance * meta.intensity_spacing_multiplier
        min_spacing_bps = max(volatility_min_relative, intensity_relative) * _BASIS_POINTS
        execution_floor_bps = execution_cost * meta.execution_cost_multiplier * _BASIS_POINTS
        volatility_spacing_bps = volatility * meta.spacing_volatility_multiplier * _BASIS_POINTS
        max_spacing_bps = (
            volatility * meta.max_spacing_vol_units * _BASIS_POINTS
        ).to_integral_value(rounding=ROUND_FLOOR)
        economic_floor_bps = max(
            min_spacing_bps,
            execution_floor_bps,
            volatility_spacing_bps,
        )
        if max_spacing_bps <= 0 or economic_floor_bps > max_spacing_bps:
            raise ValueError("economic spacing floor must not exceed max spacing")
        initial_spacing_bps = int(economic_floor_bps.to_integral_value(rounding=ROUND_CEILING))
        reservation_skew_bps = volatility * meta.reservation_skew_vol_units * _BASIS_POINTS
        order_book_shift_bps = volatility * meta.order_book_shift_vol_units * _BASIS_POINTS

        funding_signal = calibrated.funding_score if active_features.funding_bias else _ZERO
        order_book_signal = (
            calibrated.order_book_score if active_features.order_book_reference else _ZERO
        )
        microprice: Decimal | None = None
        if active_features.order_book_reference:
            displacement = calibrated.estimated_microprice_displacement
            if displacement is None or order_book_signal is None:
                raise AssertionError("S7 readiness must guarantee order-book outputs")
            microprice = snapshot.mid * (_ONE + displacement)
            if microprice <= 0:
                raise ValueError("calibrated microprice must remain positive")
        if funding_signal is None or order_book_signal is None:
            raise AssertionError("stage readiness must guarantee normalized signals")

    calibrated_snapshot = replace(snapshot, realized_volatility=volatility)
    signals = AdaptiveSignals(
        trend_score=trend,
        funding_rate=funding_signal,
        order_book_imbalance=order_book_signal,
        microprice=microprice,
    )
    policy_config = AdaptiveGridPolicyConfig(
        center=DynamicCenterConfig(
            reanchor_threshold_bps=center_reanchor_bps,
            max_step_bps=center_max_step_bps,
        ),
        spacing=VolatilitySpacingConfig(
            min_spacing_bps=min_spacing_bps,
            max_spacing_bps=max_spacing_bps,
            volatility_multiplier=meta.spacing_volatility_multiplier,
            execution_cost_floor_bps=execution_floor_bps,
        ),
        ladder=AdaptiveLadderConfig(
            levels=meta.levels,
            spacing_bps=initial_spacing_bps,
            order_quantity=order_quantity,
            tick_size=venue.tick_size,
            max_abs_inventory=effective_q_max,
            instrument_id=snapshot.instrument_id,
            instrument=instrument,
        ),
        inventory=InventoryTargetConfig(
            base_long_target=base_long_target,
            max_abs_target=effective_q_max,
            reservation_skew_bps=reservation_skew_bps,
            side_skew_strength=meta.side_skew_strength,
        ),
        de_risk=DeRiskConfig(
            warning_trend_threshold=meta.warning_trend_threshold,
            severe_trend_threshold=meta.severe_trend_threshold,
            warning_target_fraction=meta.warning_target_fraction,
            severe_target_fraction=meta.severe_target_fraction,
        ),
        short=ShortOverlayConfig(
            entry_trend_threshold=meta.short_entry_trend_threshold,
            max_short_target=max_short_target,
        ),
        funding=FundingBiasConfig(
            funding_scale=_ONE,
            max_abs_target=effective_q_max,
            max_target_shift_fraction=meta.funding_max_target_shift_fraction,
        ),
        order_book=OrderBookReferenceConfig(
            microprice_weight=meta.order_book_microprice_weight,
            imbalance_shift_bps=order_book_shift_bps,
        ),
        stage=meta.stage,
        features=active_features,
        target_profile=target_profile,
    )
    return CalibratedAdaptivePreparation(
        inputs=CalibratedAdaptiveInputs(
            snapshot=calibrated_snapshot,
            signals=signals,
            policy_config=policy_config,
            effective_q_max=effective_q_max,
        ),
        reason="ready",
    )


def initialize_calibrated_adaptive_grid(
    inputs: CalibratedAdaptiveInputs,
) -> tuple[CalibratedAdaptiveState, tuple[PassiveOrderIntent, ...]]:
    policy_state, ladder = initialize_adaptive_grid(
        inputs.snapshot,
        inputs.signals,
        inputs.policy_config,
    )
    return (
        CalibratedAdaptiveState(
            policy_state=policy_state,
            applied_config=inputs.policy_config,
        ),
        ladder,
    )


def transition_calibrated_adaptive_grid(
    *,
    inputs: CalibratedAdaptiveInputs,
    state: CalibratedAdaptiveState,
    risk_limits: RiskLimits,
    risk_state: RiskState,
    working_orders: tuple[WorkingOrder, ...],
) -> CalibratedAdaptiveTransition:
    decision, candidate_policy_state, proposed_ladder = decide_adaptive_grid(
        inputs.snapshot,
        inputs.signals,
        state.policy_state,
        inputs.policy_config,
        previous_config=state.applied_config,
    )
    candidate_config = (
        inputs.policy_config if decision.economic_ladder_changed else state.applied_config
    )
    candidate_state = CalibratedAdaptiveState(
        policy_state=candidate_policy_state,
        applied_config=candidate_config,
    )
    return transition_passive_policy(
        decision=decision,
        previous_state=state,
        candidate_state=candidate_state,
        snapshot=inputs.snapshot,
        risk_limits=risk_limits,
        risk_state=risk_state,
        working_orders=working_orders,
        proposed_ladder=proposed_ladder,
    )


def continue_calibrated_adaptive_reconciliation(
    transition: CalibratedAdaptiveTransition,
    *,
    snapshot: MarketSnapshot,
    risk_limits: RiskLimits,
    risk_state: RiskState,
    working_orders: tuple[WorkingOrder, ...],
) -> CalibratedAdaptiveTransition:
    return continue_passive_policy_reconciliation(
        transition,
        snapshot=snapshot,
        risk_limits=risk_limits,
        risk_state=risk_state,
        working_orders=working_orders,
    )


__all__ = [
    "CalibratedAdaptiveInputs",
    "CalibratedAdaptiveMetaConfig",
    "CalibratedAdaptivePreparation",
    "CalibratedAdaptiveState",
    "CalibratedAdaptiveTransition",
    "VenueGridConstraints",
    "continue_calibrated_adaptive_reconciliation",
    "initialize_calibrated_adaptive_grid",
    "prepare_calibrated_adaptive_inputs",
    "transition_calibrated_adaptive_grid",
]
