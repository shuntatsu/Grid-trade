from datetime import UTC, datetime
from decimal import Decimal

from grid_trade.datasets.audit import (
    DatasetAuditExpectations,
    audit_canonical_dataset,
    audit_report_digest,
)
from grid_trade.datasets.canonical import (
    CanonicalBookLevel,
    CanonicalBookSnapshot,
    CanonicalEventEnvelope,
    CanonicalEventType,
    CanonicalTrade,
    TradeSide,
)
from grid_trade.datasets.contracts import (
    DatasetAcceptance,
    DatasetType,
    RawObjectIdentity,
    RawObjectRef,
    SourceFamily,
)

_BOOK_HASH = "a" * 64
_TRADE_HASH = "b" * 64


def _raw(dataset_type: DatasetType, digest: str) -> RawObjectRef:
    return RawObjectRef(
        identity=RawObjectIdentity(
            source_family=SourceFamily.ARCHIVE,
            dataset_type=dataset_type,
            instrument="BTC",
            sha256=digest,
        ),
        byte_length=10,
        acquired_at=datetime(2026, 8, 10, tzinfo=UTC),
        source_locator=f"fixture://audit/{dataset_type.value}",
        collector_schema_version="fixture-v1",
        decoder_schema_version="canonical-v1",
    )


def _book(
    timestamp_ns: int,
    *,
    bid: str = "99.0",
    ask: str = "101.0",
    ordinal: int = 0,
) -> CanonicalEventEnvelope:
    return CanonicalEventEnvelope(
        event_type=CanonicalEventType.BOOK_SNAPSHOT,
        instrument="BTC",
        exchange_ts_ns=timestamp_ns,
        local_receive_ts_ns=None,
        source_sequence=None,
        raw_object_sha256=_BOOK_HASH,
        raw_record_ordinal=ordinal,
        normalization_schema_version="canonical-v1",
        payload=CanonicalBookSnapshot(
            bids=(CanonicalBookLevel(Decimal(bid), Decimal("1.00"), 1),),
            asks=(CanonicalBookLevel(Decimal(ask), Decimal("1.00"), 1),),
        ),
    )


def _trade(
    timestamp_ns: int,
    *,
    price: str = "100.0",
    quantity: str = "0.25",
    ordinal: int = 0,
) -> CanonicalEventEnvelope:
    return CanonicalEventEnvelope(
        event_type=CanonicalEventType.TRADE,
        instrument="BTC",
        exchange_ts_ns=timestamp_ns,
        local_receive_ts_ns=None,
        source_sequence=None,
        raw_object_sha256=_TRADE_HASH,
        raw_record_ordinal=ordinal,
        normalization_schema_version="canonical-v1",
        payload=CanonicalTrade(
            side=TradeSide.BUY,
            price=Decimal(price),
            quantity=Decimal(quantity),
            stable_identity=f"trade-{ordinal}",
        ),
    )


def _raw_objects() -> tuple[RawObjectRef, ...]:
    return (
        _raw(DatasetType.L2_BOOK, _BOOK_HASH),
        _raw(DatasetType.TRADES, _TRADE_HASH),
    )


def test_requested_coverage_is_bound_to_audit_and_missing_tail_is_rejected() -> None:
    events = (_book(100), _trade(150), _book(200, ordinal=1))
    expectations = DatasetAuditExpectations(requested_start_ns=100, requested_end_ns=300)

    report = audit_canonical_dataset(
        events,
        raw_objects=_raw_objects(),
        expectations=expectations,
    )

    assert report.acceptance is DatasetAcceptance.REJECTED
    assert report.requested_start_ns == 100
    assert report.requested_end_ns == 300
    assert report.observed_start_ns == 100
    assert report.observed_end_ns == 200
    assert any(finding.code == "requested_coverage_missing" for finding in report.findings)


def test_declared_tick_and_lot_alignment_fail_closed() -> None:
    events = (_book(100, bid="99.05", ask="101.05"), _trade(150, quantity="0.015"))
    expectations = DatasetAuditExpectations(
        tick_size=Decimal("0.1"),
        lot_size=Decimal("0.01"),
    )

    report = audit_canonical_dataset(
        events,
        raw_objects=_raw_objects(),
        expectations=expectations,
    )

    assert report.acceptance is DatasetAcceptance.REJECTED
    assert any(finding.code == "tick_alignment_violation" for finding in report.findings)
    assert any(finding.code == "lot_alignment_violation" for finding in report.findings)


def test_book_trade_overlap_requirement_rejects_disjoint_sources() -> None:
    events = (
        _book(100, ordinal=0),
        _book(200, ordinal=1),
        _trade(300, ordinal=0),
        _trade(400, ordinal=1),
    )

    report = audit_canonical_dataset(
        events,
        raw_objects=_raw_objects(),
        expectations=DatasetAuditExpectations(require_book_trade_overlap=True),
    )

    assert report.acceptance is DatasetAcceptance.REJECTED
    assert report.book_start_ns == 100
    assert report.book_end_ns == 200
    assert report.trade_start_ns == 300
    assert report.trade_end_ns == 400
    assert report.book_trade_overlap_ns == 0
    assert any(finding.code == "book_trade_overlap_missing" for finding in report.findings)


def test_gap_statistics_are_deterministic_and_part_of_audit_identity() -> None:
    events = (
        _book(100, ordinal=0),
        _trade(110, ordinal=0),
        _book(210, ordinal=1),
        _trade(410, ordinal=1),
    )

    report = audit_canonical_dataset(events, raw_objects=_raw_objects())
    changed_expectation = audit_canonical_dataset(
        events,
        raw_objects=_raw_objects(),
        expectations=DatasetAuditExpectations(
            requested_start_ns=100,
            requested_end_ns=410,
        ),
    )

    assert report.max_exchange_gap_ns == 200
    assert report.p95_exchange_gap_ns == 200
    assert audit_report_digest(report) != audit_report_digest(changed_expectation)
