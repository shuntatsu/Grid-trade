from decimal import Decimal

import pytest

from grid_trade.strategy.adaptive_signals import AdaptiveSignals
from grid_trade.strategy.conditional_short import (
    ShortOverlayConfig,
    ShortPhase,
    apply_conditional_short,
    enforce_flat_before_reverse,
)
from grid_trade.strategy.de_risk import DeRiskConfig, DeRiskRegime, apply_partial_de_risk
from grid_trade.strategy.funding_bias import FundingBiasConfig, apply_funding_bias
from grid_trade.strategy.inventory_target import InventoryTargetConfig, decide_inventory_target
from grid_trade.strategy.order_book_reference import (
    OrderBookReferenceConfig,
    decide_order_book_reference,
)


def test_adaptive_signals_validate_causal_ranges() -> None:
    signals = AdaptiveSignals(
        trend_score=Decimal("-0.4"),
        funding_rate=Decimal("0.0001"),
        order_book_imbalance=Decimal("0.25"),
        microprice=Decimal("100.2"),
    )
    assert signals.trend_score == Decimal("-0.4")

    with pytest.raises(ValueError, match="trend_score"):
        AdaptiveSignals(Decimal("1.1"), Decimal(0), Decimal(0), None)
    with pytest.raises(ValueError, match="order_book_imbalance"):
        AdaptiveSignals(Decimal(0), Decimal(0), Decimal("-1.1"), None)
    with pytest.raises(ValueError, match="microprice"):
        AdaptiveSignals(Decimal(0), Decimal(0), Decimal(0), Decimal(0))


def test_s3_excess_long_suppresses_bids_and_skews_reference_down() -> None:
    decision = decide_inventory_target(
        position=Decimal("0.08"),
        target=Decimal("0.05"),
        config=InventoryTargetConfig(
            base_long_target=Decimal("0.05"),
            max_abs_target=Decimal("0.10"),
            reservation_skew_bps=Decimal("20"),
            side_skew_strength=Decimal("1"),
        ),
    )

    assert decision.normalized_inventory_error == Decimal("0.3")
    assert decision.reservation_shift_bps == Decimal("-6.0")
    assert decision.bid_scale == Decimal("0.7")
    assert decision.ask_scale == Decimal("1")


def test_s3_under_target_suppresses_asks_without_amplifying_bids() -> None:
    decision = decide_inventory_target(
        position=Decimal("0.02"),
        target=Decimal("0.05"),
        config=InventoryTargetConfig(
            base_long_target=Decimal("0.05"),
            max_abs_target=Decimal("0.10"),
            reservation_skew_bps=Decimal("20"),
            side_skew_strength=Decimal("1"),
        ),
    )
    assert decision.bid_scale == Decimal("1")
    assert decision.ask_scale == Decimal("0.7")
    assert decision.reservation_shift_bps == Decimal("6.0")


def _derisk_config() -> DeRiskConfig:
    return DeRiskConfig(
        warning_trend_threshold=Decimal("-0.25"),
        severe_trend_threshold=Decimal("-0.60"),
        warning_target_fraction=Decimal("0.50"),
        severe_target_fraction=Decimal("0"),
    )


def test_s4_partial_derisk_is_staged_and_never_creates_short() -> None:
    healthy = apply_partial_de_risk(Decimal("0.06"), Decimal("0.1"), _derisk_config())
    warning = apply_partial_de_risk(Decimal("0.06"), Decimal("-0.4"), _derisk_config())
    severe = apply_partial_de_risk(Decimal("0.06"), Decimal("-0.8"), _derisk_config())

    assert healthy.regime is DeRiskRegime.HEALTHY
    assert healthy.effective_target == Decimal("0.06")
    assert warning.regime is DeRiskRegime.WARNING
    assert warning.effective_target == Decimal("0.030")
    assert severe.regime is DeRiskRegime.SEVERE
    assert severe.effective_target == Decimal("0.00")


def _short_config() -> ShortOverlayConfig:
    return ShortOverlayConfig(
        entry_trend_threshold=Decimal("-0.60"),
        max_short_target=Decimal("0.08"),
    )


def test_s5_long_to_short_must_pass_through_flat() -> None:
    decision = apply_conditional_short(
        long_target=Decimal("0"),
        position=Decimal("0.05"),
        trend_score=Decimal("-0.90"),
        config=_short_config(),
    )
    assert decision.requested_target < 0
    assert decision.effective_target == Decimal(0)
    assert decision.phase is ShortPhase.FLATTEN_LONG

    flat = apply_conditional_short(
        long_target=Decimal("0"),
        position=Decimal(0),
        trend_score=Decimal("-0.90"),
        config=_short_config(),
    )
    assert flat.effective_target < 0
    assert flat.phase is ShortPhase.SHORT


def test_flat_before_reverse_is_symmetric() -> None:
    assert enforce_flat_before_reverse(Decimal("-0.03"), Decimal("0.04")) == Decimal(0)
    assert enforce_flat_before_reverse(Decimal("0.03"), Decimal("-0.04")) == Decimal(0)
    assert enforce_flat_before_reverse(Decimal(0), Decimal("-0.04")) == Decimal("-0.04")


def test_s6_positive_funding_reduces_long_target_and_cannot_bypass_flat_gate() -> None:
    config = FundingBiasConfig(
        funding_scale=Decimal("0.001"),
        max_abs_target=Decimal("0.10"),
        max_target_shift_fraction=Decimal("0.50"),
    )
    moderate = apply_funding_bias(
        target=Decimal("0.05"),
        position=Decimal("0.05"),
        funding_rate=Decimal("0.0005"),
        config=config,
    )
    assert moderate.requested_target == Decimal("0.025")
    assert moderate.effective_target == Decimal("0.025")

    expensive = apply_funding_bias(
        target=Decimal("0.01"),
        position=Decimal("0.02"),
        funding_rate=Decimal("0.001"),
        config=config,
    )
    assert expensive.requested_target < 0
    assert expensive.effective_target == Decimal(0)


def test_s6_negative_funding_supports_long_bias_with_clip() -> None:
    decision = apply_funding_bias(
        target=Decimal("0.08"),
        position=Decimal("0.04"),
        funding_rate=Decimal("-0.001"),
        config=FundingBiasConfig(
            funding_scale=Decimal("0.001"),
            max_abs_target=Decimal("0.10"),
            max_target_shift_fraction=Decimal("0.50"),
        ),
    )
    assert decision.requested_target == Decimal("0.10")
    assert decision.effective_target == Decimal("0.10")


def test_s7_microprice_and_positive_imbalance_shift_reference_up() -> None:
    decision = decide_order_book_reference(
        center=Decimal("100"),
        market_mid=Decimal("100"),
        signals=AdaptiveSignals(
            trend_score=Decimal(0),
            funding_rate=Decimal(0),
            order_book_imbalance=Decimal("0.5"),
            microprice=Decimal("100.2"),
        ),
        config=OrderBookReferenceConfig(
            microprice_weight=Decimal("0.5"),
            imbalance_shift_bps=Decimal("10"),
        ),
    )
    assert decision.blended_reference == Decimal("100.100")
    assert decision.effective_reference == Decimal("100.1500500")


def test_s7_missing_microprice_falls_back_to_center() -> None:
    decision = decide_order_book_reference(
        center=Decimal("100"),
        market_mid=Decimal("100"),
        signals=AdaptiveSignals(Decimal(0), Decimal(0), Decimal(0), None),
        config=OrderBookReferenceConfig(Decimal("1"), Decimal("10")),
    )
    assert decision.blended_reference == Decimal("100")
    assert decision.effective_reference == Decimal("100")
