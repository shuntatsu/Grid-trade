from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from grid_trade.domain.orders import OrderSide, PassiveOrderIntent

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


def _round_to_tick(price: Decimal, tick_size: Decimal, side: OrderSide) -> Decimal:
    rounding = ROUND_FLOOR if side is OrderSide.BUY else ROUND_CEILING
    return (price / tick_size).to_integral_value(rounding=rounding) * tick_size


@dataclass(frozen=True, slots=True)
class AdaptiveLadderConfig:
    levels: int
    spacing_bps: int
    order_quantity: Decimal
    tick_size: Decimal
    max_abs_inventory: Decimal

    def __post_init__(self) -> None:
        if not 1 <= self.levels <= 50:
            raise ValueError("levels must be within [1, 50]")
        if self.spacing_bps <= 0:
            raise ValueError("spacing_bps must be positive")
        _require_positive(self.order_quantity, field="order_quantity")
        _require_positive(self.tick_size, field="tick_size")
        _require_positive(self.max_abs_inventory, field="max_abs_inventory")


def _validate_scale(value: Decimal, *, field: str) -> None:
    _require_finite(value, field=field)
    if not _ZERO <= value <= _ONE:
        raise ValueError(f"{field} must be within [0, 1]")


def _side_orders(
    *,
    reference: Decimal,
    side: OrderSide,
    reduce_only: bool,
    capacity: Decimal,
    scale: Decimal,
    config: AdaptiveLadderConfig,
    generation: int,
    stage: str,
) -> tuple[PassiveOrderIntent, ...]:
    if capacity <= 0 or scale <= 0:
        return ()

    per_level = config.order_quantity * scale
    remaining = capacity
    orders: list[PassiveOrderIntent] = []
    previous_price: Decimal | None = None

    for level in range(1, config.levels + 1):
        if remaining <= 0:
            break
        quantity = min(per_level, remaining)
        if quantity <= 0:
            break

        offset = Decimal(level * config.spacing_bps) / _BASIS_POINTS
        multiplier = _ONE - offset if side is OrderSide.BUY else _ONE + offset
        price = _round_to_tick(reference * multiplier, config.tick_size, side)
        if price <= 0:
            raise ValueError("adaptive ladder price must remain positive after tick rounding")
        if previous_price is not None:
            if price == previous_price:
                continue
            if side is OrderSide.BUY and price > previous_price:
                raise ValueError("buy ladder must remain descending")
            if side is OrderSide.SELL and price < previous_price:
                raise ValueError("sell ladder must remain ascending")

        orders.append(
            PassiveOrderIntent(
                client_order_id=f"{stage}:g{generation}:{side.value}:l{level}",
                generation=generation,
                level=level,
                side=side,
                price=price,
                quantity=quantity,
                reduce_only=reduce_only,
            ),
        )
        remaining -= quantity
        previous_price = price

    return tuple(orders)


def build_adaptive_ladder(
    *,
    reference: Decimal,
    position: Decimal,
    target: Decimal,
    bid_scale: Decimal,
    ask_scale: Decimal,
    config: AdaptiveLadderConfig,
    generation: int,
    stage: str,
) -> tuple[PassiveOrderIntent, ...]:
    _require_positive(reference, field="reference")
    _require_finite(position, field="position")
    _require_finite(target, field="target")
    _validate_scale(bid_scale, field="bid_scale")
    _validate_scale(ask_scale, field="ask_scale")
    if generation < 0:
        raise ValueError("generation must be non-negative")
    if not stage.strip():
        raise ValueError("stage must be non-empty")
    if abs(target) > config.max_abs_inventory:
        raise ValueError("target must be within max_abs_inventory")
    if position > 0 and target < 0:
        raise ValueError("flat-before-reverse gate requires target zero before short")
    if position < 0 and target > 0:
        raise ValueError("flat-before-reverse gate requires target zero before long")

    if target > 0:
        buy_capacity = max(_ZERO, config.max_abs_inventory - max(position, _ZERO))
        sell_capacity = max(_ZERO, position)
        buys = _side_orders(
            reference=reference,
            side=OrderSide.BUY,
            reduce_only=False,
            capacity=buy_capacity,
            scale=bid_scale,
            config=config,
            generation=generation,
            stage=stage,
        )
        sells = _side_orders(
            reference=reference,
            side=OrderSide.SELL,
            reduce_only=True,
            capacity=sell_capacity,
            scale=ask_scale,
            config=config,
            generation=generation,
            stage=stage,
        )
        return buys + sells

    if target < 0:
        buy_capacity = max(_ZERO, -position)
        sell_capacity = max(_ZERO, config.max_abs_inventory + min(position, _ZERO))
        buys = _side_orders(
            reference=reference,
            side=OrderSide.BUY,
            reduce_only=True,
            capacity=buy_capacity,
            scale=bid_scale,
            config=config,
            generation=generation,
            stage=stage,
        )
        sells = _side_orders(
            reference=reference,
            side=OrderSide.SELL,
            reduce_only=False,
            capacity=sell_capacity,
            scale=ask_scale,
            config=config,
            generation=generation,
            stage=stage,
        )
        return buys + sells

    if position > 0:
        return _side_orders(
            reference=reference,
            side=OrderSide.SELL,
            reduce_only=True,
            capacity=position,
            scale=ask_scale,
            config=config,
            generation=generation,
            stage=stage,
        )
    if position < 0:
        return _side_orders(
            reference=reference,
            side=OrderSide.BUY,
            reduce_only=True,
            capacity=-position,
            scale=bid_scale,
            config=config,
            generation=generation,
            stage=stage,
        )
    return ()


__all__ = ["AdaptiveLadderConfig", "build_adaptive_ladder"]
