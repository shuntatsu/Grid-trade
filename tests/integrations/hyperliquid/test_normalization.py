import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from grid_trade.datasets.canonical import (
    CanonicalBookSnapshot,
    CanonicalEventType,
    CanonicalFundingReference,
    CanonicalTrade,
    TradeSide,
)
from grid_trade.integrations.hyperliquid.normalization import (
    ASSET_CONTEXT_DECODER_VERSION,
    L2_BOOK_DECODER_VERSION,
    TRADES_DECODER_VERSION,
    normalize_l2_book,
    normalize_meta_and_asset_ctxs,
    normalize_ws_trades,
)

pytestmark = pytest.mark.research

_FIXTURES = Path("tests/fixtures/hyperliquid")
_RAW_HASH = "a" * 64


def _fixture(name: str) -> Any:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def test_l2_book_normalization_preserves_decimal_and_provenance() -> None:
    event = normalize_l2_book(
        _fixture("l2book.json"),
        raw_object_sha256=_RAW_HASH,
        raw_record_ordinal=7,
        local_receive_ts_ns=1_754_450_974_240_000_000,
    )

    assert event.event_type is CanonicalEventType.BOOK_SNAPSHOT
    assert event.instrument == "BTC"
    assert event.exchange_ts_ns == 1_754_450_974_231_000_000
    assert event.local_receive_ts_ns == 1_754_450_974_240_000_000
    assert event.raw_object_sha256 == _RAW_HASH
    assert event.raw_record_ordinal == 7
    assert event.normalization_schema_version == L2_BOOK_DECODER_VERSION

    assert isinstance(event.payload, CanonicalBookSnapshot)
    assert event.payload.bids[0].price == Decimal("113377.0")
    assert event.payload.bids[0].quantity == Decimal("7.6699")
    assert event.payload.bids[0].order_count == 17
    assert event.payload.asks[1].price == Decimal("113398.0")
    assert str(event.payload.asks[1].quantity) == "1.25000"


def test_ws_trade_normalization_uses_official_global_identity_and_side_semantics() -> None:
    events = normalize_ws_trades(
        _fixture("trades.json"),
        raw_object_sha256=_RAW_HASH,
        first_raw_record_ordinal=20,
        local_receive_ts_ns=1_754_450_974_260_000_000,
    )

    assert len(events) == 2
    buy, sell = events
    assert buy.event_type is CanonicalEventType.TRADE
    assert buy.exchange_ts_ns == 1_754_450_974_250_000_000
    assert buy.raw_record_ordinal == 20
    assert buy.normalization_schema_version == TRADES_DECODER_VERSION
    assert isinstance(buy.payload, CanonicalTrade)
    assert buy.payload.side is TradeSide.BUY
    assert buy.payload.price == Decimal("113390.5")
    assert buy.payload.quantity == Decimal("0.0100")
    assert buy.payload.stable_identity == "1754450974250:BTC:123456789"

    assert sell.raw_record_ordinal == 21
    assert isinstance(sell.payload, CanonicalTrade)
    assert sell.payload.side is TradeSide.SELL
    assert sell.payload.stable_identity == "1754450974251:BTC:123456790"


def test_meta_and_asset_ctxs_preserves_missing_reference_as_unavailable() -> None:
    btc = normalize_meta_and_asset_ctxs(
        _fixture("meta_and_asset_ctxs.json"),
        instrument="BTC",
        exchange_ts_ns=1_754_450_978_000_000_000,
        raw_object_sha256=_RAW_HASH,
        raw_record_ordinal=0,
    )
    assert btc.normalization_schema_version == ASSET_CONTEXT_DECODER_VERSION
    assert isinstance(btc.payload, CanonicalFundingReference)
    assert btc.payload.funding_rate == Decimal("0.0000125")
    assert btc.payload.mark_price == Decimal("113390.0")
    assert btc.payload.oracle_price == Decimal("113380.0")

    eth = normalize_meta_and_asset_ctxs(
        _fixture("meta_and_asset_ctxs.json"),
        instrument="ETH",
        exchange_ts_ns=1_754_450_978_000_000_000,
        raw_object_sha256=_RAW_HASH,
        raw_record_ordinal=1,
    )
    assert isinstance(eth.payload, CanonicalFundingReference)
    assert eth.payload.funding_rate is None
    assert eth.payload.mark_price is None
    assert eth.payload.oracle_price is None


def test_unknown_decoder_version_fails_closed() -> None:
    with pytest.raises(ValueError, match="decoder schema"):
        normalize_l2_book(
            _fixture("l2book.json"),
            raw_object_sha256=_RAW_HASH,
            raw_record_ordinal=0,
            decoder_schema_version="future-v99",
        )
