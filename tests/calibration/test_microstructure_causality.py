import datetime as dt
from decimal import Decimal

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
    MicrostructureCalibrationState,
    update_microstructure_engine,
)
from grid_trade.calibration.order_flow import OfiImpactConfig


def _time(minute: int) -> dt.datetime:
    return dt.datetime(2026, 8, 9, 12, minute, tzinfo=dt.UTC)


def _book(minute: int, *, bid_size: str = "5", ask_size: str = "5") -> TopOfBookObservation:
    return TopOfBookObservation(
        timestamp=_time(minute),
        source_id="fixture",
        instrument_id="GENERIC-PERP",
        best_bid=Decimal("99"),
        bid_size=Decimal(bid_size),
        best_ask=Decimal("101"),
        ask_size=Decimal(ask_size),
    )


def _config() -> MicrostructureCalibrationConfig:
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
            max_abs_beta=Decimal("0.01"),
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


def _buckets() -> tuple[IntensityBucket, ...]:
    return (
        IntensityBucket(Decimal("0"), Decimal("100"), 1000),
        IntensityBucket(Decimal("1"), Decimal("100"), 368),
        IntensityBucket(Decimal("2"), Decimal("100"), 135),
        IntensityBucket(Decimal("3"), Decimal("100"), 50),
    )


def _matured_labels() -> tuple[OfiImpactSample, ...]:
    return (
        OfiImpactSample(_time(0), _time(2), Decimal("-1"), Decimal("-0.002")),
        OfiImpactSample(_time(1), _time(3), Decimal("1"), Decimal("0.002")),
    )


def _matured_markouts() -> tuple[MaturedMarkout, ...]:
    return (
        MaturedMarkout(
            _time(0),
            _time(2),
            MarkoutSide.BUY,
            Decimal("100"),
            Decimal("99.9"),
        ),
        MaturedMarkout(
            _time(1),
            _time(3),
            MarkoutSide.SELL,
            Decimal("100"),
            Decimal("100.2"),
        ),
    )


def _first_update(
    *,
    buckets: tuple[IntensityBucket, ...] | None = None,
    labels: tuple[OfiImpactSample, ...] | None = None,
    markouts: tuple[MaturedMarkout, ...] | None = None,
):
    return update_microstructure_engine(
        MicrostructureCalibrationState(),
        _book(10),
        volatility_scale=Decimal("0.001"),
        intensity_buckets=_buckets() if buckets is None else buckets,
        markouts=_matured_markouts() if markouts is None else markouts,
        new_ofi_impact_samples=_matured_labels() if labels is None else labels,
        maker_fee_rate=Decimal("0.0001"),
        tick_size=Decimal("0.01"),
        config=_config(),
    )


def test_future_ofi_label_cannot_change_current_estimate_or_readiness() -> None:
    baseline = _first_update()
    future = OfiImpactSample(
        feature_timestamp=_time(9),
        matured_at=_time(20),
        normalized_ofi=Decimal("100"),
        relative_price_change=Decimal("0.5"),
    )
    with_future = _first_update(labels=(*_matured_labels(), future))

    assert with_future.estimate == baseline.estimate


def test_future_markout_cannot_change_current_execution_cost() -> None:
    baseline = _first_update()
    future = MaturedMarkout(
        fill_timestamp=_time(9),
        matured_at=_time(20),
        side=MarkoutSide.BUY,
        fill_price=Decimal("100"),
        mark_price=Decimal("1"),
    )
    with_future = _first_update(markouts=(*_matured_markouts(), future))

    assert with_future.estimate.execution == baseline.estimate.execution
    assert with_future.estimate.readiness == baseline.estimate.readiness


def test_permuting_evidence_inputs_does_not_change_estimate() -> None:
    baseline = _first_update()
    permuted = _first_update(
        buckets=tuple(reversed(_buckets())),
        labels=tuple(reversed(_matured_labels())),
        markouts=tuple(reversed(_matured_markouts())),
    )

    assert permuted.estimate == baseline.estimate
    assert permuted.next_state.ofi_impact_state == baseline.next_state.ofi_impact_state


def test_future_label_becomes_eligible_only_at_exact_maturity_boundary() -> None:
    future = OfiImpactSample(
        feature_timestamp=_time(9),
        matured_at=_time(12),
        normalized_ofi=Decimal("2"),
        relative_price_change=Decimal("0.006"),
    )
    first = _first_update(labels=(*_matured_labels(), future))

    before = update_microstructure_engine(
        first.next_state,
        _book(11, bid_size="8", ask_size="4"),
        volatility_scale=Decimal("0.001"),
        intensity_buckets=_buckets(),
        markouts=_matured_markouts(),
        new_ofi_impact_samples=(),
        maker_fee_rate=Decimal("0.0001"),
        tick_size=Decimal("0.01"),
        config=_config(),
    )
    at_maturity = update_microstructure_engine(
        before.next_state,
        _book(12, bid_size="9", ask_size="3"),
        volatility_scale=Decimal("0.001"),
        intensity_buckets=_buckets(),
        markouts=_matured_markouts(),
        new_ofi_impact_samples=(),
        maker_fee_rate=Decimal("0.0001"),
        tick_size=Decimal("0.01"),
        config=_config(),
    )

    assert before.estimate.ofi_impact.sample_count == 2
    assert at_maturity.estimate.ofi_impact.sample_count == 3
    assert at_maturity.estimate.ofi_impact.beta != before.estimate.ofi_impact.beta
