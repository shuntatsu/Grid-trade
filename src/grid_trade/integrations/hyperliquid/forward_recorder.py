import json
import os
import struct
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Protocol

from grid_trade.datasets.contracts import (
    DatasetType,
    RawObjectIdentity,
    RawObjectRef,
    SourceFamily,
    sha256_bytes,
)

FORWARD_SEGMENT_MANIFEST_SCHEMA_VERSION = "hyperliquid-forward-segment-manifest-v1"
_FORWARD_SEGMENT_MAGIC = b"GT-HL-SEGMENT-V1\n"
_FRAME_HEADER = struct.Struct(">QQ")
_DEFAULT_REFERENCE_INTERVAL_NS = 60_000_000_000
_DEFAULT_HEARTBEAT_INTERVAL_NS = 30_000_000_000


def _default_directory_sync(path: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _canonical_json_line(payload: dict[str, object]) -> bytes:
    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return f"{rendered}\n".encode()


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


def _iso_utc(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def canonical_forward_segment_manifest_bytes(segment: ForwardSegment) -> bytes:
    raw = segment.raw_object
    payload = {
        "continuity_epoch": segment.continuity_epoch,
        "manifest_schema_version": FORWARD_SEGMENT_MANIFEST_SCHEMA_VERSION,
        "raw_object": {
            "acquired_at": _iso_utc(raw.acquired_at),
            "byte_length": raw.byte_length,
            "collector_schema_version": raw.collector_schema_version,
            "complete": raw.complete,
            "dataset_type": raw.identity.dataset_type.value,
            "decoder_schema_version": raw.decoder_schema_version,
            "instrument": raw.identity.instrument,
            "receive_end_ns": raw.receive_end_ns,
            "receive_start_ns": raw.receive_start_ns,
            "sha256": raw.identity.sha256,
            "source_end_ns": raw.source_end_ns,
            "source_family": raw.identity.source_family.value,
            "source_locator": raw.source_locator,
            "source_start_ns": raw.source_start_ns,
        },
        "record_count": segment.record_count,
    }
    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return f"{rendered}\n".encode()


class ForwardSegmentWriter:
    def __init__(
        self,
        final_path: Path,
        *,
        instrument: str,
        dataset_type: DatasetType,
        collector_schema_version: str,
        decoder_schema_version: str,
        continuity_epoch: int,
        source_family: SourceFamily = SourceFamily.WEBSOCKET,
        sync_fn: Callable[[int], None] | None = None,
        directory_sync_fn: Callable[[Path], None] | None = None,
    ) -> None:
        if continuity_epoch < 0:
            raise ValueError("continuity_epoch must be non-negative")
        if not instrument or not instrument.strip():
            raise ValueError("instrument must be non-empty")
        if not collector_schema_version or not collector_schema_version.strip():
            raise ValueError("collector_schema_version must be non-empty")
        if not decoder_schema_version or not decoder_schema_version.strip():
            raise ValueError("decoder_schema_version must be non-empty")
        if source_family not in {SourceFamily.WEBSOCKET, SourceFamily.INFO}:
            raise ValueError("forward segment source_family must be websocket or info")

        self._final_path = final_path
        self._partial_path = final_path.with_name(f"{final_path.name}.partial")
        self._manifest_path = final_path.with_name(f"{final_path.name}.manifest.json")
        self._manifest_partial_path = self._manifest_path.with_name(
            f"{self._manifest_path.name}.partial"
        )
        self._instrument = instrument
        self._dataset_type = dataset_type
        self._source_family = source_family
        self._collector_schema_version = collector_schema_version
        self._decoder_schema_version = decoder_schema_version
        self._continuity_epoch = continuity_epoch
        self._sync_fn = os.fsync if sync_fn is None else sync_fn
        self._directory_sync_fn = (
            _default_directory_sync if directory_sync_fn is None else directory_sync_fn
        )
        self._record_count = 0
        self._receive_start_ns: int | None = None
        self._receive_end_ns: int | None = None
        self._closed = False

        final_path.parent.mkdir(parents=True, exist_ok=True)
        for path in (
            self._final_path,
            self._partial_path,
            self._manifest_path,
            self._manifest_partial_path,
        ):
            if path.exists():
                raise FileExistsError(f"forward segment artifact already exists: {path}")
        self._file: BinaryIO = self._partial_path.open("xb")
        self._file.write(_FORWARD_SEGMENT_MAGIC)

    @property
    def partial_path(self) -> Path:
        return self._partial_path

    @property
    def final_path(self) -> Path:
        return self._final_path

    @property
    def manifest_path(self) -> Path:
        return self._manifest_path

    @property
    def manifest_partial_path(self) -> Path:
        return self._manifest_partial_path

    @property
    def instrument(self) -> str:
        return self._instrument

    @property
    def dataset_type(self) -> DatasetType:
        return self._dataset_type

    @property
    def source_family(self) -> SourceFamily:
        return self._source_family

    @property
    def continuity_epoch(self) -> int:
        return self._continuity_epoch

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("forward segment writer is closed")

    def _flush_and_sync(self) -> None:
        self._file.flush()
        self._sync_fn(self._file.fileno())

    def append(self, payload: bytes, *, receive_ts_ns: int) -> int:
        self._require_open()
        if not payload:
            raise ValueError("forward payload must be non-empty")
        if receive_ts_ns < 0:
            raise ValueError("receive_ts_ns must be non-negative")
        if self._receive_end_ns is not None and receive_ts_ns < self._receive_end_ns:
            raise ValueError("receive timestamps must be monotonic within a continuity segment")

        ordinal = self._record_count
        self._file.write(_FRAME_HEADER.pack(receive_ts_ns, len(payload)))
        self._file.write(payload)
        self._flush_and_sync()

        if self._receive_start_ns is None:
            self._receive_start_ns = receive_ts_ns
        self._receive_end_ns = receive_ts_ns
        self._record_count += 1
        return ordinal

    def _build_segment(
        self, *, path: Path, acquired_at: datetime, complete: bool
    ) -> ForwardSegment:
        payload = path.read_bytes()
        raw_object = RawObjectRef(
            identity=RawObjectIdentity(
                source_family=self._source_family,
                dataset_type=self._dataset_type,
                instrument=self._instrument,
                sha256=sha256_bytes(payload),
            ),
            byte_length=len(payload),
            acquired_at=acquired_at,
            source_locator=str(self._final_path if complete else path),
            collector_schema_version=self._collector_schema_version,
            decoder_schema_version=self._decoder_schema_version,
            receive_start_ns=self._receive_start_ns,
            receive_end_ns=self._receive_end_ns,
            complete=complete,
        )
        return ForwardSegment(
            raw_object=raw_object,
            continuity_epoch=self._continuity_epoch,
            record_count=self._record_count,
        )

    def _publish_manifest(self, segment: ForwardSegment) -> None:
        payload = canonical_forward_segment_manifest_bytes(segment)
        with self._manifest_partial_path.open("xb") as manifest_file:
            manifest_file.write(payload)
            manifest_file.flush()
            self._sync_fn(manifest_file.fileno())
        os.replace(self._manifest_partial_path, self._manifest_path)
        self._directory_sync_fn(self._manifest_path.parent)

    def finalize(self, *, acquired_at: datetime) -> ForwardSegment:
        self._require_open()
        if self._record_count == 0:
            raise ValueError("cannot finalize an empty forward segment")
        self._flush_and_sync()
        self._file.close()
        self._closed = True
        os.replace(self._partial_path, self._final_path)
        self._directory_sync_fn(self._final_path.parent)
        segment = self._build_segment(
            path=self._final_path,
            acquired_at=acquired_at,
            complete=True,
        )
        self._publish_manifest(segment)
        return segment

    def abort(self, *, acquired_at: datetime) -> ForwardSegment:
        self._require_open()
        self._flush_and_sync()
        self._file.close()
        self._closed = True
        return self._build_segment(path=self._partial_path, acquired_at=acquired_at, complete=False)


class ForwardRecorderSession:
    def __init__(self, config: ForwardRecorderConfig) -> None:
        self._config = config
        self._connected = False
        self._ever_connected = False
        self._connection_started_ns: int | None = None
        self._last_outbound_ns: int | None = None
        self._last_reference_receive_ns: int | None = None
        self._continuity_epoch = 0
        self._disconnect_ts_ns: int | None = None
        self._reconnect_ts_ns: int | None = None
        self._awaiting_authoritative_book = False
        self._continuity_records: list[ContinuityRecord] = []

    @property
    def continuity_epoch(self) -> int:
        return self._continuity_epoch

    @property
    def awaiting_authoritative_book(self) -> bool:
        return self._awaiting_authoritative_book

    @property
    def continuity_records(self) -> tuple[ContinuityRecord, ...]:
        return tuple(self._continuity_records)

    def _subscription_messages(self) -> tuple[bytes, bytes]:
        return (
            _canonical_json_line(
                {
                    "method": "subscribe",
                    "subscription": {"type": "l2Book", "coin": self._config.instrument},
                }
            ),
            _canonical_json_line(
                {
                    "method": "subscribe",
                    "subscription": {"type": "trades", "coin": self._config.instrument},
                }
            ),
        )

    def _send_subscriptions(
        self,
        transport: HyperliquidForwardTransport,
        *,
        timestamp_ns: int,
    ) -> None:
        for payload in self._subscription_messages():
            transport.send(payload)
            self._last_outbound_ns = timestamp_ns

    def connect(
        self,
        transport: HyperliquidForwardTransport,
        *,
        timestamp_ns: int,
    ) -> None:
        _require_timestamp(timestamp_ns, field="timestamp_ns")
        if self._ever_connected:
            raise RuntimeError("initial connect already occurred; use reconnect after disconnect")
        self._send_subscriptions(transport, timestamp_ns=timestamp_ns)
        self._connected = True
        self._ever_connected = True
        self._connection_started_ns = timestamp_ns

    def note_disconnect(self, *, timestamp_ns: int) -> None:
        _require_timestamp(timestamp_ns, field="timestamp_ns")
        if not self._connected:
            raise RuntimeError("cannot disconnect an inactive recorder session")
        self._connected = False
        self._disconnect_ts_ns = timestamp_ns
        self._reconnect_ts_ns = None
        self._awaiting_authoritative_book = False

    def reconnect(
        self,
        transport: HyperliquidForwardTransport,
        *,
        timestamp_ns: int,
    ) -> None:
        _require_timestamp(timestamp_ns, field="timestamp_ns")
        if self._connected or self._disconnect_ts_ns is None:
            raise RuntimeError("reconnect requires a recorded disconnect")
        if timestamp_ns < self._disconnect_ts_ns:
            raise ValueError("reconnect timestamp must not precede disconnect")
        self._continuity_epoch += 1
        self._reconnect_ts_ns = timestamp_ns
        self._awaiting_authoritative_book = True
        self._send_subscriptions(transport, timestamp_ns=timestamp_ns)
        self._connected = True
        self._connection_started_ns = timestamp_ns

    def heartbeat_due(self, timestamp_ns: int) -> bool:
        _require_timestamp(timestamp_ns, field="timestamp_ns")
        if not self._connected or self._last_outbound_ns is None:
            return False
        return timestamp_ns - self._last_outbound_ns >= self._config.heartbeat_interval_ns

    def send_heartbeat(
        self,
        transport: HyperliquidForwardTransport,
        *,
        timestamp_ns: int,
    ) -> None:
        _require_timestamp(timestamp_ns, field="timestamp_ns")
        if not self._connected:
            raise RuntimeError("cannot send heartbeat while disconnected")
        transport.send(_canonical_json_line({"method": "ping"}))
        self._last_outbound_ns = timestamp_ns

    def reference_due(self, timestamp_ns: int) -> bool:
        _require_timestamp(timestamp_ns, field="timestamp_ns")
        if not self._connected:
            return False
        if self._last_reference_receive_ns is None:
            return True
        return timestamp_ns - self._last_reference_receive_ns >= self._config.reference_interval_ns

    def reference_gap_exceeded(self, timestamp_ns: int) -> bool:
        _require_timestamp(timestamp_ns, field="timestamp_ns")
        if not self._connected:
            return False
        baseline = self._last_reference_receive_ns
        if baseline is None:
            baseline = self._connection_started_ns
        if baseline is None:
            return False
        allowed = self._config.reference_interval_ns * self._config.max_reference_gap_intervals
        return timestamp_ns - baseline > allowed

    def _validate_writer(
        self,
        writer: ForwardSegmentWriter,
        *,
        dataset_type: DatasetType,
        source_family: SourceFamily,
    ) -> None:
        if not self._connected:
            raise RuntimeError("cannot capture data while recorder session is disconnected")
        if writer.instrument != self._config.instrument:
            raise ValueError("writer instrument does not match recorder session")
        if writer.dataset_type is not dataset_type:
            raise ValueError("writer dataset type does not match recorder channel")
        if writer.source_family is not source_family:
            raise ValueError("writer source family does not match recorder channel")
        if writer.continuity_epoch != self._continuity_epoch:
            raise ValueError("writer continuity epoch does not match recorder session")

    def capture_reference(
        self,
        transport: HyperliquidForwardTransport,
        writer: ForwardSegmentWriter,
        *,
        receive_ts_ns: int,
    ) -> int:
        self._validate_writer(
            writer,
            dataset_type=DatasetType.FUNDING_REFERENCE,
            source_family=SourceFamily.INFO,
        )
        payload = transport.fetch_info(_canonical_json_line({"type": "metaAndAssetCtxs"}))
        ordinal = writer.append(payload, receive_ts_ns=receive_ts_ns)
        self._last_reference_receive_ns = receive_ts_ns
        return ordinal

    def capture_trades(
        self,
        payload: bytes,
        writer: ForwardSegmentWriter,
        *,
        receive_ts_ns: int,
    ) -> int:
        self._validate_writer(
            writer,
            dataset_type=DatasetType.TRADES,
            source_family=SourceFamily.WEBSOCKET,
        )
        return writer.append(payload, receive_ts_ns=receive_ts_ns)

    def capture_l2_book(
        self,
        payload: bytes,
        writer: ForwardSegmentWriter,
        *,
        receive_ts_ns: int,
        exchange_ts_ns: int,
    ) -> ForwardCaptureResult:
        _require_timestamp(exchange_ts_ns, field="exchange_ts_ns")
        self._validate_writer(
            writer,
            dataset_type=DatasetType.L2_BOOK,
            source_family=SourceFamily.WEBSOCKET,
        )
        ordinal = writer.append(payload, receive_ts_ns=receive_ts_ns)
        continuity_record: ContinuityRecord | None = None
        if self._awaiting_authoritative_book:
            if self._disconnect_ts_ns is None or self._reconnect_ts_ns is None:
                raise RuntimeError("reconnect continuity state is incomplete")
            continuity_record = ContinuityRecord(
                continuity_epoch=self._continuity_epoch,
                disconnect_ts_ns=self._disconnect_ts_ns,
                reconnect_ts_ns=self._reconnect_ts_ns,
                first_post_reconnect_exchange_ts_ns=exchange_ts_ns,
                first_post_reconnect_receive_ts_ns=receive_ts_ns,
                uncovered_receive_interval_ns=receive_ts_ns - self._disconnect_ts_ns,
            )
            self._continuity_records.append(continuity_record)
            self._awaiting_authoritative_book = False
            self._disconnect_ts_ns = None
            self._reconnect_ts_ns = None
        return ForwardCaptureResult(
            ordinal=ordinal,
            continuity_record=continuity_record,
        )


def read_segment_records(path: Path) -> tuple[tuple[int, bytes], ...]:
    payload = path.read_bytes()
    if not payload.startswith(_FORWARD_SEGMENT_MAGIC):
        raise ValueError("invalid Hyperliquid forward segment magic")

    offset = len(_FORWARD_SEGMENT_MAGIC)
    records: list[tuple[int, bytes]] = []
    while offset < len(payload):
        header_end = offset + _FRAME_HEADER.size
        if header_end > len(payload):
            raise ValueError("truncated Hyperliquid forward segment frame header")
        receive_ts_ns, payload_length = _FRAME_HEADER.unpack(payload[offset:header_end])
        offset = header_end
        frame_end = offset + payload_length
        if frame_end > len(payload):
            raise ValueError("truncated Hyperliquid forward segment payload")
        frame = payload[offset:frame_end]
        if not frame:
            raise ValueError("Hyperliquid forward segment contains an empty frame")
        records.append((receive_ts_ns, frame))
        offset = frame_end
    return tuple(records)


__all__ = [
    "FORWARD_SEGMENT_MANIFEST_SCHEMA_VERSION",
    "ContinuityRecord",
    "ForwardCaptureResult",
    "ForwardRecorderConfig",
    "ForwardRecorderSession",
    "ForwardSegment",
    "ForwardSegmentWriter",
    "HyperliquidForwardTransport",
    "canonical_forward_segment_manifest_bytes",
    "read_segment_records",
]
