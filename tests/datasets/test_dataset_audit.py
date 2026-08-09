from datetime import UTC, datetime
from decimal import Decimal

import pytest

from grid_trade.datasets import audit, canonical, contracts, manifest

_RAW_HASH = "a" * 64


def _raw_ref(*, digest: str = _RAW_HASH) -> contracts.RawObjectRef:
    return contracts.RawObjectRef(
        identity=contracts.RawObjectIdentity(
            source_family=contracts.SourceFamily.ARCHIVE,
            dataset_type=contracts.DatasetType.L2_BOOK,
            instrument="BTC",
            sha256=digest,
        ),
        byte_length=100,
        acquired_at=datetime(2026, 8, 10, tzinfo=UTC),
        source_locator="s3://hyperliquid-archive/example",
        collector_schema_version="hl-archive-v1",
        decoder_schema_version="canonical-v1",
    )


def _trade(
    *,
    timestamp_ns: int,
    stable_identity: str,
    price: str = "100",
    digest: str = _RAW_HASH,
    ordinal: int = 0,
) -> canonical.CanonicalEventEnvelope:
    return canonical.CanonicalEventEnvelope(
        event_type=canonical.CanonicalEventType.TRADE,
        instrument="BTC",
        exchange_ts_ns=timestamp_ns,
        local_receive_ts_ns=None,
        source_sequence=None,
        raw_object_sha256=digest,
        raw_record_ordinal=ordinal,
        normalization_schema_version="canonical-v1",
        payload=canonical.CanonicalTrade(
            side=canonical.TradeSide.BUY,
            price=Decimal(price),
            quantity=Decimal("1"),
            stable_identity=stable_identity,
        ),
    )


def _funding(timestamp_ns: int) -> canonical.CanonicalEventEnvelope:
    return canonical.CanonicalEventEnvelope(
        event_type=canonical.CanonicalEventType.FUNDING_REFERENCE,
        instrument="BTC",
        exchange_ts_ns=timestamp_ns,
        local_receive_ts_ns=None,
        source_sequence=None,
        raw_object_sha256=_RAW_HASH,
        raw_record_ordinal=9,
        normalization_schema_version="canonical-v1",
        payload=canonical.CanonicalFundingReference(
            funding_rate=Decimal("0.0001"),
            mark_price=Decimal("100"),
            oracle_price=Decimal("100.1"),
        ),
    )


def _book(timestamp_ns: int) -> canonical.CanonicalEventEnvelope:
    return canonical.CanonicalEventEnvelope(
        event_type=canonical.CanonicalEventType.BOOK_SNAPSHOT,
        instrument="BTC",
        exchange_ts_ns=timestamp_ns,
        local_receive_ts_ns=None,
        source_sequence=None,
        raw_object_sha256=_RAW_HASH,
        raw_record_ordinal=1,
        normalization_schema_version="canonical-v1",
        payload=canonical.CanonicalBookSnapshot(
            bids=(
                canonical.CanonicalBookLevel(Decimal("99"), Decimal("1"), 1),
            ),
            asks=(
                canonical.CanonicalBookLevel(Decimal("101"), Decimal("1"), 1),
            ),
        ),
    )


def test_conflicting_duplicate_trade_identity_is_rejected() -> None:
    report = audit.audit_canonical_dataset(
        (
            _trade(timestamp_ns=100, stable_identity="tid-1", price="100", ordinal=0),
            _trade(timestamp_ns=100, stable_identity="tid-1", price="101", ordinal=1),
        ),
        raw_objects=(_raw_ref(),),
    )

    assert report.acceptance is contracts.DatasetAcceptance.REJECTED
    assert report.conflicting_duplicate_count == 1
    assert any(
        finding.code == "conflicting_trade_identity"
        and finding.severity is audit.AuditSeverity.ERROR
        for finding in report.findings
    )


def test_exact_duplicate_trade_is_counted_and_deduplicated() -> None:
    first = _trade(timestamp_ns=100, stable_identity="tid-1", ordinal=0)
    duplicate = _trade(timestamp_ns=100, stable_identity="tid-1", ordinal=1)

    report = audit.audit_canonical_dataset(
        (first, duplicate),
        raw_objects=(_raw_ref(),),
    )

    assert report.acceptance is contracts.DatasetAcceptance.ACCEPTED
    assert report.exact_duplicate_count == 1
    assert report.accepted_events == (first,)


def test_missing_required_funding_boundary_is_rejected() -> None:
    report = audit.audit_canonical_dataset(
        (_book(100), _funding(200)),
        raw_objects=(_raw_ref(),),
        required_funding_timestamps_ns=(200, 300),
    )

    assert report.acceptance is contracts.DatasetAcceptance.REJECTED
    assert any(finding.code == "missing_funding_reference" for finding in report.findings)


def test_unresolved_raw_object_hash_is_rejected() -> None:
    report = audit.audit_canonical_dataset(
        (_trade(timestamp_ns=100, stable_identity="tid-1", digest="b" * 64),),
        raw_objects=(_raw_ref(),),
    )

    assert report.acceptance is contracts.DatasetAcceptance.REJECTED
    assert any(finding.code == "unresolved_raw_object" for finding in report.findings)


def test_large_exchange_gap_can_be_warning_only_and_non_promoting() -> None:
    report = audit.audit_canonical_dataset(
        (
            _trade(timestamp_ns=100, stable_identity="tid-1", ordinal=0),
            _trade(timestamp_ns=1_000, stable_identity="tid-2", ordinal=1),
        ),
        raw_objects=(_raw_ref(),),
        warning_gap_ns=100,
    )

    assert report.acceptance is contracts.DatasetAcceptance.ACCEPTED_WITH_WARNINGS
    assert any(finding.code == "large_exchange_gap" for finding in report.findings)

    dataset_manifest = manifest.DatasetManifest(
        instrument="BTC",
        raw_objects=(_raw_ref(),),
        normalization_schema_version="canonical-v1",
        ordering_schema_version="ordering-v1",
        audit_schema_version="audit-v1",
        acceptance=report.acceptance,
        created_at=datetime(2026, 8, 10, tzinfo=UTC),
        audit_digest=audit.audit_report_digest(report),
    )
    with pytest.raises(ValueError, match="ACCEPTED"):
        audit.require_promoting_dataset(dataset_manifest)


def test_audit_digest_is_deterministic_and_promoting_manifest_requires_accepted() -> None:
    report = audit.audit_canonical_dataset(
        (_trade(timestamp_ns=100, stable_identity="tid-1"),),
        raw_objects=(_raw_ref(),),
    )

    assert audit.audit_report_digest(report) == audit.audit_report_digest(report)
    dataset_manifest = manifest.DatasetManifest(
        instrument="BTC",
        raw_objects=(_raw_ref(),),
        normalization_schema_version="canonical-v1",
        ordering_schema_version="ordering-v1",
        audit_schema_version="audit-v1",
        acceptance=report.acceptance,
        created_at=datetime(2026, 8, 10, tzinfo=UTC),
        audit_digest=audit.audit_report_digest(report),
    )

    audit.require_promoting_dataset(dataset_manifest)
