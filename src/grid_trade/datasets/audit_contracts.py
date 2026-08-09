from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class DatasetAuditExpectations:
    requested_start_ns: int | None = None
    requested_end_ns: int | None = None
    tick_size: Decimal | None = None
    lot_size: Decimal | None = None
    require_book_trade_overlap: bool = False

    def __post_init__(self) -> None:
        for field_name, value in (
            ("requested_start_ns", self.requested_start_ns),
            ("requested_end_ns", self.requested_end_ns),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{field_name} must be non-negative")
        if (
            self.requested_start_ns is not None
            and self.requested_end_ns is not None
            and self.requested_start_ns > self.requested_end_ns
        ):
            raise ValueError("requested coverage range must not be reversed")
        for field_name, value in (("tick_size", self.tick_size), ("lot_size", self.lot_size)):
            if value is not None and (not value.is_finite() or value <= 0):
                raise ValueError(f"{field_name} must be finite and positive")


__all__ = ["DatasetAuditExpectations"]
