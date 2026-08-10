from dataclasses import dataclass, field, replace
from decimal import Decimal

from grid_trade.calibration.contracts import (
    CalibratedMarketState,
    CalibrationComponentStatus,
    CalibrationObservation,
)
from grid_trade.calibration.engine import (
    CalibrationEngineConfig,
    CalibrationEngineState,
    CalibrationUpdate,
    update_calibration_engine,
)
from grid_trade.calibration.microstructure_contracts import (
    IntensityBucket,
    MaturedMarkout,
    OfiImpactSample,
    TopOfBookObservation,
)
from grid_trade.calibration.microstructure_engine import (
    MicrostructureCalibrationConfig,
    MicrostructureCalibrationState,
    MicrostructureCalibrationUpdate,
    update_microstructure_engine,
)


@dataclass(frozen=True, slots=True)
class UniversalCalibrationConfig:
    foundation: CalibrationEngineConfig
    microstructure: MicrostructureCalibrationConfig


@dataclass(frozen=True, slots=True)
class UniversalCalibrationState:
    foundation_state: CalibrationEngineState = field(default_factory=CalibrationEngineState)
    microstructure_state: MicrostructureCalibrationState = field(
        default_factory=MicrostructureCalibrationState
    )

    def __post_init__(self) -> None:
        if self.foundation_state.generation != self.microstructure_state.generation:
            raise ValueError("foundation and microstructure generations must match")
        if self.foundation_state.generation == 0:
            return
        if self.foundation_state.last_timestamp != self.microstructure_state.last_timestamp:
            raise ValueError("foundation and microstructure timestamps must match")
        if self.foundation_state.source_id != self.microstructure_state.source_id:
            raise ValueError("foundation and microstructure source identity must match")
        if self.foundation_state.instrument_id != self.microstructure_state.instrument_id:
            raise ValueError("foundation and microstructure instrument identity must match")


@dataclass(frozen=True, slots=True)
class UniversalCalibrationUpdate:
    previous_state: UniversalCalibrationState
    next_state: UniversalCalibrationState
    market_state: CalibratedMarketState
    foundation: CalibrationUpdate
    microstructure: MicrostructureCalibrationUpdate


def _validate_observation_pair(
    observation: CalibrationObservation,
    book: TopOfBookObservation,
) -> None:
    if observation.timestamp != book.timestamp:
        raise ValueError("foundation and microstructure timestamp identity must match")
    if observation.source_id != book.source_id:
        raise ValueError("foundation and microstructure source identity must match")
    if observation.instrument_id != book.instrument_id:
        raise ValueError("foundation and microstructure instrument identity must match")
    if observation.mid != book.mid:
        raise ValueError("foundation and microstructure mid must match")


def update_universal_calibration(
    state: UniversalCalibrationState,
    *,
    observation: CalibrationObservation,
    book: TopOfBookObservation,
    intensity_buckets: tuple[IntensityBucket, ...],
    markouts: tuple[MaturedMarkout, ...],
    new_ofi_impact_samples: tuple[OfiImpactSample, ...],
    maker_fee_rate: Decimal,
    tick_size: Decimal,
    config: UniversalCalibrationConfig,
) -> UniversalCalibrationUpdate:
    _validate_observation_pair(observation, book)
    sampling = config.foundation.sampling
    if sampling is not None:
        for markout in markouts:
            sampling.validate_markout(markout)
        for sample in new_ofi_impact_samples:
            sampling.validate_ofi_sample(sample)

    foundation = update_calibration_engine(
        state.foundation_state,
        observation,
        config.foundation,
    )
    microstructure = update_microstructure_engine(
        state.microstructure_state,
        book,
        volatility_scale=foundation.market_state.volatility_scale,
        intensity_buckets=intensity_buckets,
        markouts=markouts,
        new_ofi_impact_samples=new_ofi_impact_samples,
        maker_fee_rate=maker_fee_rate,
        tick_size=tick_size,
        config=config.microstructure,
    )
    micro_status = CalibrationComponentStatus(
        ready=microstructure.estimate.readiness.ready,
        sample_count=microstructure.estimate.readiness.sample_count,
        reason=microstructure.estimate.readiness.reason,
    )
    market_state = replace(
        foundation.market_state,
        quote_distance_scale=microstructure.estimate.quote_distance_scale,
        execution_cost_floor=microstructure.estimate.execution.execution_cost_floor,
        order_book_score=microstructure.estimate.order_book_score,
        estimated_microprice_displacement=(
            microstructure.estimate.microprice_relative_displacement
        ),
        microstructure_status=micro_status,
    )
    next_state = UniversalCalibrationState(
        foundation_state=foundation.next_state,
        microstructure_state=microstructure.next_state,
    )
    return UniversalCalibrationUpdate(
        previous_state=state,
        next_state=next_state,
        market_state=market_state,
        foundation=foundation,
        microstructure=microstructure,
    )


__all__ = [
    "UniversalCalibrationConfig",
    "UniversalCalibrationState",
    "UniversalCalibrationUpdate",
    "update_universal_calibration",
]
