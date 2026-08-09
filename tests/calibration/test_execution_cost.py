import datetime as dt
from decimal import Decimal, localcontext

import pytest

from grid_trade.calibration.execution_cost import (
    ExecutionCostConfig,
    estimate_execution_cost,
    relative_adverse_markout,
)
from grid_trade.calibration.microstructure_contracts import MarkoutSide, MaturedMarkout


def _time(minute: int) -> dt.datetime:
    return dt.datetime(2026, 8, 9, 12, minute, tzinfo=dt.UTC)


def _markout(
    *,
    fill: int,
    maturity: int,
    side: MarkoutSide,
    fill_price: str,
    mark_price: str,
) -> MaturedMarkout:
    return MaturedMarkout(
        fill_timestamp=_time(fill),
        matured_at=_time(maturity),
        side=side,
        fill_price=Decimal(fill_price),
        mark_price=Decimal(mark_price),
    )


def _config(
    *,
    min_samples: int = 2,
    quantile: str = "0.75",
    fallback: str = "0.003",
) -> ExecutionCostConfig:
    return ExecutionCostConfig(
        markout_window=8,
        min_markout_samples=min_samples,
        adverse_quantile=Decimal(quantile),
        uncertainty_buffer=Decimal("0.0002"),
        fallback_adverse_cost=Decimal(fallback),
    )


def test_relative_adverse_markout_has_correct_buy_and_sell_sign() -> None:
    buy_bad = _markout(
        fill=0,
        maturity=1,
        side=MarkoutSide.BUY,
        fill_price="100",
        mark_price="99",
    )
    buy_good = _markout(
        fill=0,
        maturity=1,
        side=MarkoutSide.BUY,
        fill_price="100",
        mark_price="101",
    )
    sell_bad = _markout(
        fill=0,
        maturity=1,
        side=MarkoutSide.SELL,
        fill_price="100",
        mark_price="101",
    )

    assert relative_adverse_markout(buy_bad) == Decimal("0.01")
    assert relative_adverse_markout(buy_good) == Decimal("0")
    assert relative_adverse_markout(sell_bad) == Decimal("0.01")


def test_future_markout_is_excluded_until_exact_maturity() -> None:
    matured = _markout(
        fill=0,
        maturity=1,
        side=MarkoutSide.BUY,
        fill_price="100",
        mark_price="99.9",
    )
    future = _markout(
        fill=1,
        maturity=5,
        side=MarkoutSide.SELL,
        fill_price="100",
        mark_price="100.2",
    )

    before = estimate_execution_cost(
        (matured, future),
        decision_time=_time(4),
        maker_fee_rate=Decimal("0"),
        tick_size=Decimal("0.01"),
        current_mid=Decimal("100"),
        config=_config(min_samples=2),
    )
    at_maturity = estimate_execution_cost(
        (matured, future),
        decision_time=_time(5),
        maker_fee_rate=Decimal("0"),
        tick_size=Decimal("0.01"),
        current_mid=Decimal("100"),
        config=_config(min_samples=2),
    )

    assert before.markout_ready is False
    assert before.used_fallback is True
    assert before.sample_count == 1
    assert before.adverse_cost == Decimal("0.003")
    assert at_maturity.markout_ready is True
    assert at_maturity.used_fallback is False
    assert at_maturity.sample_count == 2


def test_nearest_rank_upper_quantile_is_deterministic() -> None:
    markouts = (
        _markout(fill=0, maturity=1, side=MarkoutSide.BUY, fill_price="1000", mark_price="999"),
        _markout(fill=1, maturity=2, side=MarkoutSide.BUY, fill_price="1000", mark_price="998"),
        _markout(fill=2, maturity=3, side=MarkoutSide.BUY, fill_price="1000", mark_price="996"),
        _markout(fill=3, maturity=4, side=MarkoutSide.BUY, fill_price="1000", mark_price="990"),
    )

    estimate = estimate_execution_cost(
        markouts,
        decision_time=_time(5),
        maker_fee_rate=Decimal("0"),
        tick_size=Decimal("0.01"),
        current_mid=Decimal("1000"),
        config=_config(min_samples=4, quantile="0.75"),
    )

    assert estimate.adverse_cost == Decimal("0.004")


def test_maker_rebate_cannot_make_execution_floor_negative() -> None:
    estimate = estimate_execution_cost(
        (),
        decision_time=_time(0),
        maker_fee_rate=Decimal("-0.0005"),
        tick_size=Decimal("0.01"),
        current_mid=Decimal("100"),
        config=ExecutionCostConfig(
            markout_window=8,
            min_markout_samples=2,
            adverse_quantile=Decimal("0.75"),
            uncertainty_buffer=Decimal("0"),
            fallback_adverse_cost=Decimal("0"),
        ),
    )

    assert estimate.round_trip_fee == Decimal("-0.0010")
    assert estimate.tick_floor == Decimal("0.0001")
    assert estimate.execution_cost_floor == Decimal("0.0001")


def test_adverse_cost_and_buffer_can_dominate_tick_floor() -> None:
    estimate = estimate_execution_cost(
        (),
        decision_time=_time(0),
        maker_fee_rate=Decimal("0.0001"),
        tick_size=Decimal("0.01"),
        current_mid=Decimal("100"),
        config=_config(fallback="0.003"),
    )

    assert estimate.round_trip_fee == Decimal("0.0002")
    assert estimate.execution_cost_floor == Decimal("0.0034")


def test_execution_cost_is_independent_of_ambient_decimal_precision() -> None:
    markouts = (
        _markout(
            fill=0,
            maturity=1,
            side=MarkoutSide.BUY,
            fill_price="123.456789",
            mark_price="123.400001",
        ),
        _markout(
            fill=1,
            maturity=2,
            side=MarkoutSide.SELL,
            fill_price="123.400001",
            mark_price="123.456789",
        ),
    )
    kwargs = dict(
        decision_time=_time(3),
        maker_fee_rate=Decimal("0.00015"),
        tick_size=Decimal("0.001"),
        current_mid=Decimal("123.43"),
        config=_config(min_samples=2),
    )

    with localcontext() as context:
        context.prec = 10
        low = estimate_execution_cost(markouts, **kwargs)
    with localcontext() as context:
        context.prec = 50
        high = estimate_execution_cost(markouts, **kwargs)

    assert low == high


def test_invalid_execution_cost_config_fails_closed() -> None:
    with pytest.raises(ValueError, match="adverse_quantile"):
        ExecutionCostConfig(8, 2, Decimal("0"), Decimal("0"), Decimal("0"))
