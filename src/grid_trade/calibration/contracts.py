from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class CalibrationReadiness(StrEnum):
    NOT_READY = "not_ready"
    PARTIAL = "partial"
    READY = "ready"


def _require_aware_timestamp(timestamp: datetime) -> None:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")


def _require_identity(value: str, *, field: str) -> None:
    if not value.strip():
        raise ValueError(f"{field} must be non-empty")


def _require_decimal(value: Decimal, *, field: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{field} must be a finite Decimal")


def _require_optional_decimal(value: Decimal | None, *, field: str) -> None:
    if value is not None:
        _require_decimal(value, field=field)


def _require_optional_score(value: Decimal | None, *, field: str) -> None:
    _require_optional_decimal(value, field=field)
    if value is not None and not Decimal("-1") <= value <= Decimal("1"):
        raise ValueError(f"{field} must be within [-1, 1]")


@dataclass(frozen=True, slots=True)
class CalibrationObservation:
    timestamp: datetime
    source_id: str
    instrument_id: str
    mid: Decimal
    funding_rate: Decimal | None

    def __post_init__(self) -> None:
        _require_aware_timestamp(self.timestamp)
        _require_identity(self.source_id, field="source_id")
        _require_identity(self.instrument_id, field="instrument_id")
        _require_decimal(self.mid, field="mid")
        if self.mid <= 0:
            raise ValueError("mid must be positive")
        _require_optional_decimal(self.funding_rate, field="funding_rate")


@dataclass(frozen=True, slots=True)
class CalibrationComponentStatus:
    ready: bool
    sample_count: int
    reason: str

    def __post_init__(self) -> None:
        if self.sample_count < 0:
            raise ValueError("sample_count must be non-negative")
        if not self.reason.strip():
            raise ValueError("reason must be non-empty")


@dataclass(frozen=True, slots=True)
class CalibratedMarketState:
    timestamp: datetime
    source_id: str
    instrument_id: str
    readiness: CalibrationReadiness
    volatility_scale: Decimal | None
    trend_score: Decimal | None
    funding_score: Decimal | None
    quote_distance_scale: Decimal | None
    execution_cost_floor: Decimal | None
    order_book_score: Decimal | None
    estimated_microprice_displacement: Decimal | None
    volatility_status: CalibrationComponentStatus
    trend_status: CalibrationComponentStatus
    funding_status: CalibrationComponentStatus
    microstructure_status: CalibrationComponentStatus

    def __post_init__(self) -> None:
        _require_aware_timestamp(self.timestamp)
        _require_identity(self.source_id, field="source_id")
        _require_identity(self.instrument_id, field="instrument_id")
        _require_optional_decimal(self.volatility_scale, field="volatility_scale")
        _require_optional_score(self.trend_score, field="trend_score")
        _require_optional_score(self.funding_score, field="funding_score")
        _require_optional_decimal(self.quote_distance_scale, field="quote_distance_scale")
        _require_optional_decimal(self.execution_cost_floor, field="execution_cost_floor")
        _require_optional_score(self.order_book_score, field="order_book_score")
        _require_optional_decimal(
            self.estimated_microprice_displacement,
            field="estimated_microprice_displacement",
        )

        for field_name in ("volatility_scale", "quote_distance_scale", "execution_cost_floor"):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise ValueError(f"{field_name} must be non-negative")

        if self.readiness is CalibrationReadiness.READY:
            if not self.volatility_status.ready or not self.trend_status.ready:
                raise ValueError("ready state requires ready volatility and trend components")
            if self.volatility_scale is None or self.trend_score is None:
                raise ValueError("ready state requires volatility_scale and trend_score")

    @classmethod
    def not_ready(
        cls,
        *,
        timestamp: datetime,
        source_id: str,
        instrument_id: str,
    ) -> "CalibratedMarketState":
        unavailable = CalibrationComponentStatus(
            ready=False,
            sample_count=0,
            reason="not_ready",
        )
        return cls(
            timestamp=timestamp,
            source_id=source_id,
            instrument_id=instrument_id,
            readiness=CalibrationReadiness.NOT_READY,
            volatility_scale=None,
            trend_score=None,
            funding_score=None,
            quote_distance_scale=None,
            execution_cost_floor=None,
            order_book_score=None,
            estimated_microprice_displacement=None,
            volatility_status=unavailable,
            trend_status=unavailable,
            funding_status=unavailable,
            microstructure_status=unavailable,
        )


__all__ = [
    "CalibratedMarketState",
    "CalibrationComponentStatus",
    "CalibrationObservation",
    "CalibrationReadiness",
]
