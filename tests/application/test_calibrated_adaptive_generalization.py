import datetime as dt
from dataclasses import replace
from decimal import Decimal

from grid_trade.application.calibrated_adaptive import (
    CalibratedAdaptiveInputs,
    CalibratedAdaptiveMetaConfig,
    VenueGridConstraints,
    initialize_calibrated_adaptive_grid,
    prepare_calibrated_adaptive_inputs,
    transition_calibrated_adaptive_grid,
)
from grid_trade.calibration.contracts import (
    CalibratedMarketState,
    CalibrationComponentStatus,
    CalibrationReadiness,
)
from grid_trade.calibration.execution_cost import ExecutionCostConfig
from grid_trade.calibration.intensity import IntensityCalibrationConfig
from grid_trade.calibration.microstructure_contracts import (
    IntensityBucket,
    MarkoutSide,
    MaturedMarkout,
    OfiImpactSample,
    TopOfBookObservation,
)
from grid_trade.calibration.microstructure_engine import (
    MicrostructureCalibrationConfig,
    MicrostructureCalibrationEstimate,
    MicrostructureCalibrationState,
    MicrostructureCalibrationUpdate,
    update_microstructure_engine,
)
from grid_trade.calibration.order_flow import OfiImpactConfig
from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.risk import RiskLimits, RiskState
from grid_trade.risk.sizing import (
    InventoryCapacity,
    RiskSizingConfig,
    RiskSizingInput,
    derive_inventory_capacity,
)
from grid_trade.strategy.adaptive_grid import AdaptiveStage


def _time(minute: int) -> dt.datetime:
    return dt.datetime(2026, 8, 10, 3, minute, tzinfo=dt.UTC)


def _status(
    ready: bool,
    *,
    count: int = 20,
    reason: str = "ready",
) -> CalibrationComponentStatus:
    return CalibrationComponentStatus(
        ready=ready,
        sample_count=count if ready else 0,
        reason=reason,
    )


def _market(*, minute: int = 11, trend: str = "-0.2") -> CalibratedMarketState:
    return CalibratedMarketState(
        timestamp=_time(minute),
        source_id="fixture",
        instrument_id="GENERIC-PERP",
        readiness=CalibrationReadiness.READY,
        volatility_scale=Decimal("0.001"),
        trend_score=Decimal(trend),
        funding_score=Decimal("0.4"),
        quote_distance_scale=Decimal("0.0015"),
        execution_cost_floor=Decimal("0.0008"),
        order_book_score=Decimal("0.3"),
        estimated_microprice_displacement=Decimal("0.0005"),
        volatility_status=_status(True),
        trend_status=_status(True),
        funding_status=_status(True),
        microstructure_status=_status(True),
    )


def _snapshot(*, minute: int = 11, position: str = "0") -> MarketSnapshot:
    return MarketSnapshot(
        timestamp=_time(minute),
        best_bid=Decimal("99"),
        best_ask=Decimal("101"),
        realized_volatility=Decimal("0.1"),
        position_quantity=Decimal(position),
        source_id="fixture",
    )


def _meta() -> CalibratedAdaptiveMetaConfig:
    return CalibratedAdaptiveMetaConfig(
        stage=AdaptiveStage.S7_ORDER_BOOK,
        levels=3,
        base_long_fraction=Decimal("0.5"),
        level_quantity_fraction=Decimal("0.1"),
        max_short_fraction=Decimal("0.5"),
        center_reanchor_vol_units=Decimal("0.5"),
        center_max_step_vol_units=Decimal("1"),
        min_spacing_vol_units=Decimal("0.5"),
        max_spacing_vol_units=Decimal("4"),
        spacing_volatility_multiplier=Decimal("1"),
        intensity_spacing_multiplier=Decimal("1"),
        execution_cost_multiplier=Decimal("1.5"),
        reservation_skew_vol_units=Decimal("1"),
        side_skew_strength=Decimal("0.5"),
        warning_trend_threshold=Decimal("-0.25"),
        severe_trend_threshold=Decimal("-0.6"),
        warning_target_fraction=Decimal("0.5"),
        severe_target_fraction=Decimal("0"),
        short_entry_trend_threshold=Decimal("-0.6"),
        funding_max_target_shift_fraction=Decimal("0.25"),
        order_book_microprice_weight=Decimal("0.5"),
        order_book_shift_vol_units=Decimal("1"),
    )


def _venue() -> VenueGridConstraints:
    return VenueGridConstraints(Decimal("0.01"), Decimal("0.01"))


def _capacity(equity: str) -> InventoryCapacity:
    value = Decimal(equity)
    return derive_inventory_capacity(
        RiskSizingInput(
            equity=value,
            reference_price=Decimal("100"),
            volatility_scale=Decimal("0.001"),
            max_margin_notional=value,
            venue_max_quantity=Decimal("10"),
        ),
        RiskSizingConfig(
            max_notional_fraction=Decimal("0.1"),
            max_single_move_loss_fraction=Decimal("0.01"),
            volatility_floor=Decimal("0.0001"),
        ),
    )


def _prepare(
    *,
    market: CalibratedMarketState,
    snapshot: MarketSnapshot,
    equity: str = "100",
) -> CalibratedAdaptiveInputs:
    result = prepare_calibrated_adaptive_inputs(
        snapshot=snapshot,
        calibrated=market,
        capacity=_capacity(equity),
        meta=_meta(),
        venue=_venue(),
    )
    assert result.inputs is not None
    return result.inputs


def test_risk_capacity_not_symbol_identity_scales_grid_quantities() -> None:
    smaller = _prepare(market=_market(), snapshot=_snapshot(), equity="100")
    larger = _prepare(market=_market(), snapshot=_snapshot(), equity="200")

    assert smaller.effective_q_max == Decimal("0.10")
    assert larger.effective_q_max == Decimal("0.20")
    assert larger.policy_config.inventory.base_long_target == Decimal("0.10")
    assert larger.policy_config.ladder.order_quantity == Decimal("0.02")


def _micro_config() -> MicrostructureCalibrationConfig:
    return MicrostructureCalibrationConfig(
        intensity=IntensityCalibrationConfig(
            3,
            20,
            Decimal("0.5"),
            Decimal("1.5"),
            21,
            Decimal("0.1"),
        ),
        ofi_impact=OfiImpactConfig(8, 2, Decimal("0.01"), Decimal("0.01"), Decimal("2")),
        execution_cost=ExecutionCostConfig(
            8,
            2,
            Decimal("0.75"),
            Decimal("0.0002"),
            Decimal("0.003"),
        ),
        min_microstructure_quality=Decimal("0"),
    )


def _book(minute: int, bid_size: str, ask_size: str) -> TopOfBookObservation:
    return TopOfBookObservation(
        _time(minute),
        "fixture",
        "GENERIC-PERP",
        Decimal("99"),
        Decimal(bid_size),
        Decimal("101"),
        Decimal(ask_size),
    )


def _buckets() -> tuple[IntensityBucket, ...]:
    return tuple(
        IntensityBucket(Decimal(distance), Decimal("100"), arrivals)
        for distance, arrivals in ((0, 1000), (1, 368), (2, 135), (3, 50))
    )


def _markouts() -> tuple[MaturedMarkout, ...]:
    return (
        MaturedMarkout(
            _time(0),
            _time(2),
            MarkoutSide.BUY,
            Decimal("100"),
            Decimal("99.9"),
        ),
        MaturedMarkout(
            _time(1),
            _time(3),
            MarkoutSide.SELL,
            Decimal("100"),
            Decimal("100.2"),
        ),
    )


def _labels() -> tuple[OfiImpactSample, ...]:
    return (
        OfiImpactSample(_time(0), _time(2), Decimal("-1"), Decimal("-0.002")),
        OfiImpactSample(_time(1), _time(3), Decimal("1"), Decimal("0.002")),
    )


def _micro_ready(*, future_label: bool) -> MicrostructureCalibrationUpdate:
    labels = _labels()
    if future_label:
        labels = (
            *labels,
            OfiImpactSample(_time(9), _time(20), Decimal("100"), Decimal("0.5")),
        )
    first = update_microstructure_engine(
        MicrostructureCalibrationState(),
        _book(10, "5", "5"),
        volatility_scale=Decimal("0.001"),
        intensity_buckets=_buckets(),
        markouts=_markouts(),
        new_ofi_impact_samples=labels,
        maker_fee_rate=Decimal("0.0001"),
        tick_size=Decimal("0.01"),
        config=_micro_config(),
    )
    return update_microstructure_engine(
        first.next_state,
        _book(11, "8", "4"),
        volatility_scale=Decimal("0.001"),
        intensity_buckets=_buckets(),
        markouts=_markouts(),
        new_ofi_impact_samples=(),
        maker_fee_rate=Decimal("0.0001"),
        tick_size=Decimal("0.01"),
        config=_micro_config(),
    )


def _compose(estimate: MicrostructureCalibrationEstimate) -> CalibratedMarketState:
    return replace(
        _market(),
        quote_distance_scale=estimate.quote_distance_scale,
        execution_cost_floor=estimate.execution.execution_cost_floor,
        order_book_score=estimate.order_book_score,
        estimated_microprice_displacement=estimate.microprice_relative_displacement,
        microstructure_status=_status(
            estimate.readiness.ready,
            count=estimate.readiness.sample_count,
            reason=estimate.readiness.reason,
        ),
    )


def test_unmatured_ofi_label_cannot_change_integrated_preparation() -> None:
    baseline = _micro_ready(future_label=False).estimate
    future = _micro_ready(future_label=True).estimate
    assert future == baseline
    assert _prepare(market=_compose(future), snapshot=_snapshot()) == _prepare(
        market=_compose(baseline),
        snapshot=_snapshot(),
    )


def _limits() -> RiskLimits:
    return RiskLimits(Decimal("0.10"), Decimal("0.20"), 1_000, 20)


def _risk_state(snapshot: MarketSnapshot) -> RiskState:
    return RiskState(Decimal("100"), Decimal("100"), 0, snapshot.timestamp)


def test_calibrated_path_still_requires_flat_before_short() -> None:
    initial = _prepare(market=_market(trend="0"), snapshot=_snapshot(position="0.05"))
    state, _ = initialize_calibrated_adaptive_grid(initial)

    bearish_long = _prepare(
        market=_market(minute=12, trend="-0.90"),
        snapshot=_snapshot(minute=12, position="0.05"),
    )
    flatten = transition_calibrated_adaptive_grid(
        inputs=bearish_long,
        state=state,
        risk_limits=_limits(),
        risk_state=_risk_state(bearish_long.snapshot),
        working_orders=(),
    )
    assert flatten.next_state.policy_state.target == Decimal(0)

    bearish_flat = _prepare(
        market=_market(minute=13, trend="-0.90"),
        snapshot=_snapshot(minute=13, position="0"),
    )
    short = transition_calibrated_adaptive_grid(
        inputs=bearish_flat,
        state=flatten.next_state,
        risk_limits=_limits(),
        risk_state=_risk_state(bearish_flat.snapshot),
        working_orders=(),
    )
    assert short.next_state.policy_state.target < 0
