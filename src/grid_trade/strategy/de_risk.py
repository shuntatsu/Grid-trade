from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

_ZERO = Decimal(0)
_ONE = Decimal(1)


def _require_finite(value: Decimal, *, field: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{field} must be a finite Decimal")


class DeRiskRegime(StrEnum):
    HEALTHY = "healthy"
    WARNING = "warning"
    SEVERE = "severe"


@dataclass(frozen=True, slots=True)
class DeRiskConfig:
    warning_trend_threshold: Decimal
    severe_trend_threshold: Decimal
    warning_target_fraction: Decimal
    severe_target_fraction: Decimal

    def __post_init__(self) -> None:
        for field_name in (
            "warning_trend_threshold",
            "severe_trend_threshold",
            "warning_target_fraction",
            "severe_target_fraction",
        ):
            _require_finite(getattr(self, field_name), field=field_name)
        if not Decimal("-1") <= self.severe_trend_threshold < self.warning_trend_threshold < _ZERO:
            raise ValueError("trend thresholds must satisfy -1 <= severe < warning < 0")
        if not _ZERO <= self.severe_target_fraction <= self.warning_target_fraction <= _ONE:
            raise ValueError("target fractions must satisfy 0 <= severe <= warning <= 1")


@dataclass(frozen=True, slots=True)
class DeRiskDecision:
    requested_target: Decimal
    effective_target: Decimal
    regime: DeRiskRegime


def apply_partial_de_risk(
    long_target: Decimal,
    trend_score: Decimal,
    config: DeRiskConfig,
) -> DeRiskDecision:
    _require_finite(long_target, field="long_target")
    _require_finite(trend_score, field="trend_score")
    if long_target < 0:
        raise ValueError("long_target must be non-negative")
    if not Decimal("-1") <= trend_score <= _ONE:
        raise ValueError("trend_score must be within [-1, 1]")

    if trend_score <= config.severe_trend_threshold:
        regime = DeRiskRegime.SEVERE
        fraction = config.severe_target_fraction
    elif trend_score <= config.warning_trend_threshold:
        regime = DeRiskRegime.WARNING
        fraction = config.warning_target_fraction
    else:
        regime = DeRiskRegime.HEALTHY
        fraction = _ONE

    return DeRiskDecision(
        requested_target=long_target,
        effective_target=long_target * fraction,
        regime=regime,
    )


__all__ = ["DeRiskConfig", "DeRiskDecision", "DeRiskRegime", "apply_partial_de_risk"]
