from decimal import Decimal

import pytest

from grid_trade.domain.orders import OrderSide, PassiveOrderIntent
from grid_trade.research.hftbacktest_adapter import (
    HftReplayConfig,
    MicrostructureFixture,
    MicrostructureRow,
    replay_passive_orders,
)

pytestmark = pytest.mark.research


def test_pinned_runtime_fails_closed_on_exact_queue_terminal_edge() -> None:
    """hftbacktest 2.4.4 leaves an exactly-filled exchange order resident.

    This fixture intentionally consumes queue-ahead plus our full order quantity exactly.
    The following same-price trade then reaches the pinned runtime's InvalidOrderStatus
    path (C binding code 14). Grid-trade must fail closed rather than treating that
    third-party runtime error as a valid replay result.
    """
    fixture = MicrostructureFixture(
        snapshot=(
            MicrostructureRow(
                "snapshot_bid", 1_000_000_000, 1_000_000_000, Decimal("99.0"), Decimal("0.02")
            ),
            MicrostructureRow(
                "snapshot_ask", 1_000_000_000, 1_000_000_000, Decimal("101.0"), Decimal("0.02")
            ),
        ),
        feed=(
            MicrostructureRow(
                "depth_bid", 2_000_000_000, 2_000_000_000, Decimal("99.0"), Decimal("0.02")
            ),
            MicrostructureRow(
                "depth_ask", 2_000_000_000, 2_000_000_000, Decimal("101.0"), Decimal("0.03")
            ),
            MicrostructureRow(
                "trade_sell", 3_000_000_000, 3_000_000_000, Decimal("99.0"), Decimal("0.01")
            ),
            MicrostructureRow(
                "trade_sell", 4_000_000_000, 4_000_000_000, Decimal("99.0"), Decimal("0.02")
            ),
            MicrostructureRow(
                "trade_sell", 5_000_000_000, 5_000_000_000, Decimal("99.0"), Decimal("0.02")
            ),
        ),
    )
    intent = PassiveOrderIntent(
        client_order_id="known-limit:g0:buy:l0",
        generation=0,
        level=0,
        side=OrderSide.BUY,
        price=Decimal("99.0"),
        quantity=Decimal("0.01"),
    )
    config = HftReplayConfig(
        tick_size=Decimal("0.1"),
        lot_size=Decimal("0.01"),
        maker_fee=Decimal("0.0001"),
        taker_fee=Decimal("0.0005"),
    )

    with pytest.raises(RuntimeError, match=r"feed result: 14"):
        replay_passive_orders(fixture, (intent,), config)
