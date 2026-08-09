import datetime as dt
from decimal import Decimal, localcontext

import pytest

from grid_trade.calibration.microstructure_contracts import OfiImpactSample, TopOfBookObservation
from grid_trade.calibration.order_flow import (
    OfiImpactConfig,
    OfiImpactState,
    compute_ofi,
    estimate_ofi_impact,
    microprice,
    microprice_displacement,
    normalized_ofi,
    predict_ofi_displacement,
    update_ofi_impact,
)


def _book(
    *,
    minute: int,
    bid: str = "99",
    bid_size: str = "5",
    ask: str = "101",
    ask_size: str = "5",
    price_scale: str = "1",
    size_scale: str = "1",
) -> TopOfBookObservation:
    p_scale = Decimal(price_scale)
    q_scale = Decimal(size_scale)
    return TopOfBookObservation(
        timestamp=dt.datetime(2026, 8, 9, 12, minute, tzinfo=dt.UTC),
        source_id="fixture",
        instrument_id="AAA-PERP",
        best_bid=Decimal(bid) * p_scale,
        bid_size=Decimal(bid_size) * q_scale,
        best_ask=Decimal(ask) * p_scale,
        ask_size=Decimal(ask_size) * q_scale,
    )


def _time(minute: int) -> dt.datetime:
    return dt.datetime(2026, 8, 9, 12, minute, tzinfo=dt.UTC)


def _impact_sample(*, feature: int, maturity: int, x: str, y: str) -> OfiImpactSample:
    return OfiImpactSample(
        feature_timestamp=_time(feature),
        matured_at=_time(maturity),
        normalized_ofi=Decimal(x),
        relative_price_change=Decimal(y),
    )


def _impact_config(
    *,
    window: int = 8,
    min_samples: int = 2,
    max_abs_beta: str = "0.01",
) -> OfiImpactConfig:
    return OfiImpactConfig(
        window=window,
        min_samples=min_samples,
        min_abs_feature_energy=Decimal("0.01"),
        max_abs_beta=Decimal(max_abs_beta),
        score_scale_vol_units=Decimal("2"),
    )


def test_bid_size_increase_is_positive_ofi() -> None:
    previous = _book(minute=0, bid_size="5")
    current = _book(minute=1, bid_size="8")

    assert compute_ofi(previous, current) == Decimal("3")
    assert normalized_ofi(previous, current) > 0


def test_ask_size_increase_is_negative_ofi() -> None:
    previous = _book(minute=0, ask_size="5")
    current = _book(minute=1, ask_size="8")

    assert compute_ofi(previous, current) == Decimal("-3")
    assert normalized_ofi(previous, current) < 0


def test_bid_price_improvement_uses_new_bid_queue() -> None:
    previous = _book(minute=0, bid="99", bid_size="7")
    current = _book(minute=1, bid="100", bid_size="4")

    assert compute_ofi(previous, current) == Decimal("4")


def test_ask_price_improvement_uses_new_ask_queue() -> None:
    previous = _book(minute=0, ask="102", ask_size="7")
    current = _book(minute=1, ask="101", ask_size="4")

    assert compute_ofi(previous, current) == Decimal("-4")


def test_normalized_ofi_is_invariant_to_common_size_scale() -> None:
    previous = _book(minute=0, bid_size="5", ask_size="4")
    current = _book(minute=1, bid_size="8", ask_size="3")
    previous_scaled = _book(minute=0, bid_size="5", ask_size="4", size_scale="100")
    current_scaled = _book(minute=1, bid_size="8", ask_size="3", size_scale="100")

    assert normalized_ofi(previous, current) == normalized_ofi(previous_scaled, current_scaled)


def test_microprice_lies_inside_spread_and_moves_toward_thinner_side() -> None:
    balanced = _book(minute=0, bid_size="5", ask_size="5")
    bid_heavy = _book(minute=1, bid_size="9", ask_size="1")

    assert microprice(balanced) == balanced.mid
    assert bid_heavy.best_bid < microprice(bid_heavy) < bid_heavy.best_ask
    assert microprice(bid_heavy) > bid_heavy.mid


def test_microprice_displacement_is_price_scale_invariant() -> None:
    base = _book(minute=0, bid_size="9", ask_size="1")
    scaled = _book(minute=0, bid_size="9", ask_size="1", price_scale="100")

    assert microprice_displacement(base) == microprice_displacement(scaled)


def test_order_flow_requires_identity_and_time_continuity() -> None:
    previous = _book(minute=1)
    current = _book(minute=0)

    with pytest.raises(ValueError, match="strictly newer"):
        compute_ofi(previous, current)


def test_future_ofi_label_is_retained_but_not_fitted_before_maturity() -> None:
    sample = _impact_sample(feature=0, maturity=5, x="1", y="0.002")
    next_state, estimate = update_ofi_impact(
        OfiImpactState(),
        sample,
        decision_time=_time(4),
        config=_impact_config(min_samples=1),
    )

    assert next_state.samples == (sample,)
    assert estimate.ready is False
    assert estimate.sample_count == 0

    matured = estimate_ofi_impact(
        next_state, decision_time=_time(5), config=_impact_config(min_samples=1)
    )
    assert matured.ready is True
    assert matured.sample_count == 1
    assert matured.beta == Decimal("0.002")


def test_future_label_does_not_evict_current_window() -> None:
    config = _impact_config(window=2, min_samples=2)
    first = _impact_sample(feature=0, maturity=1, x="1", y="0.002")
    second = _impact_sample(feature=1, maturity=2, x="-1", y="-0.002")
    future = _impact_sample(feature=2, maturity=10, x="1", y="0.009")
    state = OfiImpactState(samples=(first, second))
    before = estimate_ofi_impact(state, decision_time=_time(3), config=config)

    next_state, after = update_ofi_impact(
        state,
        future,
        decision_time=_time(3),
        config=config,
    )

    assert future in next_state.samples
    assert after == before


def test_ofi_impact_recovers_through_origin_beta() -> None:
    state = OfiImpactState(
        samples=(
            _impact_sample(feature=0, maturity=1, x="-2", y="-0.004"),
            _impact_sample(feature=1, maturity=2, x="-1", y="-0.002"),
            _impact_sample(feature=2, maturity=3, x="1", y="0.002"),
            _impact_sample(feature=3, maturity=4, x="2", y="0.004"),
        )
    )
    estimate = estimate_ofi_impact(state, decision_time=_time(5), config=_impact_config())

    assert estimate.ready is True
    assert estimate.beta == Decimal("0.002")
    assert estimate.fit_r2 == Decimal("1")
    assert predict_ofi_displacement(Decimal("1.5"), estimate) == Decimal("0.0030")


def test_zero_ofi_feature_energy_is_not_ready() -> None:
    state = OfiImpactState(
        samples=(
            _impact_sample(feature=0, maturity=1, x="0", y="0.001"),
            _impact_sample(feature=1, maturity=2, x="0", y="-0.001"),
        )
    )
    estimate = estimate_ofi_impact(state, decision_time=_time(3), config=_impact_config())

    assert estimate.ready is False
    assert estimate.beta is None
    assert estimate.sample_count == 2


def test_ofi_beta_is_bounded_by_configured_limit() -> None:
    state = OfiImpactState(
        samples=(
            _impact_sample(feature=0, maturity=1, x="1", y="1"),
            _impact_sample(feature=1, maturity=2, x="2", y="2"),
        )
    )
    estimate = estimate_ofi_impact(
        state,
        decision_time=_time(3),
        config=_impact_config(max_abs_beta="0.01"),
    )

    assert estimate.ready is True
    assert estimate.beta == Decimal("0.01")


def test_ofi_impact_is_independent_of_ambient_decimal_precision() -> None:
    state = OfiImpactState(
        samples=(
            _impact_sample(feature=0, maturity=1, x="0.7", y="0.0014"),
            _impact_sample(feature=1, maturity=2, x="-1.3", y="-0.0026"),
        )
    )
    config = _impact_config()

    with localcontext() as context:
        context.prec = 10
        low = estimate_ofi_impact(state, decision_time=_time(3), config=config)
    with localcontext() as context:
        context.prec = 50
        high = estimate_ofi_impact(state, decision_time=_time(3), config=config)

    assert low == high
