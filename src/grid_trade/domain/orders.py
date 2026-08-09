from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


def _require_positive_decimal(value: Decimal, *, field: str) -> None:
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{field} must be finite and positive")


def _require_aware_timestamp(timestamp: datetime) -> None:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderAction(StrEnum):
    CANCEL = "cancel"
    SUBMIT = "submit"


@dataclass(frozen=True, slots=True)
class PassiveOrderIntent:
    client_order_id: str
    generation: int
    level: int
    side: OrderSide
    price: Decimal
    quantity: Decimal
    reduce_only: bool = False

    def __post_init__(self) -> None:
        if not self.client_order_id.strip():
            raise ValueError("client_order_id must be non-empty")
        if self.generation < 0:
            raise ValueError("generation must be non-negative")
        if self.level < 0:
            raise ValueError("level must be non-negative")
        _require_positive_decimal(self.price, field="price")
        _require_positive_decimal(self.quantity, field="quantity")


@dataclass(frozen=True, slots=True)
class WorkingOrder:
    client_order_id: str
    generation: int
    level: int
    side: OrderSide
    price: Decimal
    quantity: Decimal
    filled_quantity: Decimal
    reduce_only: bool = False

    def __post_init__(self) -> None:
        if not self.client_order_id.strip():
            raise ValueError("client_order_id must be non-empty")
        if self.generation < 0:
            raise ValueError("generation must be non-negative")
        if self.level < 0:
            raise ValueError("level must be non-negative")
        _require_positive_decimal(self.price, field="price")
        _require_positive_decimal(self.quantity, field="quantity")
        if not self.filled_quantity.is_finite():
            raise ValueError("filled_quantity must be finite")
        if self.filled_quantity < 0 or self.filled_quantity > self.quantity:
            raise ValueError("filled_quantity must be within [0, quantity]")

    @property
    def remaining_quantity(self) -> Decimal:
        return self.quantity - self.filled_quantity


@dataclass(frozen=True, slots=True)
class FillEvent:
    client_order_id: str
    timestamp: datetime
    price: Decimal
    quantity: Decimal

    def __post_init__(self) -> None:
        if not self.client_order_id.strip():
            raise ValueError("client_order_id must be non-empty")
        _require_aware_timestamp(self.timestamp)
        _require_positive_decimal(self.price, field="price")
        _require_positive_decimal(self.quantity, field="quantity")


@dataclass(frozen=True, slots=True)
class ReconciliationPlan:
    cancel: tuple[str, ...] = ()
    submit: tuple[PassiveOrderIntent, ...] = ()

    def __post_init__(self) -> None:
        if self.cancel and self.submit:
            raise ValueError("cancel and submit must occur in separate reconciliation cycles")
        if any(not client_order_id.strip() for client_order_id in self.cancel):
            raise ValueError("cancel IDs must be non-empty")
        if len(set(self.cancel)) != len(self.cancel):
            raise ValueError("cancel IDs must be unique")


__all__ = [
    "FillEvent",
    "OrderAction",
    "OrderSide",
    "PassiveOrderIntent",
    "ReconciliationPlan",
    "WorkingOrder",
]
