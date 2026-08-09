from dataclasses import replace
from decimal import Decimal

import pytest

from grid_trade.datasets.audit import audit_canonical_dataset, audit_report_digest
from grid_trade.datasets.canonical import (
    CanonicalEventEnvelope,
    CanonicalEventType,
    CanonicalTrade,
    TradeSide,
)
from grid_trade.research.tier2_fixture_runner import Tier2FixtureCase, build_tier2_fixture_case
from grid_trade.research.tier2_replay import Tier2ReplayManifest

pytestmark = pytest.mark.research


def _future_trade(*, quantity: str) -> CanonicalEventEnvelope:
    return CanonicalEventEnvelope(
        event_type=CanonicalEventType.TRADE,
        instrument="BTC",
        exchange_ts_ns=3_600_000_000_000 + 1_000_000_000,
        local_receive_ts_ns=None,
        source_sequence=None,
        raw_object_sha256="b" * 64,
        raw_record_ordinal=99,
        normalization_schema_version="canonical-v1",
        payload=CanonicalTrade(
            side=TradeSide.BUY,
            price=Decimal("100"),
            quantity=Decimal(quantity),
            stable_identity="future-causality-trade",
        ),
    )


def _manifest_for_events(
    case: Tier2FixtureCase,
    events: tuple[CanonicalEventEnvelope, ...],
) -> Tier2ReplayManifest:
    report = audit_canonical_dataset(
        events,
        raw_objects=case.manifest.dataset.raw_objects,
        expected_normalization_schema_version=case.manifest.dataset.normalization_schema_version,
    )
    dataset = replace(
        case.manifest.dataset,
        acceptance=report.acceptance,
        audit_digest=audit_report_digest(report),
    )
    return replace(case.manifest, dataset=dataset)


def test_future_event_change_does_not_change_decision_digest() -> None:
    case = build_tier2_fixture_case()
    first_events = (*case.events, _future_trade(quantity="0.10"))
    second_events = (*case.events, _future_trade(quantity="0.90"))

    first = replace(
        case, manifest=_manifest_for_events(case, first_events), events=first_events
    ).run()
    second = replace(
        case, manifest=_manifest_for_events(case, second_events), events=second_events
    ).run()

    assert first.decision_digest == second.decision_digest
    assert first.evidence_digest != second.evidence_digest


def test_replay_rejects_events_that_do_not_match_manifest_audit_digest() -> None:
    case = build_tier2_fixture_case()
    tampered_events = (*case.events, _future_trade(quantity="0.90"))

    with pytest.raises(ValueError, match="audit_digest"):
        replace(case, events=tampered_events).run()
