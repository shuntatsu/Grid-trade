from decimal import Decimal

import pytest

from grid_trade.datasets.canonical import (
    CanonicalBookLevel,
    CanonicalBookSnapshot,
    CanonicalEventEnvelope,
    CanonicalEventType,
)
from grid_trade.domain.orders import OrderSide
from grid_trade.research.replay_attribution import (
    MarketImpactEligibilityConfig,
    first_order_visibility_loss_ns,
    summarize_order_liquidity,
    assess_order_liquidity_eligibility,
)

pytestmark = pytest.mark.research

_RAW_HASH = "a" * 64


def _book(
    timestamp_ns: int,
    *,
    bids: tuple[str, ...],
    asks: tuple[str, ...],
    ordinal: int,
) -> CanonicalEventEnvelope:
    return CanonicalEventEnvelope(
        event_type=CanonicalEventType.BOOK_SNAPSHOT,
        instrument="BTC",
        exchange_ts_ns=timestamp_ns,
        local_receive_ts_ns=None,
        source_sequence=None,
        raw_object_sha256=_RAW_HASH,
        raw_record_ordinal=ordinal,
        normalization_schema_version="canonical-v1",
        payload=CanonicalBookSnapshot(
            bids=tuple(
                CanonicalBookLevel(Decimal(price), Decimal("10"), 1) for price in bids
            ),
            asks=tuple(
                CanonicalBookLevel(Decimal(price), Decimal("10"), 1) for price in asks
            ),
        ),
    )


def _eligibility(*, quantity: str, same_level: str, top_n: str):
    return assess_order_liquidity_eligibility(
        order_price=Decimal("100"),
        order_quantity=Decimal(quantity),
        visible_same_level_quantity=Decimal(same_level),
        visible_top_n_notional=Decimal(top_n),
        visibility_trusted=True,
        config=MarketImpactEligibilityConfig(
            max_same_level_participation=Decimal("1"),
            max_top_n_participation=Decimal("1"),
        ),
    )


def test_first_visibility_loss_is_recorded_without_treating_confirmed_zero_as_loss() -> None:
    events = (
        _book(100, bids=("100", "99", "98"), asks=("101", "102", "103"), ordinal=0),
        # 99 disappears but remains inside the newly observable bid range: confirmed zero.
        _book(200, bids=("100", "98", "97"), asks=("101", "102", "103"), ordinal=1),
        # 98 is now outside the newly observable bid range: visibility is lost.
        _book(300, bids=("102", "101", "100"), asks=("103", "104", "105"), ordinal=2),
    )

    assert first_order_visibility_loss_ns(
        events,
        side=OrderSide.BUY,
        price=Decimal("99"),
    ) is None
    assert first_order_visibility_loss_ns(
        events,
        side=OrderSide.BUY,
        price=Decimal("98"),
    ) == 300


def test_visibility_loss_for_sell_uses_ask_side() -> None:
    events = (
        _book(100, bids=("100", "99", "98"), asks=("101", "102", "103"), ordinal=0),
        _book(200, bids=("102", "101", "100"), asks=("103", "104", "105"), ordinal=1),
    )

    assert first_order_visibility_loss_ns(
        events,
        side=OrderSide.SELL,
        price=Decimal("105"),
    ) is None
    assert first_order_visibility_loss_ns(
        events,
        side=OrderSide.SELL,
        price=Decimal("103"),
    ) is None
    assert first_order_visibility_loss_ns(
        events,
        side=OrderSide.SELL,
        price=Decimal("102"),
    ) == 200


def test_liquidity_summary_records_max_q95_and_earliest_visibility_boundary() -> None:
    decisions = (
        _eligibility(quantity="1", same_level="10", top_n="5000"),
        _eligibility(quantity="2", same_level="10", top_n="5000"),
        _eligibility(quantity="3", same_level="10", top_n="5000"),
        _eligibility(quantity="4", same_level="10", top_n="5000"),
    )
    decisions = (
        decisions[0].with_visibility_boundary(900),
        decisions[1],
        decisions[2].with_visibility_boundary(700),
        decisions[3],
    )

    summary = summarize_order_liquidity(decisions)

    assert summary.participation_quantile == Decimal("0.95")
    assert summary.max_same_level_participation == Decimal("0.4")
    assert summary.high_quantile_same_level_participation == Decimal("0.4")
    assert summary.max_top_n_participation == Decimal("0.08")
    assert summary.high_quantile_top_n_participation == Decimal("0.08")
    assert summary.earliest_visibility_boundary_ts_ns == 700


def test_liquidity_summary_handles_unavailable_participation_without_zero_filling() -> None:
    unavailable = assess_order_liquidity_eligibility(
        order_price=Decimal("100"),
        order_quantity=Decimal("1"),
        visible_same_level_quantity=None,
        visible_top_n_notional=Decimal("5000"),
        visibility_trusted=True,
        config=MarketImpactEligibilityConfig(
            max_same_level_participation=Decimal("1"),
            max_top_n_participation=Decimal("1"),
        ),
    )

    summary = summarize_order_liquidity((unavailable,))

    assert summary.max_same_level_participation is None
    assert summary.high_quantile_same_level_participation is None
    assert summary.max_top_n_participation is None
    assert summary.high_quantile_top_n_participation is None
