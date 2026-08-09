from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from grid_trade.application.calibrated_adaptive import (
    CalibratedAdaptiveMetaConfig,
    VenueGridConstraints,
)
from grid_trade.calibration.engine import CalibrationEngineConfig
from grid_trade.calibration.execution_cost import ExecutionCostConfig
from grid_trade.calibration.funding import FundingCalibrationConfig
from grid_trade.calibration.intensity import IntensityCalibrationConfig
from grid_trade.calibration.microstructure_contracts import (
    IntensityBucket,
    MarkoutSide,
    MaturedMarkout,
    OfiImpactSample,
)
from grid_trade.calibration.microstructure_engine import MicrostructureCalibrationConfig
from grid_trade.calibration.order_flow import OfiImpactConfig
from grid_trade.calibration.trend import TrendCalibrationConfig
from grid_trade.calibration.universal_engine import UniversalCalibrationConfig
from grid_trade.calibration.volatility import RobustVolatilityConfig
from grid_trade.datasets.audit import audit_canonical_dataset, audit_report_digest
from grid_trade.datasets.canonical import (
    CanonicalBookLevel,
    CanonicalBookSnapshot,
    CanonicalEventEnvelope,
    CanonicalEventType,
    CanonicalFundingReference,
)
from grid_trade.datasets.contracts import (
    DatasetAcceptance,
    DatasetType,
    RawObjectIdentity,
    RawObjectRef,
    SourceFamily,
)
from grid_trade.datasets.manifest import DatasetManifest
from grid_trade.research.tier2_calibrated_candidate import (
    Tier2CalibratedCandidateConfig,
    Tier2CalibrationEvidenceFrame,
    derive_tier2_calibrated_candidate,
)
from grid_trade.risk.sizing import RiskSizingConfig
from grid_trade.strategy.adaptive_grid import AdaptiveStage

pytestmark = pytest.mark.research

_BASE = datetime(2026, 8, 10, 4, 0, tzinfo=UTC)
_BOOK_HASH = "a" * 64
_FUNDING_HASH = "c" * 64


def _ns(value: datetime) -> int:
    return int(value.timestamp()) * 1_000_000_000 + value.microsecond * 1_000


def _time(minutes: int, *, seconds: int = 0) -> datetime:
    return _BASE + timedelta(minutes=minutes, seconds=seconds)


def _raw(dataset_type: DatasetType, digest: str) -> RawObjectRef:
    return RawObjectRef(
        identity=RawObjectIdentity(
            source_family=SourceFamily.ARCHIVE,
            dataset_type=dataset_type,
            instrument="BTC",
            sha256=digest,
        ),
        byte_length=100,
        acquired_at=datetime(2026, 8, 10, tzinfo=UTC),
        source_locator=f"fixture://tier2-calibrated/{dataset_type.value}",
        collector_schema_version="fixture-v1",
        decoder_schema_version="canonical-v1",
    )


def _funding(index: int, minute: int) -> CanonicalEventEnvelope:
    rates = ("0.0001", "0.0002", "0.0003", "0.0004")
    return CanonicalEventEnvelope(
        event_type=CanonicalEventType.FUNDING_REFERENCE,
        instrument="BTC",
        exchange_ts_ns=_ns(_time(minute, seconds=-1)),
        local_receive_ts_ns=None,
        source_sequence=None,
        raw_object_sha256=_FUNDING_HASH,
        raw_record_ordinal=index,
        normalization_schema_version="canonical-v1",
        payload=CanonicalFundingReference(
            funding_rate=Decimal(rates[index]),
            mark_price=Decimal("100"),
            oracle_price=Decimal("100"),
        ),
    )


def _book(index: int, minute: int) -> CanonicalEventEnvelope:
    mids = (Decimal("100"), Decimal("101"), Decimal("103"), Decimal("106"))
    sizes = ((5, 5), (8, 4), (9, 3), (7, 6))
    mid = mids[index]
    bid_size, ask_size = sizes[index]
    return CanonicalEventEnvelope(
        event_type=CanonicalEventType.BOOK_SNAPSHOT,
        instrument="BTC",
        exchange_ts_ns=_ns(_time(minute)),
        local_receive_ts_ns=None,
        source_sequence=None,
        raw_object_sha256=_BOOK_HASH,
        raw_record_ordinal=index,
        normalization_schema_version="canonical-v1",
        payload=CanonicalBookSnapshot(
            bids=(CanonicalBookLevel(mid - 1, Decimal(bid_size), 1),),
            asks=(CanonicalBookLevel(mid + 1, Decimal(ask_size), 1),),
        ),
    )


def _events() -> tuple[CanonicalEventEnvelope, ...]:
    events: list[CanonicalEventEnvelope] = []
    for index, minute in enumerate((10, 11, 12, 13)):
        events.extend((_funding(index, minute), _book(index, minute)))
    return tuple(events)


def _manifest(events: tuple[CanonicalEventEnvelope, ...]) -> DatasetManifest:
    raw_objects = (
        _raw(DatasetType.L2_BOOK, _BOOK_HASH),
        _raw(DatasetType.FUNDING_REFERENCE, _FUNDING_HASH),
    )
    audit = audit_canonical_dataset(
        events,
        raw_objects=raw_objects,
        expected_normalization_schema_version="canonical-v1",
    )
    assert audit.acceptance is DatasetAcceptance.ACCEPTED
    return DatasetManifest(
        instrument="BTC",
        raw_objects=raw_objects,
        normalization_schema_version="canonical-v1",
        ordering_schema_version="ordering-v1",
        audit_schema_version="audit-v1",
        acceptance=audit.acceptance,
        created_at=datetime(2026, 8, 10, tzinfo=UTC),
        audit_digest=audit_report_digest(audit),
    )


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


def _config() -> Tier2CalibratedCandidateConfig:
    return Tier2CalibratedCandidateConfig(
        universal=_universal_config(),
        adaptive_meta=_meta(),
        risk_sizing=RiskSizingConfig(
            max_notional_fraction=Decimal("0.1"),
            max_single_move_loss_fraction=Decimal("0.01"),
            volatility_floor=Decimal("0.0001"),
        ),
        venue=VenueGridConstraints(
            tick_size=Decimal("0.01"),
            quantity_step=Decimal("0.001"),
        ),
        maker_fee_rate=Decimal("0.0001"),
        max_margin_notional=Decimal("100"),
        venue_max_quantity=Decimal("10"),
    )


def _intensity() -> tuple[IntensityBucket, ...]:
    return tuple(
        IntensityBucket(Decimal(distance), Decimal("100"), arrivals)
        for distance, arrivals in ((0, 1000), (1, 368), (2, 135), (3, 50))
    )


def _markouts() -> tuple[MaturedMarkout, ...]:
    return (
        MaturedMarkout(
            _time(0),
            _time(2),
            MarkoutSide.BUY,
            Decimal("100"),
            Decimal("99.9"),
        ),
        MaturedMarkout(
            _time(1),
            _time(3),
            MarkoutSide.SELL,
            Decimal("100"),
            Decimal("100.2"),
        ),
    )


def _ofi_samples() -> tuple[OfiImpactSample, ...]:
    return (
        OfiImpactSample(_time(0), _time(2), Decimal("-1"), Decimal("-0.002")),
        OfiImpactSample(_time(1), _time(3), Decimal("1"), Decimal("0.002")),
    )


def _frames() -> tuple[Tier2CalibrationEvidenceFrame, ...]:
    return tuple(
        Tier2CalibrationEvidenceFrame(
            as_of_timestamp_ns=_ns(_time(minute)),
            intensity_buckets=_intensity(),
            matured_markouts=_markouts(),
            new_ofi_impact_samples=_ofi_samples() if index == 0 else (),
        )
        for index, minute in enumerate((10, 11, 12, 13))
    )


def test_calibrated_candidate_is_derived_from_causal_canonical_history() -> None:
    events = _events()
    decision_timestamp_ns = _ns(_time(13))

    result = derive_tier2_calibrated_candidate(
        dataset=_manifest(events),
        events=events,
        evidence_frames=_frames(),
        decision_exchange_ts_ns=decision_timestamp_ns,
        config=_config(),
        equity=Decimal("100"),
        starting_position=Decimal(0),
    )

    assert result.decision_exchange_ts_ns == decision_timestamp_ns
    assert result.calibration_generation == 4
    assert result.preparation_reason == "ready"
    assert result.candidate_orders
    assert result.calibrated_market_state.timestamp == _time(13)
    assert result.calibrated_market_state.microstructure_status.ready is True
    assert result.capacity.q_max > 0
    assert len(result.provenance_digest) == 64
    assert all(order.price % Decimal("0.01") == 0 for order in result.candidate_orders)


def test_future_evidence_frame_cannot_change_earlier_candidate() -> None:
    events = _events()
    decision_timestamp_ns = _ns(_time(13))
    base_frames = _frames()
    future = Tier2CalibrationEvidenceFrame(
        as_of_timestamp_ns=_ns(_time(14)),
        intensity_buckets=(
            IntensityBucket(Decimal("0"), Decimal("100"), 999999),
            IntensityBucket(Decimal("1"), Decimal("100"), 1),
            IntensityBucket(Decimal("2"), Decimal("100"), 1),
        ),
        matured_markouts=_markouts(),
        new_ofi_impact_samples=(),
    )

    first = derive_tier2_calibrated_candidate(
        dataset=_manifest(events),
        events=events,
        evidence_frames=base_frames,
        decision_exchange_ts_ns=decision_timestamp_ns,
        config=_config(),
        equity=Decimal("100"),
        starting_position=Decimal(0),
    )
    second = derive_tier2_calibrated_candidate(
        dataset=_manifest(events),
        events=events,
        evidence_frames=(*base_frames, future),
        decision_exchange_ts_ns=decision_timestamp_ns,
        config=_config(),
        equity=Decimal("100"),
        starting_position=Decimal(0),
    )

    assert first.candidate_orders == second.candidate_orders
    assert first.provenance_digest == second.provenance_digest


def test_evidence_frame_rejects_future_matured_label() -> None:
    future_sample = OfiImpactSample(
        _time(9),
        _time(11),
        Decimal("1"),
        Decimal("0.01"),
    )

    with pytest.raises(ValueError, match="matured"):
        Tier2CalibrationEvidenceFrame(
            as_of_timestamp_ns=_ns(_time(10)),
            intensity_buckets=_intensity(),
            matured_markouts=_markouts(),
            new_ofi_impact_samples=(future_sample,),
        )


def test_candidate_derivation_fails_closed_when_calibration_is_not_ready() -> None:
    events = _events()

    with pytest.raises(ValueError, match="not ready"):
        derive_tier2_calibrated_candidate(
            dataset=_manifest(events),
            events=events,
            evidence_frames=(),
            decision_exchange_ts_ns=_ns(_time(13)),
            config=_config(),
            equity=Decimal("100"),
            starting_position=Decimal(0),
        )
