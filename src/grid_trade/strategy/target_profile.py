from dataclasses import dataclass
from decimal import Decimal

from grid_trade.strategy.conditional_short import (
    ShortOverlayDecision,
    ShortPhase,
    enforce_flat_before_reverse,
)
from grid_trade.strategy.de_risk import DeRiskConfig, DeRiskDecision, DeRiskRegime

_ZERO = Decimal(0)
_ONE = Decimal(1)
_NEGATIVE_ONE = Decimal(-1)


def _require_finite(value: Decimal, *, field: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{field} must be a finite Decimal")


def _require_fraction(value: Decimal, *, field: str, signed: bool) -> None:
    _require_finite(value, field=field)
    lower = _NEGATIVE_ONE if signed else _ZERO
    if not lower <= value <= _ONE:
        interval = "[-1, 1]" if signed else "[0, 1]"
        raise ValueError(f"{field} must be within {interval}")


def _require_trend_score(value: Decimal) -> None:
    _require_finite(value, field="trend_score")
    if not _NEGATIVE_ONE <= value <= _ONE:
        raise ValueError("trend_score must be within [-1, 1]")


def _preferred_sign(profile: "DirectionalTargetProfileConfig") -> Decimal:
    if profile.baseline_target_fraction > 0:
        return _ONE
    if profile.baseline_target_fraction < 0:
        return -_ONE
    return _ZERO


def _clip(value: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    return min(upper, max(lower, value))


@dataclass(frozen=True, slots=True)
class DirectionalTargetProfileConfig:
    baseline_target_fraction: Decimal
    allow_opposite: bool
    opposite_entry_aligned_trend_threshold: Decimal
    max_opposite_target_fraction: Decimal

    def __post_init__(self) -> None:
        _require_fraction(
            self.baseline_target_fraction,
            field="baseline_target_fraction",
            signed=True,
        )
        if type(self.allow_opposite) is not bool:
            raise ValueError("allow_opposite must be a bool")
        _require_finite(
            self.opposite_entry_aligned_trend_threshold,
            field="opposite_entry_aligned_trend_threshold",
        )
        if not _NEGATIVE_ONE < self.opposite_entry_aligned_trend_threshold < _ZERO:
            raise ValueError("opposite_entry_aligned_trend_threshold must be within (-1, 0)")
        _require_fraction(
            self.max_opposite_target_fraction,
            field="max_opposite_target_fraction",
            signed=False,
        )
        if self.allow_opposite and self.baseline_target_fraction != 0:
            if self.max_opposite_target_fraction <= 0:
                raise ValueError(
                    "max_opposite_target_fraction must be positive when "
                    "opposite targets are allowed"
                )
        elif self.max_opposite_target_fraction != 0:
            raise ValueError(
                "max_opposite_target_fraction must be zero when opposite targets "
                "are disabled or baseline is flat"
            )

    def baseline_target(self, max_abs_target: Decimal) -> Decimal:
        _require_finite(max_abs_target, field="max_abs_target")
        if max_abs_target <= 0:
            raise ValueError("max_abs_target must be positive")
        return self.baseline_target_fraction * max_abs_target

    def max_opposite_target(self, max_abs_target: Decimal) -> Decimal:
        _require_finite(max_abs_target, field="max_abs_target")
        if max_abs_target <= 0:
            raise ValueError("max_abs_target must be positive")
        return self.max_opposite_target_fraction * max_abs_target


def apply_directional_de_risk(
    *,
    profile: DirectionalTargetProfileConfig,
    max_abs_target: Decimal,
    trend_score: Decimal,
    config: DeRiskConfig,
) -> DeRiskDecision:
    _require_trend_score(trend_score)
    baseline = profile.baseline_target(max_abs_target)
    preferred_sign = _preferred_sign(profile)
    if preferred_sign == 0:
        return DeRiskDecision(
            requested_target=_ZERO,
            effective_target=_ZERO,
            regime=DeRiskRegime.HEALTHY,
        )

    aligned_trend = preferred_sign * trend_score
    if aligned_trend <= config.severe_trend_threshold:
        regime = DeRiskRegime.SEVERE
        fraction = config.severe_target_fraction
    elif aligned_trend <= config.warning_trend_threshold:
        regime = DeRiskRegime.WARNING
        fraction = config.warning_target_fraction
    else:
        regime = DeRiskRegime.HEALTHY
        fraction = _ONE

    return DeRiskDecision(
        requested_target=baseline,
        effective_target=baseline * fraction,
        regime=regime,
    )


def _phase(
    *,
    position: Decimal,
    requested_target: Decimal,
    effective_target: Decimal,
) -> ShortPhase:
    if position > 0 and requested_target < 0:
        return ShortPhase.FLATTEN_LONG
    if position < 0 and requested_target > 0:
        return ShortPhase.FLATTEN_SHORT
    if effective_target < 0:
        return ShortPhase.SHORT
    if effective_target > 0:
        return ShortPhase.LONG
    return ShortPhase.FLAT


def apply_conditional_reversal(
    *,
    target: Decimal,
    position: Decimal,
    trend_score: Decimal,
    profile: DirectionalTargetProfileConfig,
    max_abs_target: Decimal,
) -> ShortOverlayDecision:
    for value, field in (
        (target, "target"),
        (position, "position"),
        (max_abs_target, "max_abs_target"),
    ):
        _require_finite(value, field=field)
    if max_abs_target <= 0:
        raise ValueError("max_abs_target must be positive")
    if abs(target) > max_abs_target:
        raise ValueError("target must be within max_abs_target")
    _require_trend_score(trend_score)

    preferred_sign = _preferred_sign(profile)
    if preferred_sign == 0 or not profile.allow_opposite:
        adverse_severity = _ZERO
        requested_target = target
    else:
        aligned_trend = preferred_sign * trend_score
        threshold = profile.opposite_entry_aligned_trend_threshold
        if aligned_trend <= threshold:
            denominator = _ONE - abs(threshold)
            adverse_severity = _clip(
                (abs(aligned_trend) - abs(threshold)) / denominator,
                _ZERO,
                _ONE,
            )
            requested_target = (
                -preferred_sign * profile.max_opposite_target(max_abs_target) * adverse_severity
            )
        else:
            adverse_severity = _ZERO
            requested_target = target

    effective_target = enforce_flat_before_reverse(position, requested_target)
    return ShortOverlayDecision(
        requested_target=requested_target,
        effective_target=effective_target,
        phase=_phase(
            position=position,
            requested_target=requested_target,
            effective_target=effective_target,
        ),
        bearish_severity=adverse_severity,
    )


__all__ = [
    "DirectionalTargetProfileConfig",
    "apply_conditional_reversal",
    "apply_directional_de_risk",
]
