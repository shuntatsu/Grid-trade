from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from grid_trade.application.calibrated_adaptive import (
    CalibratedAdaptiveInputs,
    continue_calibrated_adaptive_reconciliation,
    initialize_calibrated_adaptive_grid,
    transition_calibrated_adaptive_grid,
)
from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.orders import PassiveOrderIntent, WorkingOrder
from grid_trade.domain.risk import RiskLimits, RiskState
from grid_trade.strategy.adaptive_grid import AdaptiveGridPolicyConfig
from grid_trade.strategy.adaptive_ladder import AdaptiveLadderConfig
from grid_trade.strategy.adaptive_signals import AdaptiveSignals
from grid_trade.strategy.conditional_short import ShortOverlayConfig
from grid_trade.strategy.de_risk import DeRiskConfig
from grid_trade.strategy.dynamic_center import DynamicCenterConfig
from grid_trade.strategy.funding_bias import FundingBiasConfig
from grid_trade.strategy.inventory_target import InventoryTargetConfig
from grid_trade.strategy.order_book_reference import OrderBookReferenceConfig
from grid_trade.strategy.volatility_spacing import VolatilitySpacingConfig


def _snapshot(*, second: int = 0, position: str = "0") -> MarketSnapshot:
    return MarketSnapshot(
        timestamp=datetime(2026, 8, 10, 2, tzinfo=UTC) + timedelta(seconds=second),
        best_bid=Decimal("99.99"),
        best_ask=Decimal("100.01"),
        realized_volatility=Decimal("0.005"),
        position_quantity=Decimal(position),
        source_id="fixture:calibrated-app",
    )


def _config(*, order_quantity: str = "0.02") -> AdaptiveGridPolicyConfig:
    return AdaptiveGridPolicyConfig(
        center=DynamicCenterConfig(Decimal("20"), Decimal("50")),
        spacing=VolatilitySpacingConfig(
            Decimal("40"), Decimal("200"), Decimal("1"), Decimal("30")
        ),
        ladder=AdaptiveLadderConfig(
            3, 50, Decimal(order_quantity), Decimal("0.01"), Decimal("0.10")
        ),
        inventory=InventoryTargetConfig(
            Decimal("0.05"), Decimal("0.10"), Decimal("20"), Decimal("1")
        ),
        de_risk=DeRiskConfig(
            Decimal("-0.25"), Decimal("-0.60"), Decimal("0.50"), Decimal("0")
        ),
        short=ShortOverlayConfig(Decimal("-0.60"), Decimal("0.08")),
        funding=FundingBiasConfig(Decimal("1"), Decimal("0.10"), Decimal("0.50")),
        order_book=OrderBookReferenceConfig(Decimal("0.50"), Decimal("10")),
    )


def _inputs(
    *,
    second: int = 0,
    position: str = "0",
    trend: str = "0",
    order_quantity: str = "0.02",
) -> CalibratedAdaptiveInputs:
    return CalibratedAdaptiveInputs(
        snapshot=_snapshot(second=second, position=position),
        signals=AdaptiveSignals(
            trend_score=Decimal(trend),
            funding_rate=Decimal(0),
            order_book_imbalance=Decimal(0),
            microprice=None,
        ),
        policy_config=_config(order_quantity=order_quantity),
        effective_q_max=Decimal("0.10"),
    )


def _limits() -> RiskLimits:
    return RiskLimits(
        max_abs_position=Decimal("0.10"),
        max_drawdown_fraction=Decimal("0.20"),
        max_data_age_ms=1_000,
        max_open_orders=20,
    )


def _risk_state(snapshot: MarketSnapshot, open_orders: int = 0) -> RiskState:
    return RiskState(
        equity=Decimal("100"),
        peak_equity=Decimal("100"),
        open_order_count=open_orders,
        now=snapshot.timestamp,
    )


def _working(ladder: tuple[PassiveOrderIntent, ...]) -> tuple[WorkingOrder, ...]:
    return tuple(
        WorkingOrder(
            client_order_id=order.client_order_id,
            generation=order.generation,
            level=order.level,
            side=order.side,
            price=order.price,
            quantity=order.quantity,
            filled_quantity=Decimal(0),
            reduce_only=order.reduce_only,
        )
        for order in ladder
    )


def test_initialize_records_applied_runtime_config() -> None:
    inputs = _inputs()
    state, ladder = initialize_calibrated_adaptive_grid(inputs)

    assert state.applied_config == inputs.policy_config
    assert state.policy_state.generation == 0
    assert ladder


def test_cancel_phase_keeps_old_config_then_submit_commits_candidate_config() -> None:
    initial_inputs = _inputs()
    state, ladder = initialize_calibrated_adaptive_grid(initial_inputs)
    working = _working(ladder)
    next_inputs = _inputs(second=1, order_quantity="0.01")

    transition = transition_calibrated_adaptive_grid(
        inputs=next_inputs,
        state=state,
        risk_limits=_limits(),
        risk_state=_risk_state(next_inputs.snapshot, len(working)),
        working_orders=working,
    )

    assert transition.reconciliation.cancel
    assert transition.reconciliation.submit == ()
    assert transition.next_state.applied_config == initial_inputs.policy_config
    assert transition.candidate_state.applied_config == next_inputs.policy_config

    continued = continue_calibrated_adaptive_reconciliation(
        transition,
        snapshot=next_inputs.snapshot,
        risk_limits=_limits(),
        risk_state=_risk_state(next_inputs.snapshot),
        working_orders=(),
    )
    assert continued.decision is transition.decision
    assert continued.reconciliation.cancel == ()
    assert continued.reconciliation.submit
    assert continued.next_state.applied_config == next_inputs.policy_config


def test_risk_rejection_never_commits_new_runtime_config() -> None:
    initial_inputs = _inputs()
    state, ladder = initialize_calibrated_adaptive_grid(initial_inputs)
    working = _working(ladder)
    next_inputs = _inputs(second=1, order_quantity="0.01")
    stale = replace(
        _risk_state(next_inputs.snapshot, len(working)),
        now=next_inputs.snapshot.timestamp + timedelta(seconds=2),
    )

    transition = transition_calibrated_adaptive_grid(
        inputs=next_inputs,
        state=state,
        risk_limits=_limits(),
        risk_state=stale,
        working_orders=working,
    )

    assert transition.candidate_accepted is False
    assert transition.next_state == state
    assert transition.next_state.applied_config == initial_inputs.policy_config


def test_reduce_only_derisk_can_commit_candidate_state_at_hard_position_limit() -> None:
    initial_inputs = _inputs(position="0.10")
    state, _ = initialize_calibrated_adaptive_grid(initial_inputs)
    bearish = _inputs(second=1, position="0.10", trend="-0.90")

    transition = transition_calibrated_adaptive_grid(
        inputs=bearish,
        state=state,
        risk_limits=_limits(),
        risk_state=_risk_state(bearish.snapshot),
        working_orders=(),
    )

    assert transition.risk_decision.allow_new_risk is False
    assert transition.desired_ladder
    assert all(order.reduce_only for order in transition.desired_ladder)
    assert transition.next_state.policy_state.target == Decimal(0)
