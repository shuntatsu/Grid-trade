from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import cast

from grid_trade.datasets.canonical import (
    CanonicalBookLevel,
    CanonicalBookSnapshot,
    CanonicalEventEnvelope,
    CanonicalEventType,
    CanonicalFundingReference,
    CanonicalTrade,
    TradeSide,
)

L2_BOOK_DECODER_VERSION = "hyperliquid-l2book-v1"
TRADES_DECODER_VERSION = "hyperliquid-trades-v1"
ASSET_CONTEXT_DECODER_VERSION = "hyperliquid-meta-asset-ctxs-v1"


def _mapping(value: object, *, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field} keys must be strings")
    return cast(Mapping[str, object], value)


def _list(value: object, *, field: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    return value


def _string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    if value < 0:
        raise ValueError(f"{field} must be non-negative")
    return value


def _decimal(value: object, *, field: str) -> Decimal:
    text = _string(value, field=field)
    try:
        parsed = Decimal(text)
    except InvalidOperation as error:
        raise ValueError(f"{field} must be a decimal string") from error
    if not parsed.is_finite():
        raise ValueError(f"{field} must be finite")
    return parsed


def _optional_decimal(value: object, *, field: str) -> Decimal | None:
    if value is None:
        return None
    return _decimal(value, field=field)


def _require_decoder(actual: str, *, expected: str) -> None:
    if actual != expected:
        raise ValueError(f"unsupported decoder schema: expected {expected}, got {actual}")


def _book_level(value: object, *, field: str) -> CanonicalBookLevel:
    level = _mapping(value, field=field)
    try:
        price = _decimal(level["px"], field=f"{field}.px")
        quantity = _decimal(level["sz"], field=f"{field}.sz")
        order_count = _integer(level["n"], field=f"{field}.n")
    except KeyError as error:
        raise ValueError(f"{field} is missing required key {error.args[0]}") from error
    return CanonicalBookLevel(price=price, quantity=quantity, order_count=order_count)


def normalize_l2_book(
    payload: object,
    *,
    raw_object_sha256: str,
    raw_record_ordinal: int,
    local_receive_ts_ns: int | None = None,
    source_sequence: int | None = None,
    decoder_schema_version: str = L2_BOOK_DECODER_VERSION,
) -> CanonicalEventEnvelope:
    _require_decoder(decoder_schema_version, expected=L2_BOOK_DECODER_VERSION)
    book = _mapping(payload, field="l2Book")
    try:
        instrument = _string(book["coin"], field="l2Book.coin")
        exchange_time_ms = _integer(book["time"], field="l2Book.time")
        sides = _list(book["levels"], field="l2Book.levels")
    except KeyError as error:
        raise ValueError(f"l2Book is missing required key {error.args[0]}") from error
    if len(sides) != 2:
        raise ValueError("l2Book.levels must contain exactly bid and ask arrays")

    bids_raw = _list(sides[0], field="l2Book.levels[0]")
    asks_raw = _list(sides[1], field="l2Book.levels[1]")
    snapshot = CanonicalBookSnapshot(
        bids=tuple(
            _book_level(level, field=f"l2Book.levels[0][{index}]")
            for index, level in enumerate(bids_raw)
        ),
        asks=tuple(
            _book_level(level, field=f"l2Book.levels[1][{index}]")
            for index, level in enumerate(asks_raw)
        ),
    )
    return CanonicalEventEnvelope(
        event_type=CanonicalEventType.BOOK_SNAPSHOT,
        instrument=instrument,
        exchange_ts_ns=exchange_time_ms * 1_000_000,
        local_receive_ts_ns=local_receive_ts_ns,
        source_sequence=source_sequence,
        raw_object_sha256=raw_object_sha256,
        raw_record_ordinal=raw_record_ordinal,
        normalization_schema_version=decoder_schema_version,
        payload=snapshot,
    )


def _trade_side(value: object) -> TradeSide:
    side = _string(value, field="trade.side")
    if side == "B":
        return TradeSide.BUY
    if side == "A":
        return TradeSide.SELL
    raise ValueError(f"unsupported Hyperliquid trade side: {side}")


def normalize_ws_trades(
    payload: object,
    *,
    raw_object_sha256: str,
    first_raw_record_ordinal: int,
    local_receive_ts_ns: int | None = None,
    decoder_schema_version: str = TRADES_DECODER_VERSION,
) -> tuple[CanonicalEventEnvelope, ...]:
    _require_decoder(decoder_schema_version, expected=TRADES_DECODER_VERSION)
    trades = _list(payload, field="trades")
    events: list[CanonicalEventEnvelope] = []
    expected_instrument: str | None = None

    for index, raw_trade in enumerate(trades):
        trade = _mapping(raw_trade, field=f"trades[{index}]")
        try:
            instrument = _string(trade["coin"], field=f"trades[{index}].coin")
            side = _trade_side(trade["side"])
            price = _decimal(trade["px"], field=f"trades[{index}].px")
            quantity = _decimal(trade["sz"], field=f"trades[{index}].sz")
            exchange_time_ms = _integer(trade["time"], field=f"trades[{index}].time")
            trade_id = _integer(trade["tid"], field=f"trades[{index}].tid")
        except KeyError as error:
            raise ValueError(f"trades[{index}] is missing required key {error.args[0]}") from error

        if expected_instrument is None:
            expected_instrument = instrument
        elif instrument != expected_instrument:
            raise ValueError("one trades payload must not mix instruments")

        events.append(
            CanonicalEventEnvelope(
                event_type=CanonicalEventType.TRADE,
                instrument=instrument,
                exchange_ts_ns=exchange_time_ms * 1_000_000,
                local_receive_ts_ns=local_receive_ts_ns,
                source_sequence=None,
                raw_object_sha256=raw_object_sha256,
                raw_record_ordinal=first_raw_record_ordinal + index,
                normalization_schema_version=decoder_schema_version,
                payload=CanonicalTrade(
                    side=side,
                    price=price,
                    quantity=quantity,
                    stable_identity=f"{exchange_time_ms}:{instrument}:{trade_id}",
                ),
            )
        )

    return tuple(events)


def normalize_meta_and_asset_ctxs(
    payload: object,
    *,
    instrument: str,
    exchange_ts_ns: int,
    raw_object_sha256: str,
    raw_record_ordinal: int,
    local_receive_ts_ns: int | None = None,
    decoder_schema_version: str = ASSET_CONTEXT_DECODER_VERSION,
) -> CanonicalEventEnvelope:
    _require_decoder(decoder_schema_version, expected=ASSET_CONTEXT_DECODER_VERSION)
    response = _list(payload, field="metaAndAssetCtxs")
    if len(response) != 2:
        raise ValueError("metaAndAssetCtxs must contain metadata and asset contexts")

    metadata = _mapping(response[0], field="metaAndAssetCtxs[0]")
    try:
        universe = _list(metadata["universe"], field="metaAndAssetCtxs[0].universe")
    except KeyError as error:
        raise ValueError("metaAndAssetCtxs metadata is missing universe") from error
    contexts = _list(response[1], field="metaAndAssetCtxs[1]")
    if len(universe) != len(contexts):
        raise ValueError("metaAndAssetCtxs universe and context arrays must align")

    matching_index: int | None = None
    for index, raw_asset in enumerate(universe):
        asset = _mapping(raw_asset, field=f"metaAndAssetCtxs[0].universe[{index}]")
        try:
            name = _string(asset["name"], field=f"metaAndAssetCtxs[0].universe[{index}].name")
        except KeyError as error:
            raise ValueError(f"universe[{index}] is missing name") from error
        if name == instrument:
            matching_index = index
            break
    if matching_index is None:
        raise ValueError(f"instrument {instrument} is absent from metaAndAssetCtxs universe")

    context = _mapping(
        contexts[matching_index],
        field=f"metaAndAssetCtxs[1][{matching_index}]",
    )
    reference = CanonicalFundingReference(
        funding_rate=_optional_decimal(context.get("funding"), field="asset_ctx.funding"),
        mark_price=_optional_decimal(context.get("markPx"), field="asset_ctx.markPx"),
        oracle_price=_optional_decimal(context.get("oraclePx"), field="asset_ctx.oraclePx"),
    )
    return CanonicalEventEnvelope(
        event_type=CanonicalEventType.FUNDING_REFERENCE,
        instrument=instrument,
        exchange_ts_ns=exchange_ts_ns,
        local_receive_ts_ns=local_receive_ts_ns,
        source_sequence=None,
        raw_object_sha256=raw_object_sha256,
        raw_record_ordinal=raw_record_ordinal,
        normalization_schema_version=decoder_schema_version,
        payload=reference,
    )


__all__ = [
    "ASSET_CONTEXT_DECODER_VERSION",
    "L2_BOOK_DECODER_VERSION",
    "TRADES_DECODER_VERSION",
    "normalize_l2_book",
    "normalize_meta_and_asset_ctxs",
    "normalize_ws_trades",
]
