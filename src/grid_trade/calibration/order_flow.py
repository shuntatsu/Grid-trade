from decimal import Decimal

from grid_trade.calibration.microstructure_contracts import TopOfBookObservation
from grid_trade.domain.numeric import deterministic_decimal_context

_ZERO = Decimal(0)


def _validate_pair(
    previous: TopOfBookObservation,
    current: TopOfBookObservation,
) -> None:
    if current.timestamp <= previous.timestamp:
        raise ValueError("current timestamp must be strictly newer than previous timestamp")
    if current.source_id != previous.source_id:
        raise ValueError("source_id must remain constant across order-flow observations")
    if current.instrument_id != previous.instrument_id:
        raise ValueError("instrument_id must remain constant across order-flow observations")


def compute_ofi(
    previous: TopOfBookObservation,
    current: TopOfBookObservation,
) -> Decimal:
    _validate_pair(previous, current)

    with deterministic_decimal_context():
        bid_term = _ZERO
        if current.best_bid >= previous.best_bid:
            bid_term += current.bid_size
        if current.best_bid <= previous.best_bid:
            bid_term -= previous.bid_size

        ask_term = _ZERO
        if current.best_ask <= previous.best_ask:
            ask_term -= current.ask_size
        if current.best_ask >= previous.best_ask:
            ask_term += previous.ask_size

        return bid_term + ask_term


def normalized_ofi(
    previous: TopOfBookObservation,
    current: TopOfBookObservation,
) -> Decimal:
    _validate_pair(previous, current)

    with deterministic_decimal_context():
        depth_scale = (
            previous.bid_size
            + previous.ask_size
            + current.bid_size
            + current.ask_size
        ) / Decimal(4)
        if depth_scale <= 0:
            raise ValueError("average top-of-book depth must be positive")
        return compute_ofi(previous, current) / depth_scale


def microprice(book: TopOfBookObservation) -> Decimal:
    with deterministic_decimal_context():
        total_depth = book.bid_size + book.ask_size
        if total_depth <= 0:
            raise ValueError("top-of-book depth must be positive")
        return (
            book.best_ask * book.bid_size + book.best_bid * book.ask_size
        ) / total_depth


def microprice_displacement(book: TopOfBookObservation) -> Decimal:
    with deterministic_decimal_context():
        return (microprice(book) - book.mid) / book.mid


__all__ = [
    "compute_ofi",
    "microprice",
    "microprice_displacement",
    "normalized_ofi",
]
