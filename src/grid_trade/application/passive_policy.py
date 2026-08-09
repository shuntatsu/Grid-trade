from dataclasses import dataclass, replace

from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.orders import PassiveOrderIntent, ReconciliationPlan, WorkingOrder
from grid_trade.domain.risk import RiskDecision, RiskLimits, RiskState
from grid_trade.execution.reconcile import reconcile_passive_orders
from grid_trade.risk.controller import assess_passive_ladder_risk


@dataclass(frozen=True, slots=True)
class PassivePolicyTransition[StateT, DecisionT]:
    decision: DecisionT
    previous_state: StateT
    candidate_state: StateT
    next_state: StateT
    desired_ladder: tuple[PassiveOrderIntent, ...]
    risk_decision: RiskDecision
    reconciliation: ReconciliationPlan


def _prospective_risk_state(
    risk_state: RiskState,
    *,
    working_orders: tuple[WorkingOrder, ...],
    desired_order_count: int,
) -> RiskState:
    if risk_state.open_order_count < len(working_orders):
        raise ValueError("risk open_order_count cannot be below known strategy working orders")
    non_strategy_open_orders = risk_state.open_order_count - len(working_orders)
    return replace(
        risk_state,
        open_order_count=non_strategy_open_orders + desired_order_count,
    )


def _assess_desired_ladder(
    *,
    snapshot: MarketSnapshot,
    risk_limits: RiskLimits,
    risk_state: RiskState,
    working_orders: tuple[WorkingOrder, ...],
    proposed_ladder: tuple[PassiveOrderIntent, ...],
) -> tuple[RiskDecision, tuple[PassiveOrderIntent, ...]]:
    prospective_state = _prospective_risk_state(
        risk_state,
        working_orders=working_orders,
        desired_order_count=len(proposed_ladder),
    )
    return assess_passive_ladder_risk(
        snapshot,
        risk_limits,
        prospective_state,
        proposed_ladder,
    )


def _accepted_ladder(
    risk_decision: RiskDecision,
    proposed_ladder: tuple[PassiveOrderIntent, ...],
    filtered_ladder: tuple[PassiveOrderIntent, ...],
) -> tuple[bool, tuple[PassiveOrderIntent, ...]]:
    accepted = risk_decision.allow_new_risk and filtered_ladder == proposed_ladder
    if accepted:
        return True, proposed_ladder
    return False, tuple(order for order in filtered_ladder if order.reduce_only)


def transition_passive_policy[StateT, DecisionT](
    *,
    decision: DecisionT,
    previous_state: StateT,
    candidate_state: StateT,
    snapshot: MarketSnapshot,
    risk_limits: RiskLimits,
    risk_state: RiskState,
    working_orders: tuple[WorkingOrder, ...],
    proposed_ladder: tuple[PassiveOrderIntent, ...],
) -> PassivePolicyTransition[StateT, DecisionT]:
    risk_decision, filtered_ladder = _assess_desired_ladder(
        snapshot=snapshot,
        risk_limits=risk_limits,
        risk_state=risk_state,
        working_orders=working_orders,
        proposed_ladder=proposed_ladder,
    )
    accepted, desired_ladder = _accepted_ladder(
        risk_decision,
        proposed_ladder,
        filtered_ladder,
    )
    reconciliation = reconcile_passive_orders(
        desired=desired_ladder,
        working=working_orders,
    )
    next_state = candidate_state if accepted and not reconciliation.cancel else previous_state

    return PassivePolicyTransition(
        decision=decision,
        previous_state=previous_state,
        candidate_state=candidate_state,
        next_state=next_state,
        desired_ladder=desired_ladder,
        risk_decision=risk_decision,
        reconciliation=reconciliation,
    )


def continue_passive_policy_reconciliation[StateT, DecisionT](
    transition: PassivePolicyTransition[StateT, DecisionT],
    *,
    snapshot: MarketSnapshot,
    risk_limits: RiskLimits,
    risk_state: RiskState,
    working_orders: tuple[WorkingOrder, ...],
) -> PassivePolicyTransition[StateT, DecisionT]:
    """Advance one accepted policy decision without evaluating policy again."""
    risk_decision, filtered_ladder = _assess_desired_ladder(
        snapshot=snapshot,
        risk_limits=risk_limits,
        risk_state=risk_state,
        working_orders=working_orders,
        proposed_ladder=transition.desired_ladder,
    )
    accepted = (
        transition.risk_decision.allow_new_risk
        and risk_decision.allow_new_risk
        and filtered_ladder == transition.desired_ladder
    )
    desired_ladder = (
        transition.desired_ladder
        if accepted
        else tuple(order for order in filtered_ladder if order.reduce_only)
    )
    reconciliation = reconcile_passive_orders(
        desired=desired_ladder,
        working=working_orders,
    )
    next_state = (
        transition.candidate_state
        if accepted and not reconciliation.cancel
        else transition.previous_state
    )

    return PassivePolicyTransition(
        decision=transition.decision,
        previous_state=transition.previous_state,
        candidate_state=transition.candidate_state,
        next_state=next_state,
        desired_ladder=desired_ladder,
        risk_decision=risk_decision,
        reconciliation=reconciliation,
    )


__all__ = [
    "PassivePolicyTransition",
    "continue_passive_policy_reconciliation",
    "transition_passive_policy",
]
