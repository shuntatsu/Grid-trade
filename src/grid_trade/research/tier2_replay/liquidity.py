from dataclasses import replace
from decimal import Decimal

from grid_trade.datasets.canonical import (
    CanonicalBookSnapshot,
    CanonicalEventEnvelope,
    CanonicalEventType,
)
from grid_trade.domain.orders import OrderSide, PassiveOrderIntent
from grid_trade.research.replay_attribution import (
    MarketImpactEligibilityConfig,
    OrderLiquidityEligibility,
    assess_order_liquidity_eligibility,
    first_order_visibility_loss_ns,
)


def _order_liquidity(
    *,
    book: CanonicalBookSnapshot,
    order: PassiveOrderIntent,
    config: MarketImpactEligibilityConfig,
) -> OrderLiquidityEligibility:
    levels = book.bids if order.side is OrderSide.BUY else book.asks
    visible_same_level_quantity = next(
        (level.quantity for level in levels if level.price == order.price),
        None,
    )
    visible_top_n_notional = sum(
        (level.price * level.quantity for level in levels),
        Decimal(0),
    )
    return assess_order_liquidity_eligibility(
        order_price=order.price,
        order_quantity=order.quantity,
        visible_same_level_quantity=visible_same_level_quantity,
        visible_top_n_notional=visible_top_n_notional,
        visibility_trusted=True,
        config=config,
    )


def _attach_visibility_boundary(
    *,
    events: tuple[CanonicalEventEnvelope, ...],
    order: PassiveOrderIntent,
    eligibility: OrderLiquidityEligibility,
) -> OrderLiquidityEligibility:
    if not eligibility.eligible:
        return eligibility
    boundary = first_order_visibility_loss_ns(
        events,
        side=order.side,
        price=order.price,
    )
    return replace(eligibility, visibility_boundary_ts_ns=boundary)


def _trusted_replay_events(
    events: tuple[CanonicalEventEnvelope, ...],
    *,
    visibility_boundary_ts_ns: int | None,
) -> tuple[CanonicalEventEnvelope, ...]:
    if visibility_boundary_ts_ns is None:
        return events
    return tuple(event for event in events if event.exchange_ts_ns < visibility_boundary_ts_ns)


def _has_market_feed_after_initial(events: tuple[CanonicalEventEnvelope, ...]) -> bool:
    market_event_count = sum(
        event.event_type in {CanonicalEventType.BOOK_SNAPSHOT, CanonicalEventType.TRADE}
        for event in events
    )
    return market_event_count > 1
