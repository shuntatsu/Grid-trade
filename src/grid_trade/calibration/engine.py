from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from grid_trade.calibration.contracts import (
    CalibratedMarketState,
    CalibrationComponentStatus,
    CalibrationObservation,
    CalibrationReadiness,
)
from grid_trade.calibration.funding import (
    FundingCalibrationConfig,
    FundingCalibrationState,
    FundingEstimate,
    update_funding_calibration,
)
from grid_trade.calibration.trend import (
    TrendCalibrationConfig,
    TrendEstimate,
    estimate_normalized_trend,
)
from grid_trade.calibration.volatility import (
    RobustVolatilityConfig,
    RobustVolatilityState,
    VolatilityEstimate,
    update_robust_volatility,
)


@dataclass(frozen=True, slots=True)
class CalibrationEngineConfig:
    volatility: RobustVolatilityConfig
    trend: TrendCalibrationConfig
    funding: FundingCalibrationConfig


@dataclass(frozen=True, slots=True)
class CalibrationEngineState:
    prices: tuple[Decimal, ...] = ()
    volatility_state: RobustVolatilityState = field(default_factory=RobustVolatilityState)
    funding_state: FundingCalibrationState = field(default_factory=FundingCalibrationState)
    generation: int = 0
    last_timestamp: datetime | None = None
    source_id: str | None = None
    instrument_id: str | None = None

    def __post_init__(self) -> None:
        if self.generation < 0:
            raise ValueError("generation must be non-negative")
        if (self.source_id is None) != (self.instrument_id is None):
            raise ValueError("source_id and instrument_id availability must match")
        if self.last_timestamp is not None and (
            self.last_timestamp.tzinfo is None or self.last_timestamp.utcoffset() is None
        ):
            raise ValueError("last_timestamp must be timezone-aware")
        for price in self.prices:
            if not isinstance(price, Decimal) or not price.is_finite() or price <= 0:
                raise ValueError("prices must contain finite positive Decimals")

        identity_initialized = self.source_id is not None and self.instrument_id is not None
        timestamp_initialized = self.last_timestamp is not None
        has_history = bool(self.prices or self.volatility_state.prices or self.funding_state.values)

        if self.generation == 0:
            if identity_initialized or timestamp_initialized or has_history:
                raise ValueError("generation zero requires pristine calibration state")
            return

        if self.source_id is None or self.instrument_id is None or self.last_timestamp is None:
            raise ValueError("positive generation requires initialized identity and timestamp")
        if not self.source_id.strip() or not self.instrument_id.strip():
            raise ValueError("initialized source_id and instrument_id must be non-empty")
        if not self.prices or not self.volatility_state.prices:
            raise ValueError("positive generation requires price history")
        if self.prices[-1] != self.volatility_state.prices[-1]:
            raise ValueError("price history and volatility state must share the latest price")


@dataclass(frozen=True, slots=True)
class CalibrationUpdate:
    previous_state: CalibrationEngineState
    next_state: CalibrationEngineState
    market_state: CalibratedMarketState


def _status_for_volatility(estimate: VolatilityEstimate) -> CalibrationComponentStatus:
    return CalibrationComponentStatus(
        ready=estimate.ready,
        sample_count=estimate.sample_count,
        reason="ready" if estimate.ready else "warmup",
    )


def _status_for_trend(
    estimate: TrendEstimate,
    *,
    price_count: int,
) -> CalibrationComponentStatus:
    return CalibrationComponentStatus(
        ready=estimate.ready,
        sample_count=max(0, price_count - 1),
        reason="ready" if estimate.ready else "warmup",
    )


def _status_for_funding(
    estimate: FundingEstimate,
    *,
    sample_count: int,
    observation_missing: bool,
) -> CalibrationComponentStatus:
    if estimate.ready:
        reason = "ready"
    elif estimate.degenerate:
        reason = "degenerate"
    elif observation_missing:
        reason = "unavailable"
    else:
        reason = "warmup"
    return CalibrationComponentStatus(
        ready=estimate.ready,
        sample_count=sample_count,
        reason=reason,
    )


def _overall_readiness(
    volatility: VolatilityEstimate,
    trend: TrendEstimate,
) -> CalibrationReadiness:
    ready_count = int(volatility.ready) + int(trend.ready)
    if ready_count == 2:
        return CalibrationReadiness.READY
    if ready_count == 1:
        return CalibrationReadiness.PARTIAL
    return CalibrationReadiness.NOT_READY


def _validate_observation_sequence(
    state: CalibrationEngineState,
    observation: CalibrationObservation,
) -> None:
    if state.last_timestamp is not None and observation.timestamp <= state.last_timestamp:
        raise ValueError("observation timestamp must be strictly newer than last_timestamp")
    if state.source_id is not None and observation.source_id != state.source_id:
        raise ValueError("source_id must remain constant within one calibration state")
    if state.instrument_id is not None and observation.instrument_id != state.instrument_id:
        raise ValueError("instrument_id must remain constant within one calibration state")


def update_calibration_engine(
    state: CalibrationEngineState,
    observation: CalibrationObservation,
    config: CalibrationEngineConfig,
) -> CalibrationUpdate:
    _validate_observation_sequence(state, observation)

    volatility_state, volatility = update_robust_volatility(
        state.volatility_state,
        observation,
        config.volatility,
    )
    funding_state, funding = update_funding_calibration(
        state.funding_state,
        observation.funding_rate,
        config.funding,
    )

    history_capacity = max(config.volatility.window + 1, config.trend.horizon + 1)
    prices = (*state.prices, observation.mid)[-history_capacity:]
    trend = estimate_normalized_trend(prices, volatility, config.trend)

    next_state = CalibrationEngineState(
        prices=prices,
        volatility_state=volatility_state,
        funding_state=funding_state,
        generation=state.generation + 1,
        last_timestamp=observation.timestamp,
        source_id=observation.source_id,
        instrument_id=observation.instrument_id,
    )
    unavailable_microstructure = CalibrationComponentStatus(
        ready=False,
        sample_count=0,
        reason="not_implemented",
    )
    market_state = CalibratedMarketState(
        timestamp=observation.timestamp,
        source_id=observation.source_id,
        instrument_id=observation.instrument_id,
        readiness=_overall_readiness(volatility, trend),
        volatility_scale=volatility.scale,
        trend_score=trend.score,
        funding_score=funding.score,
        quote_distance_scale=None,
        execution_cost_floor=None,
        order_book_score=None,
        estimated_microprice_displacement=None,
        volatility_status=_status_for_volatility(volatility),
        trend_status=_status_for_trend(trend, price_count=len(prices)),
        funding_status=_status_for_funding(
            funding,
            sample_count=len(funding_state.values),
            observation_missing=observation.funding_rate is None,
        ),
        microstructure_status=unavailable_microstructure,
    )
    return CalibrationUpdate(
        previous_state=state,
        next_state=next_state,
        market_state=market_state,
    )


__all__ = [
    "CalibrationEngineConfig",
    "CalibrationEngineState",
    "CalibrationUpdate",
    "update_calibration_engine",
]
