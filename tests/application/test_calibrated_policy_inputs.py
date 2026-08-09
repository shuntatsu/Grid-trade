from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal, localcontext

from grid_trade.application.calibrated_policy_inputs import (
    CalibratedPolicyInputConfig,
    compose_calibrated_adaptive_inputs,
)
from grid_trade.calibration.contracts import (
    CalibratedMarketState,
    CalibrationComponentStatus,
    CalibrationReadiness,
)
from grid_trade.calibration.execution_cost import ExecutionCostEstimate
from grid_trade.calibration.intensity import IntensityEstimate
from grid_trade.calibration.microstructure_contracts import (
    MicrostructureReadiness,
    TopOfBookObservation,
)
from grid_trade.calibration.microstructure_engine import (
    MicrostructureCalibrationConfig,
    MicrostructureCalibrationEstimate,
    MicrostructureCalibrationState,
)
from grid_trade.calibration.execution_cost import ExecutionCostConfig
from grid_trade.calibration.intensity import IntensityCalibrationConfig
from grid_trade.calibration.order_flow import (
    OfiImpactConfig,
    OfiImpactEstimate,
    OfiImpactState,
)
from grid_trade.domain.market import MarketSnapshot
from grid_trade.risk.sizing import InventoryCapacity
from grid_trade.strategy.adaptive_grid import AdaptiveGridPolicyConfig, AdaptiveStage
from grid_trade.strategy.adaptive_ladder import AdaptiveLadderConfig
from grid_trade.strategy.conditional_short import ShortOverlayConfig
from grid_trade.strategy.de_risk import DeRiskConfig
from grid_trade.strategy.dynamic_center import DynamicCenterConfig
from grid_trade.strategy.funding_bias import FundingBiasConfig
from grid_trade.strategy.inventory_target import InventoryTargetConfig
from grid_trade.strategy.order_book_reference import OrderBookReferenceConfig
from grid_trade.strategy.volatility_spacing import VolatilitySpacingConfig

_TIME = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def _status(ready: bool, reason: str = "ready") -> CalibrationComponentStatus:
    return CalibrationComponentStatus(
        ready=ready,
        sample_count=100 if ready else 0,
        reason=reason,
    )


def _market_state(
    *,
    instrument_id: str = "AAA-PERP",
    volatility: Decimal | None = Decimal("0.002"),
    trend: Decimal | None = Decimal("-0.4"),
    funding: Decimal | None = Decimal("0.5"),
) -> CalibratedMarketState:
    volatility_ready = volatility is not None
    trend_ready = trend is not None
    ready_count = int(volatility_ready) + int(trend_ready)
    readiness = (
        CalibrationReadiness.READY
        if ready_count == 2
        else CalibrationReadiness.PARTIAL
        if ready_count == 1
        else CalibrationReadiness.NOT_READY
    )
    return CalibratedMarketState(
        timestamp=_TIME,
        source_id="fixture:calibrated",
        instrument_id=instrument_id,
        readiness=readiness,
        volatility_scale=volatility,
        trend_score=trend,
        funding_score=funding,
        quote_distance_scale=None,
        execution_cost_floor=None,
        order_book_score=None,
        estimated_microprice_displacement=None,
        volatility_status=_status(volatility_ready, "ready" if volatility_ready else "warmup"),
        trend_status=_status(trend_ready, "ready" if trend_ready else "warmup"),
        funding_status=_status(funding is not None, "ready" if funding is not None else "unavailable"),
        microstructure_status=_status(False, "external_phase_b"),
    )


def _micro_config() -> MicrostructureCalibrationConfig:
    return MicrostructureCalibrationConfig(
        intensity=IntensityCalibrationConfig(
            min_buckets=3,
            min_total_arrivals=20,
            k_min=Decimal("0.5"),
            k_max=Decimal("1.5"),
            k_steps=21,
            min_log_likelihood_improvement=Decimal("0.1"),
        ),
        ofi_impact=OfiImpactConfig(
            window=8,
            min_samples=2,
            min_abs_feature_energy=Decimal("0.01"),
            max_abs_beta=Decimal("0.01"),
            score_scale_vol_units=Decimal("2"),
        ),
        execution_cost=ExecutionCostConfig(
            markout_window=8,
            min_markout_samples=2,
            adverse_quantile=Decimal("0.75"),
            uncertainty_buffer=Decimal("0.0002"),
            fallback_adverse_cost=Decimal("0.003"),
        ),
        min_microstructure_quality=Decimal("0"),
    )


def _micro_state(*, instrument_id: str = "AAA-PERP") -> MicrostructureCalibrationState:
    book = TopOfBookObservation(
        timestamp=_TIME,
        source_id="fixture:calibrated",
        instrument_id=instrument_id,
        best_bid=Decimal("99"),
        bid_size=Decimal("8"),
        best_ask=Decimal("101"),
        ask_size=Decimal("4"),
    )
    return MicrostructureCalibrationState(
        config=_micro_config(),
        generation=2,
        source_id=book.source_id,
        instrument_id=instrument_id,
        last_timestamp=_TIME,
        last_book=book,
        ofi_impact_state=OfiImpactState(),
    )


def _micro_estimate(
    *,
    ready: bool = True,
    execution_floor: str = "0.0034",
) -> MicrostructureCalibrationEstimate:
    execution = ExecutionCostEstimate(
        execution_cost_floor=Decimal(execution_floor),
        adverse_cost=Decimal("0.003"),
        round_trip_fee=Decimal("0.0002"),
        tick_floor=Decimal("0.0001"),
        markout_ready=ready,
        used_fallback=not ready,
        sample_count=10 if ready else 0,
    )
    if ready:
        return MicrostructureCalibrationEstimate(
            intensity=IntensityEstimate(
                A=Decimal("10"),
                k=Decimal("1"),
                e_fold_distance_vol_units=Decimal("1"),
                log_likelihood_improvement=Decimal("10"),
                quality=Decimal("0.8"),
                sample_count=4,
                total_arrivals=100,
                ready=True,
            ),
            quote_distance_scale=Decimal("0.002"),
            execution=execution,
            current_normalized_ofi=Decimal("0.5"),
            ofi_impact=OfiImpactEstimate(
                beta=Decimal("0.002"),
                fit_r2=Decimal("0.9"),
                sample_count=10,
                ready=True,
            ),
            predicted_relative_displacement=Decimal("0.001"),
            microprice_relative_displacement=Decimal("0.0005"),
            order_book_score=Decimal("0.25"),
            readiness=MicrostructureReadiness(True, 4, "ready", Decimal("0.8")),
        )
    return MicrostructureCalibrationEstimate(
        intensity=IntensityEstimate.not_ready(sample_count=0, total_arrivals=0),
        quote_distance_scale=None,
        execution=execution,
        current_normalized_ofi=None,
        ofi_impact=OfiImpactEstimate.not_ready(sample_count=0),
        predicted_relative_displacement=None,
        microprice_relative_displacement=Decimal("0"),
        order_book_score=None,
        readiness=MicrostructureReadiness(False, 0, "warmup", Decimal("0")),
    )


def _snapshot(*, position: str = "0") -> MarketSnapshot:
    return MarketSnapshot(
        timestamp=_TIME,
        best_bid=Decimal("99"),
        best_ask=Decimal("101"),
        realized_volatility=Decimal("0.9"),
        position_quantity=Decimal(position),
        source_id="fixture:calibrated",
    )


def _capacity(q_max: str = "2") -> InventoryCapacity:
    value = Decimal(q_max)
    return InventoryCapacity(
        q_notional=value * Decimal("2"),
        q_margin=value * Decimal("3"),
        q_volatility=value,
        q_venue=value * Decimal("4"),
        q_max=value,
        binding_constraint="volatility",
    )


def _template(*, stage: AdaptiveStage = AdaptiveStage.S7_ORDER_BOOK) -> AdaptiveGridPolicyConfig:
    return AdaptiveGridPolicyConfig(
        center=DynamicCenterConfig(
            reanchor_threshold_bps=Decimal("20"),
            max_step_bps=Decimal("50"),
        ),
        spacing=VolatilitySpacingConfig(
            min_spacing_bps=Decimal("10"),
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
            funding_scale=Decimal("0.001"),
            max_abs_target=Decimal("0.10"),
            max_target_shift_fraction=Decimal("0.50"),
        ),
        order_book=OrderBookReferenceConfig(
            microprice_weight=Decimal("0.50"),
            imbalance_shift_bps=Decimal("10"),
        ),
        stage=stage,
    )


def _compose(
    *,
    stage: AdaptiveStage = AdaptiveStage.S7_ORDER_BOOK,
    market: CalibratedMarketState | None = None,
    micro_ready: bool = True,
    execution_floor: str = "0.0034",
    instrument_id: str = "AAA-PERP",
):
    return compose_calibrated_adaptive_inputs(
        snapshot=_snapshot(),
        calibrated_market=_market_state(instrument_id=instrument_id) if market is None else market,
        microstructure_state=_micro_state(instrument_id=instrument_id),
        microstructure=_micro_estimate(ready=micro_ready, execution_floor=execution_floor),
        capacity=_capacity(),
        template=_template(stage=stage),
        adapter_config=CalibratedPolicyInputConfig(capacity_utilization_fraction=Decimal("0.5")),
        venue_tick_size=Decimal("0.01"),
    )


def test_s3_fails_closed_when_volatility_is_unavailable() -> None:
    result = _compose(
        stage=AdaptiveStage.S3_INVENTORY,
        market=_market_state(volatility=None, trend=None),
    )

    assert result.status.ready is False
    assert result.status.reason == "volatility_unavailable"
    assert result.snapshot is None
    assert result.signals is None
    assert result.policy_config is None


def test_s4_fails_closed_when_trend_is_unavailable() -> None:
    result = _compose(
        stage=AdaptiveStage.S4_DERISK,
        market=_market_state(trend=None),
    )

    assert result.status.ready is False
    assert result.status.reason == "trend_unavailable"


def test_s6_fails_closed_when_funding_is_unavailable() -> None:
    result = _compose(
        stage=AdaptiveStage.S6_FUNDING,
        market=_market_state(funding=None),
    )

    assert result.status.ready is False
    assert result.status.reason == "funding_unavailable"


def test_s7_fails_closed_when_microstructure_is_not_ready() -> None:
    result = _compose(stage=AdaptiveStage.S7_ORDER_BOOK, micro_ready=False)

    assert result.status.ready is False
    assert result.status.reason == "microstructure_unavailable"


def test_ready_path_materializes_relative_spacing_signals_and_risk_capacity() -> None:
    result = _compose()

    assert result.status.ready is True
    assert result.snapshot is not None
    assert result.signals is not None
    assert result.policy_config is not None
    assert result.snapshot.realized_volatility == Decimal("0.002")
    assert result.policy_config.spacing.execution_cost_floor_bps == Decimal("34.0000")
    assert result.policy_config.ladder.max_abs_inventory == Decimal("1.0")
    assert result.policy_config.inventory.max_abs_target == Decimal("1.0")
    assert result.policy_config.funding.max_abs_target == Decimal("1.0")
    assert result.policy_config.inventory.base_long_target == Decimal("0.50")
    assert result.policy_config.ladder.order_quantity == Decimal("0.20")
    assert result.policy_config.short.max_short_target == Decimal("0.80")
    assert result.policy_config.funding.funding_scale == Decimal("1")
    assert result.signals.trend_score == Decimal("-0.4")
    assert result.signals.funding_rate == Decimal("0.5")
    assert result.signals.order_book_imbalance == Decimal("0.25")
    assert result.signals.microprice == Decimal("100.0500")
    assert result.policy_config.order_book.imbalance_shift_bps == Decimal("40.000")
    assert result.policy_config.ladder.tick_size == Decimal("0.01")
    assert result.usable_capacity == Decimal("1.0")
    assert result.usable_capacity <= result.capacity.q_max


def test_pre_s6_missing_funding_maps_to_neutral_only_when_component_is_not_active() -> None:
    result = _compose(
        stage=AdaptiveStage.S5_SHORT,
        market=_market_state(funding=None),
    )

    assert result.status.ready is True
    assert result.signals is not None
    assert result.signals.funding_rate == Decimal("0")


def test_pre_s7_missing_microstructure_signal_maps_to_neutral_but_keeps_cost_floor() -> None:
    result = _compose(stage=AdaptiveStage.S6_FUNDING, micro_ready=False)

    assert result.status.ready is True
    assert result.signals is not None
    assert result.signals.order_book_imbalance == Decimal("0")
    assert result.signals.microprice is None
    assert result.policy_config is not None
    assert result.policy_config.spacing.execution_cost_floor_bps == Decimal("34.0000")


def test_execution_cost_above_global_max_spacing_fails_closed() -> None:
    result = _compose(stage=AdaptiveStage.S3_INVENTORY, execution_floor="0.03")

    assert result.status.ready is False
    assert result.status.reason == "execution_floor_exceeds_max_spacing"


def test_symbol_identity_does_not_change_numeric_composition() -> None:
    aaa = _compose(instrument_id="AAA-PERP")
    bbb = _compose(instrument_id="BBB-PERP")

    assert aaa.status == bbb.status
    assert aaa.snapshot == bbb.snapshot
    assert aaa.signals == bbb.signals
    assert aaa.policy_config == bbb.policy_config
    assert aaa.usable_capacity == bbb.usable_capacity


def test_quantity_ratios_follow_template_not_absolute_coin_values() -> None:
    base = _compose()
    doubled_template = replace(
        _template(),
        ladder=replace(
            _template().ladder,
            order_quantity=Decimal("0.04"),
            max_abs_inventory=Decimal("0.20"),
        ),
        inventory=replace(
            _template().inventory,
            base_long_target=Decimal("0.10"),
            max_abs_target=Decimal("0.20"),
        ),
        short=replace(_template().short, max_short_target=Decimal("0.16")),
        funding=replace(_template().funding, max_abs_target=Decimal("0.20")),
    )
    doubled = compose_calibrated_adaptive_inputs(
        snapshot=_snapshot(),
        calibrated_market=_market_state(),
        microstructure_state=_micro_state(),
        microstructure=_micro_estimate(),
        capacity=_capacity(),
        template=doubled_template,
        adapter_config=CalibratedPolicyInputConfig(capacity_utilization_fraction=Decimal("0.5")),
        venue_tick_size=Decimal("0.01"),
    )

    assert doubled.policy_config == base.policy_config


def test_composition_is_independent_of_ambient_decimal_precision() -> None:
    with localcontext() as context:
        context.prec = 10
        low = _compose()
    with localcontext() as context:
        context.prec = 50
        high = _compose()

    assert low == high
