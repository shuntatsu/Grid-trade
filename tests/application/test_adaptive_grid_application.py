from datetime import UTC, datetime, timedelta
from decimal import Decimal

from grid_trade.application.adaptive_grid import (
    continue_adaptive_grid_reconciliation,
    transition_adaptive_grid,
)
from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.orders import PassiveOrderIntent, WorkingOrder
from grid_trade.domain.risk import RiskLimits, RiskState
from grid_trade.strategy.adaptive_grid import AdaptiveGridPolicyConfig, initialize_adaptive_grid
from grid_trade.strategy.adaptive_ladder import AdaptiveLadderConfig
from grid_trade.strategy.adaptive_signals import AdaptiveSignals
from grid_trade.strategy.conditional_short import ShortOverlayConfig
from grid_trade.strategy.de_risk import DeRiskConfig
from grid_trade.strategy.dynamic_center import DynamicCenterConfig
from grid_trade.strategy.funding_bias import FundingBiasConfig
from grid_trade.strategy.inventory_target import InventoryTargetConfig
from grid_trade.strategy.order_book_reference import OrderBookReferenceConfig
from grid_trade.strategy.volatility_spacing import VolatilitySpacingConfig


def _snapshot(
    *,
    second: int = 0,
    mid: str = "100",
    position: str = "0",
) -> MarketSnapshot:
    value = Decimal(mid)
    return MarketSnapshot(
        timestamp=datetime(2026, 8, 9, 13, 0, tzinfo=UTC) + timedelta(seconds=second),
        best_bid=value - Decimal("0.01"),
        best_ask=value + Decimal("0.01"),
        realized_volatility=Decimal("0.005"),
        position_quantity=Decimal(position),
        source_id=f"fixture:adaptive-app:{second}",
    )


def _signals(*, trend: str = "0") -> AdaptiveSignals:
    return AdaptiveSignals(
        trend_score=Decimal(trend),
        funding_rate=Decimal(0),
        order_book_imbalance=Decimal(0),
        microprice=None,
    )


def _config() -> AdaptiveGridPolicyConfig:
    return AdaptiveGridPolicyConfig(
        center=DynamicCenterConfig(
            reanchor_threshold_bps=Decimal("20"),
            max_step_bps=Decimal("50"),
        ),
        spacing=VolatilitySpacingConfig(
            min_spacing_bps=Decimal("40"),
            max_spacing_bps=Decimal("200"),
            volatility_multiplier=Decimal("1"),
            execution_cost_floor_bps=Decimal("30"),
        ),
        ladder=AdaptiveLadderConfig(
            levels=3,
            spacing_bps=50,
            order_quantity=Decimal("0.02"),
            tick_size=Decimal("0.01"),
            max_abs_inventory=Decimal("0.10"),
        ),
        inventory=InventoryTargetConfig(
            base_long_target=Decimal("0.05"),
            max_abs_target=Decimal("0.10"),
            reservation_skew_bps=Decimal("20"),
            side_skew_strength=Decimal("1"),
        ),
        de_risk=DeRiskConfig(
            warning_trend_threshold=Decimal("-0.25"),
            severe_trend_threshold=Decimal("-0.60"),
            warning_target_fraction=Decimal("0.50"),
            severe_target_fraction=Decimal(0),
        ),
        short=ShortOverlayConfig(
            entry_trend_threshold=Decimal("-0.60"),
            max_short_target=Decimal("0.08"),
        ),
        funding=FundingBiasConfig(
            funding_scale=Decimal("0.001"),
            max_abs_target=Decimal("0.10"),
            max_target_shift_fraction=Decimal("0.50"),
        ),
        order_book=OrderBookReferenceConfig(
            microprice_weight=Decimal("0.50"),
            imbalance_shift_bps=Decimal("10"),
        ),
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


def test_initial_application_transition_submits_current_ladder() -> None:
    snapshot = _snapshot()
    state, ladder = initialize_adaptive_grid(snapshot, _signals(), _config())

    transition = transition_adaptive_grid(
        snapshot=snapshot,
        signals=_signals(),
        state=state,
        config=_config(),
        risk_limits=_limits(),
        risk_state=_risk_state(snapshot),
        working_orders=(),
    )

    assert transition.reconciliation.cancel == ()
    assert transition.reconciliation.submit == ladder
    assert transition.next_state == state


def test_changed_policy_cancels_before_replacement_and_reuses_same_decision() -> None:
    initial = _snapshot()
    state, ladder = initialize_adaptive_grid(initial, _signals(), _config())
    working = _working(ladder)
    changed = _snapshot(second=1, mid="101")

    transition = transition_adaptive_grid(
        snapshot=changed,
        signals=_signals(),
        state=state,
        config=_config(),
        risk_limits=_limits(),
        risk_state=_risk_state(changed, len(working)),
        working_orders=working,
    )
    assert transition.reconciliation.cancel
    assert transition.reconciliation.submit == ()
    assert transition.next_state == state

    continued = continue_adaptive_grid_reconciliation(
        transition,
        snapshot=changed,
        risk_limits=_limits(),
        risk_state=_risk_state(changed),
        working_orders=(),
    )
    assert continued.decision is transition.decision
    assert continued.reconciliation.cancel == ()
    assert continued.reconciliation.submit
    assert continued.next_state == transition.candidate_state


def test_stale_data_cancels_and_never_submits_new_risk() -> None:
    snapshot = _snapshot()
    state, ladder = initialize_adaptive_grid(snapshot, _signals(), _config())
    working = _working(ladder)
    stale_state = RiskState(
        equity=Decimal("100"),
        peak_equity=Decimal("100"),
        open_order_count=len(working),
        now=snapshot.timestamp + timedelta(seconds=2),
    )

    transition = transition_adaptive_grid(
        snapshot=snapshot,
        signals=_signals(),
        state=state,
        config=_config(),
        risk_limits=_limits(),
        risk_state=stale_state,
        working_orders=working,
    )

    assert not transition.risk_decision.allow_new_risk
    assert transition.risk_decision.cancel_all_passive
    assert transition.reconciliation.cancel
    assert transition.reconciliation.submit == ()
    assert transition.next_state == state


def test_max_position_still_allows_reduce_only_derisk_state_to_commit() -> None:
    snapshot = _snapshot(position="0.10")
    state, _ = initialize_adaptive_grid(snapshot, _signals(), _config())

    transition = transition_adaptive_grid(
        snapshot=_snapshot(second=1, position="0.10"),
        signals=_signals(trend="-0.90"),
        state=state,
        config=_config(),
        risk_limits=_limits(),
        risk_state=_risk_state(_snapshot(second=1, position="0.10")),
        working_orders=(),
    )

    assert not transition.risk_decision.allow_new_risk
    assert transition.desired_ladder
    assert all(order.reduce_only for order in transition.desired_ladder)
    assert transition.reconciliation.submit == transition.desired_ladder
    assert transition.next_state.target == Decimal(0)
