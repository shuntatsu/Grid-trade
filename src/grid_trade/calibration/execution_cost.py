import decimal
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from grid_trade.calibration.microstructure_contracts import MarkoutSide, MaturedMarkout
from grid_trade.domain.numeric import deterministic_decimal_context

_ZERO = Decimal(0)
_ONE = Decimal(1)
_TWO = Decimal(2)


def _require_aware(timestamp: datetime, *, field: str) -> None:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _require_finite(value: Decimal, *, field: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{field} must be a finite Decimal")


def _require_finite_positive(value: Decimal, *, field: str) -> None:
    _require_finite(value, field=field)
    if value <= 0:
        raise ValueError(f"{field} must be positive")


def _require_finite_non_negative(value: Decimal, *, field: str) -> None:
    _require_finite(value, field=field)
    if value < 0:
        raise ValueError(f"{field} must be non-negative")


@dataclass(frozen=True, slots=True)
class ExecutionCostConfig:
    markout_window: int
    min_markout_samples: int
    adverse_quantile: Decimal
    uncertainty_buffer: Decimal
    fallback_adverse_cost: Decimal

    def __post_init__(self) -> None:
        if self.markout_window <= 0:
            raise ValueError("markout_window must be positive")
        if self.min_markout_samples <= 0 or self.min_markout_samples > self.markout_window:
            raise ValueError("min_markout_samples must be within [1, markout_window]")
        _require_finite_positive(self.adverse_quantile, field="adverse_quantile")
        if self.adverse_quantile > _ONE:
            raise ValueError("adverse_quantile must be within (0, 1]")
        _require_finite_non_negative(self.uncertainty_buffer, field="uncertainty_buffer")
        _require_finite_non_negative(
            self.fallback_adverse_cost,
            field="fallback_adverse_cost",
        )


@dataclass(frozen=True, slots=True)
class ExecutionCostEstimate:
    execution_cost_floor: Decimal
    adverse_cost: Decimal
    round_trip_fee: Decimal
    tick_floor: Decimal
    markout_ready: bool
    used_fallback: bool
    sample_count: int

    def __post_init__(self) -> None:
        if self.sample_count < 0:
            raise ValueError("sample_count must be non-negative")
        _require_finite_non_negative(
            self.execution_cost_floor,
            field="execution_cost_floor",
        )
        _require_finite_non_negative(self.adverse_cost, field="adverse_cost")
        _require_finite(self.round_trip_fee, field="round_trip_fee")
        _require_finite_non_negative(self.tick_floor, field="tick_floor")
        if self.markout_ready == self.used_fallback:
            raise ValueError("markout_ready and used_fallback must be logical opposites")


def relative_adverse_markout(markout: MaturedMarkout) -> Decimal:
    with deterministic_decimal_context():
        if markout.side is MarkoutSide.BUY:
            adverse = (markout.fill_price - markout.mark_price) / markout.fill_price
        else:
            adverse = (markout.mark_price - markout.fill_price) / markout.fill_price
        return max(_ZERO, adverse)


def _markout_sort_key(
    markout: MaturedMarkout,
) -> tuple[datetime, datetime, str, Decimal, Decimal]:
    return (
        markout.matured_at,
        markout.fill_timestamp,
        markout.side.value,
        markout.fill_price,
        markout.mark_price,
    )


def _nearest_rank_upper_quantile(
    values: tuple[Decimal, ...],
    quantile: Decimal,
) -> Decimal:
    if not values:
        raise ValueError("values must be non-empty")
    ordered = tuple(sorted(values))
    with deterministic_decimal_context():
        rank_decimal = quantile * Decimal(len(ordered))
        rank = int(rank_decimal.to_integral_value(rounding=decimal.ROUND_CEILING))
    return ordered[max(1, rank) - 1]


def estimate_execution_cost(
    markouts: tuple[MaturedMarkout, ...],
    *,
    decision_time: datetime,
    maker_fee_rate: Decimal,
    tick_size: Decimal,
    current_mid: Decimal,
    config: ExecutionCostConfig,
) -> ExecutionCostEstimate:
    _require_aware(decision_time, field="decision_time")
    _require_finite(maker_fee_rate, field="maker_fee_rate")
    _require_finite_positive(tick_size, field="tick_size")
    _require_finite_positive(current_mid, field="current_mid")

    matured = sorted(
        (markout for markout in markouts if markout.matured_at <= decision_time),
        key=_markout_sort_key,
    )
    selected = tuple(matured[-config.markout_window :])
    sample_count = len(selected)
    markout_ready = sample_count >= config.min_markout_samples
    used_fallback = not markout_ready

    if markout_ready:
        adverse_cost = _nearest_rank_upper_quantile(
            tuple(relative_adverse_markout(markout) for markout in selected),
            config.adverse_quantile,
        )
    else:
        adverse_cost = config.fallback_adverse_cost

    with deterministic_decimal_context():
        round_trip_fee = _TWO * maker_fee_rate
        tick_floor = tick_size / current_mid
        modeled_floor = round_trip_fee + adverse_cost + config.uncertainty_buffer
        execution_cost_floor = max(_ZERO, tick_floor, modeled_floor)

    return ExecutionCostEstimate(
        execution_cost_floor=execution_cost_floor,
        adverse_cost=adverse_cost,
        round_trip_fee=round_trip_fee,
        tick_floor=tick_floor,
        markout_ready=markout_ready,
        used_fallback=used_fallback,
        sample_count=sample_count,
    )


__all__ = [
    "ExecutionCostConfig",
    "ExecutionCostEstimate",
    "estimate_execution_cost",
    "relative_adverse_markout",
]
