from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal

from grid_trade.domain.market import MarketSnapshot

_BASIS_POINTS = Decimal(10_000)


def _require_finite_positive(value: Decimal, *, field: str) -> None:
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{field} must be finite and positive")


@dataclass(frozen=True, slots=True)
class VolatilitySpacingConfig:
    min_spacing_bps: Decimal
    max_spacing_bps: Decimal
    volatility_multiplier: Decimal
    execution_cost_floor_bps: Decimal

    def __post_init__(self) -> None:
        _require_finite_positive(self.min_spacing_bps, field="min_spacing_bps")
        _require_finite_positive(self.max_spacing_bps, field="max_spacing_bps")
        _require_finite_positive(self.volatility_multiplier, field="volatility_multiplier")
        _require_finite_positive(
            self.execution_cost_floor_bps,
            field="execution_cost_floor_bps",
        )
        if self.max_spacing_bps < self.min_spacing_bps:
            raise ValueError("max_spacing_bps must be at least min_spacing_bps")
        if self.execution_cost_floor_bps > self.max_spacing_bps:
            raise ValueError(
                "execution_cost_floor_bps must not exceed max_spacing_bps",
            )
        if self.max_spacing_bps != self.max_spacing_bps.to_integral_value():
            raise ValueError("max_spacing_bps must be an integer number of basis points")
        if self.max_spacing_bps >= _BASIS_POINTS:
            raise ValueError("max_spacing_bps must be strictly below 10000")


@dataclass(frozen=True, slots=True)
class SpacingDecision:
    previous_spacing_bps: int
    realized_volatility: Decimal
    volatility_spacing_bps: Decimal
    unclamped_spacing_bps: Decimal
    effective_spacing_bps: int
    changed: bool

    def __post_init__(self) -> None:
        if self.previous_spacing_bps <= 0:
            raise ValueError("previous_spacing_bps must be positive")
        if not self.realized_volatility.is_finite() or self.realized_volatility < 0:
            raise ValueError("realized_volatility must be finite and non-negative")
        if not self.volatility_spacing_bps.is_finite() or self.volatility_spacing_bps < 0:
            raise ValueError("volatility_spacing_bps must be finite and non-negative")
        if not self.unclamped_spacing_bps.is_finite() or self.unclamped_spacing_bps <= 0:
            raise ValueError("unclamped_spacing_bps must be finite and positive")
        if self.effective_spacing_bps <= 0:
            raise ValueError("effective_spacing_bps must be positive")
        if self.changed is not (self.effective_spacing_bps != self.previous_spacing_bps):
            raise ValueError("changed must reflect effective spacing equality")


def propose_volatility_spacing(
    snapshot: MarketSnapshot,
    previous_spacing_bps: int,
    config: VolatilitySpacingConfig,
) -> SpacingDecision:
    if previous_spacing_bps <= 0:
        raise ValueError("previous_spacing_bps must be positive")

    volatility_spacing_bps = (
        snapshot.realized_volatility * _BASIS_POINTS * config.volatility_multiplier
    )
    unclamped_spacing_bps = max(
        config.min_spacing_bps,
        config.execution_cost_floor_bps,
        volatility_spacing_bps,
    )
    clamped_spacing_bps = min(config.max_spacing_bps, unclamped_spacing_bps)
    effective_spacing_bps = int(
        clamped_spacing_bps.to_integral_value(rounding=ROUND_CEILING),
    )

    return SpacingDecision(
        previous_spacing_bps=previous_spacing_bps,
        realized_volatility=snapshot.realized_volatility,
        volatility_spacing_bps=volatility_spacing_bps,
        unclamped_spacing_bps=unclamped_spacing_bps,
        effective_spacing_bps=effective_spacing_bps,
        changed=effective_spacing_bps != previous_spacing_bps,
    )


__all__ = [
    "SpacingDecision",
    "VolatilitySpacingConfig",
    "propose_volatility_spacing",
]
