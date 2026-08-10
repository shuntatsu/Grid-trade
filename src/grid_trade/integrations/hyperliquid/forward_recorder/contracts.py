from dataclasses import dataclass
from typing import Protocol

from grid_trade.datasets.contracts import RawObjectRef

_DEFAULT_REFERENCE_INTERVAL_NS = 60_000_000_000
_DEFAULT_HEARTBEAT_INTERVAL_NS = 30_000_000_000


def _require_timestamp(timestamp_ns: int, *, field: str) -> None:
    if timestamp_ns < 0:
        raise ValueError(f"{field} must be non-negative")


@dataclass(frozen=True, slots=True)
class ForwardSegment:
    raw_object: RawObjectRef
    continuity_epoch: int
    record_count: int

    def __post_init__(self) -> None:
        if self.continuity_epoch < 0:
            raise ValueError("continuity_epoch must be non-negative")
        if self.record_count < 0:
            raise ValueError("record_count must be non-negative")


@dataclass(frozen=True, slots=True)
class ForwardRecorderConfig:
    instrument: str
    reference_interval_ns: int = _DEFAULT_REFERENCE_INTERVAL_NS
    heartbeat_interval_ns: int = _DEFAULT_HEARTBEAT_INTERVAL_NS
    max_reference_gap_intervals: int = 2

    def __post_init__(self) -> None:
        if not self.instrument or not self.instrument.strip():
            raise ValueError("instrument must be non-empty")
        if self.reference_interval_ns <= 0:
            raise ValueError("reference_interval_ns must be positive")
        if self.heartbeat_interval_ns <= 0:
            raise ValueError("heartbeat_interval_ns must be positive")
        if self.max_reference_gap_intervals <= 0:
            raise ValueError("max_reference_gap_intervals must be positive")


@dataclass(frozen=True, slots=True)
class ContinuityRecord:
    continuity_epoch: int
    disconnect_ts_ns: int
    reconnect_ts_ns: int
    first_post_reconnect_exchange_ts_ns: int
    first_post_reconnect_receive_ts_ns: int
    uncovered_receive_interval_ns: int

    def __post_init__(self) -> None:
        if self.continuity_epoch <= 0:
            raise ValueError("continuity_epoch must be positive for reconnect records")
        for field, value in (
            ("disconnect_ts_ns", self.disconnect_ts_ns),
            ("reconnect_ts_ns", self.reconnect_ts_ns),
            (
                "first_post_reconnect_exchange_ts_ns",
                self.first_post_reconnect_exchange_ts_ns,
            ),
            (
                "first_post_reconnect_receive_ts_ns",
                self.first_post_reconnect_receive_ts_ns,
            ),
            ("uncovered_receive_interval_ns", self.uncovered_receive_interval_ns),
        ):
            _require_timestamp(value, field=field)
        if self.reconnect_ts_ns < self.disconnect_ts_ns:
            raise ValueError("reconnect_ts_ns must not precede disconnect_ts_ns")
        if self.first_post_reconnect_receive_ts_ns < self.reconnect_ts_ns:
            raise ValueError("first post-reconnect receive timestamp must not precede reconnect")
        if self.uncovered_receive_interval_ns != (
            self.first_post_reconnect_receive_ts_ns - self.disconnect_ts_ns
        ):
            raise ValueError("uncovered_receive_interval_ns is inconsistent")


@dataclass(frozen=True, slots=True)
class ForwardCaptureResult:
    ordinal: int
    continuity_record: ContinuityRecord | None = None

    def __post_init__(self) -> None:
        if self.ordinal < 0:
            raise ValueError("ordinal must be non-negative")


class HyperliquidForwardTransport(Protocol):
    def send(self, payload: bytes) -> None: ...

    def fetch_info(self, payload: bytes) -> bytes: ...


__all__ = [
    "ContinuityRecord",
    "ForwardCaptureResult",
    "ForwardRecorderConfig",
    "ForwardSegment",
    "HyperliquidForwardTransport",
]
