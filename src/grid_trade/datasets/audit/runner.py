from decimal import Decimal
from itertools import pairwise

from grid_trade.datasets.audit.models import AuditFinding, AuditSeverity, DatasetAuditReport
from grid_trade.datasets.audit.quality import (
    alignment_findings,
    book_trade_overlap_ns,
    event_range,
    gap_statistics,
)
from grid_trade.datasets.audit_contracts import DatasetAuditExpectations
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
from grid_trade.serialization import canonical_json_digest


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
    expectations: DatasetAuditExpectations | None = None,
) -> DatasetAuditReport:
    if warning_gap_ns is not None and warning_gap_ns <= 0:
        raise ValueError("warning_gap_ns must be positive")
    if any(timestamp < 0 for timestamp in required_funding_timestamps_ns):
        raise ValueError("required funding timestamps must be non-negative")
    normalized_required_funding = tuple(sorted(set(required_funding_timestamps_ns)))
    audit_expectations = expectations or DatasetAuditExpectations()

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

    frozen_accepted_events = tuple(accepted_events)
    findings.extend(alignment_findings(frozen_accepted_events, audit_expectations))

    observed_start_ns, observed_end_ns = event_range(frozen_accepted_events)
    book_start_ns, book_end_ns = event_range(
        frozen_accepted_events,
        CanonicalEventType.BOOK_SNAPSHOT,
    )
    trade_start_ns, trade_end_ns = event_range(
        frozen_accepted_events,
        CanonicalEventType.TRADE,
    )
    overlap_ns = book_trade_overlap_ns(
        book_start_ns=book_start_ns,
        book_end_ns=book_end_ns,
        trade_start_ns=trade_start_ns,
        trade_end_ns=trade_end_ns,
    )
    max_gap_ns, p95_gap_ns = gap_statistics(frozen_accepted_events)

    if (
        audit_expectations.requested_start_ns is not None
        and (observed_start_ns is None or observed_start_ns > audit_expectations.requested_start_ns)
    ) or (
        audit_expectations.requested_end_ns is not None
        and (observed_end_ns is None or observed_end_ns < audit_expectations.requested_end_ns)
    ):
        findings.append(
            AuditFinding(
                code="requested_coverage_missing",
                severity=AuditSeverity.ERROR,
                message="observed event coverage does not span the requested dataset range",
            )
        )

    if audit_expectations.require_book_trade_overlap and overlap_ns <= 0:
        findings.append(
            AuditFinding(
                code="book_trade_overlap_missing",
                severity=AuditSeverity.ERROR,
                message="book and trade observations do not have positive temporal overlap",
            )
        )

    funding_timestamps = {
        event.exchange_ts_ns
        for event in frozen_accepted_events
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
        for previous_event, current_event in pairwise(frozen_accepted_events):
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
        accepted_events=frozen_accepted_events,
        event_count=len(events),
        exact_duplicate_count=exact_duplicate_count,
        conflicting_duplicate_count=conflicting_duplicate_count,
        expectations=audit_expectations,
        observed_start_ns=observed_start_ns,
        observed_end_ns=observed_end_ns,
        book_start_ns=book_start_ns,
        book_end_ns=book_end_ns,
        trade_start_ns=trade_start_ns,
        trade_end_ns=trade_end_ns,
        book_trade_overlap_ns=overlap_ns,
        max_exchange_gap_ns=max_gap_ns,
        p95_exchange_gap_ns=p95_gap_ns,
        required_funding_timestamps_ns=normalized_required_funding,
    )


def audit_report_digest(report: DatasetAuditReport) -> str:
    return canonical_json_digest(report)


def require_promoting_dataset(dataset_manifest: DatasetManifest) -> None:
    if dataset_manifest.acceptance is not DatasetAcceptance.ACCEPTED:
        raise ValueError("promoting replay requires DatasetAcceptance.ACCEPTED")
    if dataset_manifest.audit_digest is None:
        raise ValueError("promoting replay requires an audit_digest")


__all__ = ["audit_canonical_dataset", "audit_report_digest", "require_promoting_dataset"]
