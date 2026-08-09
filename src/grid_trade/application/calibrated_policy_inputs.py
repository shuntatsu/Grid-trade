from dataclasses import dataclass, replace
from decimal import Decimal

from grid_trade.calibration.contracts import CalibratedMarketState
from grid_trade.calibration.microstructure_engine import (
    MicrostructureCalibrationEstimate,
    MicrostructureCalibrationState,
)
from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.numeric import deterministic_decimal_context
from grid_trade.risk.sizing import InventoryCapacity
from grid_trade.strategy.adaptive_grid import AdaptiveGridPolicyConfig, AdaptiveStage
from grid_trade.strategy.adaptive_ladder import AdaptiveLadderConfig
from grid_trade.strategy.adaptive_signals import AdaptiveSignals
from grid_trade.strategy.conditional_short import ShortOverlayConfig
from grid_trade.strategy.funding_bias import FundingBiasConfig
from grid_trade.strategy.inventory_target import InventoryTargetConfig

_ZERO = Decimal(0)
_ONE = Decimal(1)
_BASIS_POINTS = Decimal(10_000)


def _require_finite_positive(value: Decimal, *, field: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"{field} must be a finite positive Decimal")


@dataclass(frozen=True, slots=True)
class CalibratedPolicyInputConfig:
    capacity_utilization_fraction: Decimal

    def __post_init__(self) -> None:
        _require_finite_positive(
            self.capacity_utilization_fraction,
            field="capacity_utilization_fraction",
        )
        if self.capacity_utilization_fraction > _ONE:
            raise ValueError("capacity_utilization_fraction must be within (0, 1]")


@dataclass(frozen=True, slots=True)
class CalibratedPolicyInputStatus:
    ready: bool
    reason: str
    stage: AdaptiveStage

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("reason must be non-empty")
        if self.ready != (self.reason == "ready"):
            raise ValueError("ready status must use the canonical ready reason")


@dataclass(frozen=True, slots=True)
class CalibratedAdaptiveInputs:
    status: CalibratedPolicyInputStatus
    capacity: InventoryCapacity
    usable_capacity: Decimal | None
    snapshot: MarketSnapshot | None
    signals: AdaptiveSignals | None
    policy_config: AdaptiveGridPolicyConfig | None

    def __post_init__(self) -> None:
        available = (
            self.usable_capacity is not None
            and self.snapshot is not None
            and self.signals is not None
            and self.policy_config is not None
        )
        if self.status.ready != available:
            raise ValueError("calibrated input availability must match status readiness")
        if self.usable_capacity is not None:
            _require_finite_positive(self.usable_capacity, field="usable_capacity")
            if self.usable_capacity > self.capacity.q_max:
                raise ValueError("usable_capacity must not exceed risk-derived q_max")


def _not_ready(
    *,
    stage: AdaptiveStage,
    reason: str,
    capacity: InventoryCapacity,
) -> CalibratedAdaptiveInputs:
    return CalibratedAdaptiveInputs(
        status=CalibratedPolicyInputStatus(ready=False, reason=reason, stage=stage),
        capacity=capacity,
        usable_capacity=None,
        snapshot=None,
        signals=None,
        policy_config=None,
    )


def _validate_provenance(
    snapshot: MarketSnapshot,
    calibrated_market: CalibratedMarketState,
    microstructure_state: MicrostructureCalibrationState,
) -> None:
    if snapshot.timestamp != calibrated_market.timestamp:
        raise ValueError("snapshot and calibrated market timestamps must match")
    if snapshot.source_id != calibrated_market.source_id:
        raise ValueError("snapshot and calibrated market source_id must match")
    if microstructure_state.last_timestamp != calibrated_market.timestamp:
        raise ValueError("microstructure and calibrated market timestamps must match")
    if microstructure_state.source_id != calibrated_market.source_id:
        raise ValueError("microstructure and calibrated market source_id must match")
    if microstructure_state.instrument_id != calibrated_market.instrument_id:
        raise ValueError("microstructure and calibrated market instrument_id must match")
    if microstructure_state.config is None:
        raise ValueError("microstructure state must have frozen config")


def _readiness_reason(
    stage: AdaptiveStage,
    calibrated_market: CalibratedMarketState,
    microstructure: MicrostructureCalibrationEstimate,
) -> str:
    if calibrated_market.volatility_scale is None:
        return "volatility_unavailable"
    if stage >= AdaptiveStage.S4_DERISK and calibrated_market.trend_score is None:
        return "trend_unavailable"
    if stage >= AdaptiveStage.S6_FUNDING and calibrated_market.funding_score is None:
        return "funding_unavailable"
    if stage >= AdaptiveStage.S7_ORDER_BOOK and not microstructure.readiness.ready:
        return "microstructure_unavailable"
    return "ready"


def _materialize_quantity_config(
    template: AdaptiveGridPolicyConfig,
    *,
    usable_capacity: Decimal,
    venue_tick_size: Decimal,
) -> tuple[AdaptiveLadderConfig, InventoryTargetConfig, ShortOverlayConfig, FundingBiasConfig]:
    with deterministic_decimal_context():
        template_capacity = template.ladder.max_abs_inventory
        _require_finite_positive(template_capacity, field="template max_abs_inventory")
        base_long_fraction = template.inventory.base_long_target / template_capacity
        order_fraction = template.ladder.order_quantity / template_capacity
        short_fraction = template.short.max_short_target / template_capacity

        if not _ZERO <= base_long_fraction <= _ONE:
            raise ValueError("template base-long fraction must be within [0, 1]")
        if not _ZERO < order_fraction <= _ONE:
            raise ValueError("template order fraction must be within (0, 1]")
        if not _ZERO <= short_fraction <= _ONE:
            raise ValueError("template short fraction must be within [0, 1]")

        ladder = replace(
            template.ladder,
            order_quantity=usable_capacity * order_fraction,
            tick_size=venue_tick_size,
            max_abs_inventory=usable_capacity,
        )
        inventory = replace(
            template.inventory,
            base_long_target=usable_capacity * base_long_fraction,
            max_abs_target=usable_capacity,
        )
        short = replace(
            template.short,
            max_short_target=usable_capacity * short_fraction,
        )
        funding = replace(
            template.funding,
            funding_scale=_ONE,
            max_abs_target=usable_capacity,
        )
    return ladder, inventory, short, funding


def compose_calibrated_adaptive_inputs(
    *,
    snapshot: MarketSnapshot,
    calibrated_market: CalibratedMarketState,
    microstructure_state: MicrostructureCalibrationState,
    microstructure: MicrostructureCalibrationEstimate,
    capacity: InventoryCapacity,
    template: AdaptiveGridPolicyConfig,
    adapter_config: CalibratedPolicyInputConfig,
    venue_tick_size: Decimal,
) -> CalibratedAdaptiveInputs:
    _validate_provenance(snapshot, calibrated_market, microstructure_state)
    _require_finite_positive(venue_tick_size, field="venue_tick_size")

    stage = template.stage
    reason = _readiness_reason(stage, calibrated_market, microstructure)
    if reason != "ready":
        return _not_ready(stage=stage, reason=reason, capacity=capacity)

    volatility = calibrated_market.volatility_scale
    if volatility is None:
        raise AssertionError("ready calibration must provide volatility")

    with deterministic_decimal_context():
        execution_floor_bps = microstructure.execution.execution_cost_floor * _BASIS_POINTS
    if execution_floor_bps > template.spacing.max_spacing_bps:
        return _not_ready(
            stage=stage,
            reason="execution_floor_exceeds_max_spacing",
            capacity=capacity,
        )

    with deterministic_decimal_context():
        usable_capacity = capacity.q_max * adapter_config.capacity_utilization_fraction
    _require_finite_positive(usable_capacity, field="usable_capacity")
    if usable_capacity > capacity.q_max:
        raise ValueError("usable_capacity must not exceed risk-derived q_max")

    ladder, inventory, short, funding = _materialize_quantity_config(
        template,
        usable_capacity=usable_capacity,
        venue_tick_size=venue_tick_size,
    )
    spacing = replace(
        template.spacing,
        execution_cost_floor_bps=execution_floor_bps,
    )

    micro_config = microstructure_state.config
    if micro_config is None:
        raise AssertionError("validated microstructure state must have config")
    with deterministic_decimal_context():
        imbalance_shift_bps = (
            volatility * micro_config.ofi_impact.score_scale_vol_units * _BASIS_POINTS
        )
    order_book = replace(
        template.order_book,
        imbalance_shift_bps=imbalance_shift_bps,
    )

    strategy_snapshot = replace(
        snapshot,
        realized_volatility=volatility,
    )
    trend_score = calibrated_market.trend_score or _ZERO
    funding_score = calibrated_market.funding_score or _ZERO

    if stage >= AdaptiveStage.S7_ORDER_BOOK:
        order_book_imbalance = microstructure.order_book_score
        if order_book_imbalance is None:
            raise AssertionError("ready S7 microstructure must provide order_book_score")
        with deterministic_decimal_context():
            microprice = strategy_snapshot.mid * (
                _ONE + microstructure.microprice_relative_displacement
            )
        if microprice <= 0:
            raise ValueError("reconstructed microprice must be positive")
    else:
        order_book_imbalance = _ZERO
        microprice = None

    signals = AdaptiveSignals(
        trend_score=trend_score,
        funding_rate=funding_score,
        order_book_imbalance=order_book_imbalance,
        microprice=microprice,
    )
    policy_config = replace(
        template,
        spacing=spacing,
        ladder=ladder,
        inventory=inventory,
        short=short,
        funding=funding,
        order_book=order_book,
    )

    return CalibratedAdaptiveInputs(
        status=CalibratedPolicyInputStatus(ready=True, reason="ready", stage=stage),
        capacity=capacity,
        usable_capacity=usable_capacity,
        snapshot=strategy_snapshot,
        signals=signals,
        policy_config=policy_config,
    )


__all__ = [
    "CalibratedAdaptiveInputs",
    "CalibratedPolicyInputConfig",
    "CalibratedPolicyInputStatus",
    "compose_calibrated_adaptive_inputs",
]
