from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


def _require_aware(timestamp: datetime, *, field: str) -> None:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _require_finite(value: Decimal, *, field: str) -> None:
    if not value.is_finite():
        raise ValueError(f"{field} must be finite")


class RiskReason(StrEnum):
    STALE_DATA = "stale_data"
    DRAWDOWN_BREACH = "drawdown_breach"
    MAX_OPEN_ORDERS = "max_open_orders"
    MAX_POSITION = "max_position"


@dataclass(frozen=True, slots=True)
class RiskLimits:
    max_abs_position: Decimal
    max_drawdown_fraction: Decimal
    max_data_age_ms: int
    max_open_orders: int

    def __post_init__(self) -> None:
        _require_finite(self.max_abs_position, field="max_abs_position")
        _require_finite(self.max_drawdown_fraction, field="max_drawdown_fraction")
        if self.max_abs_position <= 0:
            raise ValueError("max_abs_position must be positive")
        if not Decimal(0) <= self.max_drawdown_fraction < Decimal(1):
            raise ValueError("max_drawdown_fraction must be within [0, 1)")
        if self.max_data_age_ms < 0:
            raise ValueError("max_data_age_ms must be non-negative")
        if self.max_open_orders < 0:
            raise ValueError("max_open_orders must be non-negative")


@dataclass(frozen=True, slots=True)
class RiskState:
    equity: Decimal
    peak_equity: Decimal
    open_order_count: int
    now: datetime

    def __post_init__(self) -> None:
        _require_finite(self.equity, field="equity")
        _require_finite(self.peak_equity, field="peak_equity")
        _require_aware(self.now, field="now")
        if self.equity < 0:
            raise ValueError("equity must be non-negative")
        if self.peak_equity <= 0:
            raise ValueError("peak_equity must be positive")
        if self.equity > self.peak_equity:
            raise ValueError("equity must not exceed peak_equity")
        if self.open_order_count < 0:
            raise ValueError("open_order_count must be non-negative")


@dataclass(frozen=True, slots=True)
class RiskDecision:
    allow_new_risk: bool
    cancel_all_passive: bool
    target_flat: bool
    reasons: tuple[RiskReason, ...] = ()

    def __post_init__(self) -> None:
        if self.target_flat and not self.cancel_all_passive:
            raise ValueError("target_flat requires cancel_all_passive")
        if self.allow_new_risk and self.reasons:
            raise ValueError("allowed risk must not carry failure reasons")
        if self.allow_new_risk and (self.cancel_all_passive or self.target_flat):
            raise ValueError("allowed risk cannot request cancellation or flattening")


__all__ = ["RiskDecision", "RiskLimits", "RiskReason", "RiskState"]
