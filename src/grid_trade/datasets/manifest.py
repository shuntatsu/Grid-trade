import json
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any

from grid_trade.datasets.contracts import DatasetAcceptance, RawObjectRef


def _require_non_empty(value: str, *, field: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field} must be non-empty")


def _require_utc(value: datetime, *, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be timezone-aware UTC")


def _canonical_value(value: object) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        _require_utc(value, field="datetime")
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _canonical_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple | list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"unsupported canonical manifest value: {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    instrument: str
    raw_objects: tuple[RawObjectRef, ...]
    normalization_schema_version: str
    ordering_schema_version: str
    audit_schema_version: str
    acceptance: DatasetAcceptance
    created_at: datetime

    def __post_init__(self) -> None:
        _require_non_empty(self.instrument, field="instrument")
        _require_non_empty(
            self.normalization_schema_version,
            field="normalization_schema_version",
        )
        _require_non_empty(self.ordering_schema_version, field="ordering_schema_version")
        _require_non_empty(self.audit_schema_version, field="audit_schema_version")
        _require_utc(self.created_at, field="created_at")
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
    payload = json.dumps(
        _canonical_value(manifest),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return f"{payload}\n".encode()


__all__ = ["DatasetManifest", "canonical_manifest_bytes"]
