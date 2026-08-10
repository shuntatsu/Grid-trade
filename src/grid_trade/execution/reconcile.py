from collections.abc import Iterable

from grid_trade.domain.orders import PassiveOrderIntent, ReconciliationPlan, WorkingOrder


def _require_unique_ids(items: Iterable[object], *, label: str) -> None:
    seen: set[str] = set()
    for item in items:
        client_order_id = getattr(item, "client_order_id", None)
        if not isinstance(client_order_id, str):
            raise TypeError(f"{label} item must expose a string client_order_id")
        if client_order_id in seen:
            raise ValueError(f"duplicate {label} client_order_id: {client_order_id}")
        seen.add(client_order_id)


def _matches(desired: PassiveOrderIntent, working: WorkingOrder) -> bool:
    return (
        desired.instrument_id == working.instrument_id
        and desired.client_order_id == working.client_order_id
        and desired.generation == working.generation
        and desired.level == working.level
        and desired.side is working.side
        and desired.price == working.price
        and desired.quantity == working.quantity
        and desired.reduce_only is working.reduce_only
    )


def _submission_key(order: PassiveOrderIntent) -> tuple[str, int, str]:
    return (order.side.value, order.level, order.client_order_id)


def reconcile_passive_orders(
    *,
    desired: tuple[PassiveOrderIntent, ...],
    working: tuple[WorkingOrder, ...],
) -> ReconciliationPlan:
    _require_unique_ids(desired, label="desired")
    _require_unique_ids(working, label="working")

    desired_by_id = {order.client_order_id: order for order in desired}
    working_by_id = {order.client_order_id: order for order in working}
    conflicts: list[str] = []

    for client_order_id, working_order in working_by_id.items():
        desired_order = desired_by_id.get(client_order_id)
        if desired_order is None:
            conflicts.append(client_order_id)
            continue

        if desired_order.quantity < working_order.filled_quantity:
            raise ValueError(
                f"desired quantity for {client_order_id} is below already filled quantity",
            )

        if not _matches(desired_order, working_order):
            conflicts.append(client_order_id)

    if conflicts:
        return ReconciliationPlan(cancel=tuple(sorted(conflicts)), submit=())

    missing = tuple(
        sorted(
            (
                desired_order
                for client_order_id, desired_order in desired_by_id.items()
                if client_order_id not in working_by_id
            ),
            key=_submission_key,
        ),
    )
    return ReconciliationPlan(cancel=(), submit=missing)


__all__ = ["reconcile_passive_orders"]
