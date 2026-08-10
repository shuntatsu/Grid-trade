from decimal import Decimal

import pytest

from grid_trade.domain.instrument import (
    ContractType,
    InstrumentSpec,
    instruments_compatible,
    require_instruments_compatible,
)


def _spec(**overrides: object) -> InstrumentSpec:
    values: dict[str, object] = {
        "instrument_id": "BTC-PERP",
        "contract_type": ContractType.LINEAR_PERPETUAL,
        "contract_multiplier": Decimal("1"),
        "tick_size": Decimal("0.1"),
        "quantity_step": Decimal("0.001"),
        "min_quantity": Decimal("0.002"),
        "min_notional": Decimal("10"),
        "max_quantity": Decimal("5"),
        "funding_interval_seconds": 3_600,
    }
    values.update(overrides)
    return InstrumentSpec(**values)  # type: ignore[arg-type]


def test_linear_perpetual_rounding_notional_and_executability() -> None:
    spec = _spec()

    assert spec.floor_quantity(Decimal("0.0029")) == Decimal("0.002")
    assert spec.notional(Decimal("-0.002"), Decimal("60000")) == Decimal("120.000")
    assert spec.is_executable(Decimal("0.002"), Decimal("60000"))
    assert not spec.is_executable(Decimal("0.001"), Decimal("60000"))
    assert not spec.is_executable(Decimal("0.0025"), Decimal("60000"))
    assert not spec.is_executable(Decimal("5.001"), Decimal("60000"))


def test_instrument_contract_rejects_invalid_bounds_and_identity() -> None:
    with pytest.raises(ValueError, match="instrument_id"):
        _spec(instrument_id=" ")
    with pytest.raises(ValueError, match="min_quantity"):
        _spec(min_quantity=Decimal("6"))
    with pytest.raises(ValueError, match="funding_interval_seconds"):
        _spec(funding_interval_seconds=0)


def test_instrument_identity_compatibility_is_explicit() -> None:
    assert instruments_compatible("BTC-PERP", "BTC-PERP")
    assert not instruments_compatible("BTC-PERP", "ETH-PERP")

    with pytest.raises(ValueError, match="calibration instrument mismatch"):
        require_instruments_compatible(
            "BTC-PERP",
            "ETH-PERP",
            context="calibration",
        )
