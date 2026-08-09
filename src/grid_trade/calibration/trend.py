from dataclasses import dataclass
from decimal import Decimal

from grid_trade.calibration.volatility import VolatilityEstimate

_ZERO = Decimal(0)
_ONE = Decimal(1)
_STABILITY_BOUND = Decimal(20)


def _require_finite_positive(value: Decimal, *, field: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"{field} must be a finite positive Decimal")


def _clip(value: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    return min(upper, max(lower, value))


def _decimal_tanh(value: Decimal) -> Decimal:
    bounded = _clip(value, -_STABILITY_BOUND, _STABILITY_BOUND)
    exponential = (Decimal(2) * bounded).exp()
    return (exponential - _ONE) / (exponential + _ONE)


@dataclass(frozen=True, slots=True)
class TrendCalibrationConfig:
    horizon: int
    transform_gain: Decimal
    min_volatility_scale: Decimal
    max_abs_z: Decimal

    def __post_init__(self) -> None:
        if self.horizon <= 0:
            raise ValueError("horizon must be positive")
        _require_finite_positive(self.transform_gain, field="transform_gain")
        _require_finite_positive(self.min_volatility_scale, field="min_volatility_scale")
        _require_finite_positive(self.max_abs_z, field="max_abs_z")


@dataclass(frozen=True, slots=True)
class TrendEstimate:
    z_score: Decimal | None
    score: Decimal | None
    ready: bool

    def __post_init__(self) -> None:
        if self.z_score is not None and not self.z_score.is_finite():
            raise ValueError("z_score must be finite when available")
        if self.score is not None:
            if not self.score.is_finite() or not -_ONE <= self.score <= _ONE:
                raise ValueError("score must be finite and within [-1, 1]")
        available = self.z_score is not None and self.score is not None
        if self.ready != available:
            raise ValueError("ready must reflect score availability")


def estimate_normalized_trend(
    prices: tuple[Decimal, ...],
    volatility: VolatilityEstimate,
    config: TrendCalibrationConfig,
) -> TrendEstimate:
    if len(prices) < config.horizon + 1 or not volatility.ready or volatility.scale is None:
        return TrendEstimate(z_score=None, score=None, ready=False)

    for price in prices:
        _require_finite_positive(price, field="price")

    horizon_return = (prices[-1] / prices[-(config.horizon + 1)]).ln()
    volatility_scale = max(volatility.scale, config.min_volatility_scale)
    denominator = volatility_scale * Decimal(config.horizon).sqrt()
    raw_z = horizon_return / denominator
    z_score = _clip(raw_z, -config.max_abs_z, config.max_abs_z)
    score = _decimal_tanh(config.transform_gain * z_score)
    return TrendEstimate(z_score=z_score, score=score, ready=True)


__all__ = ["TrendCalibrationConfig", "TrendEstimate", "estimate_normalized_trend"]
