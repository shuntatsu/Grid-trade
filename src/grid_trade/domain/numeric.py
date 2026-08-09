from contextlib import AbstractContextManager
from decimal import Context, localcontext, ROUND_HALF_EVEN

_DETERMINISTIC_DECIMAL_CONTEXT = Context(
    prec=50,
    rounding=ROUND_HALF_EVEN,
)


def deterministic_decimal_context() -> AbstractContextManager[Context]:
    """Return a fixed local Decimal context for evidence-sensitive arithmetic."""
    return localcontext(_DETERMINISTIC_DECIMAL_CONTEXT)


__all__ = ["deterministic_decimal_context"]
