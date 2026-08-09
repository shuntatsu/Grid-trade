from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from grid_trade.calibration.execution_cost import (
    ExecutionCostConfig,
    ExecutionCostEstimate,
    estimate_execution_cost,
)
from grid_trade.calibration.intensity import (
    IntensityCalibrationConfig,
    IntensityEstimate,
    estimate_arrival_intensity,
)
from grid_trade.calibration.microstructure_contracts import (
    IntensityBucket,
    MaturedMarkout,
    MicrostructureReadiness,
    OfiImpactSample,
    TopOfBookObservation,
)
from grid_trade.calibration.order_flow import (
    OfiImpactConfig,
    OfiImpactEstimate,
    OfiImpactState,
    estimate_ofi_impact,
    microprice_displacement,
    normalized_ofi,
    predict_ofi_displacement,
    update_ofi_impact,
)
from grid_trade.domain.numeric import deterministic_decimal_context

_ZERO = Decimal(0)
_ONE = Decimal(1)
_TWO = Decimal(2)


def _require_finite_score(value: Decimal, *, field: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or not _ZERO <= value <= _ONE:
        raise ValueError(f"{field} must be a finite Decimal within [0, 1]")


def _require_optional_positive(value: Decimal | None, *, field: str) -> None:
    if value is not None and (
        not isinstance(value, Decimal) or not value.is_finite() or value <= 0
    ):
        raise ValueError(f"{field} must be a finite positive Decimal when available")


def _clip_score(value: Decimal) -> Decimal:
    return min(_ONE, max(-_ONE, value))


@dataclass(frozen=True, slots=True)
class MicrostructureCalibrationConfig:
    intensity: IntensityCalibrationConfig
    ofi_impact: OfiImpactConfig
    execution_cost: ExecutionCostConfig
    min_microstructure_quality: Decimal

    def __post_init__(self) -> None:
        _require_finite_score(
            self.min_microstructure_quality,
            field="min_microstructure_quality",
        )


@dataclass(frozen=True, slots=True)
class MicrostructureCalibrationState:
    config: MicrostructureCalibrationConfig | None = None
    generation: int = 0
    source_id: str | None = None
    instrument_id: str | None = None
    last_timestamp: datetime | None = None
    last_book: TopOfBookObservation | None = None
    ofi_impact_state: OfiImpactState = field(default_factory=OfiImpactState)

    def __post_init__(self) -> None:
        if self.generation < 0:
            raise ValueError("generation must be non-negative")
        if self.generation == 0:
            if (
                any(
                    value is not None
                    for value in (
                        self.config,
                        self.source_id,
                        self.instrument_id,
                        self.last_timestamp,
                        self.last_book,
                    )
                )
                or self.ofi_impact_state.samples
            ):
                raise ValueError("generation zero requires pristine microstructure state")
            return

        if (
            self.config is None
            or self.source_id is None
            or self.instrument_id is None
            or self.last_timestamp is None
            or self.last_book is None
        ):
            raise ValueError("positive generation requires initialized microstructure state")
        if not self.source_id.strip() or not self.instrument_id.strip():
            raise ValueError("initialized identity must be non-empty")
        if self.last_timestamp.tzinfo is None or self.last_timestamp.utcoffset() is None:
            raise ValueError("last_timestamp must be timezone-aware")
        if self.last_book.timestamp != self.last_timestamp:
            raise ValueError("last_book timestamp must match last_timestamp")
        if self.last_book.source_id != self.source_id:
            raise ValueError("last_book source_id must match state source_id")
        if self.last_book.instrument_id != self.instrument_id:
            raise ValueError("last_book instrument_id must match state instrument_id")


@dataclass(frozen=True, slots=True)
class MicrostructureCalibrationEstimate:
    intensity: IntensityEstimate
    quote_distance_scale: Decimal | None
    execution: ExecutionCostEstimate
    current_normalized_ofi: Decimal | None
    ofi_impact: OfiImpactEstimate
    predicted_relative_displacement: Decimal | None
    microprice_relative_displacement: Decimal
    order_book_score: Decimal | None
    readiness: MicrostructureReadiness

    def __post_init__(self) -> None:
        _require_optional_positive(self.quote_distance_scale, field="quote_distance_scale")
        for field_name in (
            "current_normalized_ofi",
            "predicted_relative_displacement",
            "microprice_relative_displacement",
            "order_book_score",
        ):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, Decimal) or not value.is_finite()):
                raise ValueError(f"{field_name} must be a finite Decimal when available")
        if self.order_book_score is not None and not -_ONE <= self.order_book_score <= _ONE:
            raise ValueError("order_book_score must be within [-1, 1]")


@dataclass(frozen=True, slots=True)
class MicrostructureCalibrationUpdate:
    previous_state: MicrostructureCalibrationState
    next_state: MicrostructureCalibrationState
    estimate: MicrostructureCalibrationEstimate


def _validate_sequence(
    state: MicrostructureCalibrationState,
    book: TopOfBookObservation,
    config: MicrostructureCalibrationConfig,
) -> None:
    if state.last_timestamp is not None and book.timestamp <= state.last_timestamp:
        raise ValueError("book timestamp must be strictly newer than last_timestamp")
    if state.source_id is not None and book.source_id != state.source_id:
        raise ValueError("source_id must remain constant within microstructure state")
    if state.instrument_id is not None and book.instrument_id != state.instrument_id:
        raise ValueError("instrument_id must remain constant within microstructure state")
    if state.config is not None and config != state.config:
        raise ValueError("microstructure config must remain frozen within one engine state")


def _update_ofi_state(
    state: OfiImpactState,
    samples: tuple[OfiImpactSample, ...],
    *,
    decision_time: datetime,
    config: OfiImpactConfig,
) -> tuple[OfiImpactState, OfiImpactEstimate]:
    next_state = state
    ordered = sorted(
        samples,
        key=lambda sample: (
            sample.matured_at,
            sample.feature_timestamp,
            sample.normalized_ofi,
            sample.relative_price_change,
        ),
    )
    for sample in ordered:
        next_state, _ = update_ofi_impact(
            next_state,
            sample,
            decision_time=decision_time,
            config=config,
        )
    estimate = estimate_ofi_impact(next_state, decision_time=decision_time, config=config)
    return next_state, estimate


def _component_quality(
    intensity: IntensityEstimate,
    ofi_impact: OfiImpactEstimate,
    execution: ExecutionCostEstimate,
    *,
    has_book_pair: bool,
    has_volatility: bool,
) -> Decimal:
    intensity_quality = (
        intensity.quality if intensity.ready and intensity.quality is not None else _ZERO
    )
    if ofi_impact.ready and ofi_impact.fit_r2 is not None:
        ofi_quality = min(_ONE, max(_ZERO, ofi_impact.fit_r2))
    else:
        ofi_quality = _ZERO
    execution_quality = _ONE if execution.markout_ready else _ZERO
    book_quality = _ONE if has_book_pair else _ZERO
    volatility_quality = _ONE if has_volatility else _ZERO
    return min(
        intensity_quality,
        ofi_quality,
        execution_quality,
        book_quality,
        volatility_quality,
    )


def _readiness_reason(
    *,
    has_volatility: bool,
    intensity: IntensityEstimate,
    has_book_pair: bool,
    ofi_impact: OfiImpactEstimate,
    execution: ExecutionCostEstimate,
    quality: Decimal,
    min_quality: Decimal,
) -> str:
    if not has_volatility:
        return "volatility_unavailable"
    if not intensity.ready:
        return "intensity_unavailable"
    if not has_book_pair:
        return "book_pair_unavailable"
    if not ofi_impact.ready:
        return "ofi_impact_unavailable"
    if not execution.markout_ready:
        return "markout_unavailable"
    if quality < min_quality:
        return "quality_below_threshold"
    return "ready"


def update_microstructure_engine(
    state: MicrostructureCalibrationState,
    book: TopOfBookObservation,
    *,
    volatility_scale: Decimal | None,
    intensity_buckets: tuple[IntensityBucket, ...],
    markouts: tuple[MaturedMarkout, ...],
    new_ofi_impact_samples: tuple[OfiImpactSample, ...],
    maker_fee_rate: Decimal,
    tick_size: Decimal,
    config: MicrostructureCalibrationConfig,
) -> MicrostructureCalibrationUpdate:
    _validate_sequence(state, book, config)
    _require_optional_positive(volatility_scale, field="volatility_scale")

    intensity = estimate_arrival_intensity(intensity_buckets, config.intensity)
    ofi_state, ofi_impact = _update_ofi_state(
        state.ofi_impact_state,
        new_ofi_impact_samples,
        decision_time=book.timestamp,
        config=config.ofi_impact,
    )

    with deterministic_decimal_context():
        current_mid = (book.best_bid + book.best_ask) / _TWO
    execution = estimate_execution_cost(
        markouts,
        decision_time=book.timestamp,
        maker_fee_rate=maker_fee_rate,
        tick_size=tick_size,
        current_mid=current_mid,
        config=config.execution_cost,
    )

    current_ofi = normalized_ofi(state.last_book, book) if state.last_book is not None else None
    microprice_relative = microprice_displacement(book)
    predicted = (
        predict_ofi_displacement(current_ofi, ofi_impact) if current_ofi is not None else None
    )

    quote_distance_scale: Decimal | None = None
    order_book_score: Decimal | None = None
    if (
        volatility_scale is not None
        and intensity.ready
        and intensity.e_fold_distance_vol_units is not None
    ):
        with deterministic_decimal_context():
            quote_distance_scale = volatility_scale * intensity.e_fold_distance_vol_units
    if volatility_scale is not None and predicted is not None:
        with deterministic_decimal_context():
            displacement_vol_units = predicted / volatility_scale
            order_book_score = _clip_score(
                displacement_vol_units / config.ofi_impact.score_scale_vol_units
            )

    has_book_pair = current_ofi is not None
    has_volatility = volatility_scale is not None
    quality = _component_quality(
        intensity,
        ofi_impact,
        execution,
        has_book_pair=has_book_pair,
        has_volatility=has_volatility,
    )
    reason = _readiness_reason(
        has_volatility=has_volatility,
        intensity=intensity,
        has_book_pair=has_book_pair,
        ofi_impact=ofi_impact,
        execution=execution,
        quality=quality,
        min_quality=config.min_microstructure_quality,
    )
    ready = reason == "ready"
    evidence_count = min(
        intensity.sample_count,
        ofi_impact.sample_count,
        execution.sample_count,
    )
    readiness = MicrostructureReadiness(
        ready=ready,
        sample_count=evidence_count,
        reason=reason,
        quality=quality,
    )

    next_state = MicrostructureCalibrationState(
        config=config,
        generation=state.generation + 1,
        source_id=book.source_id,
        instrument_id=book.instrument_id,
        last_timestamp=book.timestamp,
        last_book=book,
        ofi_impact_state=ofi_state,
    )
    estimate = MicrostructureCalibrationEstimate(
        intensity=intensity,
        quote_distance_scale=quote_distance_scale,
        execution=execution,
        current_normalized_ofi=current_ofi,
        ofi_impact=ofi_impact,
        predicted_relative_displacement=predicted,
        microprice_relative_displacement=microprice_relative,
        order_book_score=order_book_score,
        readiness=readiness,
    )
    return MicrostructureCalibrationUpdate(
        previous_state=state,
        next_state=next_state,
        estimate=estimate,
    )


__all__ = [
    "MicrostructureCalibrationConfig",
    "MicrostructureCalibrationEstimate",
    "MicrostructureCalibrationState",
    "MicrostructureCalibrationUpdate",
    "update_microstructure_engine",
]
