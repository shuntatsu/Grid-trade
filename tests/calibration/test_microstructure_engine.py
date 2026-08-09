import datetime as dt
from dataclasses import replace
from decimal import Decimal, localcontext

import pytest

from grid_trade.calibration.execution_cost import ExecutionCostConfig
from grid_trade.calibration.intensity import IntensityCalibrationConfig
from grid_trade.calibration.microstructure_contracts import (
    IntensityBucket,
    MarkoutSide,
    MaturedMarkout,
    OfiImpactSample,
    TopOfBookObservation,
)
from grid_trade.calibration.microstructure_engine import (
    MicrostructureCalibrationConfig,
    MicrostructureCalibrationEstimate,
    MicrostructureCalibrationState,
    update_microstructure_engine,
)
from grid_trade.calibration.order_flow import OfiImpactConfig


def _time(minute: int) -> dt.datetime:
    return dt.datetime(2026, 8, 9, 12, minute, tzinfo=dt.UTC)


def _book(
    minute: int,
    *,
    instrument: str = "AAA-PERP",
    price_scale: str = "1",
    size_scale: str = "1",
    bid_size: str = "5",
    ask_size: str = "5",
) -> TopOfBookObservation:
    p = Decimal(price_scale)
    q = Decimal(size_scale)
    return TopOfBookObservation(
        timestamp=_time(minute),
        source_id="fixture",
        instrument_id=instrument,
        best_bid=Decimal("99") * p,
        bid_size=Decimal(bid_size) * q,
        best_ask=Decimal("101") * p,
        ask_size=Decimal(ask_size) * q,
    )


def _config(*, trend_limit: str = "0.01") -> MicrostructureCalibrationConfig:
    return MicrostructureCalibrationConfig(
        intensity=IntensityCalibrationConfig(
            min_buckets=3,
            min_total_arrivals=20,
            k_min=Decimal("0.5"),
            k_max=Decimal("1.5"),
            k_steps=21,
            min_log_likelihood_improvement=Decimal("0.1"),
        ),
        ofi_impact=OfiImpactConfig(
            window=8,
            min_samples=2,
            min_abs_feature_energy=Decimal("0.01"),
            max_abs_beta=Decimal(trend_limit),
            score_scale_vol_units=Decimal("2"),
        ),
        execution_cost=ExecutionCostConfig(
            markout_window=8,
            min_markout_samples=2,
            adverse_quantile=Decimal("0.75"),
            uncertainty_buffer=Decimal("0.0002"),
            fallback_adverse_cost=Decimal("0.003"),
        ),
        min_microstructure_quality=Decimal("0"),
    )


def _intensity() -> tuple[IntensityBucket, ...]:
    return (
        IntensityBucket(Decimal("0"), Decimal("100"), 1000),
        IntensityBucket(Decimal("1"), Decimal("100"), 368),
        IntensityBucket(Decimal("2"), Decimal("100"), 135),
        IntensityBucket(Decimal("3"), Decimal("100"), 50),
    )


def _labels() -> tuple[OfiImpactSample, ...]:
    return (
        OfiImpactSample(_time(0), _time(2), Decimal("-1"), Decimal("-0.002")),
        OfiImpactSample(_time(1), _time(3), Decimal("1"), Decimal("0.002")),
    )


def _markouts(*, price_scale: str = "1") -> tuple[MaturedMarkout, ...]:
    p = Decimal(price_scale)
    return (
        MaturedMarkout(
            _time(0),
            _time(2),
            MarkoutSide.BUY,
            Decimal("100") * p,
            Decimal("99.9") * p,
        ),
        MaturedMarkout(
            _time(1),
            _time(3),
            MarkoutSide.SELL,
            Decimal("100") * p,
            Decimal("100.2") * p,
        ),
    )


def _advance_ready(
    *,
    instrument: str = "AAA-PERP",
    price_scale: str = "1",
    size_scale: str = "1",
) -> tuple[MicrostructureCalibrationState, MicrostructureCalibrationEstimate]:
    config = _config()
    initial = update_microstructure_engine(
        MicrostructureCalibrationState(),
        _book(10, instrument=instrument, price_scale=price_scale, size_scale=size_scale),
        volatility_scale=Decimal("0.001"),
        intensity_buckets=_intensity(),
        markouts=_markouts(price_scale=price_scale),
        new_ofi_impact_samples=_labels(),
        maker_fee_rate=Decimal("0.0001"),
        tick_size=Decimal("0.01") * Decimal(price_scale),
        config=config,
    )
    second = update_microstructure_engine(
        initial.next_state,
        _book(
            11,
            instrument=instrument,
            price_scale=price_scale,
            size_scale=size_scale,
            bid_size="8",
            ask_size="4",
        ),
        volatility_scale=Decimal("0.001"),
        intensity_buckets=_intensity(),
        markouts=_markouts(price_scale=price_scale),
        new_ofi_impact_samples=(),
        maker_fee_rate=Decimal("0.0001"),
        tick_size=Decimal("0.01") * Decimal(price_scale),
        config=config,
    )
    return second.next_state, second.estimate


def test_engine_does_not_fabricate_unavailable_microstructure() -> None:
    update = update_microstructure_engine(
        MicrostructureCalibrationState(),
        _book(10),
        volatility_scale=None,
        intensity_buckets=(),
        markouts=(),
        new_ofi_impact_samples=(),
        maker_fee_rate=Decimal("0.0001"),
        tick_size=Decimal("0.01"),
        config=_config(),
    )

    assert update.estimate.readiness.ready is False
    assert update.estimate.quote_distance_scale is None
    assert update.estimate.current_normalized_ofi is None
    assert update.estimate.predicted_relative_displacement is None
    assert update.estimate.order_book_score is None
    assert update.estimate.execution.used_fallback is True


def test_engine_becomes_ready_only_when_all_required_components_exist() -> None:
    _, estimate = _advance_ready()

    assert estimate.readiness.ready is True
    assert estimate.intensity.ready is True
    assert estimate.execution.markout_ready is True
    assert estimate.ofi_impact.ready is True
    assert estimate.quote_distance_scale == Decimal("0.001")
    assert estimate.current_normalized_ofi is not None
    assert estimate.predicted_relative_displacement is not None
    assert estimate.microprice_relative_displacement is not None
    assert estimate.order_book_score is not None
    assert Decimal("-1") <= estimate.order_book_score <= Decimal("1")


def test_ready_estimate_rejects_missing_required_component() -> None:
    _, estimate = _advance_ready()

    with pytest.raises(ValueError, match="ready microstructure estimate"):
        replace(estimate, quote_distance_scale=None)


def test_engine_freezes_config_after_first_observation() -> None:
    config = _config()
    first = update_microstructure_engine(
        MicrostructureCalibrationState(),
        _book(10),
        volatility_scale=Decimal("0.001"),
        intensity_buckets=_intensity(),
        markouts=_markouts(),
        new_ofi_impact_samples=_labels(),
        maker_fee_rate=Decimal("0.0001"),
        tick_size=Decimal("0.01"),
        config=config,
    )

    with pytest.raises(ValueError, match="config"):
        update_microstructure_engine(
            first.next_state,
            _book(11),
            volatility_scale=Decimal("0.001"),
            intensity_buckets=_intensity(),
            markouts=_markouts(),
            new_ofi_impact_samples=(),
            maker_fee_rate=Decimal("0.0001"),
            tick_size=Decimal("0.01"),
            config=_config(trend_limit="0.02"),
        )


def test_engine_rejects_timestamp_or_identity_discontinuity() -> None:
    first = update_microstructure_engine(
        MicrostructureCalibrationState(),
        _book(10),
        volatility_scale=Decimal("0.001"),
        intensity_buckets=(),
        markouts=(),
        new_ofi_impact_samples=(),
        maker_fee_rate=Decimal("0"),
        tick_size=Decimal("0.01"),
        config=_config(),
    )

    with pytest.raises(ValueError, match="strictly newer"):
        update_microstructure_engine(
            first.next_state,
            _book(10),
            volatility_scale=Decimal("0.001"),
            intensity_buckets=(),
            markouts=(),
            new_ofi_impact_samples=(),
            maker_fee_rate=Decimal("0"),
            tick_size=Decimal("0.01"),
            config=_config(),
        )
    with pytest.raises(ValueError, match="instrument_id"):
        update_microstructure_engine(
            first.next_state,
            _book(11, instrument="BBB-PERP"),
            volatility_scale=Decimal("0.001"),
            intensity_buckets=(),
            markouts=(),
            new_ofi_impact_samples=(),
            maker_fee_rate=Decimal("0"),
            tick_size=Decimal("0.01"),
            config=_config(),
        )


def test_symbol_name_does_not_change_numeric_microstructure_estimate() -> None:
    _, aaa = _advance_ready(instrument="AAA-PERP")
    _, bbb = _advance_ready(instrument="BBB-PERP")

    assert aaa == bbb


def test_common_price_and_size_scaling_preserves_relative_outputs() -> None:
    _, base = _advance_ready()
    _, scaled = _advance_ready(price_scale="100", size_scale="100")

    assert scaled.quote_distance_scale == base.quote_distance_scale
    assert scaled.execution.execution_cost_floor == base.execution.execution_cost_floor
    assert scaled.current_normalized_ofi == base.current_normalized_ofi
    assert scaled.predicted_relative_displacement == base.predicted_relative_displacement
    assert scaled.microprice_relative_displacement == base.microprice_relative_displacement
    assert scaled.order_book_score == base.order_book_score


def test_engine_is_independent_of_ambient_decimal_precision() -> None:
    with localcontext() as context:
        context.prec = 10
        low_state, low = _advance_ready()
    with localcontext() as context:
        context.prec = 50
        high_state, high = _advance_ready()

    assert low == high
    assert low_state == high_state
