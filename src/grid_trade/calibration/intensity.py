from dataclasses import dataclass
from decimal import Decimal

from grid_trade.calibration.microstructure_contracts import IntensityBucket
from grid_trade.domain.numeric import deterministic_decimal_context

_ZERO = Decimal(0)
_ONE = Decimal(1)


def _require_finite_non_negative(value: Decimal, *, field: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{field} must be a finite non-negative Decimal")


def _require_finite_positive(value: Decimal, *, field: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"{field} must be a finite positive Decimal")


def _clip01(value: Decimal) -> Decimal:
    return min(_ONE, max(_ZERO, value))


@dataclass(frozen=True, slots=True)
class IntensityCalibrationConfig:
    min_buckets: int
    min_total_arrivals: int
    k_min: Decimal
    k_max: Decimal
    k_steps: int
    min_log_likelihood_improvement: Decimal

    def __post_init__(self) -> None:
        if self.min_buckets < 2:
            raise ValueError("min_buckets must be at least 2")
        if self.min_total_arrivals <= 0:
            raise ValueError("min_total_arrivals must be positive")
        _require_finite_positive(self.k_min, field="k_min")
        _require_finite_positive(self.k_max, field="k_max")
        if self.k_max <= self.k_min:
            raise ValueError("k_max must be greater than k_min")
        if self.k_steps < 2:
            raise ValueError("k_steps must be at least 2")
        _require_finite_non_negative(
            self.min_log_likelihood_improvement,
            field="min_log_likelihood_improvement",
        )


@dataclass(frozen=True, slots=True)
class IntensityEstimate:
    A: Decimal | None
    k: Decimal | None
    e_fold_distance_vol_units: Decimal | None
    log_likelihood_improvement: Decimal | None
    quality: Decimal | None
    sample_count: int
    total_arrivals: int
    ready: bool

    def __post_init__(self) -> None:
        if self.sample_count < 0:
            raise ValueError("sample_count must be non-negative")
        if self.total_arrivals < 0:
            raise ValueError("total_arrivals must be non-negative")
        for field_name in (
            "A",
            "k",
            "e_fold_distance_vol_units",
            "log_likelihood_improvement",
            "quality",
        ):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, Decimal) or not value.is_finite()):
                raise ValueError(f"{field_name} must be a finite Decimal when available")
        for field_name in ("A", "k", "e_fold_distance_vol_units"):
            value = getattr(self, field_name)
            if value is not None and value <= 0:
                raise ValueError(f"{field_name} must be positive when available")
        if self.quality is not None and not _ZERO <= self.quality <= _ONE:
            raise ValueError("quality must be within [0, 1]")
        if self.ready and any(
            value is None
            for value in (
                self.A,
                self.k,
                self.e_fold_distance_vol_units,
                self.log_likelihood_improvement,
                self.quality,
            )
        ):
            raise ValueError("ready intensity estimate requires all fitted fields")

    @classmethod
    def not_ready(
        cls,
        *,
        sample_count: int,
        total_arrivals: int,
        A: Decimal | None = None,
        k: Decimal | None = None,
        e_fold_distance_vol_units: Decimal | None = None,
        log_likelihood_improvement: Decimal | None = None,
        quality: Decimal | None = None,
    ) -> "IntensityEstimate":
        return cls(
            A=A,
            k=k,
            e_fold_distance_vol_units=e_fold_distance_vol_units,
            log_likelihood_improvement=log_likelihood_improvement,
            quality=quality,
            sample_count=sample_count,
            total_arrivals=total_arrivals,
            ready=False,
        )


def _candidate_k_values(config: IntensityCalibrationConfig) -> tuple[Decimal, ...]:
    with deterministic_decimal_context():
        step = (config.k_max - config.k_min) / Decimal(config.k_steps - 1)
        return tuple(config.k_min + step * index for index in range(config.k_steps))


def _profile_A(
    buckets: tuple[IntensityBucket, ...],
    *,
    k: Decimal,
    total_arrivals: int,
) -> Decimal:
    with deterministic_decimal_context():
        weighted_exposure = sum(
            (
                bucket.exposure_seconds * (-k * bucket.distance_vol_units).exp()
                for bucket in buckets
            ),
            start=_ZERO,
        )
        if weighted_exposure <= 0:
            raise ValueError("weighted exposure must remain positive")
        return Decimal(total_arrivals) / weighted_exposure


def _poisson_log_likelihood(
    buckets: tuple[IntensityBucket, ...],
    *,
    A: Decimal,
    k: Decimal,
) -> Decimal:
    with deterministic_decimal_context():
        total = _ZERO
        for bucket in buckets:
            mu = bucket.exposure_seconds * A * (-k * bucket.distance_vol_units).exp()
            if mu <= 0:
                raise ValueError("Poisson mean must remain positive")
            total -= mu
            if bucket.arrival_count:
                total += Decimal(bucket.arrival_count) * mu.ln()
        return total


def _constant_log_likelihood(
    buckets: tuple[IntensityBucket, ...],
    *,
    total_arrivals: int,
) -> Decimal:
    with deterministic_decimal_context():
        exposure = sum((bucket.exposure_seconds for bucket in buckets), start=_ZERO)
        A = Decimal(total_arrivals) / exposure
        return _poisson_log_likelihood(buckets, A=A, k=_ZERO)


def estimate_arrival_intensity(
    buckets: tuple[IntensityBucket, ...],
    config: IntensityCalibrationConfig,
) -> IntensityEstimate:
    sample_count = len(buckets)
    total_arrivals = sum(bucket.arrival_count for bucket in buckets)
    if sample_count < config.min_buckets or total_arrivals < config.min_total_arrivals:
        return IntensityEstimate.not_ready(
            sample_count=sample_count,
            total_arrivals=total_arrivals,
        )

    best_k: Decimal | None = None
    best_A: Decimal | None = None
    best_log_likelihood: Decimal | None = None
    for candidate_k in _candidate_k_values(config):
        candidate_A = _profile_A(
            buckets,
            k=candidate_k,
            total_arrivals=total_arrivals,
        )
        log_likelihood = _poisson_log_likelihood(
            buckets,
            A=candidate_A,
            k=candidate_k,
        )
        if best_log_likelihood is None or log_likelihood > best_log_likelihood:
            best_k = candidate_k
            best_A = candidate_A
            best_log_likelihood = log_likelihood

    if best_k is None or best_A is None or best_log_likelihood is None:
        return IntensityEstimate.not_ready(
            sample_count=sample_count,
            total_arrivals=total_arrivals,
        )

    null_log_likelihood = _constant_log_likelihood(
        buckets,
        total_arrivals=total_arrivals,
    )
    with deterministic_decimal_context():
        improvement = best_log_likelihood - null_log_likelihood
        e_fold_distance = _ONE / best_k
        positive_improvement = max(_ZERO, improvement)
        quality = _clip01(
            positive_improvement / (positive_improvement + Decimal(total_arrivals))
            if positive_improvement > 0
            else _ZERO
        )

    ready = improvement >= config.min_log_likelihood_improvement
    if not ready:
        return IntensityEstimate.not_ready(
            sample_count=sample_count,
            total_arrivals=total_arrivals,
            A=best_A,
            k=best_k,
            e_fold_distance_vol_units=e_fold_distance,
            log_likelihood_improvement=improvement,
            quality=quality,
        )

    return IntensityEstimate(
        A=best_A,
        k=best_k,
        e_fold_distance_vol_units=e_fold_distance,
        log_likelihood_improvement=improvement,
        quality=quality,
        sample_count=sample_count,
        total_arrivals=total_arrivals,
        ready=True,
    )


__all__ = [
    "IntensityCalibrationConfig",
    "IntensityEstimate",
    "estimate_arrival_intensity",
]
