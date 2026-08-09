from decimal import Decimal, localcontext

import pytest

from grid_trade.calibration.intensity import (
    IntensityCalibrationConfig,
    IntensityEstimate,
    estimate_arrival_intensity,
)
from grid_trade.calibration.microstructure_contracts import IntensityBucket


def _config() -> IntensityCalibrationConfig:
    return IntensityCalibrationConfig(
        min_buckets=4,
        min_total_arrivals=50,
        k_min=Decimal("0.5"),
        k_max=Decimal("1.5"),
        k_steps=21,
        min_log_likelihood_improvement=Decimal("1"),
    )


def _synthetic_buckets() -> tuple[IntensityBucket, ...]:
    return (
        IntensityBucket(Decimal("0"), Decimal("100"), 1000),
        IntensityBucket(Decimal("1"), Decimal("100"), 368),
        IntensityBucket(Decimal("2"), Decimal("100"), 135),
        IntensityBucket(Decimal("3"), Decimal("100"), 50),
    )


def test_poisson_calibration_recovers_known_intensity_shape() -> None:
    estimate = estimate_arrival_intensity(_synthetic_buckets(), _config())

    assert estimate.ready is True
    assert estimate.k == Decimal("1.00")
    assert estimate.A is not None
    assert Decimal("9.9") < estimate.A < Decimal("10.1")
    assert estimate.e_fold_distance_vol_units == Decimal("1")
    assert estimate.log_likelihood_improvement is not None
    assert estimate.log_likelihood_improvement > 0


def test_zero_arrival_tail_remains_informative() -> None:
    base = _synthetic_buckets()
    without_tail = estimate_arrival_intensity(base, _config())
    with_tail = estimate_arrival_intensity(
        base + (IntensityBucket(Decimal("4"), Decimal("500"), 0),),
        _config(),
    )

    assert without_tail.k is not None
    assert with_tail.k is not None
    assert with_tail.k >= without_tail.k


def test_insufficient_evidence_is_not_ready() -> None:
    estimate = estimate_arrival_intensity(
        (
            IntensityBucket(Decimal("0"), Decimal("10"), 2),
            IntensityBucket(Decimal("1"), Decimal("10"), 1),
        ),
        _config(),
    )

    assert estimate == IntensityEstimate.not_ready(sample_count=2, total_arrivals=3)


def test_fit_must_beat_constant_intensity_null() -> None:
    flat = tuple(IntensityBucket(Decimal(index), Decimal("100"), 100) for index in range(4))
    estimate = estimate_arrival_intensity(flat, _config())

    assert estimate.ready is False
    assert estimate.log_likelihood_improvement is not None
    assert estimate.log_likelihood_improvement <= _config().min_log_likelihood_improvement


def test_estimate_is_independent_of_ambient_decimal_precision() -> None:
    with localcontext() as context:
        context.prec = 10
        low = estimate_arrival_intensity(_synthetic_buckets(), _config())
    with localcontext() as context:
        context.prec = 50
        high = estimate_arrival_intensity(_synthetic_buckets(), _config())

    assert low == high


def test_invalid_intensity_config_fails_closed() -> None:
    with pytest.raises(ValueError, match="min_buckets"):
        IntensityCalibrationConfig(1, 10, Decimal("0.5"), Decimal("1.5"), 21, Decimal("1"))
    with pytest.raises(ValueError, match="k_min"):
        IntensityCalibrationConfig(4, 10, Decimal("0"), Decimal("1.5"), 21, Decimal("1"))
    with pytest.raises(ValueError, match="k_max"):
        IntensityCalibrationConfig(4, 10, Decimal("1.5"), Decimal("1"), 21, Decimal("1"))
    with pytest.raises(ValueError, match="k_steps"):
        IntensityCalibrationConfig(4, 10, Decimal("0.5"), Decimal("1.5"), 1, Decimal("1"))
