from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.risk import RiskLimits, RiskReason, RiskState
from grid_trade.evidence.events import PnLBreakdown
from grid_trade.research.hftbacktest_adapter import HftReplayConfig, load_microstructure_fixture
from grid_trade.research.s0_runner import S0RunResult, run_s0
from grid_trade.strategy.fixed_grid import FixedLongGridConfig

pytestmark = pytest.mark.research

_FIXTURE = Path("tests/fixtures/microstructure/s0_round_trip.csv")
_NOW = datetime(2026, 8, 9, 7, 0, tzinfo=UTC)


def _snapshot(*, timestamp: datetime = _NOW, position: str = "0") -> MarketSnapshot:
    return MarketSnapshot(
        timestamp=timestamp,
        best_bid=Decimal("99.99"),
        best_ask=Decimal("100.01"),
        realized_volatility=Decimal("0.01"),
        position_quantity=Decimal(position),
        source_id="fixture:s0-runner",
    )


def _grid_config() -> FixedLongGridConfig:
    return FixedLongGridConfig(
        levels=3,
        spacing_bps=100,
        order_quantity=Decimal("0.02"),
        tick_size=Decimal("0.1"),
    )


def _risk_limits() -> RiskLimits:
    return RiskLimits(
        max_abs_position=Decimal("1"),
        max_drawdown_fraction=Decimal("0.10"),
        max_data_age_ms=1_000,
        max_open_orders=10,
    )


def _risk_state(*, now: datetime = _NOW, open_order_count: int = 0) -> RiskState:
    return RiskState(
        equity=Decimal("100"),
        peak_equity=Decimal("100"),
        open_order_count=open_order_count,
        now=now,
    )


def _replay_config() -> HftReplayConfig:
    return HftReplayConfig(
        tick_size=Decimal("0.1"),
        lot_size=Decimal("0.01"),
        entry_latency_ns=0,
        response_latency_ns=0,
        maker_fee=Decimal("0"),
        taker_fee=Decimal("0"),
    )


def _run(**overrides: object) -> S0RunResult:
    values: dict[str, object] = {
        "run_id": "s0-deterministic-fixture",
        "snapshot": _snapshot(),
        "grid_config": _grid_config(),
        "risk_limits": _risk_limits(),
        "risk_state": _risk_state(),
        "fixture": load_microstructure_fixture(_FIXTURE),
        "replay_config": _replay_config(),
    }
    values.update(overrides)
    return run_s0(**values)  # type: ignore[arg-type]


def test_same_s0_scenario_is_exactly_deterministic() -> None:
    left = _run()
    right = _run()

    assert left == right
    assert left.deterministic
    assert left.risk_passed
    assert left.risk_reasons == ()
    assert left.milestone_passed
    assert [order.price for order in left.desired_ladder] == [
        Decimal("99.0"),
        Decimal("98.0"),
        Decimal("97.0"),
    ]
    assert left.reconciliation.submit == left.desired_ladder
    assert left.reconciliation.cancel == ()
    assert [fill.quantity for fill in left.fills] == [Decimal("0.01"), Decimal("0.01")]
    assert left.ending_position == Decimal("0.02")
    assert left.pnl == PnLBreakdown(
        realized_grid=Decimal("0"),
        directional_mark=Decimal("0"),
        fees=Decimal("0"),
        funding=Decimal("0"),
        emergency_execution=Decimal("0"),
    )
    assert len(left.evidence_digest) == 64


def test_stale_market_state_fails_risk_gate_and_emits_no_passive_risk() -> None:
    stale_snapshot = _snapshot(timestamp=_NOW - timedelta(milliseconds=1_001))

    result = _run(snapshot=stale_snapshot)

    assert not result.risk_passed
    assert RiskReason.STALE_DATA in result.risk_reasons
    assert not result.milestone_passed
    assert result.desired_ladder == ()
    assert result.reconciliation.submit == ()
    assert result.fills == ()
    assert result.ending_position == Decimal("0")


def test_position_at_limit_blocks_new_grid_without_claiming_success() -> None:
    result = _run(snapshot=_snapshot(position="1"))

    assert not result.risk_passed
    assert RiskReason.MAX_POSITION in result.risk_reasons
    assert not result.milestone_passed
    assert result.desired_ladder == ()
    assert result.fills == ()


def test_projected_fills_over_limit_record_explicit_position_reason() -> None:
    result = _run(snapshot=_snapshot(position="0.97"))

    assert not result.risk_passed
    assert RiskReason.MAX_POSITION in result.risk_reasons
    assert not result.milestone_passed
    assert result.desired_ladder == ()
    assert result.fills == ()


def test_open_order_count_is_added_to_new_grid_risk_budget() -> None:
    result = _run(risk_state=_risk_state(open_order_count=9))

    assert not result.risk_passed
    assert RiskReason.MAX_OPEN_ORDERS in result.risk_reasons
    assert not result.milestone_passed
    assert result.desired_ladder == ()
    assert result.fills == ()


def test_s0_result_never_claims_live_or_alpha_authorization() -> None:
    result = _run()

    assert result.production_authorized is False
    assert result.alpha_validated is False
