from datetime import UTC, datetime
from decimal import Decimal

import pytest

from grid_trade.calibration import (
    CalibrationComponentStatus,
    CalibrationObservation,
    CalibrationReadiness,
    CalibratedMarketState,
)


def _timestamp() -> datetime:
    return datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def test_observation_requires_timezone_aware_timestamp() -> None:
    with pytest.raises(ValueError, match="timestamp"):
        CalibrationObservation(
            timestamp=datetime(2026, 8, 9, 12, 0),
            source_id="fixture",
            instrument_id="AAA-PERP",
            mid=Decimal("100"),
            funding_rate=None,
        )


def test_observation_requires_positive_finite_mid() -> None:
    with pytest.raises(ValueError, match="mid"):
        CalibrationObservation(
            timestamp=_timestamp(),
            source_id="fixture",
            instrument_id="AAA-PERP",
            mid=Decimal("0"),
            funding_rate=None,
        )


def test_observation_rejects_non_finite_funding() -> None:
    with pytest.raises(ValueError, match="funding_rate"):
        CalibrationObservation(
            timestamp=_timestamp(),
            source_id="fixture",
            instrument_id="AAA-PERP",
            mid=Decimal("100"),
            funding_rate=Decimal("NaN"),
        )


def test_identity_fields_must_be_non_empty() -> None:
    with pytest.raises(ValueError, match="source_id"):
        CalibrationObservation(
            timestamp=_timestamp(),
            source_id=" ",
            instrument_id="AAA-PERP",
            mid=Decimal("100"),
            funding_rate=None,
        )

    with pytest.raises(ValueError, match="instrument_id"):
        CalibrationObservation(
            timestamp=_timestamp(),
            source_id="fixture",
            instrument_id=" ",
            mid=Decimal("100"),
            funding_rate=None,
        )


def test_component_status_rejects_negative_sample_count() -> None:
    with pytest.raises(ValueError, match="sample_count"):
        CalibrationComponentStatus(ready=False, sample_count=-1, reason="warmup")


def test_not_ready_state_preserves_unavailable_components() -> None:
    state = CalibratedMarketState.not_ready(
        timestamp=_timestamp(),
        source_id="fixture",
        instrument_id="AAA-PERP",
    )

    assert state.readiness is CalibrationReadiness.NOT_READY
    assert state.volatility_scale is None
    assert state.trend_score is None
    assert state.funding_score is None
    assert state.quote_distance_scale is None
    assert state.execution_cost_floor is None
    assert state.order_book_score is None
    assert state.estimated_microprice_displacement is None
    assert state.volatility_status.ready is False
    assert state.trend_status.ready is False
    assert state.funding_status.ready is False
    assert state.microstructure_status.ready is False


def test_normalized_scores_are_bounded() -> None:
    not_ready = CalibrationComponentStatus(ready=False, sample_count=0, reason="warmup")
    ready = CalibrationComponentStatus(ready=True, sample_count=20, reason="ready")

    with pytest.raises(ValueError, match="trend_score"):
        CalibratedMarketState(
            timestamp=_timestamp(),
            source_id="fixture",
            instrument_id="AAA-PERP",
            readiness=CalibrationReadiness.READY,
            volatility_scale=Decimal("0.01"),
            trend_score=Decimal("1.01"),
            funding_score=None,
            quote_distance_scale=None,
            execution_cost_floor=None,
            order_book_score=None,
            estimated_microprice_displacement=None,
            volatility_status=ready,
            trend_status=ready,
            funding_status=not_ready,
            microstructure_status=not_ready,
        )


def test_relative_price_scales_cannot_be_negative() -> None:
    not_ready = CalibrationComponentStatus(ready=False, sample_count=0, reason="unavailable")
    ready = CalibrationComponentStatus(ready=True, sample_count=20, reason="ready")

    with pytest.raises(ValueError, match="quote_distance_scale"):
        CalibratedMarketState(
            timestamp=_timestamp(),
            source_id="fixture",
            instrument_id="AAA-PERP",
            readiness=CalibrationReadiness.READY,
            volatility_scale=Decimal("0.01"),
            trend_score=Decimal("0.2"),
            funding_score=None,
            quote_distance_scale=Decimal("-0.001"),
            execution_cost_floor=None,
            order_book_score=None,
            estimated_microprice_displacement=None,
            volatility_status=ready,
            trend_status=ready,
            funding_status=not_ready,
            microstructure_status=not_ready,
        )
