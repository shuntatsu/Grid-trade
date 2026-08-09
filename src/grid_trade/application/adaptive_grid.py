from grid_trade.application.passive_policy import (
    PassivePolicyTransition,
    continue_passive_policy_reconciliation,
    transition_passive_policy,
)
from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.orders import WorkingOrder
from grid_trade.domain.risk import RiskLimits, RiskState
from grid_trade.strategy.adaptive_grid import (
    AdaptiveGridDecision,
    AdaptiveGridPolicyConfig,
    AdaptiveGridState,
    decide_adaptive_grid,
)
from grid_trade.strategy.adaptive_signals import AdaptiveSignals

AdaptiveGridTransition = PassivePolicyTransition[AdaptiveGridState, AdaptiveGridDecision]


def transition_adaptive_grid(
    *,
    snapshot: MarketSnapshot,
    signals: AdaptiveSignals,
    state: AdaptiveGridState,
    config: AdaptiveGridPolicyConfig,
    risk_limits: RiskLimits,
    risk_state: RiskState,
    working_orders: tuple[WorkingOrder, ...],
) -> AdaptiveGridTransition:
    decision, candidate_state, proposed_ladder = decide_adaptive_grid(
        snapshot,
        signals,
        state,
        config,
    )
    return transition_passive_policy(
        decision=decision,
        previous_state=state,
        candidate_state=candidate_state,
        snapshot=snapshot,
        risk_limits=risk_limits,
        risk_state=risk_state,
        working_orders=working_orders,
        proposed_ladder=proposed_ladder,
    )


def continue_adaptive_grid_reconciliation(
    transition: AdaptiveGridTransition,
    *,
    snapshot: MarketSnapshot,
    risk_limits: RiskLimits,
    risk_state: RiskState,
    working_orders: tuple[WorkingOrder, ...],
) -> AdaptiveGridTransition:
    return continue_passive_policy_reconciliation(
        transition,
        snapshot=snapshot,
        risk_limits=risk_limits,
        risk_state=risk_state,
        working_orders=working_orders,
    )


__all__ = [
    "AdaptiveGridTransition",
    "continue_adaptive_grid_reconciliation",
    "transition_adaptive_grid",
]
