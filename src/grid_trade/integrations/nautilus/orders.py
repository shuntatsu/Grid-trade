from decimal import Decimal
from importlib.metadata import version as distribution_version
from typing import Any

from grid_trade.domain.orders import OrderSide, PassiveOrderIntent

_NAUTILUS_VERSION = "1.230.0"


def require_nautilus_runtime() -> None:
    installed = distribution_version("nautilus_trader")
    if installed != _NAUTILUS_VERSION:
        raise RuntimeError(
            f"NautilusTrader runtime mismatch: expected {_NAUTILUS_VERSION}, got {installed}",
        )


def _increment_as_decimal(value: object, *, field: str) -> Decimal:
    try:
        increment = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{field} must be convertible to Decimal") from exc
    if not increment.is_finite() or increment <= 0:
        raise ValueError(f"{field} must be finite and positive")
    return increment


def _validate_alignment(*, instrument: object, intent: PassiveOrderIntent) -> None:
    price_increment = _increment_as_decimal(
        getattr(instrument, "price_increment", None),
        field="price_increment",
    )
    size_increment = _increment_as_decimal(
        getattr(instrument, "size_increment", None),
        field="size_increment",
    )
    if intent.price % price_increment != 0:
        raise ValueError(f"order {intent.client_order_id} price is not tick aligned")
    if intent.quantity % size_increment != 0:
        raise ValueError(f"order {intent.client_order_id} quantity is not lot aligned")


def build_nautilus_post_only_order(
    *,
    strategy: object,
    instrument: object,
    intent: PassiveOrderIntent,
) -> Any:
    require_nautilus_runtime()
    _validate_alignment(instrument=instrument, intent=intent)

    from nautilus_trader.model.enums import OrderSide as NautilusOrderSide
    from nautilus_trader.model.enums import TimeInForce
    from nautilus_trader.model.objects import Price, Quantity

    instrument_id = getattr(instrument, "id", None)
    if instrument_id is None:
        raise ValueError("instrument must expose id")
    order_factory = getattr(strategy, "order_factory", None)
    if order_factory is None:
        raise ValueError("strategy must expose order_factory")

    side = (
        NautilusOrderSide.BUY
        if intent.side is OrderSide.BUY
        else NautilusOrderSide.SELL
    )
    return order_factory.limit(
        instrument_id=instrument_id,
        order_side=side,
        quantity=Quantity.from_str(str(intent.quantity)),
        price=Price.from_str(str(intent.price)),
        time_in_force=TimeInForce.GTC,
        post_only=True,
        reduce_only=intent.reduce_only,
        tags=[f"grid_client_order_id={intent.client_order_id}"],
    )


__all__ = ["build_nautilus_post_only_order", "require_nautilus_runtime"]
