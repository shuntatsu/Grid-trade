from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

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
from grid_trade.datasets.contracts import DatasetType, RawObjectIdentity, RawObjectRef, SourceFamily
from grid_trade.datasets.manifest import DatasetManifest
from grid_trade.domain.orders import OrderSide, PassiveOrderIntent
from grid_trade.domain.risk import RiskLimits, RiskState
from grid_trade.research.hftbacktest_adapter import HftReplayConfig
from grid_trade.research.replay_attribution import MarketImpactEligibilityConfig
from grid_trade.research.tier2_replay import (
    Tier2ReplayManifest,
    Tier2ReplayResult,
    run_tier2_replay,
)

_BOOK_HASH = "a" * 64
_TRADE_HASH = "b" * 64
_FUNDING_HASH = "c" * 64
_HOUR_NS = 3_600_000_000_000


@dataclass(frozen=True, slots=True)
class Tier2FixtureCase:
    manifest: Tier2ReplayManifest
    events: tuple[CanonicalEventEnvelope, ...]
    candidate_orders: tuple[PassiveOrderIntent, ...]
    risk_limits: RiskLimits
    risk_state: RiskState
    starting_position: Decimal
    realized_volatility: Decimal

    def run(self) -> Tier2ReplayResult:
        return run_tier2_replay(
            manifest=self.manifest,
            events=self.events,
            candidate_orders=self.candidate_orders,
            risk_limits=self.risk_limits,
            risk_state=self.risk_state,
            starting_position=self.starting_position,
            realized_volatility=self.realized_volatility,
        )


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
        source_locator=f"fixture://tier2/{dataset_type.value}",
        collector_schema_version="tier2-fixture-v1",
        decoder_schema_version="canonical-v1",
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
            stable_identity=f"tier2-fixture-trade-{ordinal}",
        ),
    )


def _funding(timestamp_ns: int, *, ordinal: int) -> CanonicalEventEnvelope:
    return CanonicalEventEnvelope(
        event_type=CanonicalEventType.FUNDING_REFERENCE,
        instrument="BTC",
        exchange_ts_ns=timestamp_ns,
        local_receive_ts_ns=None,
        source_sequence=None,
        raw_object_sha256=_FUNDING_HASH,
        raw_record_ordinal=ordinal,
        normalization_schema_version="canonical-v1",
        payload=CanonicalFundingReference(
            funding_rate=Decimal("0.001"),
            mark_price=Decimal("100"),
            oracle_price=Decimal("100"),
        ),
    )


def build_tier2_fixture_case() -> Tier2FixtureCase:
    raw_objects = (
        _raw_ref(DatasetType.L2_BOOK, _BOOK_HASH),
        _raw_ref(DatasetType.TRADES, _TRADE_HASH),
        _raw_ref(DatasetType.FUNDING_REFERENCE, _FUNDING_HASH),
    )
    events = (
        _book(1_000_000_000, ask_quantity="0.02", ordinal=0),
        _book(2_000_000_000, ask_quantity="0.03", ordinal=1),
        _trade(3_000_000_000, quantity="0.01", ordinal=0),
        _trade(4_000_000_000, quantity="0.03", ordinal=1),
        _trade(5_000_000_000, quantity="0.02", ordinal=2),
        _funding(_HOUR_NS, ordinal=0),
    )
    audit = audit_canonical_dataset(
        events,
        raw_objects=raw_objects,
        required_funding_timestamps_ns=(_HOUR_NS,),
        expected_normalization_schema_version="canonical-v1",
    )
    dataset = DatasetManifest(
        instrument="BTC",
        raw_objects=raw_objects,
        normalization_schema_version="canonical-v1",
        ordering_schema_version="ordering-v1",
        audit_schema_version="audit-v1",
        acceptance=audit.acceptance,
        created_at=datetime(2026, 8, 10, tzinfo=UTC),
        audit_digest=audit_report_digest(audit),
    )
    return Tier2FixtureCase(
        manifest=Tier2ReplayManifest(
            dataset=dataset,
            strategy_identity="tier2-fixture-strategy:v1",
            calibration_identity="tier2-fixture-calibration:v1",
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
        ),
        events=events,
        candidate_orders=(
            PassiveOrderIntent(
                client_order_id="tier2-fixture:g0:buy:l0",
                generation=0,
                level=0,
                side=OrderSide.BUY,
                price=Decimal("99.0"),
                quantity=Decimal("0.01"),
            ),
        ),
        risk_limits=RiskLimits(
            max_abs_position=Decimal("0.10"),
            max_drawdown_fraction=Decimal("0.20"),
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


def main() -> None:
    result = build_tier2_fixture_case().run()
    if result.production_authorized or result.alpha_validated or result.economics_validated:
        raise RuntimeError("Tier-2 fixture must remain research NO-GO")
    print(result.evidence_digest)


if __name__ == "__main__":
    main()
