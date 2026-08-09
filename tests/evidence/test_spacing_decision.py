from datetime import UTC, datetime
from decimal import Decimal

from grid_trade.evidence.events import EvidenceEvent, EvidenceKind


def test_spacing_decision_is_canonical_without_schema_bump() -> None:
    event = EvidenceEvent.create(
        run_id="s2-evidence",
        sequence=0,
        timestamp=datetime(2026, 8, 9, 11, 10, tzinfo=UTC),
        kind=EvidenceKind.SPACING_DECISION,
        payload={
            "previous_spacing_bps": 12,
            "volatility_spacing_bps": Decimal("30.000"),
            "effective_spacing_bps": 30,
            "changed": True,
        },
    )

    assert event.schema_version == 1
    assert event.kind is EvidenceKind.SPACING_DECISION
    assert event.payload_json == (
        '{"changed":true,"effective_spacing_bps":30,'
        '"previous_spacing_bps":12,"volatility_spacing_bps":"30.000"}'
    )
