from decimal import Decimal

from grid_trade.domain.numeric import deterministic_decimal_context


def median_decimal(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        raise ValueError("median requires at least one value")
    for value in values:
        if not isinstance(value, Decimal) or not value.is_finite():
            raise ValueError("robust statistics require finite Decimal values")

    ordered = tuple(sorted(values))
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    with deterministic_decimal_context():
        return (ordered[midpoint - 1] + ordered[midpoint]) / Decimal(2)


def mad_decimal(values: tuple[Decimal, ...], *, center: Decimal | None = None) -> Decimal:
    effective_center = median_decimal(values) if center is None else center
    if not isinstance(effective_center, Decimal) or not effective_center.is_finite():
        raise ValueError("center must be a finite Decimal")
    with deterministic_decimal_context():
        deviations = tuple(abs(value - effective_center) for value in values)
    return median_decimal(deviations)


__all__ = ["mad_decimal", "median_decimal"]
