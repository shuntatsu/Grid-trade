from dataclasses import dataclass, replace

from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.orders import PassiveOrderIntent, ReconciliationPlan, WorkingOrder
from grid_trade.domain.risk import RiskDecision, RiskLimits, RiskState
from grid_trade.execution.reconcile import reconcile_passive_orders
from grid_trade.risk.controller import assess_passive_ladder_risk
from grid_trade.strategy.dynamic_center import (
    CenterDecision,
    DynamicCenterConfig,
    DynamicCenterState,
    decide_dynamic_center,
)
from grid_trade.strategy.grid_geometry import FixedLongGridConfig


@dataclass(frozen=True, slots=True)
class DynamicCenterTransition:
    decision: CenterDecision
    next_state: DynamicCenterState
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


def _previous_state(decision: CenterDecision) -> DynamicCenterState:
    return DynamicCenterState(
        center=decision.previous_center,
        generation=decision.previous_generation,
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


def transition_dynamic_center(
    *,
    snapshot: MarketSnapshot,
    state: DynamicCenterState,
    center_config: DynamicCenterConfig,
    grid_config: FixedLongGridConfig,
    risk_limits: RiskLimits,
    risk_state: RiskState,
    working_orders: tuple[WorkingOrder, ...],
) -> DynamicCenterTransition:
    decision, proposed_ladder = decide_dynamic_center(
        snapshot,
        state,
        center_config,
        grid_config,
    )
    risk_decision, filtered_ladder = _assess_desired_ladder(
        snapshot=snapshot,
        risk_limits=risk_limits,
        risk_state=risk_state,
        working_orders=working_orders,
        proposed_ladder=proposed_ladder,
    )

    accepted = risk_decision.allow_new_risk and filtered_ladder == proposed_ladder
    if accepted:
        next_state = DynamicCenterState(
            center=decision.effective_center,
            generation=decision.effective_generation,
        )
        desired_ladder = proposed_ladder
    else:
        next_state = state
        desired_ladder = tuple(order for order in filtered_ladder if order.reduce_only)

    reconciliation = reconcile_passive_orders(
        desired=desired_ladder,
        working=working_orders,
    )
    return DynamicCenterTransition(
        decision=decision,
        next_state=next_state,
        desired_ladder=desired_ladder,
        risk_decision=risk_decision,
        reconciliation=reconciliation,
    )


def continue_dynamic_center_reconciliation(
    transition: DynamicCenterTransition,
    *,
    snapshot: MarketSnapshot,
    risk_limits: RiskLimits,
    risk_state: RiskState,
    working_orders: tuple[WorkingOrder, ...],
) -> DynamicCenterTransition:
    """Advance the same center decision without evaluating a new center proposal."""
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

    if accepted:
        next_state = transition.next_state
        desired_ladder = transition.desired_ladder
    else:
        next_state = _previous_state(transition.decision)
        desired_ladder = tuple(order for order in filtered_ladder if order.reduce_only)

    reconciliation = reconcile_passive_orders(
        desired=desired_ladder,
        working=working_orders,
    )
    return DynamicCenterTransition(
        decision=transition.decision,
        next_state=next_state,
        desired_ladder=desired_ladder,
        risk_decision=risk_decision,
        reconciliation=reconciliation,
    )


__all__ = [
    "DynamicCenterTransition",
    "continue_dynamic_center_reconciliation",
    "transition_dynamic_center",
]
