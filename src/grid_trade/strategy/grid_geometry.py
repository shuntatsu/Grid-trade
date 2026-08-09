from decimal import ROUND_FLOOR, Decimal

from grid_trade.domain.orders import OrderSide, PassiveOrderIntent
from grid_trade.strategy.fixed_grid import FixedLongGridConfig

_BASIS_POINTS = Decimal(10_000)


def _round_down_to_tick(price: Decimal, tick_size: Decimal) -> Decimal:
    return (price / tick_size).to_integral_value(rounding=ROUND_FLOOR) * tick_size


def build_long_grid_at_center(
    center: Decimal,
    config: FixedLongGridConfig,
    generation: int,
    stage: str,
) -> tuple[PassiveOrderIntent, ...]:
    if not center.is_finite() or center <= 0:
        raise ValueError("center must be finite and positive")
    if generation < 0:
        raise ValueError("generation must be non-negative")
    if not stage.strip():
        raise ValueError("stage must be non-empty")

    orders: list[PassiveOrderIntent] = []
    previous_price: Decimal | None = None

    for level in range(1, config.levels + 1):
        offset = Decimal(level * config.spacing_bps) / _BASIS_POINTS
        raw_price = center * (Decimal(1) - offset)
        price = _round_down_to_tick(raw_price, config.tick_size)

        if price <= 0:
            raise ValueError("grid level must remain strictly positive after tick rounding")
        if previous_price is not None and price >= previous_price:
            raise ValueError("grid levels must remain strictly descending after tick rounding")

        orders.append(
            PassiveOrderIntent(
                client_order_id=f"{stage}:g{generation}:buy:l{level}",
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


__all__ = ["build_long_grid_at_center"]
