from decimal import Decimal

from grid_trade.domain.orders import OrderSide, PassiveOrderIntent, ReconciliationPlan, WorkingOrder
from grid_trade.execution.reconcile import reconcile_passive_orders


def test_instrument_identity_is_part_of_reconciliation_match() -> None:
    desired = PassiveOrderIntent(
        client_order_id="same-id",
        generation=0,
        level=1,
        side=OrderSide.BUY,
        price=Decimal("98"),
        quantity=Decimal("0.01"),
        instrument_id="BTC-PERP",
    )
    working = WorkingOrder(
        client_order_id="same-id",
        generation=0,
        level=1,
        side=OrderSide.BUY,
        price=Decimal("98"),
        quantity=Decimal("0.01"),
        filled_quantity=Decimal("0"),
        instrument_id="ETH-PERP",
    )

    assert reconcile_passive_orders(desired=(desired,), working=(working,)) == ReconciliationPlan(
        cancel=("same-id",),
        submit=(),
    )
