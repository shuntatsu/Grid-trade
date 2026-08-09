from grid_trade.application.passive_policy import (
    PassivePolicyTransition,
    continue_passive_policy_reconciliation,
    transition_passive_policy,
)
from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.orders import WorkingOrder
from grid_trade.domain.risk import RiskLimits, RiskState
from grid_trade.strategy.dynamic_center import (
    CenterDecision,
    DynamicCenterConfig,
    DynamicCenterState,
    decide_dynamic_center,
)
from grid_trade.strategy.grid_geometry import FixedLongGridConfig

DynamicCenterTransition = PassivePolicyTransition[DynamicCenterState, CenterDecision]


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
    candidate_state = DynamicCenterState(
        center=decision.effective_center,
        generation=decision.effective_generation,
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


def continue_dynamic_center_reconciliation(
    transition: DynamicCenterTransition,
    *,
    snapshot: MarketSnapshot,
    risk_limits: RiskLimits,
    risk_state: RiskState,
    working_orders: tuple[WorkingOrder, ...],
) -> DynamicCenterTransition:
    """Advance the same center decision without evaluating a new center proposal."""
    return continue_passive_policy_reconciliation(
        transition,
        snapshot=snapshot,
        risk_limits=risk_limits,
        risk_state=risk_state,
        working_orders=working_orders,
    )


__all__ = [
    "DynamicCenterTransition",
    "continue_dynamic_center_reconciliation",
    "transition_dynamic_center",
]
