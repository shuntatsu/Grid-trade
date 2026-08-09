from dataclasses import dataclass
from decimal import Decimal


def _require_finite(value: Decimal, *, field: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{field} must be a finite Decimal")


@dataclass(frozen=True, slots=True)
class AdaptiveSignals:
    trend_score: Decimal
    funding_rate: Decimal
    order_book_imbalance: Decimal
    microprice: Decimal | None

    def __post_init__(self) -> None:
        _require_finite(self.trend_score, field="trend_score")
        _require_finite(self.funding_rate, field="funding_rate")
        _require_finite(self.order_book_imbalance, field="order_book_imbalance")
        if not Decimal("-1") <= self.trend_score <= Decimal("1"):
            raise ValueError("trend_score must be within [-1, 1]")
        if not Decimal("-1") <= self.order_book_imbalance <= Decimal("1"):
            raise ValueError("order_book_imbalance must be within [-1, 1]")
        if self.microprice is not None:
            _require_finite(self.microprice, field="microprice")
            if self.microprice <= 0:
                raise ValueError("microprice must be positive when present")


__all__ = ["AdaptiveSignals"]
