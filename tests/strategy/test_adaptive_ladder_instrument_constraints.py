from decimal import Decimal

from grid_trade.domain.instrument import ContractType, InstrumentSpec
from grid_trade.strategy.adaptive_ladder import AdaptiveLadderConfig, build_adaptive_ladder


def _instrument(*, min_notional: str = "1") -> InstrumentSpec:
    return InstrumentSpec(
        instrument_id="BTC-PERP",
        contract_type=ContractType.LINEAR_PERPETUAL,
        contract_multiplier=Decimal("1"),
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.01"),
        min_quantity=Decimal("0.01"),
        min_notional=Decimal(min_notional),
        max_quantity=Decimal("1"),
        funding_interval_seconds=3_600,
    )


def test_unexecutable_residual_quantity_is_omitted() -> None:
    spec = _instrument()
    config = AdaptiveLadderConfig(
        levels=3,
        spacing_bps=10,
        order_quantity=Decimal("0.02"),
        tick_size=spec.tick_size,
        max_abs_inventory=Decimal("0.025"),
        instrument_id=spec.instrument_id,
        instrument=spec,
    )

    orders = build_adaptive_ladder(
        reference=Decimal("100"),
        position=Decimal("0"),
        target=Decimal("0.02"),
        bid_scale=Decimal("1"),
        ask_scale=Decimal("1"),
        config=config,
        generation=0,
        stage="adaptive",
    )

    assert tuple(order.quantity for order in orders) == (Decimal("0.02"),)
    assert all(spec.is_executable(order.quantity, order.price) for order in orders)


def test_actual_level_price_enforces_minimum_notional() -> None:
    spec = _instrument(min_notional="2")
    config = AdaptiveLadderConfig(
        levels=1,
        spacing_bps=100,
        order_quantity=Decimal("0.02"),
        tick_size=spec.tick_size,
        max_abs_inventory=Decimal("1"),
        instrument_id=spec.instrument_id,
        instrument=spec,
    )

    assert (
        build_adaptive_ladder(
            reference=Decimal("100"),
            position=Decimal("0"),
            target=Decimal("0.5"),
            bid_scale=Decimal("1"),
            ask_scale=Decimal("1"),
            config=config,
            generation=0,
            stage="adaptive",
        )
        == ()
    )
