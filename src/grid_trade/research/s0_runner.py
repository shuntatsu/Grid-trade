from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.orders import PassiveOrderIntent, ReconciliationPlan
from grid_trade.domain.risk import RiskDecision, RiskLimits, RiskReason, RiskState
from grid_trade.evidence.events import EvidenceEvent, EvidenceKind, PnLBreakdown
from grid_trade.evidence.ledger import evidence_digest
from grid_trade.execution.reconcile import reconcile_passive_orders
from grid_trade.research.hftbacktest_adapter import (
    HftReplayConfig,
    MicrostructureFixture,
    ReplayFill,
    ReplaySummary,
    load_microstructure_fixture,
    replay_passive_orders,
)
from grid_trade.risk.controller import assess_passive_ladder_risk
from grid_trade.strategy.fixed_grid import FixedLongGridConfig, build_fixed_long_grid


@dataclass(frozen=True, slots=True)
class S0RunResult:
    evidence_digest: str
    desired_ladder: tuple[PassiveOrderIntent, ...]
    reconciliation: ReconciliationPlan
    fills: tuple[ReplayFill, ...]
    ending_position: Decimal
    open_order_count: int
    pnl: PnLBreakdown
    risk_passed: bool
    risk_reasons: tuple[RiskReason, ...]
    deterministic: bool
    milestone_passed: bool
    production_authorized: bool = False
    alpha_validated: bool = False

    def __post_init__(self) -> None:
        if len(self.evidence_digest) != 64:
            raise ValueError("evidence_digest must be a SHA-256 hex digest")
        if self.production_authorized:
            raise ValueError("S0 must never authorize production")
        if self.alpha_validated:
            raise ValueError("S0 must never claim alpha validation")
        if self.risk_passed != (not self.risk_reasons):
            raise ValueError("risk_passed must match an empty risk_reasons tuple")
        if self.milestone_passed != (self.risk_passed and self.deterministic):
            raise ValueError("milestone_passed must equal risk_passed and deterministic")


def _order_payload(order: PassiveOrderIntent) -> dict[str, object]:
    return {
        "client_order_id": order.client_order_id,
        "generation": order.generation,
        "level": order.level,
        "side": order.side.value,
        "price": order.price,
        "quantity": order.quantity,
        "reduce_only": order.reduce_only,
    }


def _risk_payload(decision: RiskDecision, *, proposed_order_count: int) -> dict[str, object]:
    return {
        "allow_new_risk": decision.allow_new_risk,
        "cancel_all_passive": decision.cancel_all_passive,
        "target_flat": decision.target_flat,
        "reasons": tuple(reason.value for reason in decision.reasons),
        "proposed_order_count": proposed_order_count,
    }


def _fill_payload(fill: ReplayFill) -> dict[str, object]:
    return {
        "client_order_id": fill.client_order_id,
        "timestamp_ns": fill.timestamp_ns,
        "price": fill.price,
        "quantity": fill.quantity,
        "remaining_quantity": fill.remaining_quantity,
    }


def _mechanics_pnl(
    fills: tuple[ReplayFill, ...],
    replay_config: HftReplayConfig,
) -> PnLBreakdown:
    maker_fee_pnl = -sum(
        (fill.price * fill.quantity * replay_config.maker_fee for fill in fills),
        start=Decimal(0),
    )
    return PnLBreakdown(
        realized_grid=Decimal(0),
        directional_mark=Decimal(0),
        fees=maker_fee_pnl,
        funding=Decimal(0),
        emergency_execution=Decimal(0),
    )


def _evidence_events(
    *,
    run_id: str,
    snapshot: MarketSnapshot,
    desired_ladder: tuple[PassiveOrderIntent, ...],
    reconciliation: ReconciliationPlan,
    risk_decision: RiskDecision,
    proposed_order_count: int,
    replay_summary: ReplaySummary,
    pnl: PnLBreakdown,
    deterministic: bool,
    risk_passed: bool,
) -> tuple[EvidenceEvent, ...]:
    events: list[EvidenceEvent] = []

    def append(kind: EvidenceKind, payload: dict[str, object], *, offset_ns: int = 0) -> None:
        events.append(
            EvidenceEvent.create(
                run_id=run_id,
                sequence=len(events),
                timestamp=snapshot.timestamp + timedelta(microseconds=offset_ns // 1_000),
                kind=kind,
                payload=payload,
            ),
        )

    append(
        EvidenceKind.MARKET_SNAPSHOT,
        {
            "best_bid": snapshot.best_bid,
            "best_ask": snapshot.best_ask,
            "mid": snapshot.mid,
            "realized_volatility": snapshot.realized_volatility,
            "position_quantity": snapshot.position_quantity,
            "source_id": snapshot.source_id,
        },
    )
    append(
        EvidenceKind.DESIRED_LADDER,
        {"orders": tuple(_order_payload(order) for order in desired_ladder)},
    )
    append(
        EvidenceKind.RISK_DECISION,
        _risk_payload(risk_decision, proposed_order_count=proposed_order_count),
    )
    append(
        EvidenceKind.RECONCILIATION_PLAN,
        {
            "cancel": reconciliation.cancel,
            "submit": tuple(order.client_order_id for order in reconciliation.submit),
        },
    )
    for fill in replay_summary.fills:
        append(EvidenceKind.FILL, _fill_payload(fill), offset_ns=fill.timestamp_ns)
    append(
        EvidenceKind.RUN_SUMMARY,
        {
            "ending_position": replay_summary.ending_position,
            "open_order_count": replay_summary.open_order_count,
            "pnl": {
                "realized_grid": pnl.realized_grid,
                "directional_mark": pnl.directional_mark,
                "fees": pnl.fees,
                "funding": pnl.funding,
                "emergency_execution": pnl.emergency_execution,
                "total": pnl.total,
            },
            "risk_passed": risk_passed,
            "deterministic": deterministic,
            "production_authorized": False,
            "alpha_validated": False,
            "economics_scope": "mechanics_only_no_terminal_mark",
        },
        offset_ns=max((fill.timestamp_ns for fill in replay_summary.fills), default=0),
    )
    return tuple(events)


def run_s0(
    *,
    run_id: str,
    snapshot: MarketSnapshot,
    grid_config: FixedLongGridConfig,
    risk_limits: RiskLimits,
    risk_state: RiskState,
    fixture: MicrostructureFixture,
    replay_config: HftReplayConfig,
) -> S0RunResult:
    if not run_id.strip():
        raise ValueError("run_id must be non-empty")
    if grid_config.tick_size != replay_config.tick_size:
        raise ValueError("grid tick_size must match replay tick_size")

    proposed_ladder = build_fixed_long_grid(snapshot, grid_config, generation=0)
    prospective_state = replace(
        risk_state,
        open_order_count=risk_state.open_order_count + len(proposed_ladder),
    )
    risk_decision, filtered_ladder = assess_passive_ladder_risk(
        snapshot,
        risk_limits,
        prospective_state,
        proposed_ladder,
    )
    risk_passed = risk_decision.allow_new_risk and filtered_ladder == proposed_ladder
    desired_ladder = filtered_ladder if risk_passed else ()
    reconciliation = reconcile_passive_orders(desired=desired_ladder, working=())

    if desired_ladder:
        first_replay = replay_passive_orders(fixture, reconciliation.submit, replay_config)
        second_replay = replay_passive_orders(fixture, reconciliation.submit, replay_config)
        deterministic = first_replay == second_replay
        replay_summary = first_replay
    else:
        deterministic = True
        replay_summary = ReplaySummary(
            fills=(),
            ending_position=snapshot.position_quantity,
            open_order_count=risk_state.open_order_count,
        )

    pnl = _mechanics_pnl(replay_summary.fills, replay_config)
    events = _evidence_events(
        run_id=run_id,
        snapshot=snapshot,
        desired_ladder=desired_ladder,
        reconciliation=reconciliation,
        risk_decision=risk_decision,
        proposed_order_count=len(proposed_ladder),
        replay_summary=replay_summary,
        pnl=pnl,
        deterministic=deterministic,
        risk_passed=risk_passed,
    )
    digest = evidence_digest(events)

    return S0RunResult(
        evidence_digest=digest,
        desired_ladder=desired_ladder,
        reconciliation=reconciliation,
        fills=replay_summary.fills,
        ending_position=replay_summary.ending_position,
        open_order_count=replay_summary.open_order_count,
        pnl=pnl,
        risk_passed=risk_passed,
        risk_reasons=risk_decision.reasons,
        deterministic=deterministic,
        milestone_passed=risk_passed and deterministic,
    )


def run_checked_in_fixture() -> S0RunResult:
    """Run the deterministic checked-in S0 fixture for CI/process comparison."""
    from datetime import UTC, datetime

    fixture_path = Path("tests/fixtures/microstructure/s0_round_trip.csv")
    snapshot = MarketSnapshot(
        timestamp=datetime(2026, 8, 9, 7, 0, tzinfo=UTC),
        best_bid=Decimal("99.99"),
        best_ask=Decimal("100.01"),
        realized_volatility=Decimal("0.01"),
        position_quantity=Decimal("0"),
        source_id="fixture:s0-runner",
    )
    return run_s0(
        run_id="s0-deterministic-fixture",
        snapshot=snapshot,
        grid_config=FixedLongGridConfig(
            levels=3,
            spacing_bps=100,
            order_quantity=Decimal("0.02"),
            tick_size=Decimal("0.1"),
        ),
        risk_limits=RiskLimits(
            max_abs_position=Decimal("1"),
            max_drawdown_fraction=Decimal("0.10"),
            max_data_age_ms=1_000,
            max_open_orders=10,
        ),
        risk_state=RiskState(
            equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_order_count=0,
            now=snapshot.timestamp,
        ),
        fixture=load_microstructure_fixture(fixture_path),
        replay_config=HftReplayConfig(
            tick_size=Decimal("0.1"),
            lot_size=Decimal("0.01"),
            maker_fee=Decimal("0"),
            taker_fee=Decimal("0"),
        ),
    )


if __name__ == "__main__":
    print(run_checked_in_fixture().evidence_digest)


__all__ = ["S0RunResult", "run_checked_in_fixture", "run_s0"]
