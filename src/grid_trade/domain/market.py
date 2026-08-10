from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from grid_trade.domain.numeric import deterministic_decimal_context


def _require_finite(value: Decimal, *, field: str) -> None:
    if not value.is_finite():
        raise ValueError(f"{field} must be finite")


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    timestamp: datetime
    best_bid: Decimal
    best_ask: Decimal
    realized_volatility: Decimal
    position_quantity: Decimal
    source_id: str

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        if not self.source_id.strip():
            raise ValueError("source_id must be non-empty")

        _require_finite(self.best_bid, field="best_bid")
        _require_finite(self.best_ask, field="best_ask")
        _require_finite(self.realized_volatility, field="realized_volatility")
        _require_finite(self.position_quantity, field="position_quantity")

        if self.best_bid <= 0:
            raise ValueError("best_bid must be positive")
        if self.best_ask <= 0:
            raise ValueError("best_ask must be positive")
        if self.best_bid >= self.best_ask:
            raise ValueError("best_bid must be strictly below best_ask")
        if self.realized_volatility < 0:
            raise ValueError("realized_volatility must be non-negative")

    @property
    def mid(self) -> Decimal:
        with deterministic_decimal_context():
            return (self.best_bid + self.best_ask) / Decimal(2)


__all__ = ["MarketSnapshot"]
