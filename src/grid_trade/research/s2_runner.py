from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from grid_trade.application.s2_adaptive_grid import (
    continue_s2_adaptive_grid_reconciliation,
    transition_s2_adaptive_grid,
)
from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.orders import PassiveOrderIntent, ReconciliationPlan, WorkingOrder
from grid_trade.domain.risk import RiskDecision, RiskLimits, RiskState
from grid_trade.evidence.events import EvidenceEvent, EvidenceKind, PnLBreakdown
from grid_trade.evidence.ledger import evidence_digest
from grid_trade.strategy.dynamic_center import DynamicCenterConfig
from grid_trade.strategy.grid_geometry import FixedLongGridConfig, build_long_grid_at_center
from grid_trade.strategy.s2_adaptive_grid import S2GridDecision, initialize_s2_grid
from grid_trade.strategy.volatility_spacing import VolatilitySpacingConfig

_ZERO_PNL = PnLBreakdown(
    realized_grid=Decimal(0),
    directional_mark=Decimal(0),
    fees=Decimal(0),
    funding=Decimal(0),
    emergency_execution=Decimal(0),
)


@dataclass(frozen=True, slots=True)
class S2ComparisonResult:
    evidence_digest: str
    s1_spacing_path: tuple[int, ...]
    s2_spacing_path: tuple[int, ...]
    s2_center_path: tuple[Decimal, ...]
    s2_spacing_change_count: int
    s2_generation_count: int
    cancel_count: int
    submit_count: int
    queue_reset_count: int
    risk_rejection_count: int
    risk_reasons_seen: tuple[str, ...]
    ending_inventory: Decimal
    pnl: PnLBreakdown
    deterministic: bool
    execution_scope: str
    production_authorized: bool = False
    alpha_validated: bool = False

    def __post_init__(self) -> None:
        if len(self.evidence_digest) != 64:
            raise ValueError("evidence_digest must be a SHA-256 hex digest")
        if not self.s1_spacing_path or not self.s2_spacing_path:
            raise ValueError("spacing paths must be non-empty")
        if len(self.s1_spacing_path) != len(self.s2_spacing_path):
            raise ValueError("S1 and S2 spacing paths must have equal length")
        if len(self.s2_center_path) != len(self.s2_spacing_path):
            raise ValueError("S2 center and spacing paths must have equal length")
        for field_name in (
            "s2_spacing_change_count",
            "s2_generation_count",
            "cancel_count",
            "submit_count",
            "queue_reset_count",
            "risk_rejection_count",
        ):
            if getattr(self, field_name) < 0:
                raise ValueError(f"{field_name} must be non-negative")
        if len(set(self.risk_reasons_seen)) != len(self.risk_reasons_seen):
            raise ValueError("risk_reasons_seen must not contain duplicates")
        if any(not reason.strip() for reason in self.risk_reasons_seen):
            raise ValueError("risk reasons must be non-empty")
        if not self.ending_inventory.is_finite():
            raise ValueError("ending_inventory must be finite")
        if self.execution_scope != "policy_reconciliation_only":
            raise ValueError("unsupported S2 execution scope")
        if self.production_authorized or self.alpha_validated:
            raise ValueError("controlled S2 mechanics must remain NO-GO")


def _validate_snapshots(snapshots: tuple[MarketSnapshot, ...]) -> None:
    if not snapshots:
        raise ValueError("snapshots must be non-empty")
    previous = snapshots[0].timestamp
    for snapshot in snapshots[1:]:
        if snapshot.timestamp <= previous:
            raise ValueError("snapshot timestamps must be strictly increasing")
        previous = snapshot.timestamp


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
            instrument_id=order.instrument_id,
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


def _center_payload(decision: S2GridDecision) -> dict[str, object]:
    return {
        "previous_center": decision.previous_center,
        "candidate_center": decision.candidate_center,
        "effective_center": decision.effective_center,
        "center_deviation_bps": decision.center_deviation_bps,
        "center_threshold_crossed": decision.center_threshold_crossed,
        "previous_generation": decision.previous_generation,
        "effective_generation": decision.effective_generation,
        "economic_ladder_changed": decision.economic_ladder_changed,
    }


def _spacing_payload(decision: S2GridDecision) -> dict[str, object]:
    spacing = decision.spacing_decision
    return {
        "previous_spacing_bps": decision.previous_spacing_bps,
        "candidate_spacing_bps": decision.candidate_spacing_bps,
        "effective_spacing_bps": decision.effective_spacing_bps,
        "realized_volatility": spacing.realized_volatility,
        "volatility_spacing_bps": spacing.volatility_spacing_bps,
        "unclamped_spacing_bps": spacing.unclamped_spacing_bps,
        "spacing_changed": decision.spacing_changed,
        "economic_ladder_changed": decision.economic_ladder_changed,
    }


def _record_risk_rejection(decision: RiskDecision, reasons_seen: list[str]) -> int:
    if decision.allow_new_risk:
        return 0
    for reason in decision.reasons:
        if reason.value not in reasons_seen:
            reasons_seen.append(reason.value)
    return 1


def _run_s2_once(
    *,
    run_id: str,
    snapshots: tuple[MarketSnapshot, ...],
    grid_config: FixedLongGridConfig,
    center_config: DynamicCenterConfig,
    spacing_config: VolatilitySpacingConfig,
    risk_limits: RiskLimits,
) -> S2ComparisonResult:
    if not run_id.strip():
        raise ValueError("run_id must be non-empty")
    _validate_snapshots(snapshots)

    initial_snapshot = snapshots[0]
    state = initialize_s2_grid(initial_snapshot, grid_config, spacing_config)
    initial_ladder = build_long_grid_at_center(
        state.center,
        replace(grid_config, spacing_bps=state.spacing_bps),
        generation=state.generation,
        stage="s2",
    )

    s1_spacing_path: list[int] = [grid_config.spacing_bps]
    s2_spacing_path: list[int] = [state.spacing_bps]
    s2_center_path: list[Decimal] = [state.center]
    spacing_change_count = 0
    cancel_count = 0
    submit_count = 0
    queue_reset_count = 0
    risk_rejection_count = 0
    risk_reasons_seen: list[str] = []
    events: list[EvidenceEvent] = []

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

    append_event(
        timestamp=initial_snapshot.timestamp,
        kind=EvidenceKind.MARKET_SNAPSHOT,
        payload={
            "mid": initial_snapshot.mid,
            "realized_volatility": initial_snapshot.realized_volatility,
            "position_quantity": initial_snapshot.position_quantity,
            "source_id": initial_snapshot.source_id,
        },
    )
    append_event(
        timestamp=initial_snapshot.timestamp,
        kind=EvidenceKind.CENTER_DECISION,
        payload={
            "phase": "initialized",
            "effective_center": state.center,
            "effective_generation": state.generation,
        },
    )
    append_event(
        timestamp=initial_snapshot.timestamp,
        kind=EvidenceKind.SPACING_DECISION,
        payload={
            "phase": "initialized",
            "s1_fixed_spacing_bps": grid_config.spacing_bps,
            "effective_spacing_bps": state.spacing_bps,
            "realized_volatility": initial_snapshot.realized_volatility,
        },
    )

    initial_transition = transition_s2_adaptive_grid(
        snapshot=initial_snapshot,
        state=state,
        center_config=center_config,
        grid_config=grid_config,
        spacing_config=spacing_config,
        risk_limits=risk_limits,
        risk_state=_risk_state(initial_snapshot, 0),
        working_orders=(),
    )
    append_event(
        timestamp=initial_snapshot.timestamp,
        kind=EvidenceKind.RISK_DECISION,
        payload=_risk_payload(initial_transition.risk_decision, phase="initial"),
    )
    append_event(
        timestamp=initial_snapshot.timestamp,
        kind=EvidenceKind.RECONCILIATION_PLAN,
        payload=_reconciliation_payload(initial_transition.reconciliation, phase="initial"),
    )
    risk_rejection_count += _record_risk_rejection(
        initial_transition.risk_decision,
        risk_reasons_seen,
    )
    submit_count += len(initial_transition.reconciliation.submit)
    if initial_transition.reconciliation.submit:
        working = _working_orders(initial_transition.reconciliation.submit)
        state = initial_transition.next_state
    else:
        working = _working_orders(initial_ladder)

    for snapshot in snapshots[1:]:
        append_event(
            timestamp=snapshot.timestamp,
            kind=EvidenceKind.MARKET_SNAPSHOT,
            payload={
                "mid": snapshot.mid,
                "realized_volatility": snapshot.realized_volatility,
                "position_quantity": snapshot.position_quantity,
                "source_id": snapshot.source_id,
            },
        )
        previous_state = state
        transition = transition_s2_adaptive_grid(
            snapshot=snapshot,
            state=state,
            center_config=center_config,
            grid_config=grid_config,
            spacing_config=spacing_config,
            risk_limits=risk_limits,
            risk_state=_risk_state(snapshot, len(working)),
            working_orders=working,
        )
        append_event(
            timestamp=snapshot.timestamp,
            kind=EvidenceKind.CENTER_DECISION,
            payload=_center_payload(transition.decision),
        )
        append_event(
            timestamp=snapshot.timestamp,
            kind=EvidenceKind.SPACING_DECISION,
            payload=_spacing_payload(transition.decision),
        )
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
        rejected_at_decision = not transition.risk_decision.allow_new_risk
        risk_rejection_count += _record_risk_rejection(
            transition.risk_decision,
            risk_reasons_seen,
        )

        had_cancel = bool(transition.reconciliation.cancel)
        if had_cancel:
            cancel_count += len(transition.reconciliation.cancel)
            working = ()
            if transition.risk_decision.allow_new_risk and transition.desired_ladder:
                continued = continue_s2_adaptive_grid_reconciliation(
                    transition,
                    snapshot=snapshot,
                    risk_limits=risk_limits,
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
                if not rejected_at_decision:
                    risk_rejection_count += _record_risk_rejection(
                        continued.risk_decision,
                        risk_reasons_seen,
                    )
                submit_count += len(continued.reconciliation.submit)
                working = _working_orders(continued.reconciliation.submit)
                state = continued.next_state
            else:
                state = transition.next_state
        else:
            submit_count += len(transition.reconciliation.submit)
            if transition.reconciliation.submit:
                working = _working_orders(transition.reconciliation.submit)
            state = transition.next_state

        if state.generation > previous_state.generation:
            queue_reset_count += 1
            if state.spacing_bps != previous_state.spacing_bps:
                spacing_change_count += 1

        s1_spacing_path.append(grid_config.spacing_bps)
        s2_spacing_path.append(state.spacing_bps)
        s2_center_path.append(state.center)

    append_event(
        timestamp=snapshots[-1].timestamp,
        kind=EvidenceKind.RUN_SUMMARY,
        payload={
            "s1_spacing_path": tuple(s1_spacing_path),
            "s2_spacing_path": tuple(s2_spacing_path),
            "s2_center_path": tuple(s2_center_path),
            "s2_spacing_change_count": spacing_change_count,
            "s2_generation_count": state.generation,
            "cancel_count": cancel_count,
            "submit_count": submit_count,
            "queue_reset_count": queue_reset_count,
            "risk_rejection_count": risk_rejection_count,
            "risk_reasons_seen": tuple(risk_reasons_seen),
            "ending_inventory": snapshots[-1].position_quantity,
            "execution_scope": "policy_reconciliation_only",
            "production_authorized": False,
            "alpha_validated": False,
        },
    )

    return S2ComparisonResult(
        evidence_digest=evidence_digest(tuple(events)),
        s1_spacing_path=tuple(s1_spacing_path),
        s2_spacing_path=tuple(s2_spacing_path),
        s2_center_path=tuple(s2_center_path),
        s2_spacing_change_count=spacing_change_count,
        s2_generation_count=state.generation,
        cancel_count=cancel_count,
        submit_count=submit_count,
        queue_reset_count=queue_reset_count,
        risk_rejection_count=risk_rejection_count,
        risk_reasons_seen=tuple(risk_reasons_seen),
        ending_inventory=snapshots[-1].position_quantity,
        pnl=_ZERO_PNL,
        deterministic=False,
        execution_scope="policy_reconciliation_only",
    )


def run_s2_comparison(
    *,
    run_id: str,
    snapshots: tuple[MarketSnapshot, ...],
    grid_config: FixedLongGridConfig,
    center_config: DynamicCenterConfig,
    spacing_config: VolatilitySpacingConfig,
    risk_limits: RiskLimits,
) -> S2ComparisonResult:
    first = _run_s2_once(
        run_id=run_id,
        snapshots=snapshots,
        grid_config=grid_config,
        center_config=center_config,
        spacing_config=spacing_config,
        risk_limits=risk_limits,
    )
    second = _run_s2_once(
        run_id=run_id,
        snapshots=snapshots,
        grid_config=grid_config,
        center_config=center_config,
        spacing_config=spacing_config,
        risk_limits=risk_limits,
    )
    return replace(first, deterministic=first == second)


def run_checked_in_comparison() -> S2ComparisonResult:
    values = (
        ("100.00", "0.0024"),
        ("100.10", "0.0024"),
        ("100.30", "0.0060"),
        ("100.30", "0.0060"),
        ("100.80", "0.0024"),
    )
    snapshots = tuple(
        MarketSnapshot(
            timestamp=datetime(2026, 8, 9, 11, 20, tzinfo=UTC) + timedelta(seconds=index),
            best_bid=Decimal(mid) - Decimal("0.01"),
            best_ask=Decimal(mid) + Decimal("0.01"),
            realized_volatility=Decimal(vol),
            position_quantity=Decimal(0),
            source_id=f"fixture:s2:{index}",
        )
        for index, (mid, vol) in enumerate(values)
    )
    return run_s2_comparison(
        run_id="s2-controlled",
        snapshots=snapshots,
        grid_config=FixedLongGridConfig(
            levels=3,
            spacing_bps=12,
            order_quantity=Decimal("0.01"),
            tick_size=Decimal("0.1"),
        ),
        center_config=DynamicCenterConfig(Decimal("25"), Decimal("50")),
        spacing_config=VolatilitySpacingConfig(
            min_spacing_bps=Decimal("10"),
            max_spacing_bps=Decimal("100"),
            volatility_multiplier=Decimal("0.5"),
            execution_cost_floor_bps=Decimal("12"),
        ),
        risk_limits=RiskLimits(
            max_abs_position=Decimal("1"),
            max_drawdown_fraction=Decimal("0.10"),
            max_data_age_ms=1_000,
            max_open_orders=10,
        ),
    )


if __name__ == "__main__":
    print(run_checked_in_comparison().evidence_digest)


__all__ = ["S2ComparisonResult", "run_checked_in_comparison", "run_s2_comparison"]
