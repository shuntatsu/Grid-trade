from dataclasses import dataclass
from decimal import Decimal

from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.orders import PassiveOrderIntent


def _require_finite_positive(value: Decimal, *, field: str) -> None:
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{field} must be finite and positive")


@dataclass(frozen=True, slots=True)
class FixedLongGridConfig:
    levels: int
    spacing_bps: int
    order_quantity: Decimal
    tick_size: Decimal

    def __post_init__(self) -> None:
        if not 1 <= self.levels <= 50:
            raise ValueError("levels must be within [1, 50]")
        if self.spacing_bps <= 0:
            raise ValueError("spacing_bps must be positive")
        _require_finite_positive(self.order_quantity, field="order_quantity")
        _require_finite_positive(self.tick_size, field="tick_size")


def build_fixed_long_grid(
    snapshot: MarketSnapshot,
    config: FixedLongGridConfig,
    generation: int,
) -> tuple[PassiveOrderIntent, ...]:
    from grid_trade.strategy.grid_geometry import build_long_grid_at_center

    return build_long_grid_at_center(
        snapshot.mid,
        config,
        generation=generation,
        stage="s0",
    )


__all__ = ["FixedLongGridConfig", "build_fixed_long_grid"]
