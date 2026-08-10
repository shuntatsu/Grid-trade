from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from enum import StrEnum

from grid_trade.domain.numeric import deterministic_decimal_context

LEGACY_UNSPECIFIED_INSTRUMENT = "UNSPECIFIED"


def _require_identity(value: str, *, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty")


def _require_finite_positive(value: Decimal, *, field: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"{field} must be a finite positive Decimal")


def instruments_compatible(left: str, right: str) -> bool:
    _require_identity(left, field="left instrument_id")
    _require_identity(right, field="right instrument_id")
    return left == right


def require_instruments_compatible(left: str, right: str, *, context: str) -> None:
    _require_identity(context, field="context")
    if not instruments_compatible(left, right):
        raise ValueError(f"{context} instrument mismatch: {left!r} != {right!r}")


def require_explicit_instrument(instrument_id: str, *, context: str) -> None:
    _require_identity(context, field="context")
    _require_identity(instrument_id, field="instrument_id")
    if instrument_id == LEGACY_UNSPECIFIED_INSTRUMENT:
        raise ValueError(f"{context} requires an explicit instrument_id")


class ContractType(StrEnum):
    LINEAR_PERPETUAL = "linear_perpetual"


@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    instrument_id: str
    contract_type: ContractType
    contract_multiplier: Decimal
    tick_size: Decimal
    quantity_step: Decimal
    min_quantity: Decimal
    min_notional: Decimal
    max_quantity: Decimal
    funding_interval_seconds: int

    def __post_init__(self) -> None:
        _require_identity(self.instrument_id, field="instrument_id")
        if self.instrument_id == LEGACY_UNSPECIFIED_INSTRUMENT:
            raise ValueError("instrument_id must be explicit")
        if self.contract_type is not ContractType.LINEAR_PERPETUAL:
            raise ValueError("only linear perpetual contracts are supported")
        for field_name in (
            "contract_multiplier",
            "tick_size",
            "quantity_step",
            "min_quantity",
            "min_notional",
            "max_quantity",
        ):
            _require_finite_positive(getattr(self, field_name), field=field_name)
        if self.min_quantity > self.max_quantity:
            raise ValueError("min_quantity must not exceed max_quantity")
        if self.funding_interval_seconds <= 0:
            raise ValueError("funding_interval_seconds must be positive")

    def floor_quantity(self, quantity: Decimal) -> Decimal:
        if not isinstance(quantity, Decimal) or not quantity.is_finite() or quantity < 0:
            raise ValueError("quantity must be a finite non-negative Decimal")
        with deterministic_decimal_context():
            return (quantity / self.quantity_step).to_integral_value(
                rounding=ROUND_FLOOR
            ) * self.quantity_step

    def notional(self, quantity: Decimal, price: Decimal) -> Decimal:
        if not isinstance(quantity, Decimal) or not quantity.is_finite():
            raise ValueError("quantity must be a finite Decimal")
        _require_finite_positive(price, field="price")
        with deterministic_decimal_context():
            return abs(quantity) * price * self.contract_multiplier

    def is_executable(self, quantity: Decimal, price: Decimal) -> bool:
        if (
            not isinstance(quantity, Decimal)
            or not quantity.is_finite()
            or quantity <= 0
            or not isinstance(price, Decimal)
            or not price.is_finite()
            or price <= 0
        ):
            return False
        if quantity != self.floor_quantity(quantity):
            return False
        if quantity < self.min_quantity or quantity > self.max_quantity:
            return False
        return self.notional(quantity, price) >= self.min_notional


__all__ = [
    "LEGACY_UNSPECIFIED_INSTRUMENT",
    "ContractType",
    "InstrumentSpec",
    "instruments_compatible",
    "require_explicit_instrument",
    "require_instruments_compatible",
]
