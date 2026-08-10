from datetime import UTC, datetime, timedelta
from decimal import Decimal

from grid_trade.datasets.audit import audit_canonical_dataset, audit_report_digest
from grid_trade.datasets.canonical import (
    CanonicalBookSnapshot,
    CanonicalEventEnvelope,
    CanonicalEventType,
    CanonicalFundingReference,
    canonical_event_sort_key,
)
from grid_trade.domain.market import MarketSnapshot
from grid_trade.research.tier2_replay.models import Tier2ReplayManifest

_HOUR_NS = 3_600_000_000_000


def _datetime_from_ns(timestamp_ns: int) -> datetime:
    if timestamp_ns < 0:
        raise ValueError("timestamp_ns must be non-negative")
    seconds, remainder_ns = divmod(timestamp_ns, 1_000_000_000)
    return datetime.fromtimestamp(seconds, tz=UTC) + timedelta(microseconds=remainder_ns // 1_000)


def required_hourly_funding_timestamps(
    events: tuple[CanonicalEventEnvelope, ...],
) -> tuple[int, ...]:
    if not events:
        return ()
    start_ns = min(event.exchange_ts_ns for event in events)
    end_ns = max(event.exchange_ts_ns for event in events)
    first_boundary_ns = ((start_ns + _HOUR_NS - 1) // _HOUR_NS) * _HOUR_NS
    if first_boundary_ns > end_ns:
        return ()
    return tuple(range(first_boundary_ns, end_ns + 1, _HOUR_NS))


def _validate_events(
    manifest: Tier2ReplayManifest,
    events: tuple[CanonicalEventEnvelope, ...],
) -> CanonicalEventEnvelope:
    if not events:
        raise ValueError("Tier-2 replay requires canonical events")
    if events != tuple(sorted(events, key=canonical_event_sort_key)):
        raise ValueError("Tier-2 replay events must be in deterministic canonical order")
    if any(event.instrument != manifest.dataset.instrument for event in events):
        raise ValueError("canonical event instrument must match the dataset manifest")
    for event in events:
        if event.event_type is not CanonicalEventType.BOOK_SNAPSHOT:
            continue
        if not isinstance(event.payload, CanonicalBookSnapshot):
            raise TypeError("validated book event must carry CanonicalBookSnapshot payload")
        if not event.payload.bids or not event.payload.asks:
            raise ValueError("Tier-2 replay requires two-sided initial visible depth")
        return event
    raise ValueError("Tier-2 replay requires an initial book snapshot")


def _validated_audit_events(
    manifest: Tier2ReplayManifest,
    events: tuple[CanonicalEventEnvelope, ...],
) -> tuple[CanonicalEventEnvelope, ...]:
    required_funding = required_hourly_funding_timestamps(events)
    if manifest.dataset.required_funding_timestamps_ns != required_funding:
        raise ValueError("DatasetManifest hourly funding requirements do not match replay extent")
    report = audit_canonical_dataset(
        events,
        raw_objects=manifest.dataset.raw_objects,
        required_funding_timestamps_ns=manifest.dataset.required_funding_timestamps_ns,
        expected_normalization_schema_version=manifest.dataset.normalization_schema_version,
        expectations=manifest.dataset.audit_expectations,
    )
    actual_digest = audit_report_digest(report)
    if actual_digest != manifest.dataset.audit_digest:
        raise ValueError("canonical events do not match DatasetManifest audit_digest")
    if report.acceptance is not manifest.dataset.acceptance:
        raise ValueError("canonical event audit acceptance does not match DatasetManifest")
    return report.accepted_events


def _validate_exact_hour_funding(events: tuple[CanonicalEventEnvelope, ...]) -> None:
    for event in events:
        if (
            event.event_type is not CanonicalEventType.FUNDING_REFERENCE
            or event.exchange_ts_ns % _HOUR_NS != 0
        ):
            continue
        reference = event.payload
        if not isinstance(reference, CanonicalFundingReference):
            raise TypeError("validated funding event must carry CanonicalFundingReference payload")
        if reference.funding_rate is None:
            raise ValueError("funding_rate is required at an exact-hour funding boundary")
        if reference.oracle_price is None:
            raise ValueError("oracle_price is required at an exact-hour funding boundary")


def _market_snapshot(
    *,
    initial_event: CanonicalEventEnvelope,
    manifest: Tier2ReplayManifest,
    starting_position: Decimal,
    realized_volatility: Decimal,
) -> MarketSnapshot:
    book = initial_event.payload
    if not isinstance(book, CanonicalBookSnapshot):
        raise TypeError("initial event must carry CanonicalBookSnapshot payload")
    return MarketSnapshot(
        timestamp=_datetime_from_ns(initial_event.exchange_ts_ns),
        best_bid=book.bids[0].price,
        best_ask=book.asks[0].price,
        realized_volatility=realized_volatility,
        position_quantity=starting_position,
        source_id=f"tier2:{manifest.dataset.audit_digest}",
        instrument_id=manifest.dataset.instrument,
    )


__all__ = ["required_hourly_funding_timestamps"]
