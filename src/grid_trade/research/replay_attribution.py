from dataclasses import dataclass
from decimal import Decimal

from grid_trade.datasets.canonical import CanonicalFundingReference


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


__all__ = [
    "FundingCashFlow",
    "MarketImpactEligibilityConfig",
    "OrderLiquidityEligibility",
    "ReplayPnlAttribution",
    "assess_order_liquidity_eligibility",
    "funding_cash_flow",
]
