from dataclasses import dataclass
from decimal import Decimal

from grid_trade.strategy.conditional_short import enforce_flat_before_reverse

_ZERO = Decimal(0)
_ONE = Decimal(1)


def _require_finite(value: Decimal, *, field: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{field} must be a finite Decimal")


def _clip(value: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    return min(upper, max(lower, value))


@dataclass(frozen=True, slots=True)
class FundingBiasConfig:
    funding_scale: Decimal
    max_abs_target: Decimal
    max_target_shift_fraction: Decimal

    def __post_init__(self) -> None:
        _require_finite(self.funding_scale, field="funding_scale")
        _require_finite(self.max_abs_target, field="max_abs_target")
        _require_finite(
            self.max_target_shift_fraction,
            field="max_target_shift_fraction",
        )
        if self.funding_scale <= 0:
            raise ValueError("funding_scale must be positive")
        if self.max_abs_target <= 0:
            raise ValueError("max_abs_target must be positive")
        if not _ZERO <= self.max_target_shift_fraction <= _ONE:
            raise ValueError("max_target_shift_fraction must be within [0, 1]")


@dataclass(frozen=True, slots=True)
class FundingBiasDecision:
    normalized_funding: Decimal
    target_shift: Decimal
    requested_target: Decimal
    effective_target: Decimal


def apply_funding_bias(
    *,
    target: Decimal,
    position: Decimal,
    funding_rate: Decimal,
    config: FundingBiasConfig,
) -> FundingBiasDecision:
    _require_finite(target, field="target")
    _require_finite(position, field="position")
    _require_finite(funding_rate, field="funding_rate")
    if abs(target) > config.max_abs_target:
        raise ValueError("target must be within max_abs_target")

    normalized_funding = _clip(
        funding_rate / config.funding_scale,
        -_ONE,
        _ONE,
    )
    target_shift = (
        -normalized_funding
        * config.max_abs_target
        * config.max_target_shift_fraction
    )
    requested_target = _clip(
        target + target_shift,
        -config.max_abs_target,
        config.max_abs_target,
    )
    effective_target = enforce_flat_before_reverse(position, requested_target)

    return FundingBiasDecision(
        normalized_funding=normalized_funding,
        target_shift=target_shift,
        requested_target=requested_target,
        effective_target=effective_target,
    )


__all__ = ["FundingBiasConfig", "FundingBiasDecision", "apply_funding_bias"]
