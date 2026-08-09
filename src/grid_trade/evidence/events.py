import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

type CanonicalScalar = str | int | bool | None
type CanonicalValue = CanonicalScalar | list[CanonicalValue] | dict[str, CanonicalValue]


def _utc_string(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("evidence datetime must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _canonicalize(value: object) -> CanonicalValue:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("evidence Decimal must be finite")
        return str(value)
    if isinstance(value, datetime):
        return _utc_string(value)
    if isinstance(value, StrEnum):
        return value.value
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, CanonicalValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("evidence mapping keys must be strings")
            normalized[key] = _canonicalize(item)
        return normalized
    if isinstance(value, (tuple, list)):
        return [_canonicalize(item) for item in value]
    raise TypeError(f"unsupported evidence value: {type(value).__name__}")


class EvidenceKind(StrEnum):
    MARKET_SNAPSHOT = "market_snapshot"
    DESIRED_LADDER = "desired_ladder"
    RECONCILIATION_PLAN = "reconciliation_plan"
    FILL = "fill"
    RISK_DECISION = "risk_decision"
    CENTER_DECISION = "center_decision"
    SPACING_DECISION = "spacing_decision"
    RUN_SUMMARY = "run_summary"


@dataclass(frozen=True, slots=True)
class EvidenceEvent:
    schema_version: int
    run_id: str
    sequence: int
    timestamp: datetime
    kind: EvidenceKind
    payload_json: str

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported evidence schema_version")
        if not self.run_id.strip():
            raise ValueError("run_id must be non-empty")
        if self.sequence < 0:
            raise ValueError("sequence must be non-negative")
        _utc_string(self.timestamp)
        parsed = json.loads(self.payload_json)
        if not isinstance(parsed, dict):
            raise ValueError("evidence payload must encode a JSON object")

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        sequence: int,
        timestamp: datetime,
        kind: EvidenceKind,
        payload: Mapping[str, object],
    ) -> "EvidenceEvent":
        normalized = _canonicalize(payload)
        if not isinstance(normalized, dict):
            raise TypeError("evidence payload must be a mapping")
        payload_json = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return cls(
            schema_version=1,
            run_id=run_id,
            sequence=sequence,
            timestamp=timestamp,
            kind=kind,
            payload_json=payload_json,
        )


@dataclass(frozen=True, slots=True)
class PnLBreakdown:
    realized_grid: Decimal
    directional_mark: Decimal
    fees: Decimal
    funding: Decimal
    emergency_execution: Decimal

    def __post_init__(self) -> None:
        for field_name in (
            "realized_grid",
            "directional_mark",
            "fees",
            "funding",
            "emergency_execution",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f"{field_name} must be a finite Decimal")

    @property
    def total(self) -> Decimal:
        return (
            self.realized_grid
            + self.directional_mark
            + self.fees
            + self.funding
            + self.emergency_execution
        )


__all__ = ["EvidenceEvent", "EvidenceKind", "PnLBreakdown"]
