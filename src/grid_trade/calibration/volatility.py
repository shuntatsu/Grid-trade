from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise

from grid_trade.calibration.contracts import CalibrationObservation


def _require_finite_positive(value: Decimal, *, field: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"{field} must be a finite positive Decimal")


def _median(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        raise ValueError("median requires at least one value")
    ordered = tuple(sorted(values))
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / Decimal(2)


@dataclass(frozen=True, slots=True)
class RobustVolatilityConfig:
    window: int
    min_samples: int
    mad_scale: Decimal

    def __post_init__(self) -> None:
        if self.window <= 0:
            raise ValueError("window must be positive")
        if not 1 <= self.min_samples <= self.window:
            raise ValueError("min_samples must be within [1, window]")
        _require_finite_positive(self.mad_scale, field="mad_scale")


@dataclass(frozen=True, slots=True)
class RobustVolatilityState:
    prices: tuple[Decimal, ...] = ()

    def __post_init__(self) -> None:
        for price in self.prices:
            _require_finite_positive(price, field="price")


@dataclass(frozen=True, slots=True)
class VolatilityEstimate:
    scale: Decimal | None
    sample_count: int
    ready: bool

    def __post_init__(self) -> None:
        if self.sample_count < 0:
            raise ValueError("sample_count must be non-negative")
        if self.scale is not None and (not self.scale.is_finite() or self.scale < 0):
            raise ValueError("scale must be finite and non-negative")
        if self.ready != (self.scale is not None):
            raise ValueError("ready must reflect scale availability")


def update_robust_volatility(
    state: RobustVolatilityState,
    observation: CalibrationObservation,
    config: RobustVolatilityConfig,
) -> tuple[RobustVolatilityState, VolatilityEstimate]:
    prices = (*state.prices, observation.mid)[-(config.window + 1) :]
    next_state = RobustVolatilityState(prices=prices)

    returns = tuple((current / previous).ln() for previous, current in pairwise(prices))
    sample_count = len(returns)
    if sample_count < config.min_samples:
        return next_state, VolatilityEstimate(scale=None, sample_count=sample_count, ready=False)

    center = _median(returns)
    deviations = tuple(abs(value - center) for value in returns)
    mad = _median(deviations)
    scale = config.mad_scale * mad
    return next_state, VolatilityEstimate(scale=scale, sample_count=sample_count, ready=True)


__all__ = [
    "RobustVolatilityConfig",
    "RobustVolatilityState",
    "VolatilityEstimate",
    "update_robust_volatility",
]
