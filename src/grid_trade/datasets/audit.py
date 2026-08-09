import json
from dataclasses import dataclass, fields, is_dataclass
from decimal import Decimal
from enum import Enum, StrEnum
from hashlib import sha256
from itertools import pairwise
from typing import Any

from grid_trade.datasets.canonical import (
    CanonicalEventEnvelope,
    CanonicalEventType,
    CanonicalFundingReference,
    CanonicalTrade,
    TradeSide,
    canonical_event_sort_key,
)
from grid_trade.datasets.contracts import DatasetAcceptance, RawObjectRef
from grid_trade.datasets.manifest import DatasetManifest


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
    required_funding_timestamps_ns: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        for value in (
            self.event_count,
            self.exact_duplicate_count,
            self.conflicting_duplicate_count,
        ):
            if value < 0:
                raise ValueError("audit counters must be non-negative")
        if any(timestamp < 0 for timestamp in self.required_funding_timestamps_ns):
            raise ValueError("required funding timestamps must be non-negative")
        if self.required_funding_timestamps_ns != tuple(
            sorted(set(self.required_funding_timestamps_ns))
        ):
            raise ValueError("required funding timestamps must be sorted and unique")


def _canonical_value(value: object) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _canonical_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple | list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"unsupported audit value: {type(value).__name__}")


def _trade_identity(event: CanonicalEventEnvelope, trade: CanonicalTrade) -> tuple[str, str]:
    return (event.instrument, trade.stable_identity)


def _trade_fingerprint(
    event: CanonicalEventEnvelope,
    trade: CanonicalTrade,
) -> tuple[int, TradeSide, Decimal, Decimal]:
    return (
        event.exchange_ts_ns,
        trade.side,
        trade.price,
        trade.quantity,
    )


def _acceptance(findings: tuple[AuditFinding, ...]) -> DatasetAcceptance:
    if any(finding.severity is AuditSeverity.ERROR for finding in findings):
        return DatasetAcceptance.REJECTED
    if any(finding.severity is AuditSeverity.WARNING for finding in findings):
        return DatasetAcceptance.ACCEPTED_WITH_WARNINGS
    return DatasetAcceptance.ACCEPTED


def audit_canonical_dataset(
    events: tuple[CanonicalEventEnvelope, ...],
    *,
    raw_objects: tuple[RawObjectRef, ...],
    required_funding_timestamps_ns: tuple[int, ...] = (),
    warning_gap_ns: int | None = None,
    expected_normalization_schema_version: str | None = None,
) -> DatasetAuditReport:
    if warning_gap_ns is not None and warning_gap_ns <= 0:
        raise ValueError("warning_gap_ns must be positive")
    if any(timestamp < 0 for timestamp in required_funding_timestamps_ns):
        raise ValueError("required funding timestamps must be non-negative")
    normalized_required_funding = tuple(sorted(set(required_funding_timestamps_ns)))

    findings: list[AuditFinding] = []
    accepted_events: list[CanonicalEventEnvelope] = []
    exact_duplicate_count = 0
    conflicting_duplicate_count = 0

    if not events:
        findings.append(
            AuditFinding(
                code="empty_dataset",
                severity=AuditSeverity.ERROR,
                message="canonical dataset contains no events",
            )
        )

    if events != tuple(sorted(events, key=canonical_event_sort_key)):
        findings.append(
            AuditFinding(
                code="non_canonical_order",
                severity=AuditSeverity.ERROR,
                message="events are not in canonical deterministic order",
            )
        )

    available_raw_hashes = {raw.identity.sha256 for raw in raw_objects if raw.complete}
    seen_trades: dict[tuple[str, str], tuple[int, TradeSide, Decimal, Decimal]] = {}

    for index, event in enumerate(events):
        if event.raw_object_sha256 not in available_raw_hashes:
            findings.append(
                AuditFinding(
                    code="unresolved_raw_object",
                    severity=AuditSeverity.ERROR,
                    message="event raw-object SHA-256 is absent from complete raw inputs",
                    event_index=index,
                    exchange_ts_ns=event.exchange_ts_ns,
                )
            )
        if (
            expected_normalization_schema_version is not None
            and event.normalization_schema_version != expected_normalization_schema_version
        ):
            findings.append(
                AuditFinding(
                    code="normalization_schema_mismatch",
                    severity=AuditSeverity.ERROR,
                    message="event normalization schema does not match the requested schema",
                    event_index=index,
                    exchange_ts_ns=event.exchange_ts_ns,
                )
            )

        if event.event_type is CanonicalEventType.TRADE:
            trade = event.payload
            if not isinstance(trade, CanonicalTrade):
                raise TypeError("validated trade event must carry CanonicalTrade payload")
            identity = _trade_identity(event, trade)
            fingerprint = _trade_fingerprint(event, trade)
            previous_fingerprint = seen_trades.get(identity)
            if previous_fingerprint is not None:
                if previous_fingerprint == fingerprint:
                    exact_duplicate_count += 1
                else:
                    conflicting_duplicate_count += 1
                    findings.append(
                        AuditFinding(
                            code="conflicting_trade_identity",
                            severity=AuditSeverity.ERROR,
                            message="stable trade identity maps to conflicting economic payloads",
                            event_index=index,
                            exchange_ts_ns=event.exchange_ts_ns,
                        )
                    )
                continue
            seen_trades[identity] = fingerprint

        accepted_events.append(event)

    funding_timestamps = {
        event.exchange_ts_ns
        for event in accepted_events
        if event.event_type is CanonicalEventType.FUNDING_REFERENCE
        and isinstance(event.payload, CanonicalFundingReference)
        and event.payload.funding_rate is not None
        and event.payload.oracle_price is not None
    }
    for timestamp in normalized_required_funding:
        if timestamp not in funding_timestamps:
            findings.append(
                AuditFinding(
                    code="missing_funding_reference",
                    severity=AuditSeverity.ERROR,
                    message="required funding boundary lacks complete funding/reference state",
                    exchange_ts_ns=timestamp,
                )
            )

    if warning_gap_ns is not None:
        for previous_event, current_event in pairwise(accepted_events):
            gap_ns = current_event.exchange_ts_ns - previous_event.exchange_ts_ns
            if gap_ns > warning_gap_ns:
                findings.append(
                    AuditFinding(
                        code="large_exchange_gap",
                        severity=AuditSeverity.WARNING,
                        message=f"exchange timestamp gap {gap_ns}ns exceeds warning threshold",
                        exchange_ts_ns=current_event.exchange_ts_ns,
                    )
                )

    frozen_findings = tuple(findings)
    return DatasetAuditReport(
        acceptance=_acceptance(frozen_findings),
        findings=frozen_findings,
        accepted_events=tuple(accepted_events),
        event_count=len(events),
        exact_duplicate_count=exact_duplicate_count,
        conflicting_duplicate_count=conflicting_duplicate_count,
        required_funding_timestamps_ns=normalized_required_funding,
    )


def audit_report_digest(report: DatasetAuditReport) -> str:
    payload = json.dumps(
        _canonical_value(report),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return sha256(f"{payload}\n".encode()).hexdigest()


def require_promoting_dataset(dataset_manifest: DatasetManifest) -> None:
    if dataset_manifest.acceptance is not DatasetAcceptance.ACCEPTED:
        raise ValueError("promoting replay requires DatasetAcceptance.ACCEPTED")
    if dataset_manifest.audit_digest is None:
        raise ValueError("promoting replay requires an audit_digest")


__all__ = [
    "AuditFinding",
    "AuditSeverity",
    "DatasetAuditReport",
    "audit_canonical_dataset",
    "audit_report_digest",
    "require_promoting_dataset",
]
