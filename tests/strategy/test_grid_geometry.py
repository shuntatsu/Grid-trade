from decimal import Decimal

import pytest

from grid_trade.strategy.fixed_grid import FixedLongGridConfig
from grid_trade.strategy.grid_geometry import build_long_grid_at_center


def _config(**overrides: object) -> FixedLongGridConfig:
    values: dict[str, object] = {
        "levels": 3,
        "spacing_bps": 100,
        "order_quantity": Decimal("0.01"),
        "tick_size": Decimal("0.01"),
    }
    values.update(overrides)
    return FixedLongGridConfig(**values)  # type: ignore[arg-type]


def test_center_geometry_matches_existing_s0_prices() -> None:
    orders = build_long_grid_at_center(
        Decimal("100"),
        _config(),
        generation=7,
        stage="s1",
    )

    assert [order.price for order in orders] == [
        Decimal("99.00"),
        Decimal("98.00"),
        Decimal("97.00"),
    ]
    assert [order.client_order_id for order in orders] == [
        "s1:g7:buy:l1",
        "s1:g7:buy:l2",
        "s1:g7:buy:l3",
    ]


def test_geometry_rejects_empty_stage() -> None:
    with pytest.raises(ValueError, match="stage"):
        build_long_grid_at_center(
            Decimal("100"),
            _config(),
            generation=0,
            stage=" ",
        )


@pytest.mark.parametrize("center", [Decimal("0"), Decimal("-1"), Decimal("NaN")])
def test_geometry_rejects_invalid_center(center: Decimal) -> None:
    with pytest.raises(ValueError, match="center"):
        build_long_grid_at_center(center, _config(), generation=0, stage="s1")
