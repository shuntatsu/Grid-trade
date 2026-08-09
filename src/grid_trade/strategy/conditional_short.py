from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

_ZERO = Decimal(0)
_ONE = Decimal(1)


def _require_finite(value: Decimal, *, field: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{field} must be a finite Decimal")


def _clip(value: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    return min(upper, max(lower, value))


class ShortPhase(StrEnum):
    FLAT = "flat"
    LONG = "long"
    FLATTEN_LONG = "flatten_long"
    SHORT = "short"
    FLATTEN_SHORT = "flatten_short"


@dataclass(frozen=True, slots=True)
class ShortOverlayConfig:
    entry_trend_threshold: Decimal
    max_short_target: Decimal

    def __post_init__(self) -> None:
        _require_finite(self.entry_trend_threshold, field="entry_trend_threshold")
        _require_finite(self.max_short_target, field="max_short_target")
        if not Decimal("-1") < self.entry_trend_threshold < _ZERO:
            raise ValueError("entry_trend_threshold must be within (-1, 0)")
        if self.max_short_target <= 0:
            raise ValueError("max_short_target must be positive")


@dataclass(frozen=True, slots=True)
class ShortOverlayDecision:
    requested_target: Decimal
    effective_target: Decimal
    phase: ShortPhase
    bearish_severity: Decimal


def enforce_flat_before_reverse(position: Decimal, requested_target: Decimal) -> Decimal:
    _require_finite(position, field="position")
    _require_finite(requested_target, field="requested_target")
    if position > 0 and requested_target < 0:
        return _ZERO
    if position < 0 and requested_target > 0:
        return _ZERO
    return requested_target


def apply_conditional_short(
    *,
    long_target: Decimal,
    position: Decimal,
    trend_score: Decimal,
    config: ShortOverlayConfig,
) -> ShortOverlayDecision:
    _require_finite(long_target, field="long_target")
    _require_finite(position, field="position")
    _require_finite(trend_score, field="trend_score")
    if long_target < 0:
        raise ValueError("long_target must be non-negative")
    if not Decimal("-1") <= trend_score <= _ONE:
        raise ValueError("trend_score must be within [-1, 1]")

    if trend_score <= config.entry_trend_threshold:
        denominator = _ONE - abs(config.entry_trend_threshold)
        bearish_severity = _clip(
            (abs(trend_score) - abs(config.entry_trend_threshold)) / denominator,
            _ZERO,
            _ONE,
        )
        requested_target = -config.max_short_target * bearish_severity
    else:
        bearish_severity = _ZERO
        requested_target = long_target

    effective_target = enforce_flat_before_reverse(position, requested_target)
    if position > 0 and requested_target < 0:
        phase = ShortPhase.FLATTEN_LONG
    elif position < 0 and requested_target > 0:
        phase = ShortPhase.FLATTEN_SHORT
    elif effective_target < 0:
        phase = ShortPhase.SHORT
    elif effective_target > 0:
        phase = ShortPhase.LONG
    else:
        phase = ShortPhase.FLAT

    return ShortOverlayDecision(
        requested_target=requested_target,
        effective_target=effective_target,
        phase=phase,
        bearish_severity=bearish_severity,
    )


__all__ = [
    "ShortOverlayConfig",
    "ShortOverlayDecision",
    "ShortPhase",
    "apply_conditional_short",
    "enforce_flat_before_reverse",
]
