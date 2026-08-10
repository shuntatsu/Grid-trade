from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

from grid_trade.domain.instrument import (
    LEGACY_UNSPECIFIED_INSTRUMENT,
    InstrumentSpec,
    require_instruments_compatible,
)
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
    instrument_id: str = LEGACY_UNSPECIFIED_INSTRUMENT
    instrument: InstrumentSpec | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.levels <= 50:
            raise ValueError("levels must be within [1, 50]")
        if self.spacing_bps <= 0:
            raise ValueError("spacing_bps must be positive")
        _require_finite_positive(self.order_quantity, field="order_quantity")
        _require_finite_positive(self.tick_size, field="tick_size")
        if not self.instrument_id.strip():
            raise ValueError("instrument_id must be non-empty")
        if self.instrument is not None:
            require_instruments_compatible(
                self.instrument_id,
                self.instrument.instrument_id,
                context="fixed-grid config",
            )
            if self.tick_size != self.instrument.tick_size:
                raise ValueError("fixed-grid tick_size must match InstrumentSpec")
            if self.order_quantity != self.instrument.floor_quantity(self.order_quantity):
                raise ValueError("fixed-grid order_quantity must align to instrument quantity_step")
            if self.order_quantity > self.instrument.max_quantity:
                raise ValueError(
                    "fixed-grid order_quantity must not exceed instrument max_quantity"
                )


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
        previous_price = price
        if config.instrument is not None and not config.instrument.is_executable(
            config.order_quantity,
            price,
        ):
            continue

        namespace = (
            ""
            if config.instrument_id == LEGACY_UNSPECIFIED_INSTRUMENT
            else f"{config.instrument_id}:"
        )
        orders.append(
            PassiveOrderIntent(
                client_order_id=f"{namespace}{stage}:g{generation}:buy:l{level}",
                generation=generation,
                level=level,
                side=OrderSide.BUY,
                price=price,
                quantity=config.order_quantity,
                reduce_only=False,
                instrument_id=config.instrument_id,
            ),
        )

    return tuple(orders)


def ladder_economic_signature(
    ladder: tuple[PassiveOrderIntent, ...],
) -> tuple[tuple[str, str, int, Decimal, Decimal, bool], ...]:
    """Return venue-economic fields while intentionally excluding client/generation IDs."""
    return tuple(
        (
            order.instrument_id,
            order.side.value,
            order.level,
            order.price,
            order.quantity,
            order.reduce_only,
        )
        for order in ladder
    )


__all__ = [
    "FixedLongGridConfig",
    "build_long_grid_at_center",
    "ladder_economic_signature",
]
