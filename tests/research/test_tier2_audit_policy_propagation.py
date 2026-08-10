from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import pytest

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
)
from grid_trade.datasets.contracts import (
    DatasetType,
    RawObjectIdentity,
    RawObjectRef,
    SourceFamily,
)
from grid_trade.datasets.manifest import DatasetManifest
from grid_trade.research import tier2_calibrated_replay as calibrated_replay
from grid_trade.research.tier2_calibrated_candidate import (
    Tier2CalibratedCandidateConfig,
    derive_tier2_calibrated_candidate,
)

pytestmark = pytest.mark.research

_RAW_HASH = "a" * 64


def _raw_ref() -> RawObjectRef:
    return RawObjectRef(
        identity=RawObjectIdentity(
            source_family=SourceFamily.ARCHIVE,
            dataset_type=DatasetType.L2_BOOK,
            instrument="BTC",
            sha256=_RAW_HASH,
        ),
        byte_length=100,
        acquired_at=datetime(2026, 8, 10, tzinfo=UTC),
        source_locator="fixture://tier2/audit-policy",
        collector_schema_version="fixture-v1",
        decoder_schema_version="canonical-v1",
    )


def _book(timestamp_ns: int, ordinal: int) -> CanonicalEventEnvelope:
    return CanonicalEventEnvelope(
        event_type=CanonicalEventType.BOOK_SNAPSHOT,
        instrument="BTC",
        exchange_ts_ns=timestamp_ns,
        local_receive_ts_ns=None,
        source_sequence=None,
        raw_object_sha256=_RAW_HASH,
        raw_record_ordinal=ordinal,
        normalization_schema_version="canonical-v1",
        payload=CanonicalBookSnapshot(
            bids=(CanonicalBookLevel(Decimal("99.0"), Decimal("1.00"), 1),),
            asks=(CanonicalBookLevel(Decimal("101.0"), Decimal("1.00"), 1),),
        ),
    )


def _manifest(
    events: tuple[CanonicalEventEnvelope, ...],
    expectations: DatasetAuditExpectations,
) -> DatasetManifest:
    raw_objects = (_raw_ref(),)
    report = audit_canonical_dataset(
        events,
        raw_objects=raw_objects,
        expected_normalization_schema_version="canonical-v1",
        expectations=expectations,
    )
    return DatasetManifest(
        instrument="BTC",
        raw_objects=raw_objects,
        normalization_schema_version="canonical-v1",
        ordering_schema_version="ordering-v1",
        audit_schema_version="audit-v1",
        acceptance=report.acceptance,
        created_at=datetime(2026, 8, 10, tzinfo=UTC),
        audit_digest=audit_report_digest(report),
        audit_expectations=expectations,
    )


def test_calibrated_candidate_reaudit_preserves_manifest_expectations() -> None:
    events = (_book(100, 0), _book(200, 1))
    expectations = DatasetAuditExpectations(
        requested_start_ns=100,
        requested_end_ns=200,
        tick_size=Decimal("0.1"),
        lot_size=Decimal("0.01"),
    )
    dataset = _manifest(events, expectations)

    with pytest.raises(ValueError, match="no canonical events"):
        derive_tier2_calibrated_candidate(
            dataset=dataset,
            events=events,
            evidence_frames=(),
            decision_exchange_ts_ns=50,
            config=cast(Tier2CalibratedCandidateConfig, object()),
            equity=Decimal("100"),
            starting_position=Decimal(0),
        )


def test_replay_subset_rescopes_requested_coverage_and_preserves_steps() -> None:
    events = (_book(100, 0), _book(200, 1), _book(300, 2))
    expectations = DatasetAuditExpectations(
        requested_start_ns=100,
        requested_end_ns=300,
        tick_size=Decimal("0.1"),
        lot_size=Decimal("0.01"),
    )
    dataset = _manifest(events, expectations)

    replay_dataset = calibrated_replay._replay_dataset_manifest(dataset, events[1:])

    assert replay_dataset.audit_expectations == DatasetAuditExpectations(
        requested_start_ns=200,
        requested_end_ns=300,
        tick_size=Decimal("0.1"),
        lot_size=Decimal("0.01"),
    )
    assert replay_dataset.required_funding_timestamps_ns == ()
    assert replay_dataset.audit_digest != dataset.audit_digest
