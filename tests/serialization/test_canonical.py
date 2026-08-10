from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256

import pytest

from grid_trade.serialization import canonical_json_bytes, canonical_json_digest


class Mode(StrEnum):
    ACTIVE = "active"


@dataclass(frozen=True)
class Payload:
    amount: Decimal
    timestamp: datetime
    mode: Mode
    values: tuple[int, ...]


def test_canonical_json_bytes_match_existing_contract() -> None:
    payload = Payload(
        amount=Decimal("1.2300"),
        timestamp=datetime(
            2026,
            8,
            10,
            13,
            0,
            tzinfo=timezone(timedelta(hours=9)),
        ),
        mode=Mode.ACTIVE,
        values=(2, 1),
    )

    rendered = canonical_json_bytes({"z": payload, "a": True})

    assert rendered == (
        b'{"a":true,"z":{"amount":"1.2300","mode":"active",'
        b'"timestamp":"2026-08-10T04:00:00Z","values":[2,1]}}\n'
    )
    assert canonical_json_digest({"z": payload, "a": True}) == sha256(rendered).hexdigest()


def test_canonical_json_stringifies_mapping_keys() -> None:
    assert canonical_json_bytes({2: "b", 1: "a"}) == b'{"1":"a","2":"b"}\n'


def test_canonical_json_rejects_unsupported_values() -> None:
    with pytest.raises(TypeError, match="unsupported canonical value: object"):
        canonical_json_bytes(object())


def test_canonical_json_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="datetime must be timezone-aware"):
        canonical_json_bytes(datetime(2026, 8, 10, 4, 0))
