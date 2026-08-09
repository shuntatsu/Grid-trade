from dataclasses import dataclass
from decimal import Decimal

from grid_trade.domain.numeric import deterministic_decimal_context


def _require_finite_positive(value: Decimal, *, field: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"{field} must be a finite positive Decimal")


def _require_finite_non_negative(value: Decimal, *, field: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{field} must be a finite non-negative Decimal")


@dataclass(frozen=True, slots=True)
class RiskSizingConfig:
    max_notional_fraction: Decimal
    max_single_move_loss_fraction: Decimal
    volatility_floor: Decimal

    def __post_init__(self) -> None:
        _require_finite_positive(self.max_notional_fraction, field="max_notional_fraction")
        _require_finite_positive(
            self.max_single_move_loss_fraction,
            field="max_single_move_loss_fraction",
        )
        _require_finite_positive(self.volatility_floor, field="volatility_floor")
        if self.max_notional_fraction > 1:
            raise ValueError("max_notional_fraction must not exceed 1")
        if self.max_single_move_loss_fraction > 1:
            raise ValueError("max_single_move_loss_fraction must not exceed 1")


@dataclass(frozen=True, slots=True)
class RiskSizingInput:
    equity: Decimal
    reference_price: Decimal
    volatility_scale: Decimal
    max_margin_notional: Decimal
    venue_max_quantity: Decimal

    def __post_init__(self) -> None:
        _require_finite_positive(self.equity, field="equity")
        _require_finite_positive(self.reference_price, field="reference_price")
        _require_finite_non_negative(self.volatility_scale, field="volatility_scale")
        _require_finite_positive(self.max_margin_notional, field="max_margin_notional")
        _require_finite_positive(self.venue_max_quantity, field="venue_max_quantity")


@dataclass(frozen=True, slots=True)
class InventoryCapacity:
    q_notional: Decimal
    q_margin: Decimal
    q_volatility: Decimal
    q_venue: Decimal
    q_max: Decimal
    binding_constraint: str

    def __post_init__(self) -> None:
        for field_name in ("q_notional", "q_margin", "q_volatility", "q_venue", "q_max"):
            _require_finite_positive(getattr(self, field_name), field=field_name)
        if self.binding_constraint not in {"notional", "margin", "volatility", "venue"}:
            raise ValueError("binding_constraint is invalid")
        if self.q_max != min(self.q_notional, self.q_margin, self.q_volatility, self.q_venue):
            raise ValueError("q_max must equal the most conservative capacity")


def derive_inventory_capacity(
    inputs: RiskSizingInput,
    config: RiskSizingConfig,
) -> InventoryCapacity:
    with deterministic_decimal_context():
        q_notional = inputs.equity * config.max_notional_fraction / inputs.reference_price
        q_margin = inputs.max_margin_notional / inputs.reference_price
        volatility = max(inputs.volatility_scale, config.volatility_floor)
        q_volatility = (
            inputs.equity
            * config.max_single_move_loss_fraction
            / (inputs.reference_price * volatility)
        )
    q_venue = inputs.venue_max_quantity

    capacities = (
        ("notional", q_notional),
        ("margin", q_margin),
        ("volatility", q_volatility),
        ("venue", q_venue),
    )
    binding_constraint, q_max = min(capacities, key=lambda item: item[1])
    return InventoryCapacity(
        q_notional=q_notional,
        q_margin=q_margin,
        q_volatility=q_volatility,
        q_venue=q_venue,
        q_max=q_max,
        binding_constraint=binding_constraint,
    )


__all__ = [
    "InventoryCapacity",
    "RiskSizingConfig",
    "RiskSizingInput",
    "derive_inventory_capacity",
]
