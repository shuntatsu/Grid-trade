import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from grid_trade.evidence.events import EvidenceEvent, EvidenceKind, PnLBreakdown
from grid_trade.evidence.ledger import canonical_jsonl, evidence_digest

_NOW = datetime(2026, 8, 9, 7, 0, tzinfo=UTC)


def _event(
    *,
    sequence: int = 0,
    timestamp: datetime = _NOW,
    payload: dict[str, object] | None = None,
) -> EvidenceEvent:
    return EvidenceEvent.create(
        run_id="s0-fixture",
        sequence=sequence,
        timestamp=timestamp,
        kind=EvidenceKind.MARKET_SNAPSHOT,
        payload={} if payload is None else payload,
    )


def test_logically_identical_payloads_have_identical_bytes_and_digest() -> None:
    left = _event(
        payload={
            "price": Decimal("100.00"),
            "nested": {"b": 2, "a": 1},
        },
    )
    right = _event(
        payload={
            "nested": {"a": 1, "b": 2},
            "price": Decimal("100.00"),
        },
    )

    assert canonical_jsonl((left,)) == canonical_jsonl((right,))
    assert evidence_digest((left,)) == evidence_digest((right,))


def test_decimal_and_datetime_payload_values_use_canonical_strings() -> None:
    event = _event(
        payload={
            "price": Decimal("100.00"),
            "when": _NOW,
            "values": (Decimal("1.25"), Decimal("2.50")),
        },
    )

    record = json.loads(canonical_jsonl((event,)).decode("utf-8"))

    assert record["payload"] == {
        "price": "100.00",
        "values": ["1.25", "2.50"],
        "when": "2026-08-09T07:00:00Z",
    }
    assert record["timestamp"] == "2026-08-09T07:00:00Z"
    assert record["schema_version"] == 1


def test_unsupported_float_payload_is_rejected() -> None:
    with pytest.raises(TypeError, match="unsupported evidence value"):
        _event(payload={"price": 100.0})


def test_event_requires_aware_time_non_empty_run_and_non_negative_sequence() -> None:
    with pytest.raises(ValueError):
        EvidenceEvent.create(
            run_id="",
            sequence=0,
            timestamp=_NOW,
            kind=EvidenceKind.RUN_SUMMARY,
            payload={},
        )

    with pytest.raises(ValueError):
        EvidenceEvent.create(
            run_id="run",
            sequence=-1,
            timestamp=_NOW,
            kind=EvidenceKind.RUN_SUMMARY,
            payload={},
        )

    with pytest.raises(ValueError):
        EvidenceEvent.create(
            run_id="run",
            sequence=0,
            timestamp=datetime(2026, 8, 9, 7, 0),
            kind=EvidenceKind.RUN_SUMMARY,
            payload={},
        )


def test_jsonl_requires_one_run_contiguous_sequence_and_monotonic_time() -> None:
    with pytest.raises(ValueError, match="contiguous"):
        canonical_jsonl((_event(sequence=0), _event(sequence=2)))

    other_run = EvidenceEvent.create(
        run_id="other",
        sequence=1,
        timestamp=_NOW + timedelta(seconds=1),
        kind=EvidenceKind.RUN_SUMMARY,
        payload={},
    )
    with pytest.raises(ValueError, match="run_id"):
        canonical_jsonl((_event(sequence=0), other_run))

    with pytest.raises(ValueError, match="timestamp"):
        canonical_jsonl(
            (
                _event(sequence=0, timestamp=_NOW),
                _event(sequence=1, timestamp=_NOW - timedelta(seconds=1)),
            ),
        )


def test_jsonl_has_one_record_per_line_and_trailing_newline() -> None:
    events = (
        _event(sequence=0),
        _event(sequence=1, timestamp=_NOW + timedelta(seconds=1)),
    )

    encoded = canonical_jsonl(events)

    assert encoded.endswith(b"\n")
    assert len(encoded.splitlines()) == 2


def test_pnl_breakdown_keeps_explicit_zero_buckets_and_total() -> None:
    pnl = PnLBreakdown(
        realized_grid=Decimal("2.5"),
        directional_mark=Decimal("-1"),
        fees=Decimal("-0.2"),
        funding=Decimal("0"),
        emergency_execution=Decimal("0"),
    )

    assert pnl.total == Decimal("1.3")
    assert pnl.funding == Decimal("0")
    assert pnl.emergency_execution == Decimal("0")


def test_pnl_breakdown_rejects_non_finite_values() -> None:
    with pytest.raises(ValueError):
        PnLBreakdown(
            realized_grid=Decimal("NaN"),
            directional_mark=Decimal("0"),
            fees=Decimal("0"),
            funding=Decimal("0"),
            emergency_execution=Decimal("0"),
        )
