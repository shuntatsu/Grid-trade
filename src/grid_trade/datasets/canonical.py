from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise


class CanonicalEventType(StrEnum):
    BOOK_SNAPSHOT = "book_snapshot"
    TRADE = "trade"
    FUNDING_REFERENCE = "funding_reference"


class TradeSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class BookSide(StrEnum):
    BID = "bid"
    ASK = "ask"


class VisibilityChange(StrEnum):
    UPSERT = "upsert"
    CONFIRMED_ZERO = "confirmed_zero"
    VISIBILITY_LOST = "visibility_lost"
    REESTABLISHED = "reestablished"


class _VisibilityState(StrEnum):
    VISIBLE = "visible"
    ZERO = "zero"
    LOST = "lost"


_EVENT_TYPE_PRECEDENCE: dict[CanonicalEventType, int] = {
    CanonicalEventType.BOOK_SNAPSHOT: 0,
    CanonicalEventType.TRADE: 1,
    CanonicalEventType.FUNDING_REFERENCE: 2,
}


def _require_non_empty(value: str, *, field: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field} must be non-empty")


def _require_positive_finite(value: Decimal, *, field: str) -> None:
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{field} must be finite and positive")


def _require_finite(value: Decimal, *, field: str) -> None:
    if not value.is_finite():
        raise ValueError(f"{field} must be finite")


def _require_sha256(value: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("raw_object_sha256 must be 64 lowercase hexadecimal characters")


@dataclass(frozen=True, slots=True)
class CanonicalBookLevel:
    price: Decimal
    quantity: Decimal
    order_count: int

    def __post_init__(self) -> None:
        _require_positive_finite(self.price, field="price")
        _require_positive_finite(self.quantity, field="quantity")
        if self.order_count < 0:
            raise ValueError("order_count must be non-negative")


@dataclass(frozen=True, slots=True)
class CanonicalBookSnapshot:
    bids: tuple[CanonicalBookLevel, ...]
    asks: tuple[CanonicalBookLevel, ...]

    def __post_init__(self) -> None:
        bid_prices = tuple(level.price for level in self.bids)
        ask_prices = tuple(level.price for level in self.asks)
        if len(set(bid_prices)) != len(bid_prices):
            raise ValueError("bids must not contain duplicate prices")
        if len(set(ask_prices)) != len(ask_prices):
            raise ValueError("asks must not contain duplicate prices")
        if any(left <= right for left, right in pairwise(bid_prices)):
            raise ValueError("bids must be strictly descending by price")
        if any(left >= right for left, right in pairwise(ask_prices)):
            raise ValueError("asks must be strictly ascending by price")
        if self.bids and self.asks and self.bids[0].price >= self.asks[0].price:
            raise ValueError("book must not be crossed or locked")


@dataclass(frozen=True, slots=True)
class CanonicalTrade:
    side: TradeSide
    price: Decimal
    quantity: Decimal
    stable_identity: str

    def __post_init__(self) -> None:
        _require_positive_finite(self.price, field="price")
        _require_positive_finite(self.quantity, field="quantity")
        _require_non_empty(self.stable_identity, field="stable_identity")


@dataclass(frozen=True, slots=True)
class CanonicalFundingReference:
    funding_rate: Decimal | None
    mark_price: Decimal | None
    oracle_price: Decimal | None

    def __post_init__(self) -> None:
        if self.funding_rate is not None:
            _require_finite(self.funding_rate, field="funding_rate")
        if self.mark_price is not None:
            _require_positive_finite(self.mark_price, field="mark_price")
        if self.oracle_price is not None:
            _require_positive_finite(self.oracle_price, field="oracle_price")


CanonicalPayload = CanonicalBookSnapshot | CanonicalTrade | CanonicalFundingReference


@dataclass(frozen=True, slots=True)
class CanonicalEventEnvelope:
    event_type: CanonicalEventType
    instrument: str
    exchange_ts_ns: int
    local_receive_ts_ns: int | None
    source_sequence: int | None
    raw_object_sha256: str
    raw_record_ordinal: int
    normalization_schema_version: str
    payload: CanonicalPayload

    def __post_init__(self) -> None:
        _require_non_empty(self.instrument, field="instrument")
        _require_non_empty(self.normalization_schema_version, field="normalization_schema_version")
        _require_sha256(self.raw_object_sha256)
        if self.exchange_ts_ns < 0:
            raise ValueError("exchange_ts_ns must be non-negative")
        if self.local_receive_ts_ns is not None and self.local_receive_ts_ns < 0:
            raise ValueError("local_receive_ts_ns must be non-negative")
        if self.source_sequence is not None and self.source_sequence < 0:
            raise ValueError("source_sequence must be non-negative")
        if self.raw_record_ordinal < 0:
            raise ValueError("raw_record_ordinal must be non-negative")

        expected_payload_type: type[CanonicalPayload]
        if self.event_type is CanonicalEventType.BOOK_SNAPSHOT:
            expected_payload_type = CanonicalBookSnapshot
        elif self.event_type is CanonicalEventType.TRADE:
            expected_payload_type = CanonicalTrade
        else:
            expected_payload_type = CanonicalFundingReference
        if not isinstance(self.payload, expected_payload_type):
            raise ValueError("payload type must match declared event_type")


def canonical_event_sort_key(event: CanonicalEventEnvelope) -> tuple[int, int, int, int, str, int]:
    sequence_missing = int(event.source_sequence is None)
    sequence_value = 0 if event.source_sequence is None else event.source_sequence
    return (
        event.exchange_ts_ns,
        sequence_missing,
        sequence_value,
        _EVENT_TYPE_PRECEDENCE[event.event_type],
        event.raw_object_sha256,
        event.raw_record_ordinal,
    )


@dataclass(frozen=True, slots=True)
class VisibilityEpoch:
    side: BookSide
    price: Decimal
    epoch_id: int
    active: bool


@dataclass(frozen=True, slots=True)
class VisibleDepthUpdate:
    side: BookSide
    price: Decimal
    quantity: Decimal | None
    order_count: int | None
    epoch_id: int
    change: VisibilityChange


class BookVisibilityTracker:
    def __init__(self) -> None:
        self._states: dict[BookSide, dict[Decimal, _VisibilityState]] = {
            BookSide.BID: {},
            BookSide.ASK: {},
        }
        self._epochs: dict[tuple[BookSide, Decimal], int] = {}

    @staticmethod
    def _levels_for_side(
        snapshot: CanonicalBookSnapshot,
        side: BookSide,
    ) -> tuple[CanonicalBookLevel, ...]:
        return snapshot.bids if side is BookSide.BID else snapshot.asks

    @staticmethod
    def _inside_deep_boundary(*, side: BookSide, price: Decimal, deep_boundary: Decimal) -> bool:
        if side is BookSide.BID:
            return price >= deep_boundary
        return price <= deep_boundary

    def apply(self, snapshot: CanonicalBookSnapshot) -> tuple[VisibleDepthUpdate, ...]:
        updates: list[VisibleDepthUpdate] = []
        for side in (BookSide.BID, BookSide.ASK):
            current_levels = self._levels_for_side(snapshot, side)
            current = {level.price: level for level in current_levels}
            states = self._states[side]
            deep_boundary = current_levels[-1].price if current_levels else None

            for price in tuple(states):
                if price in current:
                    continue
                key = (side, price)
                state = states[price]
                epoch_id = self._epochs.setdefault(key, 0)
                inside_boundary = deep_boundary is not None and self._inside_deep_boundary(
                    side=side,
                    price=price,
                    deep_boundary=deep_boundary,
                )

                if inside_boundary:
                    if state is _VisibilityState.VISIBLE:
                        states[price] = _VisibilityState.ZERO
                        updates.append(
                            VisibleDepthUpdate(
                                side=side,
                                price=price,
                                quantity=Decimal(0),
                                order_count=0,
                                epoch_id=epoch_id,
                                change=VisibilityChange.CONFIRMED_ZERO,
                            )
                        )
                    elif state is _VisibilityState.LOST:
                        epoch_id += 1
                        self._epochs[key] = epoch_id
                        states[price] = _VisibilityState.ZERO
                        updates.append(
                            VisibleDepthUpdate(
                                side=side,
                                price=price,
                                quantity=Decimal(0),
                                order_count=0,
                                epoch_id=epoch_id,
                                change=VisibilityChange.REESTABLISHED,
                            )
                        )
                    continue

                if state is not _VisibilityState.LOST:
                    states[price] = _VisibilityState.LOST
                    updates.append(
                        VisibleDepthUpdate(
                            side=side,
                            price=price,
                            quantity=None,
                            order_count=None,
                            epoch_id=epoch_id,
                            change=VisibilityChange.VISIBILITY_LOST,
                        )
                    )

            for level in current_levels:
                key = (side, level.price)
                state = states.get(level.price)
                if state in {_VisibilityState.ZERO, _VisibilityState.LOST}:
                    epoch_id = self._epochs.get(key, 0) + 1
                    self._epochs[key] = epoch_id
                    change = VisibilityChange.REESTABLISHED
                else:
                    epoch_id = self._epochs.setdefault(key, 0)
                    change = VisibilityChange.UPSERT
                states[level.price] = _VisibilityState.VISIBLE
                updates.append(
                    VisibleDepthUpdate(
                        side=side,
                        price=level.price,
                        quantity=level.quantity,
                        order_count=level.order_count,
                        epoch_id=epoch_id,
                        change=change,
                    )
                )
        return tuple(updates)


__all__ = [
    "BookSide",
    "BookVisibilityTracker",
    "CanonicalBookLevel",
    "CanonicalBookSnapshot",
    "CanonicalEventEnvelope",
    "CanonicalEventType",
    "CanonicalFundingReference",
    "CanonicalPayload",
    "CanonicalTrade",
    "TradeSide",
    "VisibilityChange",
    "VisibilityEpoch",
    "VisibleDepthUpdate",
    "canonical_event_sort_key",
]
