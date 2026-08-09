import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from grid_trade.application.adaptive_grid import (
    continue_adaptive_grid_reconciliation,
    transition_adaptive_grid,
)
from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.orders import OrderSide, PassiveOrderIntent, ReconciliationPlan, WorkingOrder
from grid_trade.domain.risk import RiskDecision, RiskLimits, RiskState
from grid_trade.evidence.events import EvidenceEvent, EvidenceKind, PnLBreakdown
from grid_trade.evidence.ledger import evidence_digest
from grid_trade.strategy.adaptive_grid import (
    AdaptiveGridDecision,
    AdaptiveGridPolicyConfig,
    AdaptiveStage,
    initialize_adaptive_grid,
)
from grid_trade.strategy.adaptive_ladder import AdaptiveLadderConfig
from grid_trade.strategy.adaptive_signals import AdaptiveSignals
from grid_trade.strategy.conditional_short import ShortOverlayConfig
from grid_trade.strategy.de_risk import DeRiskConfig
from grid_trade.strategy.dynamic_center import DynamicCenterConfig
from grid_trade.strategy.funding_bias import FundingBiasConfig
from grid_trade.strategy.inventory_target import InventoryTargetConfig
from grid_trade.strategy.order_book_reference import OrderBookReferenceConfig
from grid_trade.strategy.volatility_spacing import VolatilitySpacingConfig

_ZERO_PNL = PnLBreakdown(
    realized_grid=Decimal(0),
    directional_mark=Decimal(0),
    fees=Decimal(0),
    funding=Decimal(0),
    emergency_execution=Decimal(0),
)


@dataclass(frozen=True, slots=True)
class StageMechanicsResult:
    stage: AdaptiveStage
    evidence_digest: str
    target_path: tuple[Decimal, ...]
    reference_path: tuple[Decimal, ...]
    spacing_path: tuple[int, ...]
    generation_count: int
    cancel_count: int
    submit_count: int
    reduce_only_submit_count: int
    short_new_risk_submit_count: int
    risk_rejection_count: int
    risk_reasons_seen: tuple[str, ...]
    ending_inventory: Decimal
    pnl: PnLBreakdown
    execution_scope: str

    def __post_init__(self) -> None:
        if len(self.evidence_digest) != 64:
            raise ValueError("evidence_digest must be a SHA-256 hex digest")
        if not self.target_path or not self.reference_path or not self.spacing_path:
            raise ValueError("controlled paths must be non-empty")
        if not (len(self.target_path) == len(self.reference_path) == len(self.spacing_path)):
            raise ValueError("controlled paths must have equal length")
        for field_name in (
            "generation_count",
            "cancel_count",
            "submit_count",
            "reduce_only_submit_count",
            "short_new_risk_submit_count",
            "risk_rejection_count",
        ):
            if getattr(self, field_name) < 0:
                raise ValueError(f"{field_name} must be non-negative")
        if len(set(self.risk_reasons_seen)) != len(self.risk_reasons_seen):
            raise ValueError("risk_reasons_seen must not contain duplicates")
        if self.execution_scope != "policy_reconciliation_only":
            raise ValueError("unsupported adaptive execution scope")
        if not self.ending_inventory.is_finite():
            raise ValueError("ending_inventory must be finite")
        if self.pnl.total != 0:
            raise ValueError("controlled adaptive mechanics must not invent PnL")


@dataclass(frozen=True, slots=True)
class AdaptiveComparisonResult:
    evidence_digest: str
    stages: tuple[StageMechanicsResult, ...]
    deterministic: bool
    execution_scope: str
    production_authorized: bool = False
    alpha_validated: bool = False

    def __post_init__(self) -> None:
        if len(self.evidence_digest) != 64:
            raise ValueError("evidence_digest must be a SHA-256 hex digest")
        if tuple(stage.stage for stage in self.stages) != tuple(AdaptiveStage):
            raise ValueError("adaptive results must contain S3-S7 in order")
        if self.execution_scope != "policy_reconciliation_only":
            raise ValueError("unsupported adaptive comparison scope")
        if self.production_authorized or self.alpha_validated:
            raise ValueError("controlled adaptive comparison must remain NO-GO")


def _working_orders(ladder: tuple[PassiveOrderIntent, ...]) -> tuple[WorkingOrder, ...]:
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


def _risk_state(snapshot: MarketSnapshot, open_order_count: int) -> RiskState:
    return RiskState(
        equity=Decimal("100"),
        peak_equity=Decimal("100"),
        open_order_count=open_order_count,
        now=snapshot.timestamp,
    )


def _risk_payload(decision: RiskDecision, *, phase: str) -> dict[str, object]:
    return {
        "phase": phase,
        "allow_new_risk": decision.allow_new_risk,
        "cancel_all_passive": decision.cancel_all_passive,
        "target_flat": decision.target_flat,
        "reasons": tuple(reason.value for reason in decision.reasons),
    }


def _reconciliation_payload(
    reconciliation: ReconciliationPlan,
    *,
    phase: str,
) -> dict[str, object]:
    return {
        "phase": phase,
        "cancel": reconciliation.cancel,
        "submit": tuple(order.client_order_id for order in reconciliation.submit),
    }


def _record_risk_rejection(decision: RiskDecision, reasons_seen: list[str]) -> int:
    if decision.allow_new_risk:
        return 0
    for reason in decision.reasons:
        if reason.value not in reasons_seen:
            reasons_seen.append(reason.value)
    return 1


def _decision_payload(
    decision: AdaptiveGridDecision,
) -> tuple[tuple[EvidenceKind, dict[str, object]], ...]:
    return (
        (
            EvidenceKind.CENTER_DECISION,
            {
                "stage": decision.stage.name,
                "previous_center": decision.center.previous_center,
                "proposed_center": decision.center.proposed_center,
                "market_mid": decision.center.market_mid,
                "threshold_crossed": decision.center.threshold_crossed,
                "effective_generation": decision.effective_generation,
                "economic_ladder_changed": decision.economic_ladder_changed,
            },
        ),
        (
            EvidenceKind.SPACING_DECISION,
            {
                "stage": decision.stage.name,
                "previous_spacing_bps": decision.spacing.previous_spacing_bps,
                "effective_spacing_bps": decision.spacing.effective_spacing_bps,
                "realized_volatility": decision.spacing.realized_volatility,
            },
        ),
        (
            EvidenceKind.DERISK_DECISION,
            {
                "stage": decision.stage.name,
                "applied": decision.de_risk_applied,
                "regime": decision.de_risk.regime.value,
                "requested_target": decision.de_risk.requested_target,
                "effective_target": decision.de_risk.effective_target,
            },
        ),
        (
            EvidenceKind.SHORT_DECISION,
            {
                "stage": decision.stage.name,
                "applied": decision.short_applied,
                "phase": decision.short.phase.value,
                "requested_target": decision.short.requested_target,
                "effective_target": decision.short.effective_target,
            },
        ),
        (
            EvidenceKind.FUNDING_DECISION,
            {
                "stage": decision.stage.name,
                "applied": decision.funding_applied,
                "normalized_funding": decision.funding.normalized_funding,
                "target_shift": decision.funding.target_shift,
                "effective_target": decision.funding.effective_target,
            },
        ),
        (
            EvidenceKind.INVENTORY_DECISION,
            {
                "stage": decision.stage.name,
                "target": decision.inventory.target,
                "normalized_inventory_error": decision.inventory.normalized_inventory_error,
                "reservation_shift_bps": decision.inventory.reservation_shift_bps,
                "bid_scale": decision.inventory.bid_scale,
                "ask_scale": decision.inventory.ask_scale,
            },
        ),
        (
            EvidenceKind.ORDER_BOOK_DECISION,
            {
                "stage": decision.stage.name,
                "applied": decision.order_book_applied,
                "inventory_reference": decision.inventory_reference,
                "microprice_used": decision.order_book.microprice_used,
                "signed_imbalance_shift_bps": decision.order_book.signed_imbalance_shift_bps,
                "effective_reference": decision.order_book.effective_reference,
            },
        ),
    )


def _count_submissions(orders: tuple[PassiveOrderIntent, ...]) -> tuple[int, int, int]:
    reduce_only = sum(1 for order in orders if order.reduce_only)
    short_new_risk = sum(
        1 for order in orders if order.side is OrderSide.SELL and not order.reduce_only
    )
    return len(orders), reduce_only, short_new_risk


def _config(stage: AdaptiveStage) -> AdaptiveGridPolicyConfig:
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
        stage=stage,
    )


def _risk_limits() -> RiskLimits:
    return RiskLimits(
        max_abs_position=Decimal("0.10"),
        max_drawdown_fraction=Decimal("0.20"),
        max_data_age_ms=1_000,
        max_open_orders=20,
    )


def _validate_controlled_path(
    snapshots: tuple[MarketSnapshot, ...],
    signals: tuple[AdaptiveSignals, ...],
) -> None:
    if not snapshots or len(snapshots) != len(signals):
        raise ValueError("snapshots and signals must be non-empty and equal length")
    previous = snapshots[0].timestamp
    for snapshot in snapshots[1:]:
        if snapshot.timestamp <= previous:
            raise ValueError("snapshot timestamps must be strictly increasing")
        previous = snapshot.timestamp


def _run_stage_once(
    *,
    run_id: str,
    stage: AdaptiveStage,
    snapshots: tuple[MarketSnapshot, ...],
    signals: tuple[AdaptiveSignals, ...],
) -> StageMechanicsResult:
    _validate_controlled_path(snapshots, signals)
    config = _config(stage)
    limits = _risk_limits()
    state, _ = initialize_adaptive_grid(snapshots[0], signals[0], config)
    working: tuple[WorkingOrder, ...] = ()
    events: list[EvidenceEvent] = []
    target_path: list[Decimal] = []
    reference_path: list[Decimal] = []
    spacing_path: list[int] = []
    cancel_count = 0
    submit_count = 0
    reduce_only_submit_count = 0
    short_new_risk_submit_count = 0
    risk_rejection_count = 0
    risk_reasons_seen: list[str] = []

    def append_event(
        *,
        timestamp: datetime,
        kind: EvidenceKind,
        payload: dict[str, object],
    ) -> None:
        events.append(
            EvidenceEvent.create(
                run_id=run_id,
                sequence=len(events),
                timestamp=timestamp,
                kind=kind,
                payload=payload,
            ),
        )

    for snapshot, signal in zip(snapshots, signals, strict=True):
        append_event(
            timestamp=snapshot.timestamp,
            kind=EvidenceKind.MARKET_SNAPSHOT,
            payload={
                "stage": stage.name,
                "mid": snapshot.mid,
                "realized_volatility": snapshot.realized_volatility,
                "position_quantity": snapshot.position_quantity,
                "source_id": snapshot.source_id,
                "trend_score": signal.trend_score,
                "funding_rate": signal.funding_rate,
                "order_book_imbalance": signal.order_book_imbalance,
                "microprice": signal.microprice,
            },
        )
        transition = transition_adaptive_grid(
            snapshot=snapshot,
            signals=signal,
            state=state,
            config=config,
            risk_limits=limits,
            risk_state=_risk_state(snapshot, len(working)),
            working_orders=working,
        )
        for kind, payload in _decision_payload(transition.decision):
            append_event(timestamp=snapshot.timestamp, kind=kind, payload=payload)
        append_event(
            timestamp=snapshot.timestamp,
            kind=EvidenceKind.RISK_DECISION,
            payload=_risk_payload(transition.risk_decision, phase="decision"),
        )
        append_event(
            timestamp=snapshot.timestamp,
            kind=EvidenceKind.RECONCILIATION_PLAN,
            payload=_reconciliation_payload(transition.reconciliation, phase="decision"),
        )
        risk_rejection_count += _record_risk_rejection(
            transition.risk_decision,
            risk_reasons_seen,
        )

        if transition.reconciliation.cancel:
            cancel_count += len(transition.reconciliation.cancel)
            working = ()
            continued = continue_adaptive_grid_reconciliation(
                transition,
                snapshot=snapshot,
                risk_limits=limits,
                risk_state=_risk_state(snapshot, 0),
                working_orders=(),
            )
            append_event(
                timestamp=snapshot.timestamp,
                kind=EvidenceKind.RISK_DECISION,
                payload=_risk_payload(continued.risk_decision, phase="post_cancel"),
            )
            append_event(
                timestamp=snapshot.timestamp,
                kind=EvidenceKind.RECONCILIATION_PLAN,
                payload=_reconciliation_payload(
                    continued.reconciliation,
                    phase="post_cancel",
                ),
            )
            risk_rejection_count += _record_risk_rejection(
                continued.risk_decision,
                risk_reasons_seen,
            )
            submitted, reduce_only, short_new_risk = _count_submissions(
                continued.reconciliation.submit,
            )
            submit_count += submitted
            reduce_only_submit_count += reduce_only
            short_new_risk_submit_count += short_new_risk
            if continued.reconciliation.submit:
                working = _working_orders(continued.reconciliation.submit)
            state = continued.next_state
        else:
            submitted, reduce_only, short_new_risk = _count_submissions(
                transition.reconciliation.submit,
            )
            submit_count += submitted
            reduce_only_submit_count += reduce_only
            short_new_risk_submit_count += short_new_risk
            if transition.reconciliation.submit:
                working = _working_orders(transition.reconciliation.submit)
            state = transition.next_state

        target_path.append(state.target)
        reference_path.append(state.reference)
        spacing_path.append(state.spacing_bps)

    append_event(
        timestamp=snapshots[-1].timestamp,
        kind=EvidenceKind.RUN_SUMMARY,
        payload={
            "stage": stage.name,
            "target_path": tuple(target_path),
            "reference_path": tuple(reference_path),
            "spacing_path": tuple(spacing_path),
            "generation_count": state.generation,
            "cancel_count": cancel_count,
            "submit_count": submit_count,
            "reduce_only_submit_count": reduce_only_submit_count,
            "short_new_risk_submit_count": short_new_risk_submit_count,
            "risk_rejection_count": risk_rejection_count,
            "risk_reasons_seen": tuple(risk_reasons_seen),
            "ending_inventory": snapshots[-1].position_quantity,
            "execution_scope": "policy_reconciliation_only",
            "production_authorized": False,
            "alpha_validated": False,
        },
    )

    return StageMechanicsResult(
        stage=stage,
        evidence_digest=evidence_digest(tuple(events)),
        target_path=tuple(target_path),
        reference_path=tuple(reference_path),
        spacing_path=tuple(spacing_path),
        generation_count=state.generation,
        cancel_count=cancel_count,
        submit_count=submit_count,
        reduce_only_submit_count=reduce_only_submit_count,
        short_new_risk_submit_count=short_new_risk_submit_count,
        risk_rejection_count=risk_rejection_count,
        risk_reasons_seen=tuple(risk_reasons_seen),
        ending_inventory=snapshots[-1].position_quantity,
        pnl=_ZERO_PNL,
        execution_scope="policy_reconciliation_only",
    )


def _aggregate_digest(stages: tuple[StageMechanicsResult, ...]) -> str:
    payload = "\n".join(f"{stage.stage.name}:{stage.evidence_digest}" for stage in stages).encode()
    return hashlib.sha256(payload).hexdigest()


def _run_once(
    *,
    run_id: str,
    snapshots: tuple[MarketSnapshot, ...],
    signals: tuple[AdaptiveSignals, ...],
) -> AdaptiveComparisonResult:
    stages = tuple(
        _run_stage_once(
            run_id=f"{run_id}:{stage.name.lower()}",
            stage=stage,
            snapshots=snapshots,
            signals=signals,
        )
        for stage in AdaptiveStage
    )
    return AdaptiveComparisonResult(
        evidence_digest=_aggregate_digest(stages),
        stages=stages,
        deterministic=False,
        execution_scope="policy_reconciliation_only",
    )


def run_adaptive_comparison(
    *,
    run_id: str,
    snapshots: tuple[MarketSnapshot, ...],
    signals: tuple[AdaptiveSignals, ...],
) -> AdaptiveComparisonResult:
    if not run_id.strip():
        raise ValueError("run_id must be non-empty")
    first = _run_once(run_id=run_id, snapshots=snapshots, signals=signals)
    second = _run_once(run_id=run_id, snapshots=snapshots, signals=signals)
    return replace(first, deterministic=first == second)


def _controlled_fixture() -> tuple[tuple[MarketSnapshot, ...], tuple[AdaptiveSignals, ...]]:
    base = datetime(2026, 8, 9, 15, 0, tzinfo=UTC)
    rows = (
        ("100.00", "0.005", "0", "0", "0", "0", None),
        ("100.20", "0.005", "0.05", "-0.30", "0", "0", None),
        ("99.80", "0.008", "0.05", "-0.90", "0", "0", None),
        ("99.70", "0.008", "0", "-0.90", "0.001", "0", None),
        ("99.50", "0.012", "0", "-0.90", "0.001", "0.5", "99.80"),
    )
    snapshots: list[MarketSnapshot] = []
    signals: list[AdaptiveSignals] = []
    for index, (mid, volatility, position, trend, funding, imbalance, microprice) in enumerate(
        rows
    ):
        value = Decimal(mid)
        snapshots.append(
            MarketSnapshot(
                timestamp=base + timedelta(seconds=index),
                best_bid=value - Decimal("0.01"),
                best_ask=value + Decimal("0.01"),
                realized_volatility=Decimal(volatility),
                position_quantity=Decimal(position),
                source_id=f"fixture:adaptive-ablation:{index}",
            ),
        )
        signals.append(
            AdaptiveSignals(
                trend_score=Decimal(trend),
                funding_rate=Decimal(funding),
                order_book_imbalance=Decimal(imbalance),
                microprice=None if microprice is None else Decimal(microprice),
            ),
        )
    return tuple(snapshots), tuple(signals)


def run_checked_in_comparison() -> AdaptiveComparisonResult:
    snapshots, signals = _controlled_fixture()
    return run_adaptive_comparison(
        run_id="adaptive-controlled",
        snapshots=snapshots,
        signals=signals,
    )


def main() -> None:
    print(run_checked_in_comparison().evidence_digest)


if __name__ == "__main__":
    main()
