from datetime import UTC, datetime
from decimal import Decimal

import pytest

from grid_trade.datasets.audit import audit_canonical_dataset, audit_report_digest
from grid_trade.datasets.canonical import (
    CanonicalBookLevel,
    CanonicalBookSnapshot,
    CanonicalEventEnvelope,
    CanonicalEventType,
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


def _raw(dataset_type: DatasetType, digest: str) -> RawObjectRef:
    return RawObjectRef(
        identity=RawObjectIdentity(
            source_family=SourceFamily.ARCHIVE,
            dataset_type=dataset_type,
            instrument="BTC",
            sha256=digest,
        ),
        byte_length=1,
        acquired_at=datetime(2026, 8, 10, tzinfo=UTC),
        source_locator=f"s3://hyperliquid-archive/{dataset_type.value}",
        collector_schema_version="fixture-v1",
        decoder_schema_version="canonical-v1",
    )


def _book(timestamp_ns: int, bid: str, *, ordinal: int) -> CanonicalEventEnvelope:
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
            bids=(CanonicalBookLevel(Decimal(bid), Decimal("0.02"), 1),),
            asks=(CanonicalBookLevel(Decimal("101"), Decimal("0.02"), 1),),
        ),
    )


def _trade(timestamp_ns: int) -> CanonicalEventEnvelope:
    return CanonicalEventEnvelope(
        event_type=CanonicalEventType.TRADE,
        instrument="BTC",
        exchange_ts_ns=timestamp_ns,
        local_receive_ts_ns=None,
        source_sequence=None,
        raw_object_sha256=_TRADE_HASH,
        raw_record_ordinal=0,
        normalization_schema_version="canonical-v1",
        payload=CanonicalTrade(
            side=TradeSide.SELL,
            price=Decimal("99"),
            quantity=Decimal("1"),
            stable_identity="post-boundary-trade",
        ),
    )


def test_tier2_does_not_claim_fill_after_order_price_leaves_top_n_visibility() -> None:
    raw_objects = (
        _raw(DatasetType.L2_BOOK, _BOOK_HASH),
        _raw(DatasetType.TRADES, _TRADE_HASH),
    )
    events = (
        _book(1_000_000_000, "99", ordinal=0),
        # Bid 99 falls below the new one-level observable bid boundary at 100.
        _book(2_000_000_000, "100", ordinal=1),
        _trade(3_000_000_000),
    )
    audit = audit_canonical_dataset(
        events,
        raw_objects=raw_objects,
        expected_normalization_schema_version="canonical-v1",
    )
    dataset = DatasetManifest(
        instrument="BTC",
        raw_objects=raw_objects,
        normalization_schema_version="canonical-v1",
        ordering_schema_version="ordering-v1",
        audit_schema_version="audit-v1",
        acceptance=DatasetAcceptance.ACCEPTED,
        created_at=datetime(2026, 8, 10, tzinfo=UTC),
        audit_digest=audit_report_digest(audit),
    )
    manifest = Tier2ReplayManifest(
        dataset=dataset,
        strategy_identity="test-strategy:v1",
        calibration_identity="test-calibration:v1",
        hft=HftReplayConfig(
            tick_size=Decimal("1"),
            lot_size=Decimal("0.01"),
            maker_fee=Decimal(0),
            taker_fee=Decimal(0),
        ),
        market_impact=MarketImpactEligibilityConfig(
            max_same_level_participation=Decimal("0.5"),
            max_top_n_participation=Decimal("0.5"),
        ),
        synthetic_receive_latency_ns=0,
    )
    order = PassiveOrderIntent(
        client_order_id="tier2:g0:buy:l0",
        generation=0,
        level=0,
        side=OrderSide.BUY,
        price=Decimal("99"),
        quantity=Decimal("0.01"),
    )

    result = run_tier2_replay(
        manifest=manifest,
        events=events,
        candidate_orders=(order,),
        risk_limits=RiskLimits(
            max_abs_position=Decimal("1"),
            max_drawdown_fraction=Decimal("0.2"),
            max_data_age_ms=1_000,
            max_open_orders=10,
        ),
        risk_state=RiskState(
            equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_order_count=0,
            now=datetime.fromtimestamp(1, tz=UTC),
        ),
        starting_position=Decimal(0),
        realized_volatility=Decimal("0.01"),
    )

    assert result.eligible_order_count == 1
    assert result.order_eligibility[0].visibility_boundary_ts_ns == 2_000_000_000
    assert result.liquidity_summary.earliest_visibility_boundary_ts_ns == 2_000_000_000
    assert result.replay_summary.fills == ()
    assert result.replay_summary.open_order_count == 0
