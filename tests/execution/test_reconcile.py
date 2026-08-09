from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from grid_trade.domain.orders import (
    OrderSide,
    PassiveOrderIntent,
    ReconciliationPlan,
    WorkingOrder,
)
from grid_trade.execution.reconcile import reconcile_passive_orders


def _intent(
    *,
    generation: int = 1,
    level: int = 1,
    price: str = "99.00",
    quantity: str = "0.10",
) -> PassiveOrderIntent:
    return PassiveOrderIntent(
        client_order_id=f"s0:g{generation}:buy:l{level}",
        generation=generation,
        level=level,
        side=OrderSide.BUY,
        price=Decimal(price),
        quantity=Decimal(quantity),
        reduce_only=False,
    )


def _working(
    intent: PassiveOrderIntent,
    *,
    price: Decimal | None = None,
    quantity: Decimal | None = None,
    filled_quantity: Decimal = Decimal("0"),
) -> WorkingOrder:
    return WorkingOrder(
        client_order_id=intent.client_order_id,
        generation=intent.generation,
        level=intent.level,
        side=intent.side,
        price=intent.price if price is None else price,
        quantity=intent.quantity if quantity is None else quantity,
        filled_quantity=filled_quantity,
        reduce_only=intent.reduce_only,
    )


def _ladder(generation: int = 1) -> tuple[PassiveOrderIntent, ...]:
    return (
        _intent(generation=generation, level=1, price="99.00"),
        _intent(generation=generation, level=2, price="98.00"),
        _intent(generation=generation, level=3, price="97.00"),
    )


def test_no_working_orders_submits_entire_desired_ladder() -> None:
    desired = _ladder()

    assert reconcile_passive_orders(desired=desired, working=()) == ReconciliationPlan(
        cancel=(),
        submit=desired,
    )


def test_exact_working_ladder_is_idempotent() -> None:
    desired = _ladder()
    working = tuple(_working(intent) for intent in desired)

    assert reconcile_passive_orders(desired=desired, working=working) == ReconciliationPlan()


def test_stale_generation_is_cancelled_without_same_cycle_replacement() -> None:
    stale = _ladder(generation=1)
    desired = _ladder(generation=2)
    working = tuple(_working(intent) for intent in stale)

    plan = reconcile_passive_orders(desired=desired, working=working)

    assert plan.cancel == tuple(sorted(order.client_order_id for order in stale))
    assert plan.submit == ()


def test_after_terminal_cancellation_new_generation_is_submitted() -> None:
    desired = _ladder(generation=2)

    plan = reconcile_passive_orders(desired=desired, working=())

    assert plan == ReconciliationPlan(cancel=(), submit=desired)


def test_mismatched_price_is_cancelled_before_replacement() -> None:
    desired = (_intent(),)
    working = (_working(desired[0], price=Decimal("98.50")),)

    assert reconcile_passive_orders(desired=desired, working=working) == ReconciliationPlan(
        cancel=(desired[0].client_order_id,),
        submit=(),
    )


def test_matching_partial_fill_remains_working() -> None:
    desired = (_intent(quantity="0.10"),)
    working = (_working(desired[0], filled_quantity=Decimal("0.04")),)

    assert reconcile_passive_orders(desired=desired, working=working) == ReconciliationPlan()


def test_desired_quantity_below_already_filled_quantity_is_invalid() -> None:
    desired = (_intent(quantity="0.03"),)
    working = (
        _working(
            desired[0],
            quantity=Decimal("0.10"),
            filled_quantity=Decimal("0.04"),
        ),
    )

    with pytest.raises(ValueError, match="filled quantity"):
        reconcile_passive_orders(desired=desired, working=working)


def test_changed_total_quantity_cancels_before_replace() -> None:
    desired = (_intent(quantity="0.08"),)
    working = (
        _working(
            desired[0],
            quantity=Decimal("0.10"),
            filled_quantity=Decimal("0.04"),
        ),
    )

    assert reconcile_passive_orders(desired=desired, working=working) == ReconciliationPlan(
        cancel=(desired[0].client_order_id,),
        submit=(),
    )


def test_extra_working_order_is_cancelled() -> None:
    desired = (_intent(generation=2),)
    stale_intent = _intent(generation=1)

    assert reconcile_passive_orders(
        desired=desired,
        working=(_working(stale_intent),),
    ) == ReconciliationPlan(cancel=(stale_intent.client_order_id,), submit=())


def test_duplicate_client_ids_fail_closed() -> None:
    desired = _intent()

    with pytest.raises(ValueError, match="duplicate desired"):
        reconcile_passive_orders(desired=(desired, desired), working=())

    working = _working(desired)
    with pytest.raises(ValueError, match="duplicate working"):
        reconcile_passive_orders(desired=(desired,), working=(working, working))


@given(ordering=st.permutations([0, 1, 2]))
def test_reconciliation_is_independent_of_input_tuple_order(ordering: list[int]) -> None:
    desired = _ladder()
    reordered_desired = tuple(desired[index] for index in ordering)
    reordered_working = tuple(_working(desired[index]) for index in reversed(ordering))

    assert reconcile_passive_orders(
        desired=reordered_desired,
        working=reordered_working,
    ) == ReconciliationPlan()
