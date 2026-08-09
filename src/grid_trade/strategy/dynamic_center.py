from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.orders import PassiveOrderIntent
from grid_trade.strategy.fixed_grid import FixedLongGridConfig
from grid_trade.strategy.grid_geometry import build_long_grid_at_center

_BASIS_POINTS = Decimal(10_000)


def _require_finite_positive(value: Decimal, *, field: str) -> None:
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{field} must be finite and positive")


@dataclass(frozen=True, slots=True)
class DynamicCenterConfig:
    reanchor_threshold_bps: Decimal
    max_step_bps: Decimal

    def __post_init__(self) -> None:
        _require_finite_positive(
            self.reanchor_threshold_bps,
            field="reanchor_threshold_bps",
        )
        _require_finite_positive(self.max_step_bps, field="max_step_bps")
        if self.max_step_bps >= _BASIS_POINTS:
            raise ValueError("max_step_bps must be strictly below 10000")


@dataclass(frozen=True, slots=True)
class DynamicCenterState:
    center: Decimal
    generation: int

    def __post_init__(self) -> None:
        _require_finite_positive(self.center, field="center")
        if self.generation < 0:
            raise ValueError("generation must be non-negative")


@dataclass(frozen=True, slots=True)
class CenterProposal:
    previous_center: Decimal
    market_mid: Decimal
    deviation_bps: Decimal
    proposed_center: Decimal
    previous_generation: int
    threshold_crossed: bool

    def __post_init__(self) -> None:
        _require_finite_positive(self.previous_center, field="previous_center")
        _require_finite_positive(self.market_mid, field="market_mid")
        if not self.deviation_bps.is_finite():
            raise ValueError("deviation_bps must be finite")
        _require_finite_positive(self.proposed_center, field="proposed_center")
        if self.previous_generation < 0:
            raise ValueError("previous_generation must be non-negative")


class CenterDecisionReason(StrEnum):
    WITHIN_THRESHOLD = "within_threshold"
    BOUNDED_REANCHOR = "bounded_reanchor"
    NO_EFFECTIVE_LADDER_CHANGE = "no_effective_ladder_change"


@dataclass(frozen=True, slots=True)
class CenterDecision:
    previous_center: Decimal
    market_mid: Decimal
    deviation_bps: Decimal
    proposed_center: Decimal
    effective_center: Decimal
    previous_generation: int
    effective_generation: int
    reanchored: bool
    economic_ladder_changed: bool
    reason: CenterDecisionReason

    def __post_init__(self) -> None:
        for field_name in (
            "previous_center",
            "market_mid",
            "proposed_center",
            "effective_center",
        ):
            _require_finite_positive(getattr(self, field_name), field=field_name)
        if not self.deviation_bps.is_finite():
            raise ValueError("deviation_bps must be finite")
        if self.previous_generation < 0 or self.effective_generation < 0:
            raise ValueError("generations must be non-negative")
        expected_generation = self.previous_generation + (1 if self.reanchored else 0)
        if self.effective_generation != expected_generation:
            raise ValueError("effective_generation must change exactly once on re-anchor")
        if self.reanchored != self.economic_ladder_changed:
            raise ValueError("reanchored must equal economic_ladder_changed")


def initialize_dynamic_center(snapshot: MarketSnapshot) -> DynamicCenterState:
    return DynamicCenterState(center=snapshot.mid, generation=0)


def propose_dynamic_center(
    snapshot: MarketSnapshot,
    state: DynamicCenterState,
    config: DynamicCenterConfig,
) -> CenterProposal:
    market_mid = snapshot.mid
    deviation_bps = (market_mid - state.center) / state.center * _BASIS_POINTS
    threshold_crossed = abs(deviation_bps) >= config.reanchor_threshold_bps

    if not threshold_crossed:
        proposed_center = state.center
    else:
        step_bps = min(abs(deviation_bps), config.max_step_bps)
        signed_step_bps = step_bps if deviation_bps > 0 else -step_bps
        proposed_center = state.center * (Decimal(1) + signed_step_bps / _BASIS_POINTS)

    return CenterProposal(
        previous_center=state.center,
        market_mid=market_mid,
        deviation_bps=deviation_bps,
        proposed_center=proposed_center,
        previous_generation=state.generation,
        threshold_crossed=threshold_crossed,
    )


def _economic_signature(
    ladder: tuple[PassiveOrderIntent, ...],
) -> tuple[tuple[str, int, Decimal, Decimal, bool], ...]:
    return tuple(
        (
            order.side.value,
            order.level,
            order.price,
            order.quantity,
            order.reduce_only,
        )
        for order in ladder
    )


def decide_dynamic_center(
    snapshot: MarketSnapshot,
    state: DynamicCenterState,
    center_config: DynamicCenterConfig,
    grid_config: FixedLongGridConfig,
) -> tuple[CenterDecision, tuple[PassiveOrderIntent, ...]]:
    proposal = propose_dynamic_center(snapshot, state, center_config)
    current_ladder = build_long_grid_at_center(
        state.center,
        grid_config,
        generation=state.generation,
        stage="s1",
    )

    if not proposal.threshold_crossed:
        return (
            CenterDecision(
                previous_center=state.center,
                market_mid=proposal.market_mid,
                deviation_bps=proposal.deviation_bps,
                proposed_center=proposal.proposed_center,
                effective_center=state.center,
                previous_generation=state.generation,
                effective_generation=state.generation,
                reanchored=False,
                economic_ladder_changed=False,
                reason=CenterDecisionReason.WITHIN_THRESHOLD,
            ),
            current_ladder,
        )

    candidate_generation = state.generation + 1
    candidate_ladder = build_long_grid_at_center(
        proposal.proposed_center,
        grid_config,
        generation=candidate_generation,
        stage="s1",
    )
    if _economic_signature(candidate_ladder) == _economic_signature(current_ladder):
        return (
            CenterDecision(
                previous_center=state.center,
                market_mid=proposal.market_mid,
                deviation_bps=proposal.deviation_bps,
                proposed_center=proposal.proposed_center,
                effective_center=state.center,
                previous_generation=state.generation,
                effective_generation=state.generation,
                reanchored=False,
                economic_ladder_changed=False,
                reason=CenterDecisionReason.NO_EFFECTIVE_LADDER_CHANGE,
            ),
            current_ladder,
        )

    return (
        CenterDecision(
            previous_center=state.center,
            market_mid=proposal.market_mid,
            deviation_bps=proposal.deviation_bps,
            proposed_center=proposal.proposed_center,
            effective_center=proposal.proposed_center,
            previous_generation=state.generation,
            effective_generation=candidate_generation,
            reanchored=True,
            economic_ladder_changed=True,
            reason=CenterDecisionReason.BOUNDED_REANCHOR,
        ),
        candidate_ladder,
    )


__all__ = [
    "CenterDecision",
    "CenterDecisionReason",
    "CenterProposal",
    "DynamicCenterConfig",
    "DynamicCenterState",
    "decide_dynamic_center",
    "initialize_dynamic_center",
    "propose_dynamic_center",
]
