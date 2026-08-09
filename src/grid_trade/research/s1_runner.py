from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.orders import PassiveOrderIntent, WorkingOrder
from grid_trade.domain.risk import RiskLimits, RiskState
from grid_trade.evidence.events import EvidenceEvent, EvidenceKind, PnLBreakdown
from grid_trade.evidence.ledger import evidence_digest
from grid_trade.strategy.dynamic_center import (
    CenterDecision,
    DynamicCenterConfig,
    DynamicCenterState,
    initialize_dynamic_center,
)
from grid_trade.strategy.dynamic_center_transition import (
    continue_dynamic_center_reconciliation,
    transition_dynamic_center,
)
from grid_trade.strategy.fixed_grid import FixedLongGridConfig
from grid_trade.strategy.grid_geometry import build_long_grid_at_center

_BASIS_POINTS = Decimal(10_000)
_ZERO_PNL = PnLBreakdown(
    realized_grid=Decimal(0),
    directional_mark=Decimal(0),
    fees=Decimal(0),
    funding=Decimal(0),
    emergency_execution=Decimal(0),
)


@dataclass(frozen=True, slots=True)
class S1ComparisonResult:
    evidence_digest: str
    s0_center_path: tuple[Decimal, ...]
    s1_center_path: tuple[Decimal, ...]
    s0_mean_abs_center_error_bps: Decimal
    s1_mean_abs_center_error_bps: Decimal
    s1_reanchor_count: int
    s1_generation_count: int
    cancel_count: int
    submit_count: int
    queue_reset_count: int
    ending_inventory: Decimal
    pnl: PnLBreakdown
    deterministic: bool
    execution_scope: str
    production_authorized: bool = False
    alpha_validated: bool = False

    def __post_init__(self) -> None:
        if len(self.evidence_digest) != 64:
            raise ValueError("evidence_digest must be a SHA-256 hex digest")
        if not self.s0_center_path or not self.s1_center_path:
            raise ValueError("center paths must be non-empty")
        if len(self.s0_center_path) != len(self.s1_center_path):
            raise ValueError("S0 and S1 center paths must have equal length")
        if self.s1_reanchor_count < 0 or self.s1_generation_count < 0:
            raise ValueError("S1 counts must be non-negative")
        if self.cancel_count < 0 or self.submit_count < 0 or self.queue_reset_count < 0:
            raise ValueError("execution counts must be non-negative")
        if not self.ending_inventory.is_finite():
            raise ValueError("ending_inventory must be finite")
        if self.execution_scope != "policy_reconciliation_only":
            raise ValueError("unsupported S1 execution scope")
        if self.production_authorized or self.alpha_validated:
            raise ValueError("controlled S1 mechanics must remain NO-GO")


def _working_orders(
    ladder: tuple[PassiveOrderIntent, ...],
) -> tuple[WorkingOrder, ...]:
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


def _center_error_bps(center: Decimal, mid: Decimal) -> Decimal:
    return abs(center - mid) / mid * _BASIS_POINTS


def _mean(values: tuple[Decimal, ...]) -> Decimal:
    return sum(values, start=Decimal(0)) / Decimal(len(values))


def _validate_snapshots(snapshots: tuple[MarketSnapshot, ...]) -> None:
    if not snapshots:
        raise ValueError("snapshots must be non-empty")
    previous = snapshots[0].timestamp
    for snapshot in snapshots[1:]:
        if snapshot.timestamp <= previous:
            raise ValueError("snapshot timestamps must be strictly increasing")
        previous = snapshot.timestamp


def _center_payload(
    decision: CenterDecision,
    center_config: DynamicCenterConfig,
) -> dict[str, object]:
    return {
        "previous_center": decision.previous_center,
        "market_mid": decision.market_mid,
        "deviation_bps": decision.deviation_bps,
        "reanchor_threshold_bps": center_config.reanchor_threshold_bps,
        "max_step_bps": center_config.max_step_bps,
        "proposed_center": decision.proposed_center,
        "effective_center": decision.effective_center,
        "previous_generation": decision.previous_generation,
        "effective_generation": decision.effective_generation,
        "reanchored": decision.reanchored,
        "economic_ladder_changed": decision.economic_ladder_changed,
        "reason": decision.reason,
    }


def run_s1_comparison(
    *,
    run_id: str,
    snapshots: tuple[MarketSnapshot, ...],
    grid_config: FixedLongGridConfig,
    center_config: DynamicCenterConfig,
    risk_limits: RiskLimits,
) -> S1ComparisonResult:
    if not run_id.strip():
        raise ValueError("run_id must be non-empty")
    _validate_snapshots(snapshots)

    initial_snapshot = snapshots[0]
    s0_center = initial_snapshot.mid
    state = initialize_dynamic_center(initial_snapshot)
    initial_ladder = build_long_grid_at_center(
        state.center,
        grid_config,
        generation=state.generation,
        stage="s1",
    )
    working = _working_orders(initial_ladder)

    s0_centers: list[Decimal] = [s0_center]
    s1_centers: list[Decimal] = [state.center]
    s0_errors: list[Decimal] = [_center_error_bps(s0_center, initial_snapshot.mid)]
    s1_errors: list[Decimal] = [_center_error_bps(state.center, initial_snapshot.mid)]
    reanchor_count = 0
    cancel_count = 0
    submit_count = len(initial_ladder)
    queue_reset_count = 0
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
            "source_id": initial_snapshot.source_id,
            "position_quantity": initial_snapshot.position_quantity,
        },
    )
    append_event(
        timestamp=initial_snapshot.timestamp,
        kind=EvidenceKind.CENTER_DECISION,
        payload={
            "previous_center": state.center,
            "market_mid": initial_snapshot.mid,
            "deviation_bps": Decimal(0),
            "reanchor_threshold_bps": center_config.reanchor_threshold_bps,
            "max_step_bps": center_config.max_step_bps,
            "proposed_center": state.center,
            "effective_center": state.center,
            "previous_generation": 0,
            "effective_generation": 0,
            "reanchored": False,
            "economic_ladder_changed": False,
            "reason": "initialized",
        },
    )

    for snapshot in snapshots[1:]:
        append_event(
            timestamp=snapshot.timestamp,
            kind=EvidenceKind.MARKET_SNAPSHOT,
            payload={
                "mid": snapshot.mid,
                "source_id": snapshot.source_id,
                "position_quantity": snapshot.position_quantity,
            },
        )
        risk_state = RiskState(
            equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_order_count=len(working),
            now=snapshot.timestamp,
        )
        transition = transition_dynamic_center(
            snapshot=snapshot,
            state=state,
            center_config=center_config,
            grid_config=grid_config,
            risk_limits=risk_limits,
            risk_state=risk_state,
            working_orders=working,
        )
        append_event(
            timestamp=snapshot.timestamp,
            kind=EvidenceKind.CENTER_DECISION,
            payload=_center_payload(transition.decision, center_config),
        )

        previous_generation = state.generation
        if transition.reconciliation.cancel:
            cancel_count += len(transition.reconciliation.cancel)
            if transition.decision.reanchored:
                queue_reset_count += 1
            continued = continue_dynamic_center_reconciliation(
                transition,
                snapshot=snapshot,
                risk_limits=risk_limits,
                risk_state=RiskState(
                    equity=Decimal("100"),
                    peak_equity=Decimal("100"),
                    open_order_count=0,
                    now=snapshot.timestamp,
                ),
                working_orders=(),
            )
            submit_count += len(continued.reconciliation.submit)
            working = _working_orders(continued.reconciliation.submit)
            state = continued.next_state
        else:
            submit_count += len(transition.reconciliation.submit)
            if transition.reconciliation.submit:
                working = _working_orders(transition.reconciliation.submit)
            state = transition.next_state

        if state.generation > previous_generation:
            reanchor_count += 1

        s0_centers.append(s0_center)
        s1_centers.append(state.center)
        s0_errors.append(_center_error_bps(s0_center, snapshot.mid))
        s1_errors.append(_center_error_bps(state.center, snapshot.mid))

    append_event(
        timestamp=snapshots[-1].timestamp,
        kind=EvidenceKind.RUN_SUMMARY,
        payload={
            "s0_mean_abs_center_error_bps": _mean(tuple(s0_errors)),
            "s1_mean_abs_center_error_bps": _mean(tuple(s1_errors)),
            "s1_reanchor_count": reanchor_count,
            "s1_generation_count": state.generation,
            "cancel_count": cancel_count,
            "submit_count": submit_count,
            "queue_reset_count": queue_reset_count,
            "ending_inventory": snapshots[-1].position_quantity,
            "execution_scope": "policy_reconciliation_only",
            "production_authorized": False,
            "alpha_validated": False,
        },
    )
    digest = evidence_digest(tuple(events))

    return S1ComparisonResult(
        evidence_digest=digest,
        s0_center_path=tuple(s0_centers),
        s1_center_path=tuple(s1_centers),
        s0_mean_abs_center_error_bps=_mean(tuple(s0_errors)),
        s1_mean_abs_center_error_bps=_mean(tuple(s1_errors)),
        s1_reanchor_count=reanchor_count,
        s1_generation_count=state.generation,
        cancel_count=cancel_count,
        submit_count=submit_count,
        queue_reset_count=queue_reset_count,
        ending_inventory=snapshots[-1].position_quantity,
        pnl=_ZERO_PNL,
        deterministic=True,
        execution_scope="policy_reconciliation_only",
    )


def run_checked_in_comparison() -> S1ComparisonResult:
    mids = ("100.00", "100.20", "100.26", "100.27", "101.50")
    snapshots = tuple(
        MarketSnapshot(
            timestamp=datetime(2026, 8, 9, 9, 0, tzinfo=UTC) + timedelta(seconds=index),
            best_bid=Decimal(mid) - Decimal("0.01"),
            best_ask=Decimal(mid) + Decimal("0.01"),
            realized_volatility=Decimal("0.01"),
            position_quantity=Decimal(0),
            source_id=f"fixture:s1:{index}",
        )
        for index, mid in enumerate(mids)
    )
    return run_s1_comparison(
        run_id="s1-controlled",
        snapshots=snapshots,
        grid_config=FixedLongGridConfig(
            levels=3,
            spacing_bps=100,
            order_quantity=Decimal("0.01"),
            tick_size=Decimal("0.1"),
        ),
        center_config=DynamicCenterConfig(
            reanchor_threshold_bps=Decimal("25"),
            max_step_bps=Decimal("50"),
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


__all__ = ["S1ComparisonResult", "run_checked_in_comparison", "run_s1_comparison"]
