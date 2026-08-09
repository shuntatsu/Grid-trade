from datetime import UTC, datetime
from decimal import Decimal

from grid_trade.evidence.events import EvidenceEvent, EvidenceKind

_NOW = datetime(2026, 8, 9, 9, 0, tzinfo=UTC)


def test_center_decision_decimal_payload_is_canonical() -> None:
    event = EvidenceEvent.create(
        run_id="s1",
        sequence=0,
        timestamp=_NOW,
        kind=EvidenceKind.CENTER_DECISION,
        payload={
            "previous_center": Decimal("100.00"),
            "deviation_bps": Decimal("25.0"),
        },
    )

    assert event.payload_json == '{"deviation_bps":"25.0","previous_center":"100.00"}'
