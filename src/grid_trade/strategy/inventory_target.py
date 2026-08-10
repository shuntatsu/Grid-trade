from dataclasses import dataclass
from decimal import Decimal

_ONE = Decimal(1)
_ZERO = Decimal(0)


def _require_finite(value: Decimal, *, field: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{field} must be a finite Decimal")


def _clip(value: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    return min(upper, max(lower, value))


@dataclass(frozen=True, slots=True)
class InventoryTargetConfig:
    base_long_target: Decimal
    max_abs_target: Decimal
    reservation_skew_bps: Decimal
    side_skew_strength: Decimal

    def __post_init__(self) -> None:
        for field_name in (
            "base_long_target",
            "max_abs_target",
            "reservation_skew_bps",
            "side_skew_strength",
        ):
            _require_finite(getattr(self, field_name), field=field_name)
        if self.base_long_target < 0:
            raise ValueError("base_long_target must be non-negative")
        if self.max_abs_target <= 0:
            raise ValueError("max_abs_target must be positive")
        if self.base_long_target > self.max_abs_target:
            raise ValueError("base_long_target must not exceed max_abs_target")
        if self.reservation_skew_bps < 0:
            raise ValueError("reservation_skew_bps must be non-negative")
        if not _ZERO <= self.side_skew_strength <= _ONE:
            raise ValueError("side_skew_strength must be within [0, 1]")


@dataclass(frozen=True, slots=True)
class InventoryTargetDecision:
    target: Decimal
    normalized_inventory_error: Decimal
    reservation_shift_bps: Decimal
    bid_scale: Decimal
    ask_scale: Decimal

    def __post_init__(self) -> None:
        for field_name in (
            "target",
            "normalized_inventory_error",
            "reservation_shift_bps",
            "bid_scale",
            "ask_scale",
        ):
            _require_finite(getattr(self, field_name), field=field_name)
        if not _ZERO <= self.bid_scale <= _ONE:
            raise ValueError("bid_scale must be within [0, 1]")
        if not _ZERO <= self.ask_scale <= _ONE:
            raise ValueError("ask_scale must be within [0, 1]")


def decide_inventory_target(
    *,
    position: Decimal,
    target: Decimal,
    config: InventoryTargetConfig,
    enabled: bool = True,
) -> InventoryTargetDecision:
    _require_finite(position, field="position")
    _require_finite(target, field="target")
    if abs(target) > config.max_abs_target:
        raise ValueError("target must be within max_abs_target")
    if type(enabled) is not bool:
        raise ValueError("enabled must be a bool")

    normalized_error = (position - target) / config.max_abs_target
    if not enabled:
        return InventoryTargetDecision(
            target=target,
            normalized_inventory_error=normalized_error,
            reservation_shift_bps=_ZERO,
            bid_scale=_ONE,
            ask_scale=_ONE,
        )
    clipped_error = _clip(normalized_error, -_ONE, _ONE)
    reservation_shift = -config.reservation_skew_bps * clipped_error

    if normalized_error > 0:
        bid_scale = max(
            _ZERO,
            _ONE - config.side_skew_strength * min(normalized_error, _ONE),
        )
        ask_scale = _ONE
    elif normalized_error < 0:
        bid_scale = _ONE
        ask_scale = max(
            _ZERO,
            _ONE - config.side_skew_strength * min(-normalized_error, _ONE),
        )
    else:
        bid_scale = _ONE
        ask_scale = _ONE

    return InventoryTargetDecision(
        target=target,
        normalized_inventory_error=normalized_error,
        reservation_shift_bps=reservation_shift,
        bid_scale=bid_scale,
        ask_scale=ask_scale,
    )


__all__ = [
    "InventoryTargetConfig",
    "InventoryTargetDecision",
    "decide_inventory_target",
]
