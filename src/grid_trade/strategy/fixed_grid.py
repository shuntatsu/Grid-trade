from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.orders import OrderSide, PassiveOrderIntent

_BASIS_POINTS = Decimal(10_000)


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


def _round_down_to_tick(price: Decimal, tick_size: Decimal) -> Decimal:
    return (price / tick_size).to_integral_value(rounding=ROUND_FLOOR) * tick_size


def build_fixed_long_grid(
    snapshot: MarketSnapshot,
    config: FixedLongGridConfig,
    generation: int,
) -> tuple[PassiveOrderIntent, ...]:
    if generation < 0:
        raise ValueError("generation must be non-negative")

    orders: list[PassiveOrderIntent] = []
    previous_price: Decimal | None = None

    for level in range(1, config.levels + 1):
        offset = Decimal(level * config.spacing_bps) / _BASIS_POINTS
        raw_price = snapshot.mid * (Decimal(1) - offset)
        price = _round_down_to_tick(raw_price, config.tick_size)

        if price <= 0:
            raise ValueError("grid level must remain strictly positive after tick rounding")
        if previous_price is not None and price >= previous_price:
            raise ValueError("grid levels must remain strictly descending after tick rounding")

        orders.append(
            PassiveOrderIntent(
                client_order_id=f"s0:g{generation}:buy:l{level}",
                generation=generation,
                level=level,
                side=OrderSide.BUY,
                price=price,
                quantity=config.order_quantity,
                reduce_only=False,
            ),
        )
        previous_price = price

    return tuple(orders)


__all__ = ["FixedLongGridConfig", "build_fixed_long_grid"]
