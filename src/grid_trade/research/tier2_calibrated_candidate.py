import json
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from hashlib import sha256
from typing import Any

from grid_trade.application.calibrated_adaptive import (
    CalibratedAdaptiveMetaConfig,
    VenueGridConstraints,
    initialize_calibrated_adaptive_grid,
    prepare_calibrated_adaptive_inputs,
)
from grid_trade.calibration.contracts import CalibratedMarketState, CalibrationObservation
from grid_trade.calibration.microstructure_contracts import (
    IntensityBucket,
    MaturedMarkout,
    OfiImpactSample,
    TopOfBookObservation,
)
from grid_trade.calibration.universal_engine import (
    UniversalCalibrationConfig,
    UniversalCalibrationState,
    update_universal_calibration,
)
from grid_trade.datasets.audit import audit_canonical_dataset, audit_report_digest
from grid_trade.datasets.canonical import (
    CanonicalBookSnapshot,
    CanonicalEventEnvelope,
    CanonicalEventType,
    CanonicalFundingReference,
    canonical_event_sort_key,
)
from grid_trade.datasets.contracts import DatasetAcceptance
from grid_trade.datasets.manifest import DatasetManifest
from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.orders import PassiveOrderIntent
from grid_trade.risk.sizing import (
    InventoryCapacity,
    RiskSizingConfig,
    RiskSizingInput,
    derive_inventory_capacity,
)


def _require_finite(value: Decimal, *, field: str) -> None:
    if not value.is_finite():
        raise ValueError(f"{field} must be finite")


def _require_positive(value: Decimal, *, field: str) -> None:
    _require_finite(value, field=field)
    if value <= 0:
        raise ValueError(f"{field} must be positive")


def _datetime_from_ns(timestamp_ns: int) -> datetime:
    if timestamp_ns < 0:
        raise ValueError("timestamp must be non-negative")
    seconds, remainder_ns = divmod(timestamp_ns, 1_000_000_000)
    return datetime.fromtimestamp(seconds, tz=UTC) + timedelta(microseconds=remainder_ns // 1_000)


def _canonical_value(value: object) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _canonical_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple | list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"unsupported calibrated candidate value: {type(value).__name__}")


def _digest(value: object) -> str:
    rendered = json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return sha256(f"{rendered}\n".encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Tier2CalibrationEvidenceFrame:
    as_of_timestamp_ns: int
    intensity_buckets: tuple[IntensityBucket, ...]
    matured_markouts: tuple[MaturedMarkout, ...]
    new_ofi_impact_samples: tuple[OfiImpactSample, ...]

    def __post_init__(self) -> None:
        as_of = _datetime_from_ns(self.as_of_timestamp_ns)
        if any(markout.matured_at > as_of for markout in self.matured_markouts):
            raise ValueError("matured markout cannot be newer than evidence-frame as_of timestamp")
        if any(sample.matured_at > as_of for sample in self.new_ofi_impact_samples):
            raise ValueError(
                "matured OFI sample cannot be newer than evidence-frame as_of timestamp"
            )


@dataclass(frozen=True, slots=True)
class Tier2CalibratedCandidateConfig:
    universal: UniversalCalibrationConfig
    adaptive_meta: CalibratedAdaptiveMetaConfig
    risk_sizing: RiskSizingConfig
    venue: VenueGridConstraints
    maker_fee_rate: Decimal
    max_margin_notional: Decimal
    venue_max_quantity: Decimal

    def __post_init__(self) -> None:
        _require_finite(self.maker_fee_rate, field="maker_fee_rate")
        _require_positive(self.max_margin_notional, field="max_margin_notional")
        _require_positive(self.venue_max_quantity, field="venue_max_quantity")


@dataclass(frozen=True, slots=True)
class Tier2CalibratedCandidateResult:
    decision_exchange_ts_ns: int
    calibration_generation: int
    calibrated_market_state: CalibratedMarketState
    capacity: InventoryCapacity
    preparation_reason: str
    candidate_orders: tuple[PassiveOrderIntent, ...]
    provenance_digest: str

    def __post_init__(self) -> None:
        if self.decision_exchange_ts_ns < 0:
            raise ValueError("decision_exchange_ts_ns must be non-negative")
        if self.calibration_generation <= 0:
            raise ValueError("calibration_generation must be positive")
        if not self.preparation_reason.strip():
            raise ValueError("preparation_reason must be non-empty")
        if len(self.provenance_digest) != 64:
            raise ValueError("provenance_digest must be a SHA-256 hex digest")


def _bind_audited_events(
    dataset: DatasetManifest,
    events: tuple[CanonicalEventEnvelope, ...],
) -> tuple[CanonicalEventEnvelope, ...]:
    if dataset.acceptance is not DatasetAcceptance.ACCEPTED:
        raise ValueError("calibrated candidate requires DatasetAcceptance.ACCEPTED")
    if dataset.audit_digest is None:
        raise ValueError("calibrated candidate requires dataset audit_digest")
    if not events:
        raise ValueError("calibrated candidate requires canonical events")
    if events != tuple(sorted(events, key=canonical_event_sort_key)):
        raise ValueError("canonical events must be in deterministic order")
    if any(event.instrument != dataset.instrument for event in events):
        raise ValueError("canonical event instrument must match DatasetManifest")

    report = audit_canonical_dataset(
        events,
        raw_objects=dataset.raw_objects,
        required_funding_timestamps_ns=dataset.required_funding_timestamps_ns,
        expected_normalization_schema_version=dataset.normalization_schema_version,
        expectations=dataset.audit_expectations,
    )
    if report.acceptance is not dataset.acceptance:
        raise ValueError("canonical event acceptance does not match DatasetManifest")
    if audit_report_digest(report) != dataset.audit_digest:
        raise ValueError("canonical events do not match DatasetManifest audit_digest")
    return report.accepted_events


def _validate_frames(frames: tuple[Tier2CalibrationEvidenceFrame, ...]) -> None:
    timestamps = tuple(frame.as_of_timestamp_ns for frame in frames)
    if timestamps != tuple(sorted(timestamps)):
        raise ValueError("calibration evidence frames must be ordered by as_of timestamp")
    if len(set(timestamps)) != len(timestamps):
        raise ValueError("calibration evidence-frame as_of timestamps must be unique")


def _latest_funding_rate(
    reference: CanonicalFundingReference | None,
) -> Decimal | None:
    if reference is None:
        return None
    return reference.funding_rate


def _book_context(
    event: CanonicalEventEnvelope,
    *,
    source_id: str,
    funding_reference: CanonicalFundingReference | None,
) -> tuple[CalibrationObservation, TopOfBookObservation, MarketSnapshot]:
    book = event.payload
    if not isinstance(book, CanonicalBookSnapshot):
        raise TypeError("book event must carry CanonicalBookSnapshot payload")
    if not book.bids or not book.asks:
        raise ValueError("calibration requires two-sided top-of-book depth")

    timestamp = _datetime_from_ns(event.exchange_ts_ns)
    best_bid = book.bids[0]
    best_ask = book.asks[0]
    mid = (best_bid.price + best_ask.price) / Decimal(2)
    observation = CalibrationObservation(
        timestamp=timestamp,
        source_id=source_id,
        instrument_id=event.instrument,
        mid=mid,
        funding_rate=_latest_funding_rate(funding_reference),
    )
    top = TopOfBookObservation(
        timestamp=timestamp,
        source_id=source_id,
        instrument_id=event.instrument,
        best_bid=best_bid.price,
        bid_size=best_bid.quantity,
        best_ask=best_ask.price,
        ask_size=best_ask.quantity,
    )
    snapshot = MarketSnapshot(
        timestamp=timestamp,
        best_bid=best_bid.price,
        best_ask=best_ask.price,
        realized_volatility=Decimal(0),
        position_quantity=Decimal(0),
        source_id=source_id,
    )
    return observation, top, snapshot


def _causal_raw_hashes(events: tuple[CanonicalEventEnvelope, ...]) -> tuple[str, ...]:
    return tuple(sorted({event.raw_object_sha256 for event in events}))


def derive_tier2_calibrated_candidate(
    *,
    dataset: DatasetManifest,
    events: tuple[CanonicalEventEnvelope, ...],
    evidence_frames: tuple[Tier2CalibrationEvidenceFrame, ...],
    decision_exchange_ts_ns: int,
    config: Tier2CalibratedCandidateConfig,
    equity: Decimal,
    starting_position: Decimal,
) -> Tier2CalibratedCandidateResult:
    _require_positive(equity, field="equity")
    _require_finite(starting_position, field="starting_position")
    _validate_frames(evidence_frames)
    accepted_events = _bind_audited_events(dataset, events)

    causal_events = tuple(
        event for event in accepted_events if event.exchange_ts_ns <= decision_exchange_ts_ns
    )
    if not causal_events:
        raise ValueError("no canonical events are available at the decision timestamp")

    frames = tuple(
        frame for frame in evidence_frames if frame.as_of_timestamp_ns <= decision_exchange_ts_ns
    )
    frame_index = 0
    intensity_buckets: tuple[IntensityBucket, ...] = ()
    matured_markouts: tuple[MaturedMarkout, ...] = ()
    pending_ofi_samples: list[OfiImpactSample] = []
    funding_reference: CanonicalFundingReference | None = None
    universal_state = UniversalCalibrationState()
    final_market_state: CalibratedMarketState | None = None
    final_snapshot: MarketSnapshot | None = None
    decision_book_seen = False
    source_id = f"tier2-calibration:{dataset.instrument}"
    consumed_frames: list[Tier2CalibrationEvidenceFrame] = []

    for event in causal_events:
        if event.event_type is CanonicalEventType.FUNDING_REFERENCE:
            reference = event.payload
            if not isinstance(reference, CanonicalFundingReference):
                raise TypeError("funding event must carry CanonicalFundingReference payload")
            funding_reference = reference
            continue
        if event.event_type is not CanonicalEventType.BOOK_SNAPSHOT:
            continue

        while (
            frame_index < len(frames)
            and frames[frame_index].as_of_timestamp_ns <= event.exchange_ts_ns
        ):
            frame = frames[frame_index]
            intensity_buckets = frame.intensity_buckets
            matured_markouts = frame.matured_markouts
            pending_ofi_samples.extend(frame.new_ofi_impact_samples)
            consumed_frames.append(frame)
            frame_index += 1

        observation, book, snapshot = _book_context(
            event,
            source_id=source_id,
            funding_reference=funding_reference,
        )
        update = update_universal_calibration(
            universal_state,
            observation=observation,
            book=book,
            intensity_buckets=intensity_buckets,
            markouts=matured_markouts,
            new_ofi_impact_samples=tuple(pending_ofi_samples),
            maker_fee_rate=config.maker_fee_rate,
            tick_size=config.venue.tick_size,
            config=config.universal,
        )
        universal_state = update.next_state
        pending_ofi_samples.clear()
        final_market_state = update.market_state
        final_snapshot = snapshot
        if event.exchange_ts_ns == decision_exchange_ts_ns:
            decision_book_seen = True

    if not decision_book_seen or final_market_state is None or final_snapshot is None:
        raise ValueError("decision timestamp must identify a canonical book snapshot")

    volatility = final_market_state.volatility_scale
    if volatility is None:
        raise ValueError("calibrated adaptive candidate is not ready: volatility unavailable")
    capacity = derive_inventory_capacity(
        RiskSizingInput(
            equity=equity,
            reference_price=final_snapshot.mid,
            volatility_scale=volatility,
            max_margin_notional=config.max_margin_notional,
            venue_max_quantity=config.venue_max_quantity,
        ),
        config.risk_sizing,
    )
    positioned_snapshot = MarketSnapshot(
        timestamp=final_snapshot.timestamp,
        best_bid=final_snapshot.best_bid,
        best_ask=final_snapshot.best_ask,
        realized_volatility=final_snapshot.realized_volatility,
        position_quantity=starting_position,
        source_id=final_snapshot.source_id,
    )
    preparation = prepare_calibrated_adaptive_inputs(
        snapshot=positioned_snapshot,
        calibrated=final_market_state,
        capacity=capacity,
        meta=config.adaptive_meta,
        venue=config.venue,
    )
    if preparation.inputs is None:
        raise ValueError(f"calibrated adaptive candidate is not ready: {preparation.reason}")
    _, candidate_orders = initialize_calibrated_adaptive_grid(preparation.inputs)

    provenance_digest = _digest(
        {
            "decision_exchange_ts_ns": decision_exchange_ts_ns,
            "instrument": dataset.instrument,
            "normalization_schema_version": dataset.normalization_schema_version,
            "raw_object_sha256": _causal_raw_hashes(causal_events),
            "causal_events": causal_events,
            "consumed_evidence_frames": tuple(consumed_frames),
            "config": config,
            "equity": equity,
            "starting_position": starting_position,
            "calibrated_market_state": final_market_state,
            "capacity": capacity,
            "candidate_orders": candidate_orders,
        }
    )
    return Tier2CalibratedCandidateResult(
        decision_exchange_ts_ns=decision_exchange_ts_ns,
        calibration_generation=universal_state.foundation_state.generation,
        calibrated_market_state=final_market_state,
        capacity=capacity,
        preparation_reason=preparation.reason,
        candidate_orders=candidate_orders,
        provenance_digest=provenance_digest,
    )


__all__ = [
    "Tier2CalibratedCandidateConfig",
    "Tier2CalibratedCandidateResult",
    "Tier2CalibrationEvidenceFrame",
    "derive_tier2_calibrated_candidate",
]
