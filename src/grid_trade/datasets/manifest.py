import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from grid_trade.datasets.audit_contracts import DatasetAuditExpectations
from grid_trade.datasets.contracts import DatasetAcceptance, RawObjectRef
from grid_trade.serialization import canonical_json_bytes

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _require_non_empty(value: str, *, field: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field} must be non-empty")


def _require_utc(value: datetime, *, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be timezone-aware UTC")


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    instrument: str
    raw_objects: tuple[RawObjectRef, ...]
    normalization_schema_version: str
    ordering_schema_version: str
    audit_schema_version: str
    acceptance: DatasetAcceptance
    created_at: datetime
    audit_digest: str | None = None
    required_funding_timestamps_ns: tuple[int, ...] = ()
    audit_expectations: DatasetAuditExpectations = field(default_factory=DatasetAuditExpectations)

    def __post_init__(self) -> None:
        _require_non_empty(self.instrument, field="instrument")
        _require_non_empty(
            self.normalization_schema_version,
            field="normalization_schema_version",
        )
        _require_non_empty(self.ordering_schema_version, field="ordering_schema_version")
        _require_non_empty(self.audit_schema_version, field="audit_schema_version")
        _require_utc(self.created_at, field="created_at")
        if self.audit_digest is not None and _SHA256_RE.fullmatch(self.audit_digest) is None:
            raise ValueError("audit_digest must be 64 lowercase hexadecimal characters")
        if any(timestamp < 0 for timestamp in self.required_funding_timestamps_ns):
            raise ValueError("required funding timestamps must be non-negative")
        if self.required_funding_timestamps_ns != tuple(
            sorted(set(self.required_funding_timestamps_ns))
        ):
            raise ValueError("required funding timestamps must be sorted and unique")
        if self.acceptance is not DatasetAcceptance.REJECTED and not self.raw_objects:
            raise ValueError("accepted dataset manifest requires at least one raw object")
        if any(raw.identity.instrument != self.instrument for raw in self.raw_objects):
            raise ValueError("raw object instrument must match manifest instrument")
        identities = [raw.identity for raw in self.raw_objects]
        if len(set(identities)) != len(identities):
            raise ValueError("dataset manifest must not contain duplicate raw object identities")
        if self.acceptance is not DatasetAcceptance.REJECTED and any(
            not raw.complete for raw in self.raw_objects
        ):
            raise ValueError("accepted dataset manifest cannot contain incomplete raw objects")


def canonical_manifest_bytes(manifest: DatasetManifest) -> bytes:
    return canonical_json_bytes(manifest)


__all__ = ["DatasetManifest", "canonical_manifest_bytes"]
