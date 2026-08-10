from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from grid_trade.application.calibrated_adaptive import (
    CalibratedAdaptiveInputs,
    CalibratedAdaptiveMetaConfig,
    CalibratedAdaptiveState,
    VenueGridConstraints,
    continue_calibrated_adaptive_reconciliation,
    initialize_calibrated_adaptive_grid,
    prepare_calibrated_adaptive_inputs,
    transition_calibrated_adaptive_grid,
)
from grid_trade.calibration.contracts import CalibrationObservation
from grid_trade.calibration.engine import CalibrationEngineConfig
from grid_trade.calibration.execution_cost import ExecutionCostConfig
from grid_trade.calibration.funding import FundingCalibrationConfig
from grid_trade.calibration.intensity import IntensityCalibrationConfig
from grid_trade.calibration.microstructure_contracts import (
    IntensityBucket,
    MarkoutSide,
    MaturedMarkout,
    OfiImpactSample,
    TopOfBookObservation,
)
from grid_trade.calibration.microstructure_engine import MicrostructureCalibrationConfig
from grid_trade.calibration.order_flow import OfiImpactConfig
from grid_trade.calibration.trend import TrendCalibrationConfig
from grid_trade.calibration.universal_engine import (
    UniversalCalibrationConfig,
    UniversalCalibrationState,
    update_universal_calibration,
)
from grid_trade.calibration.volatility import RobustVolatilityConfig
from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.orders import PassiveOrderIntent, WorkingOrder
from grid_trade.domain.risk import RiskLimits, RiskState
from grid_trade.evidence.events import EvidenceEvent, EvidenceKind
from grid_trade.evidence.ledger import evidence_digest
from grid_trade.risk.sizing import (
    InventoryCapacity,
    RiskSizingConfig,
    RiskSizingInput,
    derive_inventory_capacity,
)
from grid_trade.strategy.adaptive_grid import AdaptiveStage

_RUN_ID = "universal-calibrated-adaptive-deterministic-fixture"
_BASE_TIME = datetime(2026, 8, 10, 4, 0, tzinfo=UTC)
_ONE = Decimal(1)


@dataclass(frozen=True, slots=True)
class CalibratedAdaptiveRunResult:
    evidence_digest: str
    deterministic: bool
    symbol_invariant: bool
    scale_invariant: bool
    preparation_ready: bool
    calibration_generation: int
    adaptive_generation: int
    milestone_passed: bool
    economics_validated: bool = False
    production_authorized: bool = False
    alpha_validated: bool = False

    def __post_init__(self) -> None:
        if len(self.evidence_digest) != 64:
            raise ValueError("evidence_digest must be a SHA-256 hex digest")
        if self.calibration_generation <= 0:
            raise ValueError("calibration_generation must be positive")
        if self.adaptive_generation < 0:
            raise ValueError("adaptive_generation must be non-negative")
        expected = (
            self.deterministic
            and self.symbol_invariant
            and self.scale_invariant
            and self.preparation_ready
        )
        if self.milestone_passed != expected:
            raise ValueError("milestone_passed must match calibrated adaptive mechanics gates")
        if self.economics_validated or self.production_authorized or self.alpha_validated:
            raise ValueError("calibrated adaptive research must remain NO-GO")


@dataclass(frozen=True, slots=True)
class _PathResult:
    normalized_signature: tuple[object, ...]
    final_inputs: CalibratedAdaptiveInputs
    final_state: CalibratedAdaptiveState
    initial_ladder: tuple[PassiveOrderIntent, ...]
    transition_ladder: tuple[PassiveOrderIntent, ...]
    cancel_count: int
    submit_count: int
    risk_allowed: bool
    calibration_generation: int
    capacity: InventoryCapacity


def _time(minutes: int) -> datetime:
    return _BASE_TIME + timedelta(minutes=minutes)


def _universal_config() -> UniversalCalibrationConfig:
    robust_scale = Decimal("1.4826")
    return UniversalCalibrationConfig(
        foundation=CalibrationEngineConfig(
            volatility=RobustVolatilityConfig(4, 2, robust_scale),
            trend=TrendCalibrationConfig(2, Decimal("1"), Decimal("0.000001"), Decimal("5")),
            funding=FundingCalibrationConfig(4, 2, robust_scale, Decimal("3")),
        ),
        microstructure=MicrostructureCalibrationConfig(
            intensity=IntensityCalibrationConfig(
                3,
                20,
                Decimal("0.5"),
                Decimal("1.5"),
                21,
                Decimal("0.1"),
            ),
            ofi_impact=OfiImpactConfig(
                8,
                2,
                Decimal("0.01"),
                Decimal("0.01"),
                Decimal("2"),
            ),
            execution_cost=ExecutionCostConfig(
                8,
                2,
                Decimal("0.75"),
                Decimal("0.0002"),
                Decimal("0.003"),
            ),
            min_microstructure_quality=Decimal("0"),
        ),
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


def _risk_config() -> RiskSizingConfig:
    return RiskSizingConfig(
        max_notional_fraction=Decimal("0.1"),
        max_single_move_loss_fraction=Decimal("0.01"),
        volatility_floor=Decimal("0.0001"),
    )


def _intensity_buckets() -> tuple[IntensityBucket, ...]:
    return tuple(
        IntensityBucket(Decimal(distance), Decimal("100"), arrivals)
        for distance, arrivals in ((0, 1000), (1, 368), (2, 135), (3, 50))
    )


def _ofi_labels() -> tuple[OfiImpactSample, ...]:
    return (
        OfiImpactSample(_time(0), _time(2), Decimal("-1"), Decimal("-0.002")),
        OfiImpactSample(_time(1), _time(3), Decimal("1"), Decimal("0.002")),
    )


def _markouts(price_scale: Decimal) -> tuple[MaturedMarkout, ...]:
    return (
        MaturedMarkout(
            _time(0),
            _time(2),
            MarkoutSide.BUY,
            Decimal("100") * price_scale,
            Decimal("99.9") * price_scale,
        ),
        MaturedMarkout(
            _time(1),
            _time(3),
            MarkoutSide.SELL,
            Decimal("100") * price_scale,
            Decimal("100.2") * price_scale,
        ),
    )


def _mid(index: int, price_scale: Decimal) -> Decimal:
    return (Decimal(100), Decimal(101), Decimal(103), Decimal(106))[index] * price_scale


def _funding(index: int) -> Decimal:
    return (Decimal("0.0001"), Decimal("0.0002"), Decimal("0.0003"), Decimal("0.0004"))[index]


def _book_sizes(index: int, size_scale: Decimal) -> tuple[Decimal, Decimal]:
    bid, ask = ((5, 5), (8, 4), (9, 3), (7, 6))[index]
    return Decimal(bid) * size_scale, Decimal(ask) * size_scale


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


def _normalized_signature(
    *,
    final_inputs: CalibratedAdaptiveInputs,
    final_state: CalibratedAdaptiveState,
    transition_ladder: tuple[PassiveOrderIntent, ...],
    cancel_count: int,
    submit_count: int,
    capacity: InventoryCapacity,
) -> tuple[object, ...]:
    mid = final_inputs.snapshot.mid
    microprice_ratio = (
        None if final_inputs.signals.microprice is None else final_inputs.signals.microprice / mid
    )
    normalized_prices = tuple(order.price / mid for order in transition_ladder)
    return (
        final_inputs.snapshot.realized_volatility,
        final_inputs.signals.trend_score,
        final_inputs.signals.funding_rate,
        final_inputs.signals.order_book_imbalance,
        microprice_ratio,
        final_inputs.effective_q_max,
        capacity.binding_constraint,
        final_inputs.policy_config.center.reanchor_threshold_bps,
        final_inputs.policy_config.center.max_step_bps,
        final_inputs.policy_config.spacing.min_spacing_bps,
        final_inputs.policy_config.spacing.max_spacing_bps,
        final_inputs.policy_config.spacing.execution_cost_floor_bps,
        final_inputs.policy_config.ladder.spacing_bps,
        final_inputs.policy_config.ladder.order_quantity,
        final_inputs.policy_config.inventory.base_long_target,
        final_inputs.policy_config.short.max_short_target,
        final_state.policy_state.center / mid,
        final_state.policy_state.reference / mid,
        final_state.policy_state.spacing_bps,
        final_state.policy_state.target,
        final_state.policy_state.bid_scale,
        final_state.policy_state.ask_scale,
        final_state.policy_state.generation,
        normalized_prices,
        tuple(order.quantity for order in transition_ladder),
        tuple(order.side.value for order in transition_ladder),
        tuple(order.reduce_only for order in transition_ladder),
        cancel_count,
        submit_count,
    )


def _run_path(
    *,
    instrument_id: str,
    price_scale: Decimal,
    size_scale: Decimal,
) -> _PathResult:
    config = _universal_config()
    universal_state = UniversalCalibrationState()
    updates = []
    labels = _ofi_labels()
    markouts = _markouts(price_scale)
    source_id = "fixture:universal-calibrated-adaptive"

    for index, minute in enumerate((10, 11, 12, 13)):
        mid = _mid(index, price_scale)
        bid_size, ask_size = _book_sizes(index, size_scale)
        observation = CalibrationObservation(
            timestamp=_time(minute),
            source_id=source_id,
            instrument_id=instrument_id,
            mid=mid,
            funding_rate=_funding(index),
        )
        book = TopOfBookObservation(
            timestamp=_time(minute),
            source_id=source_id,
            instrument_id=instrument_id,
            best_bid=mid - price_scale,
            bid_size=bid_size,
            best_ask=mid + price_scale,
            ask_size=ask_size,
        )
        update = update_universal_calibration(
            universal_state,
            observation=observation,
            book=book,
            intensity_buckets=_intensity_buckets(),
            markouts=markouts,
            new_ofi_impact_samples=labels if index == 0 else (),
            maker_fee_rate=Decimal("0.0001"),
            tick_size=Decimal("0.01") * price_scale,
            config=config,
        )
        universal_state = update.next_state
        updates.append(update)

    init_update = updates[2]
    transition_update = updates[3]
    equity = Decimal("100") * price_scale
    init_capacity = derive_inventory_capacity(
        RiskSizingInput(
            equity=equity,
            reference_price=_mid(2, price_scale),
            volatility_scale=init_update.market_state.volatility_scale or Decimal(0),
            max_margin_notional=equity,
            venue_max_quantity=Decimal("10"),
        ),
        _risk_config(),
    )
    init_snapshot = MarketSnapshot(
        timestamp=_time(12),
        best_bid=_mid(2, price_scale) - price_scale,
        best_ask=_mid(2, price_scale) + price_scale,
        realized_volatility=Decimal(0),
        position_quantity=Decimal(0),
        source_id=source_id,
    )
    init_preparation = prepare_calibrated_adaptive_inputs(
        snapshot=init_snapshot,
        calibrated=init_update.market_state,
        capacity=init_capacity,
        meta=_meta(),
        venue=VenueGridConstraints(
            tick_size=Decimal("0.01") * price_scale,
            quantity_step=Decimal("0.001"),
        ),
    )
    if init_preparation.inputs is None:
        raise RuntimeError(f"initial preparation failed: {init_preparation.reason}")
    app_state, initial_ladder = initialize_calibrated_adaptive_grid(init_preparation.inputs)

    transition_capacity = derive_inventory_capacity(
        RiskSizingInput(
            equity=equity,
            reference_price=_mid(3, price_scale),
            volatility_scale=transition_update.market_state.volatility_scale or Decimal(0),
            max_margin_notional=equity,
            venue_max_quantity=Decimal("10"),
        ),
        _risk_config(),
    )
    transition_snapshot = MarketSnapshot(
        timestamp=_time(13),
        best_bid=_mid(3, price_scale) - price_scale,
        best_ask=_mid(3, price_scale) + price_scale,
        realized_volatility=Decimal(0),
        position_quantity=Decimal(0),
        source_id=source_id,
    )
    preparation = prepare_calibrated_adaptive_inputs(
        snapshot=transition_snapshot,
        calibrated=transition_update.market_state,
        capacity=transition_capacity,
        meta=_meta(),
        venue=VenueGridConstraints(
            tick_size=Decimal("0.01") * price_scale,
            quantity_step=Decimal("0.001"),
        ),
    )
    if preparation.inputs is None:
        raise RuntimeError(f"transition preparation failed: {preparation.reason}")

    working = _working(initial_ladder)
    risk_limits = RiskLimits(
        max_abs_position=preparation.inputs.effective_q_max,
        max_drawdown_fraction=Decimal("0.20"),
        max_data_age_ms=1_000,
        max_open_orders=20,
    )
    risk_state = RiskState(
        equity=equity,
        peak_equity=equity,
        open_order_count=len(working),
        now=transition_snapshot.timestamp,
    )
    transition = transition_calibrated_adaptive_grid(
        inputs=preparation.inputs,
        state=app_state,
        risk_limits=risk_limits,
        risk_state=risk_state,
        working_orders=working,
    )
    final_transition = transition
    if transition.reconciliation.cancel:
        final_transition = continue_calibrated_adaptive_reconciliation(
            transition,
            snapshot=transition_snapshot,
            risk_limits=risk_limits,
            risk_state=RiskState(
                equity=equity,
                peak_equity=equity,
                open_order_count=0,
                now=transition_snapshot.timestamp,
            ),
            working_orders=(),
        )

    final_state = final_transition.next_state
    transition_ladder = final_transition.desired_ladder
    signature = _normalized_signature(
        final_inputs=preparation.inputs,
        final_state=final_state,
        transition_ladder=transition_ladder,
        cancel_count=len(transition.reconciliation.cancel),
        submit_count=len(final_transition.reconciliation.submit),
        capacity=transition_capacity,
    )
    return _PathResult(
        normalized_signature=signature,
        final_inputs=preparation.inputs,
        final_state=final_state,
        initial_ladder=initial_ladder,
        transition_ladder=transition_ladder,
        cancel_count=len(transition.reconciliation.cancel),
        submit_count=len(final_transition.reconciliation.submit),
        risk_allowed=final_transition.risk_decision.allow_new_risk,
        calibration_generation=universal_state.foundation_state.generation,
        capacity=transition_capacity,
    )


def _events(
    baseline: _PathResult,
    *,
    deterministic: bool,
    symbol_invariant: bool,
    scale_invariant: bool,
) -> tuple[EvidenceEvent, ...]:
    timestamp = _time(13)
    inputs = baseline.final_inputs
    state = baseline.final_state
    events = [
        EvidenceEvent.create(
            run_id=_RUN_ID,
            sequence=0,
            timestamp=timestamp,
            kind=EvidenceKind.MARKET_SNAPSHOT,
            payload={
                "source_id": inputs.snapshot.source_id,
                "mid": inputs.snapshot.mid,
                "calibrated_volatility": inputs.snapshot.realized_volatility,
                "trend_score": inputs.signals.trend_score,
                "funding_score": inputs.signals.funding_rate,
                "order_book_score": inputs.signals.order_book_imbalance,
                "calibration_generation": baseline.calibration_generation,
            },
        ),
        EvidenceEvent.create(
            run_id=_RUN_ID,
            sequence=1,
            timestamp=timestamp,
            kind=EvidenceKind.DESIRED_LADDER,
            payload={
                "q_max": inputs.effective_q_max,
                "binding_constraint": baseline.capacity.binding_constraint,
                "center_reanchor_bps": inputs.policy_config.center.reanchor_threshold_bps,
                "center_max_step_bps": inputs.policy_config.center.max_step_bps,
                "min_spacing_bps": inputs.policy_config.spacing.min_spacing_bps,
                "max_spacing_bps": inputs.policy_config.spacing.max_spacing_bps,
                "execution_cost_floor_bps": (inputs.policy_config.spacing.execution_cost_floor_bps),
                "order_quantity": inputs.policy_config.ladder.order_quantity,
                "base_long_target": inputs.policy_config.inventory.base_long_target,
                "max_short_target": inputs.policy_config.short.max_short_target,
                "adaptive_generation": state.policy_state.generation,
                "order_count": len(baseline.transition_ladder),
            },
        ),
        EvidenceEvent.create(
            run_id=_RUN_ID,
            sequence=2,
            timestamp=timestamp,
            kind=EvidenceKind.RECONCILIATION_PLAN,
            payload={
                "cancel_count": baseline.cancel_count,
                "submit_count": baseline.submit_count,
                "risk_allowed": baseline.risk_allowed,
            },
        ),
        EvidenceEvent.create(
            run_id=_RUN_ID,
            sequence=3,
            timestamp=timestamp,
            kind=EvidenceKind.RUN_SUMMARY,
            payload={
                "deterministic": deterministic,
                "symbol_invariant": symbol_invariant,
                "scale_invariant": scale_invariant,
                "preparation_ready": True,
                "calibration_generation": baseline.calibration_generation,
                "adaptive_generation": state.policy_state.generation,
                "milestone_passed": (deterministic and symbol_invariant and scale_invariant),
                "economics_validated": False,
                "alpha_validated": False,
                "production_authorized": False,
                "scope": "universal_calibration_to_adaptive_mechanics_only",
            },
        ),
    ]
    return tuple(events)


def run_checked_in_calibrated_adaptive() -> CalibratedAdaptiveRunResult:
    baseline = _run_path(
        instrument_id="GENERIC-PERP",
        price_scale=Decimal("1"),
        size_scale=Decimal("1"),
    )
    repeated = _run_path(
        instrument_id="GENERIC-PERP",
        price_scale=Decimal("1"),
        size_scale=Decimal("1"),
    )
    renamed = _run_path(
        instrument_id="ALT-PERP",
        price_scale=Decimal("1"),
        size_scale=Decimal("1"),
    )
    scaled = _run_path(
        instrument_id="GENERIC-PERP",
        price_scale=Decimal("100"),
        size_scale=Decimal("100"),
    )

    deterministic = baseline.normalized_signature == repeated.normalized_signature
    symbol_invariant = baseline.normalized_signature == renamed.normalized_signature
    scale_invariant = baseline.normalized_signature == scaled.normalized_signature
    preparation_ready = True
    milestone_passed = deterministic and symbol_invariant and scale_invariant and preparation_ready
    events = _events(
        baseline,
        deterministic=deterministic,
        symbol_invariant=symbol_invariant,
        scale_invariant=scale_invariant,
    )
    return CalibratedAdaptiveRunResult(
        evidence_digest=evidence_digest(events),
        deterministic=deterministic,
        symbol_invariant=symbol_invariant,
        scale_invariant=scale_invariant,
        preparation_ready=preparation_ready,
        calibration_generation=baseline.calibration_generation,
        adaptive_generation=baseline.final_state.policy_state.generation,
        milestone_passed=milestone_passed,
    )


if __name__ == "__main__":
    print(run_checked_in_calibrated_adaptive().evidence_digest)


__all__ = [
    "CalibratedAdaptiveRunResult",
    "run_checked_in_calibrated_adaptive",
]
