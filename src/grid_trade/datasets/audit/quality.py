from decimal import Decimal
from itertools import pairwise

from grid_trade.datasets.audit.models import AuditFinding, AuditSeverity
from grid_trade.datasets.audit_contracts import DatasetAuditExpectations
from grid_trade.datasets.canonical import (
    CanonicalBookSnapshot,
    CanonicalEventEnvelope,
    CanonicalEventType,
    CanonicalTrade,
)


def event_range(
    events: tuple[CanonicalEventEnvelope, ...],
    event_type: CanonicalEventType | None = None,
) -> tuple[int | None, int | None]:
    timestamps = tuple(
        event.exchange_ts_ns
        for event in events
        if event_type is None or event.event_type is event_type
    )
    if not timestamps:
        return None, None
    return min(timestamps), max(timestamps)


def book_trade_overlap_ns(
    *,
    book_start_ns: int | None,
    book_end_ns: int | None,
    trade_start_ns: int | None,
    trade_end_ns: int | None,
) -> int:
    if None in (book_start_ns, book_end_ns, trade_start_ns, trade_end_ns):
        return 0
    assert book_start_ns is not None
    assert book_end_ns is not None
    assert trade_start_ns is not None
    assert trade_end_ns is not None
    return max(0, min(book_end_ns, trade_end_ns) - max(book_start_ns, trade_start_ns))


def gap_statistics(events: tuple[CanonicalEventEnvelope, ...]) -> tuple[int, int]:
    timestamps = sorted(event.exchange_ts_ns for event in events)
    gaps = sorted(current - previous for previous, current in pairwise(timestamps))
    if not gaps:
        return 0, 0
    nearest_rank_index = max(0, (95 * len(gaps) + 99) // 100 - 1)
    return gaps[-1], gaps[nearest_rank_index]


def _aligned(value: Decimal, step: Decimal) -> bool:
    return value % step == 0


def alignment_findings(
    events: tuple[CanonicalEventEnvelope, ...],
    expectations: DatasetAuditExpectations,
) -> tuple[AuditFinding, ...]:
    findings: list[AuditFinding] = []
    for index, event in enumerate(events):
        prices: tuple[Decimal, ...] = ()
        quantities: tuple[Decimal, ...] = ()
        if event.event_type is CanonicalEventType.BOOK_SNAPSHOT:
            book = event.payload
            if not isinstance(book, CanonicalBookSnapshot):
                raise TypeError("validated book event must carry CanonicalBookSnapshot payload")
            levels = (*book.bids, *book.asks)
            prices = tuple(level.price for level in levels)
            quantities = tuple(level.quantity for level in levels)
        elif event.event_type is CanonicalEventType.TRADE:
            trade = event.payload
            if not isinstance(trade, CanonicalTrade):
                raise TypeError("validated trade event must carry CanonicalTrade payload")
            prices = (trade.price,)
            quantities = (trade.quantity,)

        if expectations.tick_size is not None and any(
            not _aligned(price, expectations.tick_size) for price in prices
        ):
            findings.append(
                AuditFinding(
                    code="tick_alignment_violation",
                    severity=AuditSeverity.ERROR,
                    message="book/trade price is not aligned to the declared tick size",
                    event_index=index,
                    exchange_ts_ns=event.exchange_ts_ns,
                )
            )
        if expectations.lot_size is not None and any(
            not _aligned(quantity, expectations.lot_size) for quantity in quantities
        ):
            findings.append(
                AuditFinding(
                    code="lot_alignment_violation",
                    severity=AuditSeverity.ERROR,
                    message="book/trade quantity is not aligned to the declared lot size",
                    event_index=index,
                    exchange_ts_ns=event.exchange_ts_ns,
                )
            )
    return tuple(findings)


__all__ = [
    "alignment_findings",
    "book_trade_overlap_ns",
    "event_range",
    "gap_statistics",
]
