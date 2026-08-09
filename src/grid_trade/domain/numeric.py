from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Context, ROUND_HALF_EVEN, localcontext

_DETERMINISTIC_DECIMAL_CONTEXT = Context(
    prec=50,
    rounding=ROUND_HALF_EVEN,
)


@contextmanager
def deterministic_decimal_context() -> Iterator[Context]:
    """Run evidence-sensitive Decimal arithmetic under a fixed local context."""
    with localcontext(_DETERMINISTIC_DECIMAL_CONTEXT) as context:
        yield context


__all__ = ["deterministic_decimal_context"]
