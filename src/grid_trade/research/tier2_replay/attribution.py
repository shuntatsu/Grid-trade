from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from grid_trade.datasets.canonical import (
    CanonicalEventEnvelope,
    CanonicalEventType,
    CanonicalFundingReference,
)
from grid_trade.domain.orders import OrderSide
from grid_trade.research.replay_attribution import FundingCashFlow, funding_cash_flow
from grid_trade.research.tier2_replay.dataset import _HOUR_NS

if TYPE_CHECKING:
    from grid_trade.research.hftbacktest_adapter import ReplaySummary


def _signed_fill_quantity(
    *,
    client_order_id: str,
    quantity: Decimal,
    side_by_client_id: dict[str, OrderSide],
) -> Decimal:
    side = side_by_client_id[client_order_id]
    return quantity if side is OrderSide.BUY else -quantity


def _position_at(
    *,
    timestamp_ns: int,
    starting_position: Decimal,
    replay_summary: ReplaySummary,
    side_by_client_id: dict[str, OrderSide],
) -> Decimal:
    position = starting_position
    for fill in replay_summary.fills:
        if fill.timestamp_ns > timestamp_ns:
            break
        position += _signed_fill_quantity(
            client_order_id=fill.client_order_id,
            quantity=fill.quantity,
            side_by_client_id=side_by_client_id,
        )
    return position


def _funding_cash_flows(
    *,
    events: tuple[CanonicalEventEnvelope, ...],
    starting_position: Decimal,
    replay_summary: ReplaySummary,
    side_by_client_id: dict[str, OrderSide],
) -> tuple[FundingCashFlow, ...]:
    flows: list[FundingCashFlow] = []
    for event in events:
        if event.event_type is not CanonicalEventType.FUNDING_REFERENCE:
            continue
        if event.exchange_ts_ns % _HOUR_NS != 0:
            continue
        reference = event.payload
        if not isinstance(reference, CanonicalFundingReference):
            raise TypeError("validated funding event must carry CanonicalFundingReference payload")
        position = _position_at(
            timestamp_ns=event.exchange_ts_ns,
            starting_position=starting_position,
            replay_summary=replay_summary,
            side_by_client_id=side_by_client_id,
        )
        flows.append(
            funding_cash_flow(
                timestamp_ns=event.exchange_ts_ns,
                position=position,
                reference=reference,
            )
        )
    return tuple(flows)
