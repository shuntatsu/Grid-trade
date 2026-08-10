import contextlib
import decimal

_DETERMINISTIC_DECIMAL_CONTEXT = decimal.Context(
    prec=50,
    rounding=decimal.ROUND_HALF_EVEN,
)


def deterministic_decimal_context() -> contextlib.AbstractContextManager[decimal.Context]:
    """Return a fixed local Decimal context for evidence-sensitive arithmetic."""
    return decimal.localcontext(_DETERMINISTIC_DECIMAL_CONTEXT)


__all__ = ["deterministic_decimal_context"]
