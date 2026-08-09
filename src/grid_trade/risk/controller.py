from datetime import timedelta
from decimal import Decimal

from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.orders import OrderSide, PassiveOrderIntent
from grid_trade.domain.risk import RiskDecision, RiskLimits, RiskReason, RiskState


def evaluate_risk(
    snapshot: MarketSnapshot,
    limits: RiskLimits,
    state: RiskState,
) -> RiskDecision:
    if snapshot.timestamp > state.now:
        raise ValueError("market snapshot timestamp is in the future")

    reasons: list[RiskReason] = []
    cancel_all = False
    target_flat = False

    if state.now - snapshot.timestamp > timedelta(milliseconds=limits.max_data_age_ms):
        reasons.append(RiskReason.STALE_DATA)
        cancel_all = True

    drawdown = (state.peak_equity - state.equity) / state.peak_equity
    if drawdown > limits.max_drawdown_fraction:
        reasons.append(RiskReason.DRAWDOWN_BREACH)
        cancel_all = True
        target_flat = True

    if state.open_order_count > limits.max_open_orders:
        reasons.append(RiskReason.MAX_OPEN_ORDERS)
        cancel_all = True

    absolute_position = abs(snapshot.position_quantity)
    if absolute_position >= limits.max_abs_position:
        reasons.append(RiskReason.MAX_POSITION)
        if absolute_position > limits.max_abs_position:
            cancel_all = True
            target_flat = True

    return RiskDecision(
        allow_new_risk=not reasons,
        cancel_all_passive=cancel_all,
        target_flat=target_flat,
        reasons=tuple(reasons),
    )


def _project_position(position: Decimal, order: PassiveOrderIntent) -> Decimal:
    if order.side is OrderSide.BUY:
        return position + order.quantity
    return position - order.quantity


def filter_passive_orders(
    snapshot: MarketSnapshot,
    limits: RiskLimits,
    decision: RiskDecision,
    orders: tuple[PassiveOrderIntent, ...],
) -> tuple[PassiveOrderIntent, ...]:
    if decision.cancel_all_passive or decision.target_flat:
        return ()

    if not decision.allow_new_risk:
        return tuple(order for order in orders if order.reduce_only)

    projected_position = snapshot.position_quantity
    accepted: list[PassiveOrderIntent] = []

    for order in orders:
        if order.reduce_only:
            accepted.append(order)
            continue

        candidate = _project_position(projected_position, order)
        if abs(candidate) <= limits.max_abs_position:
            accepted.append(order)
            projected_position = candidate

    return tuple(accepted)


__all__ = ["evaluate_risk", "filter_passive_orders"]
