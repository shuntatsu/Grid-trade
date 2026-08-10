from decimal import Decimal

import pytest

from grid_trade.datasets.canonical import CanonicalFundingReference
from grid_trade.research.replay_attribution import (
    MarketImpactEligibilityConfig,
    ReplayPnlAttribution,
    assess_order_liquidity_eligibility,
    funding_cash_flow,
)

pytestmark = pytest.mark.research


def test_pnl_attribution_matches_additive_identity() -> None:
    attribution = ReplayPnlAttribution(
        realized_spread_capture=Decimal("10"),
        directional_inventory_pnl=Decimal("3"),
        funding_pnl=Decimal("2"),
        fee_cost=Decimal("1"),
        adverse_selection_cost=Decimal("0.5"),
        emergency_execution_cost=Decimal("0.25"),
    )

    assert attribution.net_pnl == Decimal("13.25")


def test_positive_funding_rate_charges_long_and_pays_short_using_oracle() -> None:
    reference = CanonicalFundingReference(
        funding_rate=Decimal("0.001"),
        mark_price=Decimal("101"),
        oracle_price=Decimal("100"),
    )

    long_flow = funding_cash_flow(
        timestamp_ns=3_600_000_000_000,
        position=Decimal("2"),
        reference=reference,
    )
    short_flow = funding_cash_flow(
        timestamp_ns=3_600_000_000_000,
        position=Decimal("-2"),
        reference=reference,
    )

    assert long_flow.reference_price == Decimal("100")
    assert long_flow.cash_flow == Decimal("-0.2")
    assert short_flow.cash_flow == Decimal("0.2")


def test_missing_required_funding_or_oracle_fails_closed() -> None:
    missing_funding = CanonicalFundingReference(
        funding_rate=None,
        mark_price=Decimal("101"),
        oracle_price=Decimal("100"),
    )
    missing_oracle = CanonicalFundingReference(
        funding_rate=Decimal("0.001"),
        mark_price=Decimal("101"),
        oracle_price=None,
    )

    with pytest.raises(ValueError, match="funding_rate"):
        funding_cash_flow(timestamp_ns=1, position=Decimal("1"), reference=missing_funding)
    with pytest.raises(ValueError, match="oracle_price"):
        funding_cash_flow(timestamp_ns=1, position=Decimal("1"), reference=missing_oracle)


def test_liquidity_eligibility_passes_only_with_trusted_visible_capacity() -> None:
    config = MarketImpactEligibilityConfig(
        max_same_level_participation=Decimal("0.25"),
        max_top_n_participation=Decimal("0.05"),
    )

    eligible = assess_order_liquidity_eligibility(
        order_price=Decimal("100"),
        order_quantity=Decimal("1"),
        visible_same_level_quantity=Decimal("10"),
        visible_top_n_notional=Decimal("5000"),
        visibility_trusted=True,
        config=config,
    )
    untrusted = assess_order_liquidity_eligibility(
        order_price=Decimal("100"),
        order_quantity=Decimal("1"),
        visible_same_level_quantity=Decimal("10"),
        visible_top_n_notional=Decimal("5000"),
        visibility_trusted=False,
        config=config,
    )
    oversized = assess_order_liquidity_eligibility(
        order_price=Decimal("100"),
        order_quantity=Decimal("3"),
        visible_same_level_quantity=Decimal("10"),
        visible_top_n_notional=Decimal("5000"),
        visibility_trusted=True,
        config=config,
    )

    assert eligible.eligible is True
    assert eligible.same_level_participation == Decimal("0.1")
    assert eligible.top_n_participation == Decimal("0.02")
    assert untrusted.eligible is False
    assert untrusted.reason == "visibility_untrusted"
    assert oversized.eligible is False
    assert oversized.reason == "same_level_participation_exceeded"


def test_liquidity_eligibility_rejects_missing_or_zero_visible_capacity() -> None:
    config = MarketImpactEligibilityConfig(
        max_same_level_participation=Decimal("0.25"),
        max_top_n_participation=Decimal("0.05"),
    )

    result = assess_order_liquidity_eligibility(
        order_price=Decimal("100"),
        order_quantity=Decimal("1"),
        visible_same_level_quantity=None,
        visible_top_n_notional=Decimal("5000"),
        visibility_trusted=True,
        config=config,
    )

    assert result.eligible is False
    assert result.reason == "same_level_visibility_unavailable"
