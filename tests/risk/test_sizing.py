from decimal import Decimal, localcontext

import pytest

from grid_trade.risk.sizing import (
    RiskSizingConfig,
    RiskSizingInput,
    derive_inventory_capacity,
)


def _config() -> RiskSizingConfig:
    return RiskSizingConfig(
        max_notional_fraction=Decimal("0.50"),
        max_single_move_loss_fraction=Decimal("0.01"),
        volatility_floor=Decimal("0.001"),
    )


def _input(
    *,
    price: str = "100",
    volatility: str = "0.02",
    max_margin_notional: str = "1000",
    venue_max_quantity: str = "100",
    contract_multiplier: str = "1",
) -> RiskSizingInput:
    return RiskSizingInput(
        equity=Decimal("1000"),
        reference_price=Decimal(price),
        volatility_scale=Decimal(volatility),
        max_margin_notional=Decimal(max_margin_notional),
        venue_max_quantity=Decimal(venue_max_quantity),
        contract_multiplier=Decimal(contract_multiplier),
    )


def test_quantity_capacity_scales_inverse_to_price() -> None:
    low = derive_inventory_capacity(_input(price="100"), _config())
    high = derive_inventory_capacity(_input(price="200"), _config())

    assert high.q_notional == low.q_notional / 2
    assert high.q_margin == low.q_margin / 2
    assert high.q_volatility == low.q_volatility / 2


def test_quantity_capacity_scales_inverse_to_contract_multiplier() -> None:
    unit = derive_inventory_capacity(_input(contract_multiplier="1"), _config())
    tenfold = derive_inventory_capacity(_input(contract_multiplier="10"), _config())

    assert tenfold.q_notional == unit.q_notional / 10
    assert tenfold.q_margin == unit.q_margin / 10
    assert tenfold.q_volatility == unit.q_volatility / 10


def test_sizing_is_independent_of_ambient_decimal_precision() -> None:
    inputs = RiskSizingInput(
        equity=Decimal("1234.567"),
        reference_price=Decimal("137.29"),
        volatility_scale=Decimal("0.0173"),
        max_margin_notional=Decimal("987.654"),
        venue_max_quantity=Decimal("12.3456"),
    )
    with localcontext() as context:
        context.prec = 10
        low_precision = derive_inventory_capacity(inputs, _config())
    with localcontext() as context:
        context.prec = 50
        high_precision = derive_inventory_capacity(inputs, _config())

    assert low_precision == high_precision


def test_most_conservative_constraint_binds() -> None:
    result = derive_inventory_capacity(_input(venue_max_quantity="0.01"), _config())

    assert result.q_max == Decimal("0.01")
    assert result.binding_constraint == "venue"


def test_volatility_floor_prevents_division_by_zero() -> None:
    zero = derive_inventory_capacity(_input(volatility="0"), _config())
    floored = derive_inventory_capacity(_input(volatility="0.0001"), _config())

    assert zero.q_volatility == floored.q_volatility


def test_qmax_never_exceeds_any_component() -> None:
    result = derive_inventory_capacity(_input(), _config())

    assert result.q_max <= result.q_notional
    assert result.q_max <= result.q_margin
    assert result.q_max <= result.q_volatility
    assert result.q_max <= result.q_venue


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("equity", Decimal("0")),
        ("reference_price", Decimal("0")),
        ("volatility_scale", Decimal("-0.1")),
        ("max_margin_notional", Decimal("0")),
        ("venue_max_quantity", Decimal("0")),
        ("contract_multiplier", Decimal("0")),
    ],
)
def test_invalid_sizing_inputs_fail_closed(field: str, value: Decimal) -> None:
    values = {
        "equity": Decimal("1000"),
        "reference_price": Decimal("100"),
        "volatility_scale": Decimal("0.02"),
        "max_margin_notional": Decimal("1000"),
        "venue_max_quantity": Decimal("100"),
        "contract_multiplier": Decimal("1"),
    }
    values[field] = value
    with pytest.raises(ValueError, match=field):
        RiskSizingInput(**values)


def test_invalid_sizing_config_fails_closed() -> None:
    with pytest.raises(ValueError, match="max_notional_fraction"):
        RiskSizingConfig(Decimal("0"), Decimal("0.01"), Decimal("0.001"))
    with pytest.raises(ValueError, match="max_single_move_loss_fraction"):
        RiskSizingConfig(Decimal("0.5"), Decimal("0"), Decimal("0.001"))
    with pytest.raises(ValueError, match="volatility_floor"):
        RiskSizingConfig(Decimal("0.5"), Decimal("0.01"), Decimal("0"))
