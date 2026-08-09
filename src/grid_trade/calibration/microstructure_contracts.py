from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from grid_trade.domain.numeric import deterministic_decimal_context


def _require_aware(timestamp: datetime, *, field: str) -> None:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError(f"{field} timestamp must be timezone-aware")


def _require_identity(value: str, *, field: str) -> None:
    if not value.strip():
        raise ValueError(f"{field} must be non-empty")


def _require_finite(value: Decimal, *, field: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{field} must be a finite Decimal")


def _require_positive(value: Decimal, *, field: str) -> None:
    _require_finite(value, field=field)
    if value <= 0:
        raise ValueError(f"{field} must be positive")


def _require_non_negative(value: Decimal, *, field: str) -> None:
    _require_finite(value, field=field)
    if value < 0:
        raise ValueError(f"{field} must be non-negative")


@dataclass(frozen=True, slots=True)
class IntensityBucket:
    distance_vol_units: Decimal
    exposure_seconds: Decimal
    arrival_count: int

    def __post_init__(self) -> None:
        _require_non_negative(self.distance_vol_units, field="distance_vol_units")
        _require_positive(self.exposure_seconds, field="exposure_seconds")
        if self.arrival_count < 0:
            raise ValueError("arrival_count must be non-negative")


@dataclass(frozen=True, slots=True)
class TopOfBookObservation:
    timestamp: datetime
    source_id: str
    instrument_id: str
    best_bid: Decimal
    bid_size: Decimal
    best_ask: Decimal
    ask_size: Decimal

    def __post_init__(self) -> None:
        _require_aware(self.timestamp, field="timestamp")
        _require_identity(self.source_id, field="source_id")
        _require_identity(self.instrument_id, field="instrument_id")
        _require_positive(self.best_bid, field="best_bid")
        _require_non_negative(self.bid_size, field="bid_size")
        _require_positive(self.best_ask, field="best_ask")
        _require_non_negative(self.ask_size, field="ask_size")
        if self.best_bid >= self.best_ask:
            raise ValueError("best_bid must be strictly below best_ask")
        with deterministic_decimal_context():
            if self.bid_size + self.ask_size <= 0:
                raise ValueError("top-of-book depth must be positive")

    @property
    def mid(self) -> Decimal:
        with deterministic_decimal_context():
            return (self.best_bid + self.best_ask) / Decimal(2)


class MarkoutSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True, slots=True)
class MaturedMarkout:
    fill_timestamp: datetime
    matured_at: datetime
    side: MarkoutSide
    fill_price: Decimal
    mark_price: Decimal

    def __post_init__(self) -> None:
        _require_aware(self.fill_timestamp, field="fill_timestamp")
        _require_aware(self.matured_at, field="matured_at")
        if self.matured_at < self.fill_timestamp:
            raise ValueError("matured_at must not precede fill_timestamp")
        _require_positive(self.fill_price, field="fill_price")
        _require_positive(self.mark_price, field="mark_price")


@dataclass(frozen=True, slots=True)
class OfiImpactSample:
    feature_timestamp: datetime
    matured_at: datetime
    normalized_ofi: Decimal
    relative_price_change: Decimal

    def __post_init__(self) -> None:
        _require_aware(self.feature_timestamp, field="feature_timestamp")
        _require_aware(self.matured_at, field="matured_at")
        if self.matured_at < self.feature_timestamp:
            raise ValueError("matured_at must not precede feature_timestamp")
        _require_finite(self.normalized_ofi, field="normalized_ofi")
        _require_finite(self.relative_price_change, field="relative_price_change")


@dataclass(frozen=True, slots=True)
class MicrostructureReadiness:
    ready: bool
    sample_count: int
    reason: str
    quality: Decimal | None

    def __post_init__(self) -> None:
        if self.sample_count < 0:
            raise ValueError("sample_count must be non-negative")
        if not self.reason.strip():
            raise ValueError("reason must be non-empty")
        if self.quality is not None:
            _require_finite(self.quality, field="quality")
            if not Decimal(0) <= self.quality <= Decimal(1):
                raise ValueError("quality must be within [0, 1]")


__all__ = [
    "IntensityBucket",
    "MarkoutSide",
    "MaturedMarkout",
    "MicrostructureReadiness",
    "OfiImpactSample",
    "TopOfBookObservation",
]
