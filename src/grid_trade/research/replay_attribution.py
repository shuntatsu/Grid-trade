from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal

from grid_trade.datasets.canonical import (
    BookSide,
    BookVisibilityTracker,
    CanonicalBookSnapshot,
    CanonicalEventEnvelope,
    CanonicalEventType,
    CanonicalFundingReference,
    VisibilityChange,
    canonical_event_sort_key,
)
from grid_trade.domain.orders import OrderSide

_DEFAULT_PARTICIPATION_QUANTILE = Decimal("0.95")


def _require_finite(value: Decimal, *, field: str) -> None:
    if not value.is_finite():
        raise ValueError(f"{field} must be finite")


def _require_finite_positive(value: Decimal, *, field: str) -> None:
    _require_finite(value, field=field)
    if value <= 0:
        raise ValueError(f"{field} must be positive")


def _require_finite_non_negative(value: Decimal, *, field: str) -> None:
    _require_finite(value, field=field)
    if value < 0:
        raise ValueError(f"{field} must be non-negative")


@dataclass(frozen=True, slots=True)
class FundingCashFlow:
    timestamp_ns: int
    position: Decimal
    funding_rate: Decimal
    reference_price: Decimal
    cash_flow: Decimal

    def __post_init__(self) -> None:
        if self.timestamp_ns < 0:
            raise ValueError("timestamp_ns must be non-negative")
        _require_finite(self.position, field="position")
        _require_finite(self.funding_rate, field="funding_rate")
        _require_finite_positive(self.reference_price, field="reference_price")
        _require_finite(self.cash_flow, field="cash_flow")


@dataclass(frozen=True, slots=True)
class ReplayPnlAttribution:
    realized_spread_capture: Decimal
    directional_inventory_pnl: Decimal
    funding_pnl: Decimal
    fee_cost: Decimal
    adverse_selection_cost: Decimal
    emergency_execution_cost: Decimal

    def __post_init__(self) -> None:
        _require_finite(self.realized_spread_capture, field="realized_spread_capture")
        _require_finite(self.directional_inventory_pnl, field="directional_inventory_pnl")
        _require_finite(self.funding_pnl, field="funding_pnl")
        _require_finite_non_negative(self.fee_cost, field="fee_cost")
        _require_finite_non_negative(self.adverse_selection_cost, field="adverse_selection_cost")
        _require_finite_non_negative(
            self.emergency_execution_cost,
            field="emergency_execution_cost",
        )

    @property
    def net_pnl(self) -> Decimal:
        return (
            self.realized_spread_capture
            + self.directional_inventory_pnl
            + self.funding_pnl
            - self.fee_cost
            - self.adverse_selection_cost
            - self.emergency_execution_cost
        )


@dataclass(frozen=True, slots=True)
class MarketImpactEligibilityConfig:
    max_same_level_participation: Decimal
    max_top_n_participation: Decimal

    def __post_init__(self) -> None:
        for field, value in (
            ("max_same_level_participation", self.max_same_level_participation),
            ("max_top_n_participation", self.max_top_n_participation),
        ):
            _require_finite_positive(value, field=field)
            if value > 1:
                raise ValueError(f"{field} must be at most 1")


@dataclass(frozen=True, slots=True)
class OrderLiquidityEligibility:
    eligible: bool
    reason: str
    order_notional: Decimal
    visible_same_level_quantity: Decimal | None
    visible_top_n_notional: Decimal | None
    same_level_participation: Decimal | None
    top_n_participation: Decimal | None
    visibility_boundary_ts_ns: int | None = None

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("reason must be non-empty")
        _require_finite_positive(self.order_notional, field="order_notional")
        for field, value in (
            ("visible_same_level_quantity", self.visible_same_level_quantity),
            ("visible_top_n_notional", self.visible_top_n_notional),
            ("same_level_participation", self.same_level_participation),
            ("top_n_participation", self.top_n_participation),
        ):
            if value is not None:
                _require_finite_non_negative(value, field=field)
        if self.visibility_boundary_ts_ns is not None and self.visibility_boundary_ts_ns < 0:
            raise ValueError("visibility_boundary_ts_ns must be non-negative")


@dataclass(frozen=True, slots=True)
class ReplayLiquiditySummary:
    participation_quantile: Decimal
    max_same_level_participation: Decimal | None
    high_quantile_same_level_participation: Decimal | None
    max_top_n_participation: Decimal | None
    high_quantile_top_n_participation: Decimal | None
    earliest_visibility_boundary_ts_ns: int | None

    def __post_init__(self) -> None:
        _require_finite_positive(self.participation_quantile, field="participation_quantile")
        if self.participation_quantile > 1:
            raise ValueError("participation_quantile must be at most 1")
        for field, value in (
            ("max_same_level_participation", self.max_same_level_participation),
            (
                "high_quantile_same_level_participation",
                self.high_quantile_same_level_participation,
            ),
            ("max_top_n_participation", self.max_top_n_participation),
            ("high_quantile_top_n_participation", self.high_quantile_top_n_participation),
        ):
            if value is not None:
                _require_finite_non_negative(value, field=field)
        if (
            self.earliest_visibility_boundary_ts_ns is not None
            and self.earliest_visibility_boundary_ts_ns < 0
        ):
            raise ValueError("earliest_visibility_boundary_ts_ns must be non-negative")


def funding_cash_flow(
    *,
    timestamp_ns: int,
    position: Decimal,
    reference: CanonicalFundingReference,
) -> FundingCashFlow:
    if reference.funding_rate is None:
        raise ValueError("funding_rate is required for funding cash flow")
    if reference.oracle_price is None:
        raise ValueError("oracle_price is required for funding cash flow")
    _require_finite(position, field="position")
    payment = position * reference.oracle_price * reference.funding_rate
    return FundingCashFlow(
        timestamp_ns=timestamp_ns,
        position=position,
        funding_rate=reference.funding_rate,
        reference_price=reference.oracle_price,
        cash_flow=-payment,
    )


def _ineligible(
    *,
    reason: str,
    order_notional: Decimal,
    visible_same_level_quantity: Decimal | None,
    visible_top_n_notional: Decimal | None,
    same_level_participation: Decimal | None = None,
    top_n_participation: Decimal | None = None,
) -> OrderLiquidityEligibility:
    return OrderLiquidityEligibility(
        eligible=False,
        reason=reason,
        order_notional=order_notional,
        visible_same_level_quantity=visible_same_level_quantity,
        visible_top_n_notional=visible_top_n_notional,
        same_level_participation=same_level_participation,
        top_n_participation=top_n_participation,
    )


def assess_order_liquidity_eligibility(
    *,
    order_price: Decimal,
    order_quantity: Decimal,
    visible_same_level_quantity: Decimal | None,
    visible_top_n_notional: Decimal | None,
    visibility_trusted: bool,
    config: MarketImpactEligibilityConfig,
) -> OrderLiquidityEligibility:
    _require_finite_positive(order_price, field="order_price")
    _require_finite_positive(order_quantity, field="order_quantity")
    order_notional = order_price * order_quantity

    if not visibility_trusted:
        return _ineligible(
            reason="visibility_untrusted",
            order_notional=order_notional,
            visible_same_level_quantity=visible_same_level_quantity,
            visible_top_n_notional=visible_top_n_notional,
        )
    if visible_same_level_quantity is None or visible_same_level_quantity <= 0:
        if visible_same_level_quantity is not None:
            _require_finite_non_negative(
                visible_same_level_quantity,
                field="visible_same_level_quantity",
            )
        return _ineligible(
            reason="same_level_visibility_unavailable",
            order_notional=order_notional,
            visible_same_level_quantity=visible_same_level_quantity,
            visible_top_n_notional=visible_top_n_notional,
        )
    if visible_top_n_notional is None or visible_top_n_notional <= 0:
        if visible_top_n_notional is not None:
            _require_finite_non_negative(
                visible_top_n_notional,
                field="visible_top_n_notional",
            )
        return _ineligible(
            reason="top_n_visibility_unavailable",
            order_notional=order_notional,
            visible_same_level_quantity=visible_same_level_quantity,
            visible_top_n_notional=visible_top_n_notional,
        )

    _require_finite_positive(
        visible_same_level_quantity,
        field="visible_same_level_quantity",
    )
    _require_finite_positive(visible_top_n_notional, field="visible_top_n_notional")
    same_level_participation = order_quantity / visible_same_level_quantity
    top_n_participation = order_notional / visible_top_n_notional

    if same_level_participation > config.max_same_level_participation:
        return _ineligible(
            reason="same_level_participation_exceeded",
            order_notional=order_notional,
            visible_same_level_quantity=visible_same_level_quantity,
            visible_top_n_notional=visible_top_n_notional,
            same_level_participation=same_level_participation,
            top_n_participation=top_n_participation,
        )
    if top_n_participation > config.max_top_n_participation:
        return _ineligible(
            reason="top_n_participation_exceeded",
            order_notional=order_notional,
            visible_same_level_quantity=visible_same_level_quantity,
            visible_top_n_notional=visible_top_n_notional,
            same_level_participation=same_level_participation,
            top_n_participation=top_n_participation,
        )

    return OrderLiquidityEligibility(
        eligible=True,
        reason="eligible",
        order_notional=order_notional,
        visible_same_level_quantity=visible_same_level_quantity,
        visible_top_n_notional=visible_top_n_notional,
        same_level_participation=same_level_participation,
        top_n_participation=top_n_participation,
    )


def first_order_visibility_loss_ns(
    events: tuple[CanonicalEventEnvelope, ...],
    *,
    side: OrderSide,
    price: Decimal,
) -> int | None:
    _require_finite_positive(price, field="price")
    if events != tuple(sorted(events, key=canonical_event_sort_key)):
        raise ValueError("events must be in canonical order")

    book_side = BookSide.BID if side is OrderSide.BUY else BookSide.ASK
    tracker = BookVisibilityTracker()
    for event in events:
        if event.event_type is not CanonicalEventType.BOOK_SNAPSHOT:
            continue
        snapshot = event.payload
        if not isinstance(snapshot, CanonicalBookSnapshot):
            raise TypeError("validated book event must carry CanonicalBookSnapshot payload")
        updates = tracker.apply(snapshot)
        if any(
            update.side is book_side
            and update.price == price
            and update.change is VisibilityChange.VISIBILITY_LOST
            for update in updates
        ):
            return event.exchange_ts_ns
    return None


def _nearest_rank(values: tuple[Decimal, ...], quantile: Decimal) -> Decimal | None:
    if not values:
        return None
    ordered = tuple(sorted(values))
    rank = int((quantile * len(ordered)).to_integral_value(rounding=ROUND_CEILING))
    return ordered[max(0, rank - 1)]


def summarize_order_liquidity(
    decisions: tuple[OrderLiquidityEligibility, ...],
    *,
    participation_quantile: Decimal = _DEFAULT_PARTICIPATION_QUANTILE,
) -> ReplayLiquiditySummary:
    _require_finite_positive(participation_quantile, field="participation_quantile")
    if participation_quantile > 1:
        raise ValueError("participation_quantile must be at most 1")

    same_level = tuple(
        decision.same_level_participation
        for decision in decisions
        if decision.same_level_participation is not None
    )
    top_n = tuple(
        decision.top_n_participation
        for decision in decisions
        if decision.top_n_participation is not None
    )
    boundaries = tuple(
        decision.visibility_boundary_ts_ns
        for decision in decisions
        if decision.visibility_boundary_ts_ns is not None
    )
    return ReplayLiquiditySummary(
        participation_quantile=participation_quantile,
        max_same_level_participation=max(same_level, default=None),
        high_quantile_same_level_participation=_nearest_rank(same_level, participation_quantile),
        max_top_n_participation=max(top_n, default=None),
        high_quantile_top_n_participation=_nearest_rank(top_n, participation_quantile),
        earliest_visibility_boundary_ts_ns=min(boundaries, default=None),
    )


__all__ = [
    "FundingCashFlow",
    "MarketImpactEligibilityConfig",
    "OrderLiquidityEligibility",
    "ReplayLiquiditySummary",
    "ReplayPnlAttribution",
    "assess_order_liquidity_eligibility",
    "first_order_visibility_loss_ns",
    "funding_cash_flow",
    "summarize_order_liquidity",
]
