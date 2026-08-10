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


def _required_reduce_only_side(position: Decimal) -> OrderSide | None:
    if position > 0:
        return OrderSide.SELL
    if position < 0:
        return OrderSide.BUY
    return None


def _filter_passive_orders(
    snapshot: MarketSnapshot,
    limits: RiskLimits,
    decision: RiskDecision,
    orders: tuple[PassiveOrderIntent, ...],
) -> tuple[tuple[PassiveOrderIntent, ...], bool, bool]:
    if decision.cancel_all_passive or decision.target_flat:
        return (), False, False

    projected_new_risk_position = snapshot.position_quantity
    remaining_reduce_only_capacity = abs(snapshot.position_quantity)
    required_reduce_only_side = _required_reduce_only_side(snapshot.position_quantity)
    accepted: list[PassiveOrderIntent] = []
    invalid_reduce_only = False
    max_position_filtered = False

    for order in orders:
        if order.reduce_only:
            valid_side = (
                required_reduce_only_side is not None
                and order.side is required_reduce_only_side
            )
            valid_quantity = order.quantity <= remaining_reduce_only_capacity
            if not valid_side or not valid_quantity:
                invalid_reduce_only = True
                continue
            accepted.append(order)
            remaining_reduce_only_capacity -= order.quantity
            continue

        if not decision.allow_new_risk:
            continue

        candidate = _project_position(projected_new_risk_position, order)
        if abs(candidate) <= limits.max_abs_position:
            accepted.append(order)
            projected_new_risk_position = candidate
        else:
            max_position_filtered = True

    return tuple(accepted), invalid_reduce_only, max_position_filtered


def filter_passive_orders(
    snapshot: MarketSnapshot,
    limits: RiskLimits,
    decision: RiskDecision,
    orders: tuple[PassiveOrderIntent, ...],
) -> tuple[PassiveOrderIntent, ...]:
    filtered, _, _ = _filter_passive_orders(snapshot, limits, decision, orders)
    return filtered


def _reject_with_additional_reasons(
    decision: RiskDecision,
    additional_reasons: tuple[RiskReason, ...],
) -> RiskDecision:
    reasons = list(decision.reasons)
    for reason in additional_reasons:
        if reason not in reasons:
            reasons.append(reason)
    return RiskDecision(
        allow_new_risk=False,
        cancel_all_passive=decision.cancel_all_passive,
        target_flat=decision.target_flat,
        reasons=tuple(reasons),
    )


def assess_passive_ladder_risk(
    snapshot: MarketSnapshot,
    limits: RiskLimits,
    state: RiskState,
    orders: tuple[PassiveOrderIntent, ...],
) -> tuple[RiskDecision, tuple[PassiveOrderIntent, ...]]:
    """Evaluate hard risk and make order-level filtering explicit."""
    decision = evaluate_risk(snapshot, limits, state)
    filtered, invalid_reduce_only, max_position_filtered = _filter_passive_orders(
        snapshot,
        limits,
        decision,
        orders,
    )

    additional_reasons: list[RiskReason] = []
    if invalid_reduce_only:
        additional_reasons.append(RiskReason.INVALID_REDUCE_ONLY)
    if decision.allow_new_risk and max_position_filtered:
        additional_reasons.append(RiskReason.MAX_POSITION)

    if additional_reasons:
        return (
            _reject_with_additional_reasons(decision, tuple(additional_reasons)),
            filtered,
        )

    return decision, filtered


__all__ = ["assess_passive_ladder_risk", "evaluate_risk", "filter_passive_orders"]
