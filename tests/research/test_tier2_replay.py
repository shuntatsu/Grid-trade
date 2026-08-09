from datetime import UTC, datetime
from decimal import Decimal

import pytest

from grid_trade.datasets.audit import audit_canonical_dataset, audit_report_digest
from grid_trade.datasets.canonical import (
    CanonicalBookLevel,
    CanonicalBookSnapshot,
    CanonicalEventEnvelope,
    CanonicalEventType,
    CanonicalFundingReference,
    CanonicalTrade,
    TradeSide,
)
from grid_trade.datasets.contracts import (
    DatasetAcceptance,
    DatasetType,
    RawObjectIdentity,
    RawObjectRef,
    SourceFamily,
)
from grid_trade.datasets.manifest import DatasetManifest
from grid_trade.domain.orders import OrderSide, PassiveOrderIntent
from grid_trade.domain.risk import RiskLimits, RiskState
from grid_trade.research.hftbacktest_adapter import HftReplayConfig
from grid_trade.research.replay_attribution import MarketImpactEligibilityConfig
from grid_trade.research.tier2_replay import Tier2ReplayManifest, run_tier2_replay

pytestmark = pytest.mark.research

_BOOK_HASH = "a" * 64
_TRADE_HASH = "b" * 64
_FUNDING_HASH = "c" * 64
_HOUR_NS = 3_600_000_000_000


def _dt(timestamp_ns: int) -> datetime:
    seconds, remainder_ns = divmod(timestamp_ns, 1_000_000_000)
    return datetime.fromtimestamp(seconds, tz=UTC).replace(microsecond=remainder_ns // 1_000)


def _raw_ref(dataset_type: DatasetType, digest: str) -> RawObjectRef:
    return RawObjectRef(
        identity=RawObjectIdentity(
            source_family=SourceFamily.ARCHIVE,
            dataset_type=dataset_type,
            instrument="BTC",
            sha256=digest,
        ),
        byte_length=100,
        acquired_at=datetime(2026, 8, 10, tzinfo=UTC),
        source_locator=f"s3://hyperliquid-archive/{dataset_type.value}",
        collector_schema_version="fixture-v1",
        decoder_schema_version="canonical-v1",
    )


def _raw_objects() -> tuple[RawObjectRef, ...]:
    return (
        _raw_ref(DatasetType.L2_BOOK, _BOOK_HASH),
        _raw_ref(DatasetType.TRADES, _TRADE_HASH),
        _raw_ref(DatasetType.FUNDING_REFERENCE, _FUNDING_HASH),
    )


def _dataset_manifest(
    events: tuple[CanonicalEventEnvelope, ...],
    acceptance: DatasetAcceptance = DatasetAcceptance.ACCEPTED,
) -> DatasetManifest:
    raw_objects = _raw_objects()
    report = audit_canonical_dataset(
        events,
        raw_objects=raw_objects,
        expected_normalization_schema_version="canonical-v1",
    )
    return DatasetManifest(
        instrument="BTC",
        raw_objects=raw_objects,
        normalization_schema_version="canonical-v1",
        ordering_schema_version="ordering-v1",
        audit_schema_version="audit-v1",
        acceptance=acceptance,
        created_at=datetime(2026, 8, 10, tzinfo=UTC),
        audit_digest=audit_report_digest(report),
    )


def _book(timestamp_ns: int, *, ask_quantity: str, ordinal: int) -> CanonicalEventEnvelope:
    return CanonicalEventEnvelope(
        event_type=CanonicalEventType.BOOK_SNAPSHOT,
        instrument="BTC",
        exchange_ts_ns=timestamp_ns,
        local_receive_ts_ns=None,
        source_sequence=None,
        raw_object_sha256=_BOOK_HASH,
        raw_record_ordinal=ordinal,
        normalization_schema_version="canonical-v1",
        payload=CanonicalBookSnapshot(
            bids=(CanonicalBookLevel(Decimal("99.0"), Decimal("0.02"), 1),),
            asks=(CanonicalBookLevel(Decimal("101.0"), Decimal(ask_quantity), 1),),
        ),
    )


def _trade(timestamp_ns: int, *, quantity: str, ordinal: int) -> CanonicalEventEnvelope:
    return CanonicalEventEnvelope(
        event_type=CanonicalEventType.TRADE,
        instrument="BTC",
        exchange_ts_ns=timestamp_ns,
        local_receive_ts_ns=None,
        source_sequence=None,
        raw_object_sha256=_TRADE_HASH,
        raw_record_ordinal=ordinal,
        normalization_schema_version="canonical-v1",
        payload=CanonicalTrade(
            side=TradeSide.SELL,
            price=Decimal("99.0"),
            quantity=Decimal(quantity),
            stable_identity=f"trade-{ordinal}",
        ),
    )


def _funding(
    timestamp_ns: int = _HOUR_NS,
    *,
    funding_rate: Decimal | None = Decimal("0.001"),
    oracle_price: Decimal | None = Decimal("100"),
) -> CanonicalEventEnvelope:
    return CanonicalEventEnvelope(
        event_type=CanonicalEventType.FUNDING_REFERENCE,
        instrument="BTC",
        exchange_ts_ns=timestamp_ns,
        local_receive_ts_ns=None,
        source_sequence=None,
        raw_object_sha256=_FUNDING_HASH,
        raw_record_ordinal=0,
        normalization_schema_version="canonical-v1",
        payload=CanonicalFundingReference(
            funding_rate=funding_rate,
            mark_price=Decimal("100"),
            oracle_price=oracle_price,
        ),
    )


def _events(*, include_funding: bool = True) -> tuple[CanonicalEventEnvelope, ...]:
    events: tuple[CanonicalEventEnvelope, ...] = (
        _book(1_000_000_000, ask_quantity="0.02", ordinal=0),
        _book(2_000_000_000, ask_quantity="0.03", ordinal=1),
        _trade(3_000_000_000, quantity="0.01", ordinal=0),
        # Keep the normal Tier-2 path away from hftbacktest 2.4.4's exact-terminal
        # PartialFillExchange edge. A dedicated regression test covers that fail-closed case.
        _trade(4_000_000_000, quantity="0.03", ordinal=1),
        _trade(5_000_000_000, quantity="0.02", ordinal=2),
    )
    if include_funding:
        return (*events, _funding())
    return events


def _candidate(*, price: str = "99.0", quantity: str = "0.01") -> PassiveOrderIntent:
    return PassiveOrderIntent(
        client_order_id="tier2:g0:buy:l0",
        generation=0,
        level=0,
        side=OrderSide.BUY,
        price=Decimal(price),
        quantity=Decimal(quantity),
    )


def _replay_manifest(
    acceptance: DatasetAcceptance = DatasetAcceptance.ACCEPTED,
    *,
    events: tuple[CanonicalEventEnvelope, ...] | None = None,
) -> Tier2ReplayManifest:
    audited_events = _events() if events is None else events
    return Tier2ReplayManifest(
        dataset=_dataset_manifest(audited_events, acceptance),
        strategy_identity="universal-calibrated-adaptive:S7:v1",
        calibration_identity="universal-calibration:v1",
        hft=HftReplayConfig(
            tick_size=Decimal("0.1"),
            lot_size=Decimal("0.01"),
            maker_fee=Decimal("0.0001"),
            taker_fee=Decimal("0.0005"),
        ),
        market_impact=MarketImpactEligibilityConfig(
            max_same_level_participation=Decimal("0.5"),
            max_top_n_participation=Decimal("0.5"),
        ),
        synthetic_receive_latency_ns=0,
    )


def _risk_limits(max_abs_position: str = "0.10") -> RiskLimits:
    return RiskLimits(
        max_abs_position=Decimal(max_abs_position),
        max_drawdown_fraction=Decimal("0.20"),
        max_data_age_ms=1_000,
        max_open_orders=10,
    )


def _risk_state() -> RiskState:
    return RiskState(
        equity=Decimal("100"),
        peak_equity=Decimal("100"),
        open_order_count=0,
        now=_dt(1_000_000_000),
    )


@pytest.mark.parametrize(
    "acceptance",
    [DatasetAcceptance.ACCEPTED_WITH_WARNINGS, DatasetAcceptance.REJECTED],
)
def test_non_accepted_dataset_cannot_enter_promoting_replay(
    acceptance: DatasetAcceptance,
) -> None:
    with pytest.raises(ValueError, match="ACCEPTED"):
        run_tier2_replay(
            manifest=_replay_manifest(acceptance),
            events=_events(),
            candidate_orders=(_candidate(),),
            risk_limits=_risk_limits(),
            risk_state=_risk_state(),
            starting_position=Decimal(0),
            realized_volatility=Decimal("0.01"),
        )


def test_hard_risk_remains_authoritative_before_replay() -> None:
    result = run_tier2_replay(
        manifest=_replay_manifest(),
        events=_events(),
        candidate_orders=(_candidate(quantity="0.02"),),
        risk_limits=_risk_limits("0.01"),
        risk_state=_risk_state(),
        starting_position=Decimal(0),
        realized_volatility=Decimal("0.01"),
    )

    assert result.candidate_order_count == 1
    assert result.risk_accepted_order_count == 0
    assert result.eligible_order_count == 0
    assert result.risk_decision.allow_new_risk is False
    assert tuple(reason.value for reason in result.risk_decision.reasons) == ("max_position",)
    assert result.replay_summary.fills == ()


def test_visibility_ineligible_order_is_skipped_not_guessed() -> None:
    result = run_tier2_replay(
        manifest=_replay_manifest(),
        events=_events(),
        candidate_orders=(_candidate(price="98.0"),),
        risk_limits=_risk_limits(),
        risk_state=_risk_state(),
        starting_position=Decimal(0),
        realized_volatility=Decimal("0.01"),
    )

    assert result.risk_accepted_order_count == 1
    assert result.eligible_order_count == 0
    assert result.order_eligibility[0].eligible is False
    assert result.order_eligibility[0].reason == "same_level_visibility_unavailable"
    assert result.replay_summary.fills == ()


def test_hourly_funding_uses_position_after_causal_fills() -> None:
    result = run_tier2_replay(
        manifest=_replay_manifest(),
        events=_events(),
        candidate_orders=(_candidate(),),
        risk_limits=_risk_limits(),
        risk_state=_risk_state(),
        starting_position=Decimal(0),
        realized_volatility=Decimal("0.01"),
    )

    assert result.eligible_order_count == 1
    assert sum((fill.quantity for fill in result.replay_summary.fills), Decimal(0)) == Decimal(
        "0.01"
    )
    assert len(result.funding_cash_flows) == 1
    assert result.funding_cash_flows[0].timestamp_ns == _HOUR_NS
    assert result.funding_cash_flows[0].position == Decimal("0.01")
    assert result.funding_cash_flows[0].cash_flow == Decimal("-0.001")
    assert result.funding_pnl == Decimal("-0.001")


def test_incomplete_exact_hour_funding_fails_closed() -> None:
    events = (*_events(include_funding=False), _funding(funding_rate=None))

    with pytest.raises(ValueError, match="funding_rate"):
        run_tier2_replay(
            manifest=_replay_manifest(events=events),
            events=events,
            candidate_orders=(_candidate(),),
            risk_limits=_risk_limits(),
            risk_state=_risk_state(),
            starting_position=Decimal(0),
            realized_volatility=Decimal("0.01"),
        )


def test_non_hour_reference_observation_is_not_applied_as_funding() -> None:
    events = (*_events(include_funding=False), _funding(_HOUR_NS + 1))

    result = run_tier2_replay(
        manifest=_replay_manifest(events=events),
        events=events,
        candidate_orders=(_candidate(),),
        risk_limits=_risk_limits(),
        risk_state=_risk_state(),
        starting_position=Decimal(0),
        realized_volatility=Decimal("0.01"),
    )

    assert result.funding_cash_flows == ()
    assert result.funding_pnl == 0


def test_research_flags_remain_no_go_even_when_mechanics_pass() -> None:
    result = run_tier2_replay(
        manifest=_replay_manifest(),
        events=_events(),
        candidate_orders=(_candidate(),),
        risk_limits=_risk_limits(),
        risk_state=_risk_state(),
        starting_position=Decimal(0),
        realized_volatility=Decimal("0.01"),
    )

    assert result.production_authorized is False
    assert result.alpha_validated is False
    assert result.economics_validated is False
