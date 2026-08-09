import hashlib
import json
from datetime import UTC

from grid_trade.evidence.events import EvidenceEvent


def _validate_sequence(events: tuple[EvidenceEvent, ...]) -> None:
    if not events:
        return

    run_id = events[0].run_id
    previous_timestamp = events[0].timestamp
    for expected_sequence, event in enumerate(events):
        if event.sequence != expected_sequence:
            raise ValueError("evidence sequence must be contiguous from zero")
        if event.run_id != run_id:
            raise ValueError("all evidence events must share one run_id")
        if event.timestamp < previous_timestamp:
            raise ValueError("evidence timestamp must be monotonic")
        previous_timestamp = event.timestamp


def canonical_jsonl(events: tuple[EvidenceEvent, ...]) -> bytes:
    _validate_sequence(events)
    if not events:
        return b""

    lines: list[str] = []
    for event in events:
        record = {
            "kind": event.kind.value,
            "payload": json.loads(event.payload_json),
            "run_id": event.run_id,
            "schema_version": event.schema_version,
            "sequence": event.sequence,
            "timestamp": event.timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        }
        lines.append(
            json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    return ("\n".join(lines) + "\n").encode("utf-8")


def evidence_digest(events: tuple[EvidenceEvent, ...]) -> str:
    return hashlib.sha256(canonical_jsonl(events)).hexdigest()


__all__ = ["canonical_jsonl", "evidence_digest"]
