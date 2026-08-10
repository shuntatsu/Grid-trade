from dataclasses import dataclass, field
from enum import StrEnum

from grid_trade.datasets.audit_contracts import DatasetAuditExpectations
from grid_trade.datasets.canonical import CanonicalEventEnvelope
from grid_trade.datasets.contracts import DatasetAcceptance


class AuditSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class AuditFinding:
    code: str
    severity: AuditSeverity
    message: str
    event_index: int | None = None
    exchange_ts_ns: int | None = None

    def __post_init__(self) -> None:
        if not self.code or not self.code.strip():
            raise ValueError("finding code must be non-empty")
        if not self.message or not self.message.strip():
            raise ValueError("finding message must be non-empty")
        if self.event_index is not None and self.event_index < 0:
            raise ValueError("event_index must be non-negative")
        if self.exchange_ts_ns is not None and self.exchange_ts_ns < 0:
            raise ValueError("exchange_ts_ns must be non-negative")


@dataclass(frozen=True, slots=True)
class DatasetAuditReport:
    acceptance: DatasetAcceptance
    findings: tuple[AuditFinding, ...]
    accepted_events: tuple[CanonicalEventEnvelope, ...]
    event_count: int
    exact_duplicate_count: int
    conflicting_duplicate_count: int
    expectations: DatasetAuditExpectations = field(default_factory=DatasetAuditExpectations)
    observed_start_ns: int | None = None
    observed_end_ns: int | None = None
    book_start_ns: int | None = None
    book_end_ns: int | None = None
    trade_start_ns: int | None = None
    trade_end_ns: int | None = None
    book_trade_overlap_ns: int = 0
    max_exchange_gap_ns: int = 0
    p95_exchange_gap_ns: int = 0
    required_funding_timestamps_ns: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        for counter in (
            self.event_count,
            self.exact_duplicate_count,
            self.conflicting_duplicate_count,
            self.book_trade_overlap_ns,
            self.max_exchange_gap_ns,
            self.p95_exchange_gap_ns,
        ):
            if counter < 0:
                raise ValueError("audit counters and statistics must be non-negative")
        for timestamp_ns in (
            self.observed_start_ns,
            self.observed_end_ns,
            self.book_start_ns,
            self.book_end_ns,
            self.trade_start_ns,
            self.trade_end_ns,
        ):
            if timestamp_ns is not None and timestamp_ns < 0:
                raise ValueError("audit timestamps must be non-negative")
        if any(timestamp < 0 for timestamp in self.required_funding_timestamps_ns):
            raise ValueError("required funding timestamps must be non-negative")
        if self.required_funding_timestamps_ns != tuple(
            sorted(set(self.required_funding_timestamps_ns))
        ):
            raise ValueError("required funding timestamps must be sorted and unique")

    @property
    def requested_start_ns(self) -> int | None:
        return self.expectations.requested_start_ns

    @property
    def requested_end_ns(self) -> int | None:
        return self.expectations.requested_end_ns


__all__ = ["AuditFinding", "AuditSeverity", "DatasetAuditReport"]
