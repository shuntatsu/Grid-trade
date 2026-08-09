from dataclasses import dataclass
from decimal import Decimal

from grid_trade.strategy.adaptive_signals import AdaptiveSignals

_BASIS_POINTS = Decimal(10_000)
_ZERO = Decimal(0)
_ONE = Decimal(1)


def _require_finite_positive(value: Decimal, *, field: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"{field} must be a finite positive Decimal")


@dataclass(frozen=True, slots=True)
class OrderBookReferenceConfig:
    microprice_weight: Decimal
    imbalance_shift_bps: Decimal

    def __post_init__(self) -> None:
        if not self.microprice_weight.is_finite():
            raise ValueError("microprice_weight must be finite")
        if not _ZERO <= self.microprice_weight <= _ONE:
            raise ValueError("microprice_weight must be within [0, 1]")
        if not self.imbalance_shift_bps.is_finite() or self.imbalance_shift_bps < 0:
            raise ValueError("imbalance_shift_bps must be finite and non-negative")
        if self.imbalance_shift_bps >= _BASIS_POINTS:
            raise ValueError("imbalance_shift_bps must be strictly below 10000")


@dataclass(frozen=True, slots=True)
class OrderBookReferenceDecision:
    blended_reference: Decimal
    signed_imbalance_shift_bps: Decimal
    effective_reference: Decimal
    microprice_used: bool

    def __post_init__(self) -> None:
        _require_finite_positive(self.blended_reference, field="blended_reference")
        if not self.signed_imbalance_shift_bps.is_finite():
            raise ValueError("signed_imbalance_shift_bps must be finite")
        _require_finite_positive(self.effective_reference, field="effective_reference")


def decide_order_book_reference(
    *,
    center: Decimal,
    market_mid: Decimal,
    signals: AdaptiveSignals,
    config: OrderBookReferenceConfig,
) -> OrderBookReferenceDecision:
    _require_finite_positive(center, field="center")
    _require_finite_positive(market_mid, field="market_mid")

    if signals.microprice is None:
        blended_reference = center
        microprice_used = False
    else:
        relative_displacement = signals.microprice / market_mid - _ONE
        blended_reference = center * (_ONE + config.microprice_weight * relative_displacement)
        microprice_used = True

    signed_shift_bps = signals.order_book_imbalance * config.imbalance_shift_bps
    effective_reference = blended_reference * (_ONE + signed_shift_bps / _BASIS_POINTS)
    _require_finite_positive(effective_reference, field="effective_reference")

    return OrderBookReferenceDecision(
        blended_reference=blended_reference,
        signed_imbalance_shift_bps=signed_shift_bps,
        effective_reference=effective_reference,
        microprice_used=microprice_used,
    )


__all__ = [
    "OrderBookReferenceConfig",
    "OrderBookReferenceDecision",
    "decide_order_book_reference",
]
