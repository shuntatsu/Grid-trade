from grid_trade.application.passive_policy import (
    PassivePolicyTransition,
    continue_passive_policy_reconciliation,
    transition_passive_policy,
)
from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.orders import WorkingOrder
from grid_trade.domain.risk import RiskLimits, RiskState
from grid_trade.strategy.dynamic_center import DynamicCenterConfig
from grid_trade.strategy.grid_geometry import FixedLongGridConfig
from grid_trade.strategy.s2_adaptive_grid import (
    S2GridDecision,
    S2GridState,
    decide_s2_grid,
)
from grid_trade.strategy.volatility_spacing import VolatilitySpacingConfig

S2AdaptiveGridTransition = PassivePolicyTransition[S2GridState, S2GridDecision]


def transition_s2_adaptive_grid(
    *,
    snapshot: MarketSnapshot,
    state: S2GridState,
    center_config: DynamicCenterConfig,
    grid_config: FixedLongGridConfig,
    spacing_config: VolatilitySpacingConfig,
    risk_limits: RiskLimits,
    risk_state: RiskState,
    working_orders: tuple[WorkingOrder, ...],
) -> S2AdaptiveGridTransition:
    decision, candidate_state, proposed_ladder = decide_s2_grid(
        snapshot,
        state,
        center_config,
        grid_config,
        spacing_config,
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


def continue_s2_adaptive_grid_reconciliation(
    transition: S2AdaptiveGridTransition,
    *,
    snapshot: MarketSnapshot,
    risk_limits: RiskLimits,
    risk_state: RiskState,
    working_orders: tuple[WorkingOrder, ...],
) -> S2AdaptiveGridTransition:
    """Advance one S2 decision through reconciliation without recomputing policy."""
    return continue_passive_policy_reconciliation(
        transition,
        snapshot=snapshot,
        risk_limits=risk_limits,
        risk_state=risk_state,
        working_orders=working_orders,
    )


__all__ = [
    "S2AdaptiveGridTransition",
    "continue_s2_adaptive_grid_reconciliation",
    "transition_s2_adaptive_grid",
]
