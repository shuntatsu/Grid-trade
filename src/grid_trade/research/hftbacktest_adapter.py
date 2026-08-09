import csv
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from importlib import import_module
from importlib.metadata import version as distribution_version
from pathlib import Path
from types import ModuleType
from typing import Any

from grid_trade.datasets.canonical import (
    BookSide,
    BookVisibilityTracker,
    CanonicalBookSnapshot,
    CanonicalEventEnvelope,
    CanonicalEventType,
    CanonicalTrade,
    TradeSide,
    VisibilityChange,
    canonical_event_sort_key,
)
from grid_trade.domain.orders import OrderSide, PassiveOrderIntent

_HFTBACKTEST_VERSION = "2.4.4"


def _require_finite_positive(value: Decimal, *, field: str) -> None:
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{field} must be finite and positive")


def _require_finite_non_negative(value: Decimal, *, field: str) -> None:
    if not value.is_finite() or value < 0:
        raise ValueError(f"{field} must be finite and non-negative")


def _require_finite(value: Decimal, *, field: str) -> None:
    if not value.is_finite():
        raise ValueError(f"{field} must be finite")


def require_hftbacktest_runtime() -> None:
    installed = distribution_version("hftbacktest")
    if installed != _HFTBACKTEST_VERSION:
        raise RuntimeError(
            f"hftbacktest runtime mismatch: expected {_HFTBACKTEST_VERSION}, got {installed}",
        )


class ReceiveTimestampMode(StrEnum):
    OBSERVED = "observed"
    SYNTHETIC = "synthetic"
    MIXED = "mixed"


@dataclass(frozen=True, slots=True)
class HftReplayConfig:
    tick_size: Decimal
    lot_size: Decimal
    entry_latency_ns: int = 0
    response_latency_ns: int = 0
    maker_fee: Decimal = Decimal(0)
    taker_fee: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        _require_finite_positive(self.tick_size, field="tick_size")
        _require_finite_positive(self.lot_size, field="lot_size")
        if self.entry_latency_ns < 0:
            raise ValueError("entry_latency_ns must be non-negative")
        if self.response_latency_ns < 0:
            raise ValueError("response_latency_ns must be non-negative")
        _require_finite(self.maker_fee, field="maker_fee")
        _require_finite(self.taker_fee, field="taker_fee")


@dataclass(frozen=True, slots=True)
class MicrostructureRow:
    kind: str
    exch_ts: int
    local_ts: int
    price: Decimal
    quantity: Decimal

    def __post_init__(self) -> None:
        supported = {
            "snapshot_bid",
            "snapshot_ask",
            "depth_bid",
            "depth_ask",
            "trade_buy",
            "trade_sell",
        }
        if self.kind not in supported:
            raise ValueError(f"unsupported microstructure kind: {self.kind}")
        if self.exch_ts < 0 or self.local_ts < 0:
            raise ValueError("timestamps must be non-negative")
        _require_finite_positive(self.price, field="price")
        if self.kind in {"depth_bid", "depth_ask"}:
            _require_finite_non_negative(self.quantity, field="quantity")
        else:
            _require_finite_positive(self.quantity, field="quantity")


@dataclass(frozen=True, slots=True)
class MicrostructureFixture:
    snapshot: tuple[MicrostructureRow, ...]
    feed: tuple[MicrostructureRow, ...]

    def __post_init__(self) -> None:
        if not self.snapshot:
            raise ValueError("fixture must contain an initial snapshot")
        if not self.feed:
            raise ValueError("fixture must contain feed events")
        if any(not row.kind.startswith("snapshot_") for row in self.snapshot):
            raise ValueError("snapshot section contains a non-snapshot row")
        if any(row.kind.startswith("snapshot_") for row in self.feed):
            raise ValueError("feed section contains a snapshot row")
        local_times = [row.local_ts for row in self.feed]
        exchange_times = [row.exch_ts for row in self.feed]
        if local_times != sorted(local_times):
            raise ValueError("feed local timestamps must be monotonic")
        if exchange_times != sorted(exchange_times):
            raise ValueError("feed exchange timestamps must be monotonic")


@dataclass(frozen=True, slots=True)
class CanonicalHftReplayFixture:
    fixture: MicrostructureFixture
    receive_timestamp_mode: ReceiveTimestampMode
    synthetic_receive_latency_ns: int

    def __post_init__(self) -> None:
        if self.synthetic_receive_latency_ns < 0:
            raise ValueError("synthetic_receive_latency_ns must be non-negative")


@dataclass(frozen=True, slots=True)
class ReplayFill:
    client_order_id: str
    timestamp_ns: int
    price: Decimal
    quantity: Decimal
    remaining_quantity: Decimal

    def __post_init__(self) -> None:
        if not self.client_order_id.strip():
            raise ValueError("client_order_id must be non-empty")
        if self.timestamp_ns < 0:
            raise ValueError("timestamp_ns must be non-negative")
        _require_finite_positive(self.price, field="price")
        _require_finite_positive(self.quantity, field="quantity")
        _require_finite(self.remaining_quantity, field="remaining_quantity")
        if self.remaining_quantity < 0:
            raise ValueError("remaining_quantity must be non-negative")


@dataclass(frozen=True, slots=True)
class ReplaySummary:
    fills: tuple[ReplayFill, ...]
    ending_position: Decimal
    open_order_count: int


def load_microstructure_fixture(path: Path) -> MicrostructureFixture:
    snapshot: list[MicrostructureRow] = []
    feed: list[MicrostructureRow] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        expected = ["kind", "exch_ts", "local_ts", "price", "quantity"]
        if reader.fieldnames != expected:
            raise ValueError(f"unexpected fixture columns: {reader.fieldnames}")
        for raw in reader:
            row = MicrostructureRow(
                kind=raw["kind"],
                exch_ts=int(raw["exch_ts"]),
                local_ts=int(raw["local_ts"]),
                price=Decimal(raw["price"]),
                quantity=Decimal(raw["quantity"]),
            )
            if row.kind.startswith("snapshot_"):
                snapshot.append(row)
            else:
                feed.append(row)
    return MicrostructureFixture(snapshot=tuple(snapshot), feed=tuple(feed))


def _canonical_local_timestamp(
    event: CanonicalEventEnvelope,
    *,
    synthetic_receive_latency_ns: int,
) -> tuple[int, bool]:
    if event.local_receive_ts_ns is not None:
        return event.local_receive_ts_ns, True
    return event.exchange_ts_ns + synthetic_receive_latency_ns, False


def _receive_timestamp_mode(*, observed_count: int, synthetic_count: int) -> ReceiveTimestampMode:
    if observed_count and synthetic_count:
        return ReceiveTimestampMode.MIXED
    if observed_count:
        return ReceiveTimestampMode.OBSERVED
    return ReceiveTimestampMode.SYNTHETIC


def _depth_kind(side: BookSide) -> str:
    return "depth_bid" if side is BookSide.BID else "depth_ask"


def canonical_events_to_hftbacktest_fixture(
    events: tuple[CanonicalEventEnvelope, ...],
    *,
    synthetic_receive_latency_ns: int,
) -> CanonicalHftReplayFixture:
    if synthetic_receive_latency_ns < 0:
        raise ValueError("synthetic_receive_latency_ns must be non-negative")
    if not events:
        raise ValueError("canonical replay requires an initial book snapshot")
    if events != tuple(sorted(events, key=canonical_event_sort_key)):
        raise ValueError("canonical replay events must be in deterministic canonical order")

    instruments = {event.instrument for event in events}
    if len(instruments) != 1:
        raise ValueError("canonical replay requires exactly one instrument")

    snapshot_rows: list[MicrostructureRow] = []
    feed_rows: list[MicrostructureRow] = []
    tracker = BookVisibilityTracker()
    visible_quantities: dict[tuple[BookSide, Decimal], Decimal] = {}
    initial_book_seen = False
    observed_count = 0
    synthetic_count = 0

    for event in events:
        if event.event_type is CanonicalEventType.FUNDING_REFERENCE:
            continue

        local_ts, observed = _canonical_local_timestamp(
            event,
            synthetic_receive_latency_ns=synthetic_receive_latency_ns,
        )
        if observed:
            observed_count += 1
        else:
            synthetic_count += 1

        if event.event_type is CanonicalEventType.TRADE:
            if not initial_book_seen:
                raise ValueError("canonical replay requires an initial book snapshot before trades")
            trade = event.payload
            if not isinstance(trade, CanonicalTrade):
                raise TypeError("validated trade event must carry CanonicalTrade payload")
            feed_rows.append(
                MicrostructureRow(
                    kind="trade_buy" if trade.side is TradeSide.BUY else "trade_sell",
                    exch_ts=event.exchange_ts_ns,
                    local_ts=local_ts,
                    price=trade.price,
                    quantity=trade.quantity,
                )
            )
            continue

        book = event.payload
        if not isinstance(book, CanonicalBookSnapshot):
            raise TypeError("validated book event must carry CanonicalBookSnapshot payload")

        if not initial_book_seen:
            initial_book_seen = True
            for side, levels in ((BookSide.BID, book.bids), (BookSide.ASK, book.asks)):
                kind = "snapshot_bid" if side is BookSide.BID else "snapshot_ask"
                for level in levels:
                    snapshot_rows.append(
                        MicrostructureRow(
                            kind=kind,
                            exch_ts=event.exchange_ts_ns,
                            local_ts=local_ts,
                            price=level.price,
                            quantity=level.quantity,
                        )
                    )
                    visible_quantities[(side, level.price)] = level.quantity
            tracker.apply(book)
            continue

        for update in tracker.apply(book):
            key = (update.side, update.price)
            if update.change is VisibilityChange.VISIBILITY_LOST:
                visible_quantities.pop(key, None)
                continue
            if update.change is VisibilityChange.CONFIRMED_ZERO:
                if key in visible_quantities:
                    feed_rows.append(
                        MicrostructureRow(
                            kind=_depth_kind(update.side),
                            exch_ts=event.exchange_ts_ns,
                            local_ts=local_ts,
                            price=update.price,
                            quantity=Decimal(0),
                        )
                    )
                    visible_quantities.pop(key, None)
                continue
            if update.quantity is None:
                raise ValueError("visible depth update must carry quantity")
            if visible_quantities.get(key) == update.quantity:
                continue
            feed_rows.append(
                MicrostructureRow(
                    kind=_depth_kind(update.side),
                    exch_ts=event.exchange_ts_ns,
                    local_ts=local_ts,
                    price=update.price,
                    quantity=update.quantity,
                )
            )
            visible_quantities[key] = update.quantity

    if not initial_book_seen:
        raise ValueError("canonical replay requires an initial book snapshot")
    if not feed_rows:
        raise ValueError(
            "canonical replay requires at least one feed event after the initial snapshot"
        )

    fixture = MicrostructureFixture(snapshot=tuple(snapshot_rows), feed=tuple(feed_rows))
    return CanonicalHftReplayFixture(
        fixture=fixture,
        receive_timestamp_mode=_receive_timestamp_mode(
            observed_count=observed_count,
            synthetic_count=synthetic_count,
        ),
        synthetic_receive_latency_ns=synthetic_receive_latency_ns,
    )


def _runtime_modules() -> tuple[ModuleType, ModuleType, ModuleType]:
    require_hftbacktest_runtime()
    return (
        import_module("hftbacktest"),
        import_module("hftbacktest.order"),
        import_module("numpy"),
    )


def _event_flags(hft: ModuleType, row: MicrostructureRow) -> int:
    if row.kind == "snapshot_bid":
        return int(hft.DEPTH_SNAPSHOT_EVENT | hft.BUY_EVENT | hft.EXCH_EVENT | hft.LOCAL_EVENT)
    if row.kind == "snapshot_ask":
        return int(hft.DEPTH_SNAPSHOT_EVENT | hft.SELL_EVENT | hft.EXCH_EVENT | hft.LOCAL_EVENT)
    if row.kind == "depth_bid":
        return int(hft.DEPTH_EVENT | hft.BUY_EVENT | hft.EXCH_EVENT | hft.LOCAL_EVENT)
    if row.kind == "depth_ask":
        return int(hft.DEPTH_EVENT | hft.SELL_EVENT | hft.EXCH_EVENT | hft.LOCAL_EVENT)
    if row.kind == "trade_buy":
        return int(hft.TRADE_EVENT | hft.BUY_EVENT | hft.EXCH_EVENT | hft.LOCAL_EVENT)
    if row.kind == "trade_sell":
        return int(hft.TRADE_EVENT | hft.SELL_EVENT | hft.EXCH_EVENT | hft.LOCAL_EVENT)
    raise ValueError(f"unsupported microstructure kind: {row.kind}")


def _to_event_array(hft: ModuleType, np: ModuleType, rows: tuple[MicrostructureRow, ...]) -> Any:
    array = np.zeros(len(rows), dtype=hft.event_dtype)
    for index, row in enumerate(rows):
        array[index]["ev"] = _event_flags(hft, row)
        array[index]["exch_ts"] = row.exch_ts
        array[index]["local_ts"] = row.local_ts
        array[index]["px"] = float(row.price)
        array[index]["qty"] = float(row.quantity)
        array[index]["order_id"] = 0
        array[index]["ival"] = 0
        array[index]["fval"] = 0.0
    return array


def _validate_order_alignment(
    intent: PassiveOrderIntent,
    config: HftReplayConfig,
) -> None:
    if intent.price % config.tick_size != 0:
        raise ValueError(f"order {intent.client_order_id} price is not tick aligned")
    if intent.quantity % config.lot_size != 0:
        raise ValueError(f"order {intent.client_order_id} quantity is not lot aligned")


def _wait_timeout_ns(fixture: MicrostructureFixture, config: HftReplayConfig) -> int:
    all_times = [
        *(row.local_ts for row in fixture.feed),
        *(row.exch_ts for row in fixture.feed),
    ]
    span = max(all_times) - min(all_times)
    latency_allowance = config.entry_latency_ns + config.response_latency_ns
    return max(1, span + latency_allowance + 1)


def _submit_order(
    hft: ModuleType,
    backtest: Any,
    *,
    asset_no: int,
    numeric_order_id: int,
    intent: PassiveOrderIntent,
) -> None:
    if intent.side is OrderSide.BUY:
        result = backtest.submit_buy_order(
            asset_no,
            numeric_order_id,
            float(intent.price),
            float(intent.quantity),
            hft.GTX,
            hft.LIMIT,
            True,
        )
    else:
        result = backtest.submit_sell_order(
            asset_no,
            numeric_order_id,
            float(intent.price),
            float(intent.quantity),
            hft.GTX,
            hft.LIMIT,
            True,
        )
    if result != 0:
        raise RuntimeError(f"hftbacktest failed to submit order {intent.client_order_id}: {result}")


def _ending_position_from_fills(
    fills: tuple[ReplayFill, ...],
    side_by_client_id: dict[str, OrderSide],
) -> Decimal:
    position = Decimal(0)
    for fill in fills:
        side = side_by_client_id[fill.client_order_id]
        if side is OrderSide.BUY:
            position += fill.quantity
        else:
            position -= fill.quantity
    return position


def replay_passive_orders(
    fixture: MicrostructureFixture,
    intents: tuple[PassiveOrderIntent, ...],
    config: HftReplayConfig,
) -> ReplaySummary:
    hft, hft_order, np = _runtime_modules()
    wait_timeout_ns = _wait_timeout_ns(fixture, config)
    if len({intent.client_order_id for intent in intents}) != len(intents):
        raise ValueError("passive replay requires unique client_order_id values")
    for intent in intents:
        _validate_order_alignment(intent, config)

    snapshot = _to_event_array(hft, np, fixture.snapshot)
    feed = _to_event_array(hft, np, fixture.feed)
    asset = (
        hft.BacktestAsset()
        .data(feed)
        .initial_snapshot(snapshot)
        .linear_asset(1.0)
        .constant_order_latency(config.entry_latency_ns, config.response_latency_ns)
        .risk_adverse_queue_model()
        .partial_fill_exchange()
        .trading_value_fee_model(float(config.maker_fee), float(config.taker_fee))
        .tick_size(float(config.tick_size))
        .lot_size(float(config.lot_size))
    )
    backtest = hft.HashMapMarketDepthBacktest([asset])
    numeric_to_client: dict[int, str] = {}
    side_by_client_id = {intent.client_order_id: intent.side for intent in intents}
    seen_execution: dict[int, tuple[int, Decimal, Decimal, Decimal]] = {}
    fills: list[ReplayFill] = []

    try:
        bootstrap_result = int(backtest.wait_next_feed(False, wait_timeout_ns))
        if bootstrap_result not in {0, 2}:
            raise RuntimeError(
                f"expected successful market-feed bootstrap, got {bootstrap_result}",
            )

        for numeric_order_id, intent in enumerate(intents, start=1):
            numeric_to_client[numeric_order_id] = intent.client_order_id
            _submit_order(
                hft,
                backtest,
                asset_no=0,
                numeric_order_id=numeric_order_id,
                intent=intent,
            )

        while True:
            result = int(backtest.wait_next_feed(True, wait_timeout_ns))
            if result == 1:
                break
            if result not in {0, 2, 3}:
                raise RuntimeError(f"unexpected hftbacktest feed result: {result}")
            if result != 3:
                continue

            orders = backtest.orders(0)
            for numeric_order_id, client_order_id in numeric_to_client.items():
                order = orders.get(numeric_order_id)
                if order is None or order.status not in {
                    hft_order.PARTIALLY_FILLED,
                    hft_order.FILLED,
                }:
                    continue
                quantity = Decimal(str(order.exec_qty))
                if quantity <= 0:
                    continue
                remaining_quantity = Decimal(str(order.leaves_qty))
                signature = (
                    int(order.exch_timestamp),
                    Decimal(str(order.exec_price)),
                    quantity,
                    remaining_quantity,
                )
                if seen_execution.get(numeric_order_id) == signature:
                    continue
                seen_execution[numeric_order_id] = signature
                fills.append(
                    ReplayFill(
                        client_order_id=client_order_id,
                        timestamp_ns=int(order.exch_timestamp),
                        price=Decimal(str(order.exec_price)),
                        quantity=quantity,
                        remaining_quantity=remaining_quantity,
                    ),
                )

        open_order_count = 0
        orders = backtest.orders(0)
        for numeric_order_id in numeric_to_client:
            order = orders.get(numeric_order_id)
            if order is not None and order.status in {
                hft_order.NEW,
                hft_order.PARTIALLY_FILLED,
            }:
                open_order_count += 1

        fill_tuple = tuple(fills)
        return ReplaySummary(
            fills=fill_tuple,
            ending_position=_ending_position_from_fills(fill_tuple, side_by_client_id),
            open_order_count=open_order_count,
        )
    finally:
        close_result = backtest.close()
        if close_result not in {None, 0}:
            raise RuntimeError(f"hftbacktest close failed: {close_result}")


__all__ = [
    "CanonicalHftReplayFixture",
    "HftReplayConfig",
    "MicrostructureFixture",
    "MicrostructureRow",
    "ReceiveTimestampMode",
    "ReplayFill",
    "ReplaySummary",
    "canonical_events_to_hftbacktest_fixture",
    "load_microstructure_fixture",
    "replay_passive_orders",
    "require_hftbacktest_runtime",
]
