from datetime import UTC, datetime
from decimal import Decimal

import pytest

from grid_trade.domain.market import MarketSnapshot
from grid_trade.strategy.volatility_spacing import (
    VolatilitySpacingConfig,
    propose_volatility_spacing,
)

_NOW = datetime(2026, 8, 9, 10, 40, tzinfo=UTC)


def _snapshot(*, vol: str) -> MarketSnapshot:
    return MarketSnapshot(
        timestamp=_NOW,
        best_bid=Decimal("99.99"),
        best_ask=Decimal("100.01"),
        realized_volatility=Decimal(vol),
        position_quantity=Decimal("0"),
        source_id="fixture:s2-spacing",
    )


def _config(**overrides: Decimal) -> VolatilitySpacingConfig:
    values = {
        "min_spacing_bps": Decimal("10"),
        "max_spacing_bps": Decimal("100"),
        "volatility_multiplier": Decimal("0.5"),
        "execution_cost_floor_bps": Decimal("12"),
    }
    values.update(overrides)
    return VolatilitySpacingConfig(**values)


def test_volatility_component_widens_spacing() -> None:
    decision = propose_volatility_spacing(
        _snapshot(vol="0.004"),
        previous_spacing_bps=12,
        config=_config(),
    )

    assert decision.volatility_spacing_bps == Decimal("20.0000")
    assert decision.unclamped_spacing_bps == Decimal("20.0000")
    assert decision.effective_spacing_bps == 20
    assert decision.changed is True


def test_execution_floor_is_rounded_up_not_down() -> None:
    decision = propose_volatility_spacing(
        _snapshot(vol="0"),
        previous_spacing_bps=13,
        config=_config(
            min_spacing_bps=Decimal("8"),
            execution_cost_floor_bps=Decimal("12.1"),
            volatility_multiplier=Decimal("1"),
        ),
    )

    assert decision.unclamped_spacing_bps == Decimal("12.1")
    assert decision.effective_spacing_bps == 13
    assert decision.changed is False


def test_maximum_caps_extreme_volatility() -> None:
    decision = propose_volatility_spacing(
        _snapshot(vol="0.5"),
        previous_spacing_bps=20,
        config=_config(max_spacing_bps=Decimal("75")),
    )

    assert decision.volatility_spacing_bps == Decimal("2500.00")
    assert decision.unclamped_spacing_bps == Decimal("2500.00")
    assert decision.effective_spacing_bps == 75


def test_low_volatility_can_narrow_but_not_below_floor() -> None:
    decision = propose_volatility_spacing(
        _snapshot(vol="0.0001"),
        previous_spacing_bps=50,
        config=_config(),
    )

    assert decision.volatility_spacing_bps == Decimal("0.50000")
    assert decision.effective_spacing_bps == 12
    assert decision.changed is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("min_spacing_bps", Decimal("0")),
        ("min_spacing_bps", Decimal("NaN")),
        ("max_spacing_bps", Decimal("0")),
        ("volatility_multiplier", Decimal("0")),
        ("execution_cost_floor_bps", Decimal("0")),
    ],
)
def test_config_requires_finite_positive_values(field: str, value: Decimal) -> None:
    with pytest.raises(ValueError, match=field):
        _config(**{field: value})


def test_config_rejects_max_below_minimum() -> None:
    with pytest.raises(ValueError, match="max_spacing_bps"):
        _config(min_spacing_bps=Decimal("20"), max_spacing_bps=Decimal("19"))


def test_config_rejects_execution_floor_above_maximum() -> None:
    with pytest.raises(ValueError, match="execution_cost_floor_bps"):
        _config(max_spacing_bps=Decimal("12"), execution_cost_floor_bps=Decimal("12.1"))


def test_config_rejects_non_integral_or_too_large_maximum() -> None:
    with pytest.raises(ValueError, match="max_spacing_bps"):
        _config(max_spacing_bps=Decimal("100.5"))
    with pytest.raises(ValueError, match="max_spacing_bps"):
        _config(max_spacing_bps=Decimal("10000"))


def test_previous_spacing_must_be_positive() -> None:
    with pytest.raises(ValueError, match="previous_spacing_bps"):
        propose_volatility_spacing(_snapshot(vol="0.001"), 0, _config())
