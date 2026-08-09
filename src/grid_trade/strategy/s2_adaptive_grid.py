from dataclasses import dataclass, replace
from decimal import Decimal

from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.orders import PassiveOrderIntent
from grid_trade.strategy.dynamic_center import (
    DynamicCenterConfig,
    DynamicCenterState,
    propose_dynamic_center,
)
from grid_trade.strategy.grid_geometry import (
    FixedLongGridConfig,
    build_long_grid_at_center,
    ladder_economic_signature,
)
from grid_trade.strategy.volatility_spacing import (
    SpacingDecision,
    VolatilitySpacingConfig,
    propose_volatility_spacing,
)


def _require_finite_positive(value: Decimal, *, field: str) -> None:
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{field} must be finite and positive")


@dataclass(frozen=True, slots=True)
class S2GridState:
    center: Decimal
    spacing_bps: int
    generation: int

    def __post_init__(self) -> None:
        _require_finite_positive(self.center, field="center")
        if self.spacing_bps <= 0:
            raise ValueError("spacing_bps must be positive")
        if self.generation < 0:
            raise ValueError("generation must be non-negative")


@dataclass(frozen=True, slots=True)
class S2GridDecision:
    previous_center: Decimal
    candidate_center: Decimal
    effective_center: Decimal
    previous_spacing_bps: int
    candidate_spacing_bps: int
    effective_spacing_bps: int
    previous_generation: int
    effective_generation: int
    economic_ladder_changed: bool
    center_threshold_crossed: bool
    spacing_changed: bool
    center_deviation_bps: Decimal
    spacing_decision: SpacingDecision

    def __post_init__(self) -> None:
        for field_name in ("previous_center", "candidate_center", "effective_center"):
            _require_finite_positive(getattr(self, field_name), field=field_name)
        for field_name in (
            "previous_spacing_bps",
            "candidate_spacing_bps",
            "effective_spacing_bps",
        ):
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} must be positive")
        if self.previous_generation < 0 or self.effective_generation < 0:
            raise ValueError("generations must be non-negative")
        expected_generation = self.previous_generation + (
            1 if self.economic_ladder_changed else 0
        )
        if self.effective_generation != expected_generation:
            raise ValueError("effective_generation must track one economic ladder change")
        if not self.center_deviation_bps.is_finite():
            raise ValueError("center_deviation_bps must be finite")


def _grid_config_with_spacing(
    config: FixedLongGridConfig,
    spacing_bps: int,
) -> FixedLongGridConfig:
    return replace(config, spacing_bps=spacing_bps)


def initialize_s2_grid(
    snapshot: MarketSnapshot,
    grid_config: FixedLongGridConfig,
    spacing_config: VolatilitySpacingConfig,
) -> S2GridState:
    spacing = propose_volatility_spacing(
        snapshot,
        grid_config.spacing_bps,
        spacing_config,
    ).effective_spacing_bps
    state = S2GridState(
        center=snapshot.mid,
        spacing_bps=spacing,
        generation=0,
    )
    build_long_grid_at_center(
        state.center,
        _grid_config_with_spacing(grid_config, state.spacing_bps),
        generation=state.generation,
        stage="s2",
    )
    return state


def decide_s2_grid(
    snapshot: MarketSnapshot,
    state: S2GridState,
    center_config: DynamicCenterConfig,
    grid_config: FixedLongGridConfig,
    spacing_config: VolatilitySpacingConfig,
) -> tuple[S2GridDecision, S2GridState, tuple[PassiveOrderIntent, ...]]:
    current_config = _grid_config_with_spacing(grid_config, state.spacing_bps)
    current_ladder = build_long_grid_at_center(
        state.center,
        current_config,
        generation=state.generation,
        stage="s2",
    )

    center_proposal = propose_dynamic_center(
        snapshot,
        DynamicCenterState(center=state.center, generation=state.generation),
        center_config,
    )
    spacing_decision = propose_volatility_spacing(
        snapshot,
        state.spacing_bps,
        spacing_config,
    )
    candidate_center = center_proposal.proposed_center
    candidate_spacing_bps = spacing_decision.effective_spacing_bps
    candidate_generation = state.generation + 1
    candidate_ladder = build_long_grid_at_center(
        candidate_center,
        _grid_config_with_spacing(grid_config, candidate_spacing_bps),
        generation=candidate_generation,
        stage="s2",
    )
    economic_ladder_changed = (
        ladder_economic_signature(candidate_ladder)
        != ladder_economic_signature(current_ladder)
    )

    if economic_ladder_changed:
        candidate_state = S2GridState(
            center=candidate_center,
            spacing_bps=candidate_spacing_bps,
            generation=candidate_generation,
        )
        effective_ladder = candidate_ladder
    else:
        candidate_state = state
        effective_ladder = current_ladder

    decision = S2GridDecision(
        previous_center=state.center,
        candidate_center=candidate_center,
        effective_center=candidate_state.center,
        previous_spacing_bps=state.spacing_bps,
        candidate_spacing_bps=candidate_spacing_bps,
        effective_spacing_bps=candidate_state.spacing_bps,
        previous_generation=state.generation,
        effective_generation=candidate_state.generation,
        economic_ladder_changed=economic_ladder_changed,
        center_threshold_crossed=center_proposal.threshold_crossed,
        spacing_changed=spacing_decision.changed,
        center_deviation_bps=center_proposal.deviation_bps,
        spacing_decision=spacing_decision,
    )
    return decision, candidate_state, effective_ladder


__all__ = [
    "S2GridDecision",
    "S2GridState",
    "decide_s2_grid",
    "initialize_s2_grid",
]
