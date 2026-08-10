import datetime as dt
from decimal import Decimal, localcontext

import pytest

from grid_trade.calibration.microstructure_contracts import (
    IntensityBucket,
    MarkoutSide,
    MaturedMarkout,
    MicrostructureReadiness,
    OfiImpactSample,
    TopOfBookObservation,
)


def _time(minutes: int = 0) -> dt.datetime:
    return dt.datetime(2026, 8, 9, 12, 0, tzinfo=dt.UTC) + dt.timedelta(minutes=minutes)


def test_intensity_bucket_accepts_zero_arrivals_but_requires_positive_exposure() -> None:
    bucket = IntensityBucket(
        distance_vol_units=Decimal("1.5"),
        exposure_seconds=Decimal("20"),
        arrival_count=0,
    )
    assert bucket.arrival_count == 0

    with pytest.raises(ValueError, match="exposure_seconds"):
        IntensityBucket(Decimal("1"), Decimal("0"), 1)

    with pytest.raises(ValueError, match="distance_vol_units"):
        IntensityBucket(Decimal("-0.1"), Decimal("20"), 1)


def test_top_of_book_requires_valid_uncrossed_positive_book() -> None:
    book = TopOfBookObservation(
        timestamp=_time(),
        source_id="fixture",
        instrument_id="AAA-PERP",
        best_bid=Decimal("99"),
        bid_size=Decimal("4"),
        best_ask=Decimal("101"),
        ask_size=Decimal("6"),
    )
    assert book.mid == Decimal("100")

    with pytest.raises(ValueError, match="best_bid"):
        TopOfBookObservation(
            timestamp=_time(),
            source_id="fixture",
            instrument_id="AAA-PERP",
            best_bid=Decimal("101"),
            bid_size=Decimal("4"),
            best_ask=Decimal("101"),
            ask_size=Decimal("6"),
        )


def test_top_of_book_mid_is_independent_of_ambient_decimal_precision() -> None:
    book = TopOfBookObservation(
        timestamp=_time(),
        source_id="fixture",
        instrument_id="AAA-PERP",
        best_bid=Decimal("1.23456789123456789"),
        bid_size=Decimal("4"),
        best_ask=Decimal("1.23456799123456789"),
        ask_size=Decimal("6"),
    )

    with localcontext() as context:
        context.prec = 10
        low = book.mid
    with localcontext() as context:
        context.prec = 50
        high = book.mid

    assert low == high


def test_markout_maturity_must_not_precede_fill() -> None:
    with pytest.raises(ValueError, match="matured_at"):
        MaturedMarkout(
            fill_timestamp=_time(1),
            matured_at=_time(0),
            side=MarkoutSide.BUY,
            fill_price=Decimal("100"),
            mark_price=Decimal("99"),
        )


def test_ofi_impact_label_maturity_must_not_precede_feature() -> None:
    with pytest.raises(ValueError, match="matured_at"):
        OfiImpactSample(
            feature_timestamp=_time(1),
            matured_at=_time(0),
            normalized_ofi=Decimal("0.5"),
            relative_price_change=Decimal("0.001"),
        )


def test_microstructure_readiness_quality_is_bounded() -> None:
    ready = MicrostructureReadiness(
        ready=True,
        sample_count=10,
        reason="ready",
        quality=Decimal("0.8"),
    )
    assert ready.quality == Decimal("0.8")

    with pytest.raises(ValueError, match="quality"):
        MicrostructureReadiness(True, 10, "ready", Decimal("1.1"))


def test_contracts_reject_naive_timestamps() -> None:
    with pytest.raises(ValueError, match="timestamp"):
        TopOfBookObservation(
            timestamp=dt.datetime(2026, 8, 9, 12, 0),
            source_id="fixture",
            instrument_id="AAA-PERP",
            best_bid=Decimal("99"),
            bid_size=Decimal("4"),
            best_ask=Decimal("101"),
            ask_size=Decimal("6"),
        )
