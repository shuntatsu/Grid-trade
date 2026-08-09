from datetime import UTC, datetime
from decimal import Decimal

import pytest

from grid_trade.datasets.audit import audit_canonical_dataset, audit_report_digest
from grid_trade.datasets.canonical import (
    CanonicalBookLevel,
    CanonicalBookSnapshot,
    CanonicalEventEnvelope,
    CanonicalEventType,
)
from grid_trade.datasets.contracts import (
    DatasetAcceptance,
    DatasetType,
    RawObjectIdentity,
    RawObjectRef,
    SourceFamily,
)
from grid_trade.datasets.manifest import DatasetManifest
from grid_trade.domain.risk import RiskLimits, RiskState
from grid_trade.research.hftbacktest_adapter import HftReplayConfig
from grid_trade.research.replay_attribution import MarketImpactEligibilityConfig
from grid_trade.research.tier2_replay import (
    Tier2ReplayManifest,
    required_hourly_funding_timestamps,
    run_tier2_replay,
)

pytestmark = pytest.mark.research

_HOUR_NS = 3_600_000_000_000
_RAW_HASH = "a" * 64


def _book(timestamp_ns: int, ordinal: int) -> CanonicalEventEnvelope:
    return CanonicalEventEnvelope(
        event_type=CanonicalEventType.BOOK_SNAPSHOT,
        instrument="BTC",
        exchange_ts_ns=timestamp_ns,
        local_receive_ts_ns=None,
        source_sequence=None,
        raw_object_sha256=_RAW_HASH,
        raw_record_ordinal=ordinal,
        normalization_schema_version="canonical-v1",
        payload=CanonicalBookSnapshot(
            bids=(CanonicalBookLevel(Decimal("99"), Decimal("1"), 1),),
            asks=(CanonicalBookLevel(Decimal("101"), Decimal("1"), 1),),
        ),
    )


def _raw_ref() -> RawObjectRef:
    return RawObjectRef(
        identity=RawObjectIdentity(
            source_family=SourceFamily.ARCHIVE,
            dataset_type=DatasetType.L2_BOOK,
            instrument="BTC",
            sha256=_RAW_HASH,
        ),
        byte_length=100,
        acquired_at=datetime(2026, 8, 10, tzinfo=UTC),
        source_locator="fixture://tier2/funding-completeness",
        collector_schema_version="fixture-v1",
        decoder_schema_version="canonical-v1",
    )


def test_hourly_funding_schedule_is_derived_from_replay_extent() -> None:
    events = (
        _book(1_000_000_000, 0),
        _book(_HOUR_NS + 1_000_000_000, 1),
    )

    assert required_hourly_funding_timestamps(events) == (_HOUR_NS,)


def test_promoting_replay_rejects_undeclared_hourly_funding_requirement() -> None:
    events = (
        _book(1_000_000_000, 0),
        _book(_HOUR_NS + 1_000_000_000, 1),
    )
    raw_objects = (_raw_ref(),)
    report = audit_canonical_dataset(events, raw_objects=raw_objects)
    assert report.acceptance is DatasetAcceptance.ACCEPTED
    dataset = DatasetManifest(
        instrument="BTC",
        raw_objects=raw_objects,
        normalization_schema_version="canonical-v1",
        ordering_schema_version="ordering-v1",
        audit_schema_version="audit-v1",
        acceptance=report.acceptance,
        created_at=datetime(2026, 8, 10, tzinfo=UTC),
        audit_digest=audit_report_digest(report),
    )
    manifest = Tier2ReplayManifest(
        dataset=dataset,
        strategy_identity="test-strategy:v1",
        calibration_identity="test-calibration:v1",
        hft=HftReplayConfig(
            tick_size=Decimal("1"),
            lot_size=Decimal("1"),
        ),
        market_impact=MarketImpactEligibilityConfig(
            max_same_level_participation=Decimal("1"),
            max_top_n_participation=Decimal("1"),
        ),
        synthetic_receive_latency_ns=0,
        required_funding_timestamps_ns=(),
    )

    with pytest.raises(ValueError, match="hourly funding requirements"):
        run_tier2_replay(
            manifest=manifest,
            events=events,
            candidate_orders=(),
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
