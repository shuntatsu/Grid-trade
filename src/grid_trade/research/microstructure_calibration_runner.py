from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from grid_trade.calibration.execution_cost import ExecutionCostConfig
from grid_trade.calibration.intensity import IntensityCalibrationConfig
from grid_trade.calibration.microstructure_contracts import (
    IntensityBucket,
    MarkoutSide,
    MaturedMarkout,
    OfiImpactSample,
    TopOfBookObservation,
)
from grid_trade.calibration.microstructure_engine import (
    MicrostructureCalibrationConfig,
    MicrostructureCalibrationEstimate,
    MicrostructureCalibrationState,
    update_microstructure_engine,
)
from grid_trade.calibration.order_flow import OfiImpactConfig
from grid_trade.evidence.events import EvidenceEvent, EvidenceKind
from grid_trade.evidence.ledger import evidence_digest

_RUN_ID = "microstructure-calibration-deterministic-fixture"
_BASE_TIME = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
_ZERO = Decimal(0)
_ONE = Decimal(1)


@dataclass(frozen=True, slots=True)
class MicrostructureCalibrationRunResult:
    evidence_digest: str
    deterministic: bool
    symbol_invariant: bool
    scale_invariant: bool
    step_count: int
    ready_step_count: int
    final_ready: bool
    milestone_passed: bool
    economics_validated: bool = False
    production_authorized: bool = False
    alpha_validated: bool = False

    def __post_init__(self) -> None:
        if len(self.evidence_digest) != 64:
            raise ValueError("evidence_digest must be a SHA-256 hex digest")
        if self.step_count <= 0:
            raise ValueError("step_count must be positive")
        if not 0 <= self.ready_step_count <= self.step_count:
            raise ValueError("ready_step_count must be within [0, step_count]")
        expected_milestone = (
            self.deterministic
            and self.symbol_invariant
            and self.scale_invariant
            and self.final_ready
        )
        if self.milestone_passed != expected_milestone:
            raise ValueError("milestone_passed must match deterministic calibration gates")
        if self.economics_validated or self.production_authorized or self.alpha_validated:
            raise ValueError("microstructure calibration research must remain NO-GO")


@dataclass(frozen=True, slots=True)
class _CalibrationRecord:
    generation: int
    estimate: MicrostructureCalibrationEstimate

    def __post_init__(self) -> None:
        if self.generation <= 0:
            raise ValueError("generation must be positive")


def _time(minutes: int) -> datetime:
    return _BASE_TIME + timedelta(minutes=minutes)


def _config() -> MicrostructureCalibrationConfig:
    return MicrostructureCalibrationConfig(
        intensity=IntensityCalibrationConfig(
            min_buckets=3,
            min_total_arrivals=20,
            k_min=Decimal("0.5"),
            k_max=Decimal("1.5"),
            k_steps=21,
            min_log_likelihood_improvement=Decimal("0.1"),
        ),
        ofi_impact=OfiImpactConfig(
            window=8,
            min_samples=2,
            min_abs_feature_energy=Decimal("0.01"),
            max_abs_beta=Decimal("0.01"),
            score_scale_vol_units=Decimal("2"),
        ),
        execution_cost=ExecutionCostConfig(
            markout_window=8,
            min_markout_samples=2,
            adverse_quantile=Decimal("0.75"),
            uncertainty_buffer=Decimal("0.0002"),
            fallback_adverse_cost=Decimal("0.003"),
        ),
        min_microstructure_quality=Decimal("0"),
    )


def _intensity_buckets() -> tuple[IntensityBucket, ...]:
    return (
        IntensityBucket(Decimal("0"), Decimal("100"), 1000),
        IntensityBucket(Decimal("1"), Decimal("100"), 368),
        IntensityBucket(Decimal("2"), Decimal("100"), 135),
        IntensityBucket(Decimal("3"), Decimal("100"), 50),
    )


def _ofi_labels() -> tuple[OfiImpactSample, ...]:
    return (
        OfiImpactSample(_time(0), _time(2), Decimal("-1"), Decimal("-0.002")),
        OfiImpactSample(_time(1), _time(3), Decimal("1"), Decimal("0.002")),
    )


def _markouts(price_scale: Decimal) -> tuple[MaturedMarkout, ...]:
    return (
        MaturedMarkout(
            fill_timestamp=_time(0),
            matured_at=_time(2),
            side=MarkoutSide.BUY,
            fill_price=Decimal("100") * price_scale,
            mark_price=Decimal("99.9") * price_scale,
        ),
        MaturedMarkout(
            fill_timestamp=_time(1),
            matured_at=_time(3),
            side=MarkoutSide.SELL,
            fill_price=Decimal("100") * price_scale,
            mark_price=Decimal("100.2") * price_scale,
        ),
    )


def _books(
    *,
    instrument_id: str,
    price_scale: Decimal,
    size_scale: Decimal,
) -> tuple[TopOfBookObservation, ...]:
    return (
        TopOfBookObservation(
            timestamp=_time(10),
            source_id="fixture:microstructure-calibration",
            instrument_id=instrument_id,
            best_bid=Decimal("99") * price_scale,
            bid_size=Decimal("5") * size_scale,
            best_ask=Decimal("101") * price_scale,
            ask_size=Decimal("5") * size_scale,
        ),
        TopOfBookObservation(
            timestamp=_time(11),
            source_id="fixture:microstructure-calibration",
            instrument_id=instrument_id,
            best_bid=Decimal("99") * price_scale,
            bid_size=Decimal("8") * size_scale,
            best_ask=Decimal("101") * price_scale,
            ask_size=Decimal("4") * size_scale,
        ),
    )


def _run_path(
    *,
    instrument_id: str,
    price_scale: Decimal,
    size_scale: Decimal,
) -> tuple[_CalibrationRecord, ...]:
    config = _config()
    state = MicrostructureCalibrationState()
    records: list[_CalibrationRecord] = []
    markouts = _markouts(price_scale)
    labels = _ofi_labels()

    for index, book in enumerate(
        _books(
            instrument_id=instrument_id,
            price_scale=price_scale,
            size_scale=size_scale,
        )
    ):
        update = update_microstructure_engine(
            state,
            book,
            volatility_scale=Decimal("0.001"),
            intensity_buckets=_intensity_buckets(),
            markouts=markouts,
            new_ofi_impact_samples=labels if index == 0 else (),
            maker_fee_rate=Decimal("0.0001"),
            tick_size=Decimal("0.01") * price_scale,
            config=config,
        )
        state = update.next_state
        records.append(
            _CalibrationRecord(
                generation=state.generation,
                estimate=update.estimate,
            )
        )

    return tuple(records)


def _config_payload(config: MicrostructureCalibrationConfig) -> dict[str, object]:
    return {
        "intensity": {
            "min_buckets": config.intensity.min_buckets,
            "min_total_arrivals": config.intensity.min_total_arrivals,
            "k_min": config.intensity.k_min,
            "k_max": config.intensity.k_max,
            "k_steps": config.intensity.k_steps,
            "min_log_likelihood_improvement": config.intensity.min_log_likelihood_improvement,
        },
        "ofi_impact": {
            "window": config.ofi_impact.window,
            "min_samples": config.ofi_impact.min_samples,
            "min_abs_feature_energy": config.ofi_impact.min_abs_feature_energy,
            "max_abs_beta": config.ofi_impact.max_abs_beta,
            "score_scale_vol_units": config.ofi_impact.score_scale_vol_units,
        },
        "execution_cost": {
            "markout_window": config.execution_cost.markout_window,
            "min_markout_samples": config.execution_cost.min_markout_samples,
            "adverse_quantile": config.execution_cost.adverse_quantile,
            "uncertainty_buffer": config.execution_cost.uncertainty_buffer,
            "fallback_adverse_cost": config.execution_cost.fallback_adverse_cost,
        },
        "min_microstructure_quality": config.min_microstructure_quality,
    }


def _ofi_quality(estimate: MicrostructureCalibrationEstimate) -> Decimal | None:
    if not estimate.ofi_impact.ready or estimate.ofi_impact.fit_r2 is None:
        return None
    return min(_ONE, max(_ZERO, estimate.ofi_impact.fit_r2))


def _calibration_payload(
    record: _CalibrationRecord,
    *,
    config: MicrostructureCalibrationConfig,
) -> dict[str, object]:
    estimate = record.estimate
    return {
        "state_generation": record.generation,
        "frozen_config": _config_payload(config),
        "intensity": {
            "ready": estimate.intensity.ready,
            "A": estimate.intensity.A,
            "k": estimate.intensity.k,
            "e_fold_distance_vol_units": estimate.intensity.e_fold_distance_vol_units,
            "log_likelihood_improvement": estimate.intensity.log_likelihood_improvement,
            "quality": estimate.intensity.quality,
            "sample_count": estimate.intensity.sample_count,
            "total_arrivals": estimate.intensity.total_arrivals,
        },
        "quote_distance_scale": estimate.quote_distance_scale,
        "execution": {
            "execution_cost_floor": estimate.execution.execution_cost_floor,
            "adverse_cost": estimate.execution.adverse_cost,
            "round_trip_fee": estimate.execution.round_trip_fee,
            "tick_floor": estimate.execution.tick_floor,
            "markout_ready": estimate.execution.markout_ready,
            "used_fallback": estimate.execution.used_fallback,
            "sample_count": estimate.execution.sample_count,
        },
        "current_normalized_ofi": estimate.current_normalized_ofi,
        "ofi_impact": {
            "ready": estimate.ofi_impact.ready,
            "beta": estimate.ofi_impact.beta,
            "fit_r2": estimate.ofi_impact.fit_r2,
            "quality": _ofi_quality(estimate),
            "sample_count": estimate.ofi_impact.sample_count,
        },
        "predicted_relative_displacement": estimate.predicted_relative_displacement,
        "microprice_relative_displacement": estimate.microprice_relative_displacement,
        "order_book_score": estimate.order_book_score,
        "readiness": {
            "ready": estimate.readiness.ready,
            "sample_count": estimate.readiness.sample_count,
            "reason": estimate.readiness.reason,
            "quality": estimate.readiness.quality,
        },
    }


def _evidence_events(
    *,
    records: tuple[_CalibrationRecord, ...],
    deterministic: bool,
    symbol_invariant: bool,
    scale_invariant: bool,
) -> tuple[EvidenceEvent, ...]:
    config = _config()
    books = _books(
        instrument_id="GENERIC-PERP",
        price_scale=Decimal("1"),
        size_scale=Decimal("1"),
    )
    events: list[EvidenceEvent] = []

    for book, record in zip(books, records, strict=True):
        events.append(
            EvidenceEvent.create(
                run_id=_RUN_ID,
                sequence=len(events),
                timestamp=book.timestamp,
                kind=EvidenceKind.MARKET_SNAPSHOT,
                payload={
                    "source_id": book.source_id,
                    "instrument_id": book.instrument_id,
                    "best_bid": book.best_bid,
                    "bid_size": book.bid_size,
                    "best_ask": book.best_ask,
                    "ask_size": book.ask_size,
                    "volatility_scale": Decimal("0.001"),
                },
            )
        )
        events.append(
            EvidenceEvent.create(
                run_id=_RUN_ID,
                sequence=len(events),
                timestamp=book.timestamp,
                kind=EvidenceKind.MICROSTRUCTURE_CALIBRATION,
                payload=_calibration_payload(record, config=config),
            )
        )

    ready_step_count = sum(1 for record in records if record.estimate.readiness.ready)
    final_ready = records[-1].estimate.readiness.ready
    milestone_passed = deterministic and symbol_invariant and scale_invariant and final_ready
    events.append(
        EvidenceEvent.create(
            run_id=_RUN_ID,
            sequence=len(events),
            timestamp=books[-1].timestamp,
            kind=EvidenceKind.RUN_SUMMARY,
            payload={
                "step_count": len(records),
                "ready_step_count": ready_step_count,
                "final_ready": final_ready,
                "final_state_generation": records[-1].generation,
                "frozen_config": _config_payload(config),
                "deterministic": deterministic,
                "symbol_invariant": symbol_invariant,
                "scale_invariant": scale_invariant,
                "milestone_passed": milestone_passed,
                "economics_validated": False,
                "production_authorized": False,
                "alpha_validated": False,
                "scope": "causal_microstructure_calibration_only",
            },
        )
    )
    return tuple(events)


def run_checked_in_microstructure_calibration() -> MicrostructureCalibrationRunResult:
    baseline = _run_path(
        instrument_id="GENERIC-PERP",
        price_scale=Decimal("1"),
        size_scale=Decimal("1"),
    )
    repeated = _run_path(
        instrument_id="GENERIC-PERP",
        price_scale=Decimal("1"),
        size_scale=Decimal("1"),
    )
    renamed = _run_path(
        instrument_id="ALT-PERP",
        price_scale=Decimal("1"),
        size_scale=Decimal("1"),
    )
    scaled = _run_path(
        instrument_id="GENERIC-PERP",
        price_scale=Decimal("100"),
        size_scale=Decimal("100"),
    )

    deterministic = baseline == repeated
    symbol_invariant = baseline == renamed
    scale_invariant = baseline == scaled
    ready_step_count = sum(1 for record in baseline if record.estimate.readiness.ready)
    final_ready = baseline[-1].estimate.readiness.ready
    milestone_passed = deterministic and symbol_invariant and scale_invariant and final_ready
    events = _evidence_events(
        records=baseline,
        deterministic=deterministic,
        symbol_invariant=symbol_invariant,
        scale_invariant=scale_invariant,
    )

    return MicrostructureCalibrationRunResult(
        evidence_digest=evidence_digest(events),
        deterministic=deterministic,
        symbol_invariant=symbol_invariant,
        scale_invariant=scale_invariant,
        step_count=len(baseline),
        ready_step_count=ready_step_count,
        final_ready=final_ready,
        milestone_passed=milestone_passed,
    )


if __name__ == "__main__":
    print(run_checked_in_microstructure_calibration().evidence_digest)


__all__ = [
    "MicrostructureCalibrationRunResult",
    "run_checked_in_microstructure_calibration",
]
