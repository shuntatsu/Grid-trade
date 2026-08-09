import os
import struct
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from grid_trade.datasets.contracts import (
    DatasetType,
    RawObjectIdentity,
    RawObjectRef,
    SourceFamily,
    sha256_bytes,
)

_FORWARD_SEGMENT_MAGIC = b"GT-HL-SEGMENT-V1\n"
_FRAME_HEADER = struct.Struct(">QQ")


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
        sync_fn: Callable[[int], None] | None = None,
    ) -> None:
        if continuity_epoch < 0:
            raise ValueError("continuity_epoch must be non-negative")
        if not instrument or not instrument.strip():
            raise ValueError("instrument must be non-empty")
        if not collector_schema_version or not collector_schema_version.strip():
            raise ValueError("collector_schema_version must be non-empty")
        if not decoder_schema_version or not decoder_schema_version.strip():
            raise ValueError("decoder_schema_version must be non-empty")

        self._final_path = final_path
        self._partial_path = final_path.with_name(f"{final_path.name}.partial")
        self._instrument = instrument
        self._dataset_type = dataset_type
        self._collector_schema_version = collector_schema_version
        self._decoder_schema_version = decoder_schema_version
        self._continuity_epoch = continuity_epoch
        self._sync_fn = os.fsync if sync_fn is None else sync_fn
        self._record_count = 0
        self._receive_start_ns: int | None = None
        self._receive_end_ns: int | None = None
        self._closed = False

        final_path.parent.mkdir(parents=True, exist_ok=True)
        if final_path.exists():
            raise FileExistsError(f"final segment already exists: {final_path}")
        self._file: BinaryIO = self._partial_path.open("xb")
        self._file.write(_FORWARD_SEGMENT_MAGIC)

    @property
    def partial_path(self) -> Path:
        return self._partial_path

    @property
    def final_path(self) -> Path:
        return self._final_path

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
                source_family=SourceFamily.WEBSOCKET,
                dataset_type=self._dataset_type,
                instrument=self._instrument,
                sha256=sha256_bytes(payload),
            ),
            byte_length=len(payload),
            acquired_at=acquired_at,
            source_locator=str(path),
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

    def finalize(self, *, acquired_at: datetime) -> ForwardSegment:
        self._require_open()
        if self._record_count == 0:
            raise ValueError("cannot finalize an empty forward segment")
        self._flush_and_sync()
        self._file.close()
        self._closed = True
        os.replace(self._partial_path, self._final_path)
        return self._build_segment(path=self._final_path, acquired_at=acquired_at, complete=True)

    def abort(self, *, acquired_at: datetime) -> ForwardSegment:
        self._require_open()
        self._flush_and_sync()
        self._file.close()
        self._closed = True
        return self._build_segment(path=self._partial_path, acquired_at=acquired_at, complete=False)


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


__all__ = ["ForwardSegment", "ForwardSegmentWriter", "read_segment_records"]
