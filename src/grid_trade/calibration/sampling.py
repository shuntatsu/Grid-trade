from dataclasses import dataclass
from datetime import datetime

from grid_trade.calibration.microstructure_contracts import MaturedMarkout, OfiImpactSample


def _require_aware(value: datetime, *, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _elapsed_microseconds(start: datetime, end: datetime, *, field: str) -> int:
    _require_aware(start, field=f"{field} start")
    _require_aware(end, field=f"{field} end")
    delta = end - start
    if delta.total_seconds() < 0:
        raise ValueError(f"{field} end must not precede start")
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


@dataclass(frozen=True, slots=True)
class SamplingSpec:
    observation_interval_ms: int
    interval_tolerance_ms: int
    volatility_window_ms: int
    trend_horizon_ms: int
    markout_horizon_ms: int
    ofi_horizon_ms: int

    def __post_init__(self) -> None:
        for field_name in (
            "observation_interval_ms",
            "volatility_window_ms",
            "trend_horizon_ms",
            "markout_horizon_ms",
            "ofi_horizon_ms",
        ):
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} must be positive")
        if self.interval_tolerance_ms < 0:
            raise ValueError("interval_tolerance_ms must be non-negative")
        if self.interval_tolerance_ms >= self.observation_interval_ms:
            raise ValueError("interval_tolerance_ms must be smaller than observation_interval_ms")

    def validate_engine_counts(
        self,
        *,
        volatility_window: int,
        trend_horizon: int,
    ) -> None:
        if volatility_window * self.observation_interval_ms != self.volatility_window_ms:
            raise ValueError("volatility window count does not match SamplingSpec elapsed time")
        if trend_horizon * self.observation_interval_ms != self.trend_horizon_ms:
            raise ValueError("trend horizon count does not match SamplingSpec elapsed time")

    def _validate_elapsed(
        self,
        *,
        start: datetime,
        end: datetime,
        expected_ms: int,
        field: str,
    ) -> None:
        elapsed_us = _elapsed_microseconds(start, end, field=field)
        expected_us = expected_ms * 1_000
        tolerance_us = self.interval_tolerance_ms * 1_000
        if abs(elapsed_us - expected_us) > tolerance_us:
            raise ValueError(
                f"{field} mismatch: expected {expected_ms} ms "
                f"within ±{self.interval_tolerance_ms} ms"
            )

    def validate_observation_delta(self, previous: datetime, current: datetime) -> None:
        if current <= previous:
            raise ValueError("observation timestamp must be strictly newer")
        self._validate_elapsed(
            start=previous,
            end=current,
            expected_ms=self.observation_interval_ms,
            field="observation cadence",
        )

    def validate_markout(self, markout: MaturedMarkout) -> None:
        self._validate_elapsed(
            start=markout.fill_timestamp,
            end=markout.matured_at,
            expected_ms=self.markout_horizon_ms,
            field="markout horizon",
        )

    def validate_ofi_sample(self, sample: OfiImpactSample) -> None:
        self._validate_elapsed(
            start=sample.feature_timestamp,
            end=sample.matured_at,
            expected_ms=self.ofi_horizon_ms,
            field="OFI horizon",
        )


__all__ = ["SamplingSpec"]
