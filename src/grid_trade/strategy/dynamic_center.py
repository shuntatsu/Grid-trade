from dataclasses import dataclass
from decimal import Decimal

from grid_trade.domain.market import MarketSnapshot

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


__all__ = [
    "CenterProposal",
    "DynamicCenterConfig",
    "DynamicCenterState",
    "initialize_dynamic_center",
    "propose_dynamic_center",
]
