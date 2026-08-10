import json

from grid_trade.datasets.contracts import DatasetType, SourceFamily
from grid_trade.integrations.hyperliquid.forward_recorder.contracts import (
    ContinuityRecord,
    ForwardCaptureResult,
    ForwardRecorderConfig,
    HyperliquidForwardTransport,
    _require_timestamp,
)
from grid_trade.integrations.hyperliquid.forward_recorder.segment import ForwardSegmentWriter


def _canonical_json_line(payload: dict[str, object]) -> bytes:
    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return f"{rendered}\n".encode()


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


__all__ = ["ForwardRecorderSession"]
