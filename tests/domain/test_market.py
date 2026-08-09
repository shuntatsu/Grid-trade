from decimal import Decimal
from datetime import UTC, datetime

import pytest
from hypothesis import given, strategies as st

from grid_trade.domain.market import MarketSnapshot


def _snapshot(**overrides: object) -> MarketSnapshot:
    values: dict[str, object] = {
        "timestamp": datetime(2026, 8, 9, 7, 0, tzinfo=UTC),
        "best_bid": Decimal("99"),
        "best_ask": Decimal("101"),
        "realized_volatility": Decimal("0.02"),
        "position_quantity": Decimal("0"),
        "source_id": "fixture:l1",
    }
    values.update(overrides)
    return MarketSnapshot(**values)  # type: ignore[arg-type]


def test_mid_is_exact_decimal_average() -> None:
    snapshot = _snapshot(best_bid=Decimal("99.99"), best_ask=Decimal("100.01"))

    assert snapshot.mid == Decimal("100.00")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("best_bid", Decimal("0")),
        ("best_bid", Decimal("-1")),
        ("best_ask", Decimal("0")),
        ("best_ask", Decimal("-1")),
        ("realized_volatility", Decimal("-0.0001")),
    ],
)
def test_snapshot_rejects_invalid_positive_market_fields(field: str, value: Decimal) -> None:
    with pytest.raises(ValueError):
        _snapshot(**{field: value})


def test_snapshot_rejects_crossed_or_locked_market() -> None:
    with pytest.raises(ValueError):
        _snapshot(best_bid=Decimal("100"), best_ask=Decimal("100"))

    with pytest.raises(ValueError):
        _snapshot(best_bid=Decimal("101"), best_ask=Decimal("100"))


def test_snapshot_rejects_naive_timestamp() -> None:
    with pytest.raises(ValueError):
        _snapshot(timestamp=datetime(2026, 8, 9, 7, 0))


def test_snapshot_rejects_empty_source_id() -> None:
    with pytest.raises(ValueError):
        _snapshot(source_id="")


@given(
    bid=st.decimals(min_value="0.01", max_value="999999", places=2),
    spread=st.decimals(min_value="0.01", max_value="1000", places=2),
)
def test_valid_snapshot_mid_is_strictly_inside_spread(bid: Decimal, spread: Decimal) -> None:
    ask = bid + spread
    snapshot = _snapshot(best_bid=bid, best_ask=ask)

    assert snapshot.best_bid < snapshot.mid < snapshot.best_ask
