from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest

import grid_trade.application.calibrated_adaptive_grid as calibrated_module
from grid_trade.application.adaptive_grid import transition_adaptive_grid
from grid_trade.application.calibrated_adaptive_grid import transition_calibrated_adaptive_grid
from grid_trade.application.calibrated_policy_inputs import (
    CalibratedAdaptiveInputs,
    CalibratedPolicyInputConfig,
    CalibratedPolicyInputStatus,
)
from grid_trade.calibration.contracts import CalibratedMarketState
from grid_trade.calibration.microstructure_engine import (
    MicrostructureCalibrationEstimate,
    MicrostructureCalibrationState,
)
from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.orders import WorkingOrder
from grid_trade.domain.risk import RiskLimits, RiskState
from grid_trade.risk.sizing import InventoryCapacity
from grid_trade.strategy.adaptive_grid import (
    AdaptiveGridPolicyConfig,
    AdaptiveStage,
    initialize_adaptive_grid,
)
from grid_trade.strategy.adaptive_ladder import AdaptiveLadderConfig
from grid_trade.strategy.adaptive_signals import AdaptiveSignals
from grid_trade.strategy.conditional_short import ShortOverlayConfig, ShortPhase
from grid_trade.strategy.de_risk import DeRiskConfig
from grid_trade.strategy.dynamic_center import DynamicCenterConfig
from grid_trade.strategy.funding_bias import FundingBiasConfig
from grid_trade.strategy.inventory_target import InventoryTargetConfig
from grid_trade.strategy.order_book_reference import OrderBookReferenceConfig
from grid_trade.strategy.volatility_spacing import VolatilitySpacingConfig

_TIME = datetime(2026, 8, 9, 13, 0, tzinfo=UTC)


def _snapshot(*, position: str = "0") -> MarketSnapshot:
    return MarketSnapshot(
        timestamp=_TIME,
        best_bid=Decimal("99.99"),
        best_ask=Decimal("100.01"),
        realized_volatility=Decimal("0.005"),
        position_quantity=Decimal(position),
        source_id="fixture:calibrated-wrapper",
    )


def _signals(*, trend: str = "0") -> AdaptiveSignals:
    return AdaptiveSignals(
        trend_score=Decimal(trend),
        funding_rate=Decimal("0"),
        order_book_imbalance=Decimal("0"),
        microprice=None,
    )


def _config(*, stage: AdaptiveStage = AdaptiveStage.S7_ORDER_BOOK) -> AdaptiveGridPolicyConfig:
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
            severe_target_fraction=Decimal("0"),
        ),
        short=ShortOverlayConfig(
            entry_trend_threshold=Decimal("-0.60"),
            max_short_target=Decimal("0.08"),
        ),
        funding=FundingBiasConfig(
            funding_scale=Decimal("1"),
            max_abs_target=Decimal("0.10"),
            max_target_shift_fraction=Decimal("0.50"),
        ),
        order_book=OrderBookReferenceConfig(
            microprice_weight=Decimal("0.50"),
            imbalance_shift_bps=Decimal("10"),
        ),
        stage=stage,
    )


def _capacity() -> InventoryCapacity:
    return InventoryCapacity(
        q_notional=Decimal("0.20"),
        q_margin=Decimal("0.20"),
        q_volatility=Decimal("0.10"),
        q_venue=Decimal("0.20"),
        q_max=Decimal("0.10"),
        binding_constraint="volatility",
    )


def _ready_inputs(
    *,
    stage: AdaptiveStage = AdaptiveStage.S7_ORDER_BOOK,
    position: str = "0",
    trend: str = "0",
) -> CalibratedAdaptiveInputs:
    snapshot = _snapshot(position=position)
    return CalibratedAdaptiveInputs(
        status=CalibratedPolicyInputStatus(True, "ready", stage),
        capacity=_capacity(),
        usable_capacity=Decimal("0.10"),
        snapshot=snapshot,
        signals=_signals(trend=trend),
        policy_config=_config(stage=stage),
    )


def _not_ready_inputs(stage: AdaptiveStage = AdaptiveStage.S7_ORDER_BOOK) -> CalibratedAdaptiveInputs:
    return CalibratedAdaptiveInputs(
        status=CalibratedPolicyInputStatus(False, "microstructure_unavailable", stage),
        capacity=_capacity(),
        usable_capacity=None,
        snapshot=None,
        signals=None,
        policy_config=None,
    )


def _limits() -> RiskLimits:
    return RiskLimits(
        max_abs_position=Decimal("0.10"),
        max_drawdown_fraction=Decimal("0.20"),
        max_data_age_ms=1_000,
        max_open_orders=20,
    )


def _risk_state(snapshot: MarketSnapshot, *, stale: bool = False) -> RiskState:
    return RiskState(
        equity=Decimal("100"),
        peak_equity=Decimal("100"),
        open_order_count=0,
        now=snapshot.timestamp + (timedelta(seconds=2) if stale else timedelta(0)),
    )


def _placeholder_market() -> CalibratedMarketState:
    return cast(CalibratedMarketState, object())


def _placeholder_micro_state() -> MicrostructureCalibrationState:
    return cast(MicrostructureCalibrationState, object())


def _placeholder_micro_estimate() -> MicrostructureCalibrationEstimate:
    return cast(MicrostructureCalibrationEstimate, object())


def _patch_composition(
    monkeypatch: pytest.MonkeyPatch,
    inputs: CalibratedAdaptiveInputs,
) -> None:
    monkeypatch.setattr(
        calibrated_module,
        "compose_calibrated_adaptive_inputs",
        lambda **_: inputs,
    )


def _call_wrapper(
    *,
    state,
    risk_state: RiskState,
    working_orders: tuple[WorkingOrder, ...] = (),
):
    return transition_calibrated_adaptive_grid(
        snapshot=_snapshot(position=str(state.position_basis)),
        calibrated_market=_placeholder_market(),
        microstructure_state=_placeholder_micro_state(),
        microstructure=_placeholder_micro_estimate(),
        capacity=_capacity(),
        template=_config(),
        adapter_config=CalibratedPolicyInputConfig(Decimal("1")),
        venue_tick_size=Decimal("0.01"),
        state=state,
        risk_limits=_limits(),
        risk_state=risk_state,
        working_orders=working_orders,
    )


def test_not_ready_composition_produces_no_strategy_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _not_ready_inputs()
    _patch_composition(monkeypatch, inputs)
    ready = _ready_inputs()
    assert ready.snapshot is not None
    assert ready.signals is not None
    assert ready.policy_config is not None
    state, _ = initialize_adaptive_grid(ready.snapshot, ready.signals, ready.policy_config)

    result = _call_wrapper(state=state, risk_state=_risk_state(ready.snapshot))

    assert result.inputs is inputs
    assert result.transition is None


def test_ready_wrapper_matches_existing_application_transition_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _ready_inputs()
    _patch_composition(monkeypatch, inputs)
    assert inputs.snapshot is not None
    assert inputs.signals is not None
    assert inputs.policy_config is not None
    state, _ = initialize_adaptive_grid(inputs.snapshot, inputs.signals, inputs.policy_config)
    risk_state = _risk_state(inputs.snapshot)

    expected = transition_adaptive_grid(
        snapshot=inputs.snapshot,
        signals=inputs.signals,
        state=state,
        config=inputs.policy_config,
        risk_limits=_limits(),
        risk_state=risk_state,
        working_orders=(),
    )
    result = _call_wrapper(state=state, risk_state=risk_state)

    assert result.inputs is inputs
    assert result.transition == expected


def test_hard_risk_stale_data_veto_is_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _ready_inputs()
    _patch_composition(monkeypatch, inputs)
    assert inputs.snapshot is not None
    assert inputs.signals is not None
    assert inputs.policy_config is not None
    state, _ = initialize_adaptive_grid(inputs.snapshot, inputs.signals, inputs.policy_config)

    result = _call_wrapper(
        state=state,
        risk_state=_risk_state(inputs.snapshot, stale=True),
    )

    assert result.transition is not None
    assert result.transition.risk_decision.allow_new_risk is False
    assert result.transition.risk_decision.cancel_all_passive is True
    assert result.transition.reconciliation.submit == ()


def test_calibrated_s5_still_flattens_long_before_short(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    neutral = _ready_inputs(stage=AdaptiveStage.S5_SHORT, position="0.10", trend="0")
    assert neutral.snapshot is not None
    assert neutral.signals is not None
    assert neutral.policy_config is not None
    state, _ = initialize_adaptive_grid(neutral.snapshot, neutral.signals, neutral.policy_config)

    bearish = _ready_inputs(stage=AdaptiveStage.S5_SHORT, position="0.10", trend="-1")
    _patch_composition(monkeypatch, bearish)
    result = transition_calibrated_adaptive_grid(
        snapshot=bearish.snapshot,
        calibrated_market=_placeholder_market(),
        microstructure_state=_placeholder_micro_state(),
        microstructure=_placeholder_micro_estimate(),
        capacity=_capacity(),
        template=_config(stage=AdaptiveStage.S5_SHORT),
        adapter_config=CalibratedPolicyInputConfig(Decimal("1")),
        venue_tick_size=Decimal("0.01"),
        state=state,
        risk_limits=_limits(),
        risk_state=_risk_state(neutral.snapshot),
        working_orders=(),
    )

    assert result.transition is not None
    assert result.transition.decision.short.phase is ShortPhase.FLATTEN_LONG
    assert result.transition.decision.candidate_target == Decimal("0")
    assert all(order.reduce_only for order in result.transition.desired_ladder)
