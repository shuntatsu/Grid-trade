from dataclasses import dataclass, replace
from decimal import Decimal
from enum import IntEnum

from grid_trade.domain.instrument import (
    LEGACY_UNSPECIFIED_INSTRUMENT,
    require_instruments_compatible,
)
from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.orders import PassiveOrderIntent
from grid_trade.strategy.adaptive_ladder import AdaptiveLadderConfig, build_adaptive_ladder
from grid_trade.strategy.adaptive_signals import AdaptiveSignals
from grid_trade.strategy.conditional_short import (
    ShortOverlayConfig,
    ShortOverlayDecision,
    apply_conditional_short,
)
from grid_trade.strategy.de_risk import DeRiskConfig, DeRiskDecision, apply_partial_de_risk
from grid_trade.strategy.dynamic_center import (
    CenterProposal,
    DynamicCenterConfig,
    DynamicCenterState,
    propose_dynamic_center,
)
from grid_trade.strategy.funding_bias import (
    FundingBiasConfig,
    FundingBiasDecision,
    apply_funding_bias,
)
from grid_trade.strategy.grid_geometry import ladder_economic_signature
from grid_trade.strategy.inventory_target import (
    InventoryTargetConfig,
    InventoryTargetDecision,
    decide_inventory_target,
)
from grid_trade.strategy.order_book_reference import (
    OrderBookReferenceConfig,
    OrderBookReferenceDecision,
    decide_order_book_reference,
)
from grid_trade.strategy.volatility_spacing import (
    SpacingDecision,
    VolatilitySpacingConfig,
    propose_volatility_spacing,
)

_BASIS_POINTS = Decimal(10_000)


def _require_finite_positive(value: Decimal, *, field: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"{field} must be a finite positive Decimal")


def _require_finite(value: Decimal, *, field: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{field} must be a finite Decimal")


class AdaptiveStage(IntEnum):
    S3_INVENTORY = 3
    S4_DERISK = 4
    S5_SHORT = 5
    S6_FUNDING = 6
    S7_ORDER_BOOK = 7


@dataclass(frozen=True, slots=True)
class AdaptiveGridPolicyConfig:
    center: DynamicCenterConfig
    spacing: VolatilitySpacingConfig
    ladder: AdaptiveLadderConfig
    inventory: InventoryTargetConfig
    de_risk: DeRiskConfig
    short: ShortOverlayConfig
    funding: FundingBiasConfig
    order_book: OrderBookReferenceConfig
    stage: AdaptiveStage = AdaptiveStage.S7_ORDER_BOOK

    def __post_init__(self) -> None:
        if self.inventory.max_abs_target != self.ladder.max_abs_inventory:
            raise ValueError("inventory target and ladder inventory caps must match")
        if self.funding.max_abs_target != self.ladder.max_abs_inventory:
            raise ValueError("funding and ladder inventory caps must match")
        if self.short.max_short_target > self.ladder.max_abs_inventory:
            raise ValueError("short target must not exceed ladder inventory cap")


@dataclass(frozen=True, slots=True)
class AdaptiveGridState:
    center: Decimal
    reference: Decimal
    spacing_bps: int
    target: Decimal
    bid_scale: Decimal
    ask_scale: Decimal
    position_basis: Decimal
    generation: int
    instrument_id: str = LEGACY_UNSPECIFIED_INSTRUMENT

    def __post_init__(self) -> None:
        _require_finite_positive(self.center, field="center")
        _require_finite_positive(self.reference, field="reference")
        if self.spacing_bps <= 0:
            raise ValueError("spacing_bps must be positive")
        for field_name in ("target", "bid_scale", "ask_scale", "position_basis"):
            _require_finite(getattr(self, field_name), field=field_name)
        for field_name in ("bid_scale", "ask_scale"):
            value = getattr(self, field_name)
            if not Decimal(0) <= value <= Decimal(1):
                raise ValueError(f"{field_name} must be within [0, 1]")
        if self.generation < 0:
            raise ValueError("generation must be non-negative")
        if not self.instrument_id.strip():
            raise ValueError("instrument_id must be non-empty")


@dataclass(frozen=True, slots=True)
class AdaptiveGridDecision:
    stage: AdaptiveStage
    center: CenterProposal
    spacing: SpacingDecision
    de_risk: DeRiskDecision
    short: ShortOverlayDecision
    funding: FundingBiasDecision
    inventory: InventoryTargetDecision
    order_book: OrderBookReferenceDecision
    inventory_reference: Decimal
    candidate_center: Decimal
    candidate_reference: Decimal
    candidate_target: Decimal
    previous_generation: int
    effective_generation: int
    economic_ladder_changed: bool
    de_risk_applied: bool
    short_applied: bool
    funding_applied: bool
    order_book_applied: bool

    def __post_init__(self) -> None:
        _require_finite_positive(self.inventory_reference, field="inventory_reference")
        _require_finite_positive(self.candidate_center, field="candidate_center")
        _require_finite_positive(self.candidate_reference, field="candidate_reference")
        _require_finite(self.candidate_target, field="candidate_target")
        if self.previous_generation < 0 or self.effective_generation < 0:
            raise ValueError("generations must be non-negative")
        expected = self.previous_generation + (1 if self.economic_ladder_changed else 0)
        if self.effective_generation != expected:
            raise ValueError("effective_generation must track one economic ladder change")
        if self.de_risk_applied != (self.stage >= AdaptiveStage.S4_DERISK):
            raise ValueError("de_risk_applied must match stage")
        if self.short_applied != (self.stage >= AdaptiveStage.S5_SHORT):
            raise ValueError("short_applied must match stage")
        if self.funding_applied != (self.stage >= AdaptiveStage.S6_FUNDING):
            raise ValueError("funding_applied must match stage")
        if self.order_book_applied != (self.stage >= AdaptiveStage.S7_ORDER_BOOK):
            raise ValueError("order_book_applied must match stage")


def _ladder_config_with_spacing(
    config: AdaptiveLadderConfig,
    spacing_bps: int,
) -> AdaptiveLadderConfig:
    return replace(config, spacing_bps=spacing_bps)


def _target_pipeline(
    snapshot: MarketSnapshot,
    signals: AdaptiveSignals,
    config: AdaptiveGridPolicyConfig,
) -> tuple[DeRiskDecision, ShortOverlayDecision, FundingBiasDecision, InventoryTargetDecision]:
    de_risk = apply_partial_de_risk(
        config.inventory.base_long_target,
        signals.trend_score,
        config.de_risk,
    )
    target_after_derisk = (
        de_risk.effective_target
        if config.stage >= AdaptiveStage.S4_DERISK
        else config.inventory.base_long_target
    )

    short = apply_conditional_short(
        long_target=target_after_derisk,
        position=snapshot.position_quantity,
        trend_score=signals.trend_score,
        config=config.short,
    )
    target_after_short = (
        short.effective_target if config.stage >= AdaptiveStage.S5_SHORT else target_after_derisk
    )

    funding = apply_funding_bias(
        target=target_after_short,
        position=snapshot.position_quantity,
        funding_rate=signals.funding_rate,
        config=config.funding,
    )
    target_after_funding = (
        funding.effective_target if config.stage >= AdaptiveStage.S6_FUNDING else target_after_short
    )

    inventory = decide_inventory_target(
        position=snapshot.position_quantity,
        target=target_after_funding,
        config=config.inventory,
    )
    return de_risk, short, funding, inventory


def _reference_pipeline(
    *,
    center: Decimal,
    snapshot: MarketSnapshot,
    signals: AdaptiveSignals,
    inventory: InventoryTargetDecision,
    config: AdaptiveGridPolicyConfig,
) -> tuple[Decimal, OrderBookReferenceDecision, Decimal]:
    inventory_reference = center * (Decimal(1) + inventory.reservation_shift_bps / _BASIS_POINTS)
    _require_finite_positive(inventory_reference, field="inventory_reference")
    order_book = decide_order_book_reference(
        center=inventory_reference,
        market_mid=snapshot.mid,
        signals=signals,
        config=config.order_book,
    )
    effective_reference = (
        order_book.effective_reference
        if config.stage >= AdaptiveStage.S7_ORDER_BOOK
        else inventory_reference
    )
    return inventory_reference, order_book, effective_reference


def _build_state_ladder(
    state: AdaptiveGridState,
    config: AdaptiveGridPolicyConfig,
) -> tuple[PassiveOrderIntent, ...]:
    require_instruments_compatible(
        state.instrument_id,
        config.ladder.instrument_id,
        context="adaptive state/config",
    )
    return build_adaptive_ladder(
        reference=state.reference,
        position=state.position_basis,
        target=state.target,
        bid_scale=state.bid_scale,
        ask_scale=state.ask_scale,
        config=_ladder_config_with_spacing(config.ladder, state.spacing_bps),
        generation=state.generation,
        stage="adaptive",
    )


def initialize_adaptive_grid(
    snapshot: MarketSnapshot,
    signals: AdaptiveSignals,
    config: AdaptiveGridPolicyConfig,
) -> tuple[AdaptiveGridState, tuple[PassiveOrderIntent, ...]]:
    require_instruments_compatible(
        snapshot.instrument_id,
        config.ladder.instrument_id,
        context="adaptive snapshot/config",
    )
    _, _, _, inventory = _target_pipeline(snapshot, signals, config)
    spacing = propose_volatility_spacing(
        snapshot,
        config.ladder.spacing_bps,
        config.spacing,
    )
    center = snapshot.mid
    _, _, effective_reference = _reference_pipeline(
        center=center,
        snapshot=snapshot,
        signals=signals,
        inventory=inventory,
        config=config,
    )
    state = AdaptiveGridState(
        center=center,
        reference=effective_reference,
        spacing_bps=spacing.effective_spacing_bps,
        target=inventory.target,
        bid_scale=inventory.bid_scale,
        ask_scale=inventory.ask_scale,
        position_basis=snapshot.position_quantity,
        generation=0,
        instrument_id=snapshot.instrument_id,
    )
    return state, _build_state_ladder(state, config)


def decide_adaptive_grid(
    snapshot: MarketSnapshot,
    signals: AdaptiveSignals,
    state: AdaptiveGridState,
    config: AdaptiveGridPolicyConfig,
    *,
    previous_config: AdaptiveGridPolicyConfig | None = None,
) -> tuple[AdaptiveGridDecision, AdaptiveGridState, tuple[PassiveOrderIntent, ...]]:
    require_instruments_compatible(
        snapshot.instrument_id,
        state.instrument_id,
        context="adaptive snapshot/state",
    )
    require_instruments_compatible(
        snapshot.instrument_id,
        config.ladder.instrument_id,
        context="adaptive snapshot/config",
    )
    current_ladder = _build_state_ladder(state, previous_config or config)
    center = propose_dynamic_center(
        snapshot,
        DynamicCenterState(center=state.center, generation=state.generation),
        config.center,
    )
    spacing = propose_volatility_spacing(snapshot, state.spacing_bps, config.spacing)
    de_risk, short, funding, inventory = _target_pipeline(snapshot, signals, config)
    inventory_reference, order_book, effective_reference = _reference_pipeline(
        center=center.proposed_center,
        snapshot=snapshot,
        signals=signals,
        inventory=inventory,
        config=config,
    )

    candidate_generation = state.generation + 1
    candidate_state = AdaptiveGridState(
        center=center.proposed_center,
        reference=effective_reference,
        spacing_bps=spacing.effective_spacing_bps,
        target=inventory.target,
        bid_scale=inventory.bid_scale,
        ask_scale=inventory.ask_scale,
        position_basis=snapshot.position_quantity,
        generation=candidate_generation,
        instrument_id=snapshot.instrument_id,
    )
    candidate_ladder = _build_state_ladder(candidate_state, config)
    changed = ladder_economic_signature(candidate_ladder) != ladder_economic_signature(
        current_ladder
    )

    if changed:
        effective_state = candidate_state
        effective_ladder = candidate_ladder
    else:
        effective_state = state
        effective_ladder = current_ladder

    decision = AdaptiveGridDecision(
        stage=config.stage,
        center=center,
        spacing=spacing,
        de_risk=de_risk,
        short=short,
        funding=funding,
        inventory=inventory,
        order_book=order_book,
        inventory_reference=inventory_reference,
        candidate_center=candidate_state.center,
        candidate_reference=candidate_state.reference,
        candidate_target=candidate_state.target,
        previous_generation=state.generation,
        effective_generation=effective_state.generation,
        economic_ladder_changed=changed,
        de_risk_applied=config.stage >= AdaptiveStage.S4_DERISK,
        short_applied=config.stage >= AdaptiveStage.S5_SHORT,
        funding_applied=config.stage >= AdaptiveStage.S6_FUNDING,
        order_book_applied=config.stage >= AdaptiveStage.S7_ORDER_BOOK,
    )
    return decision, effective_state, effective_ladder


__all__ = [
    "AdaptiveGridDecision",
    "AdaptiveGridPolicyConfig",
    "AdaptiveGridState",
    "AdaptiveStage",
    "decide_adaptive_grid",
    "initialize_adaptive_grid",
]
