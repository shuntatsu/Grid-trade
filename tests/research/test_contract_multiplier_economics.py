from decimal import Decimal

import pytest

from grid_trade.datasets.canonical import CanonicalFundingReference
from grid_trade.research.hftbacktest_adapter import HftReplayConfig
from grid_trade.research.replay_attribution import (
    MarketImpactEligibilityConfig,
    assess_order_liquidity_eligibility,
    funding_cash_flow,
    maker_fee_cash_flow,
)

pytestmark = pytest.mark.research


def test_funding_cash_flow_is_contract_multiplier_invariant_at_equal_notional() -> None:
    reference = CanonicalFundingReference(
        funding_rate=Decimal("0.001"),
        mark_price=Decimal("100"),
        oracle_price=Decimal("100"),
    )

    unit = funding_cash_flow(
        timestamp_ns=0,
        position=Decimal("1"),
        reference=reference,
        contract_multiplier=Decimal("1"),
    )
    scaled = funding_cash_flow(
        timestamp_ns=0,
        position=Decimal("0.1"),
        reference=reference,
        contract_multiplier=Decimal("10"),
    )

    assert scaled.cash_flow == unit.cash_flow


def test_liquidity_participation_is_contract_multiplier_invariant_at_equal_notional() -> None:
    config = MarketImpactEligibilityConfig(
        max_same_level_participation=Decimal("0.5"),
        max_top_n_participation=Decimal("0.5"),
    )

    unit = assess_order_liquidity_eligibility(
        order_price=Decimal("100"),
        order_quantity=Decimal("1"),
        contract_multiplier=Decimal("1"),
        visible_same_level_quantity=Decimal("10"),
        visible_top_n_notional=Decimal("10_000"),
        visibility_trusted=True,
        config=config,
    )
    scaled = assess_order_liquidity_eligibility(
        order_price=Decimal("100"),
        order_quantity=Decimal("0.1"),
        contract_multiplier=Decimal("10"),
        visible_same_level_quantity=Decimal("1"),
        visible_top_n_notional=Decimal("10_000"),
        visibility_trusted=True,
        config=config,
    )

    assert scaled.order_notional == unit.order_notional
    assert scaled.same_level_participation == unit.same_level_participation
    assert scaled.top_n_participation == unit.top_n_participation


def test_maker_fee_cash_flow_is_contract_multiplier_invariant_at_equal_notional() -> None:
    unit = maker_fee_cash_flow(
        price=Decimal("100"),
        quantity=Decimal("1"),
        maker_fee_rate=Decimal("0.0002"),
        contract_multiplier=Decimal("1"),
    )
    scaled = maker_fee_cash_flow(
        price=Decimal("100"),
        quantity=Decimal("0.1"),
        maker_fee_rate=Decimal("0.0002"),
        contract_multiplier=Decimal("10"),
    )

    assert scaled == unit


def test_hft_replay_config_accepts_positive_contract_multiplier() -> None:
    config = HftReplayConfig(
        tick_size=Decimal("0.01"),
        lot_size=Decimal("0.001"),
        contract_multiplier=Decimal("10"),
    )

    assert config.contract_multiplier == Decimal("10")


def test_hft_replay_config_rejects_non_positive_contract_multiplier() -> None:
    with pytest.raises(ValueError, match="contract_multiplier"):
        HftReplayConfig(
            tick_size=Decimal("0.01"),
            lot_size=Decimal("0.001"),
            contract_multiplier=Decimal("0"),
        )
