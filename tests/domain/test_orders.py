from datetime import UTC, datetime
from decimal import Decimal

import pytest
from hypothesis import given, strategies as st

from grid_trade.domain.orders import (
    FillEvent,
    OrderSide,
    PassiveOrderIntent,
    ReconciliationPlan,
    WorkingOrder,
)


def _intent(**overrides: object) -> PassiveOrderIntent:
    values: dict[str, object] = {
        "client_order_id": "s0:g1:buy:l1",
        "generation": 1,
        "level": 1,
        "side": OrderSide.BUY,
        "price": Decimal("99"),
        "quantity": Decimal("0.10"),
        "reduce_only": False,
    }
    values.update(overrides)
    return PassiveOrderIntent(**values)  # type: ignore[arg-type]


def _working(**overrides: object) -> WorkingOrder:
    values: dict[str, object] = {
        "client_order_id": "s0:g1:buy:l1",
        "generation": 1,
        "level": 1,
        "side": OrderSide.BUY,
        "price": Decimal("99"),
        "quantity": Decimal("0.10"),
        "filled_quantity": Decimal("0.04"),
        "reduce_only": False,
    }
    values.update(overrides)
    return WorkingOrder(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("client_order_id", ""),
        ("generation", -1),
        ("level", -1),
        ("price", Decimal("0")),
        ("price", Decimal("-1")),
        ("quantity", Decimal("0")),
        ("quantity", Decimal("-0.01")),
    ],
)
def test_passive_intent_rejects_invalid_fields(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        _intent(**{field: value})


def test_working_order_accepts_partial_fill() -> None:
    order = _working(filled_quantity=Decimal("0.04"))

    assert order.remaining_quantity == Decimal("0.06")


@pytest.mark.parametrize("filled", [Decimal("-0.01"), Decimal("0.11")])
def test_working_order_rejects_invalid_filled_quantity(filled: Decimal) -> None:
    with pytest.raises(ValueError):
        _working(filled_quantity=filled)


def test_fill_event_requires_aware_time_and_positive_values() -> None:
    event = FillEvent(
        client_order_id="s0:g1:buy:l1",
        timestamp=datetime(2026, 8, 9, 7, 0, tzinfo=UTC),
        price=Decimal("99"),
        quantity=Decimal("0.04"),
    )
    assert event.quantity == Decimal("0.04")

    with pytest.raises(ValueError):
        FillEvent(
            client_order_id="s0:g1:buy:l1",
            timestamp=datetime(2026, 8, 9, 7, 0),
            price=Decimal("99"),
            quantity=Decimal("0.04"),
        )


def test_reconciliation_plan_cannot_cancel_and_submit_in_same_cycle() -> None:
    with pytest.raises(ValueError):
        ReconciliationPlan(cancel=("s0:g0:buy:l1",), submit=(_intent(),))


@given(
    quantity=st.decimals(min_value="0.0001", max_value="100", places=4),
    overfill=st.decimals(min_value="0.0001", max_value="100", places=4),
)
def test_working_order_never_accepts_overfill(quantity: Decimal, overfill: Decimal) -> None:
    with pytest.raises(ValueError):
        _working(quantity=quantity, filled_quantity=quantity + overfill)
