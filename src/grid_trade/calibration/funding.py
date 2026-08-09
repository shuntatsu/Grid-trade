from dataclasses import dataclass
from decimal import Decimal

from grid_trade.calibration._robust import mad_decimal, median_decimal

_ONE = Decimal(1)


def _require_finite_positive(value: Decimal, *, field: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"{field} must be a finite positive Decimal")


def _clip(value: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    return min(upper, max(lower, value))


@dataclass(frozen=True, slots=True)
class FundingCalibrationConfig:
    window: int
    min_samples: int
    mad_scale: Decimal
    clip_z: Decimal

    def __post_init__(self) -> None:
        if self.window <= 0:
            raise ValueError("window must be positive")
        if not 1 <= self.min_samples <= self.window:
            raise ValueError("min_samples must be within [1, window]")
        _require_finite_positive(self.mad_scale, field="mad_scale")
        _require_finite_positive(self.clip_z, field="clip_z")


@dataclass(frozen=True, slots=True)
class FundingCalibrationState:
    values: tuple[Decimal, ...] = ()

    def __post_init__(self) -> None:
        for value in self.values:
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError("funding state requires finite Decimal values")


@dataclass(frozen=True, slots=True)
class FundingEstimate:
    center: Decimal | None
    scale: Decimal | None
    z_score: Decimal | None
    score: Decimal | None
    ready: bool
    degenerate: bool

    def __post_init__(self) -> None:
        for field_name in ("center", "scale", "z_score", "score"):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, Decimal) or not value.is_finite()):
                raise ValueError(f"{field_name} must be a finite Decimal when available")
        if self.scale is not None and self.scale < 0:
            raise ValueError("scale must be non-negative")
        if self.score is not None and not -_ONE <= self.score <= _ONE:
            raise ValueError("score must be within [-1, 1]")
        available = all(value is not None for value in (self.center, self.scale, self.z_score, self.score))
        if self.ready != available:
            raise ValueError("ready must reflect normalized funding availability")
        if self.degenerate and self.ready:
            raise ValueError("degenerate funding cannot be ready")

    @classmethod
    def unavailable(cls, *, degenerate: bool = False) -> "FundingEstimate":
        return cls(
            center=None,
            scale=None,
            z_score=None,
            score=None,
            ready=False,
            degenerate=degenerate,
        )


def update_funding_calibration(
    state: FundingCalibrationState,
    funding_rate: Decimal | None,
    config: FundingCalibrationConfig,
) -> tuple[FundingCalibrationState, FundingEstimate]:
    if funding_rate is None:
        return state, FundingEstimate.unavailable()
    if not isinstance(funding_rate, Decimal) or not funding_rate.is_finite():
        raise ValueError("funding_rate must be a finite Decimal when available")

    values = (*state.values, funding_rate)[-config.window :]
    next_state = FundingCalibrationState(values=values)
    if len(values) < config.min_samples:
        return next_state, FundingEstimate.unavailable()

    center = median_decimal(values)
    scale = config.mad_scale * mad_decimal(values, center=center)
    if scale == 0:
        return next_state, FundingEstimate(
            center=center,
            scale=scale,
            z_score=None,
            score=None,
            ready=False,
            degenerate=True,
        )

    raw_z = (funding_rate - center) / scale
    z_score = _clip(raw_z, -config.clip_z, config.clip_z)
    score = z_score / config.clip_z
    return next_state, FundingEstimate(
        center=center,
        scale=scale,
        z_score=z_score,
        score=score,
        ready=True,
        degenerate=False,
    )


__all__ = [
    "FundingCalibrationConfig",
    "FundingCalibrationState",
    "FundingEstimate",
    "update_funding_calibration",
]
