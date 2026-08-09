from grid_trade.application import (
    AdaptiveGridTransition,
    continue_adaptive_grid_reconciliation,
    transition_adaptive_grid,
)
from grid_trade.strategy import (
    AdaptiveGridDecision,
    AdaptiveGridPolicyConfig,
    AdaptiveGridState,
    AdaptiveStage,
    decide_adaptive_grid,
    initialize_adaptive_grid,
)


def test_adaptive_strategy_public_api_is_explicit() -> None:
    assert AdaptiveStage.S7_ORDER_BOOK.value == 7
    assert AdaptiveGridPolicyConfig is not None
    assert AdaptiveGridState is not None
    assert AdaptiveGridDecision is not None
    assert callable(initialize_adaptive_grid)
    assert callable(decide_adaptive_grid)


def test_adaptive_application_public_api_is_explicit() -> None:
    assert AdaptiveGridTransition is not None
    assert callable(transition_adaptive_grid)
    assert callable(continue_adaptive_grid_reconciliation)
