from dataclasses import replace
from decimal import Decimal

import pytest

from grid_trade.strategy.de_risk import DeRiskConfig, DeRiskRegime
from grid_trade.strategy.target_profile import (
    DirectionalTargetProfileConfig,
    apply_conditional_reversal,
    apply_directional_de_risk,
)


def _derisk() -> DeRiskConfig:
    return DeRiskConfig(
        warning_trend_threshold=Decimal("-0.25"),
        severe_trend_threshold=Decimal("-0.6"),
        warning_target_fraction=Decimal("0.5"),
        severe_target_fraction=Decimal("0"),
    )


def _long_profile() -> DirectionalTargetProfileConfig:
    return DirectionalTargetProfileConfig(
        baseline_target_fraction=Decimal("0.5"),
        allow_opposite=True,
        opposite_entry_aligned_trend_threshold=Decimal("-0.6"),
        max_opposite_target_fraction=Decimal("0.4"),
    )


def test_short_profile_is_sign_mirror_of_long_profile() -> None:
    long_profile = _long_profile()
    short_profile = replace(long_profile, baseline_target_fraction=Decimal("-0.5"))

    long_derisk = apply_directional_de_risk(
        profile=long_profile,
        max_abs_target=Decimal("2"),
        trend_score=Decimal("-0.4"),
        config=_derisk(),
    )
    short_derisk = apply_directional_de_risk(
        profile=short_profile,
        max_abs_target=Decimal("2"),
        trend_score=Decimal("0.4"),
        config=_derisk(),
    )
    long_reverse = apply_conditional_reversal(
        target=long_derisk.effective_target,
        position=Decimal("0"),
        trend_score=Decimal("-0.9"),
        profile=long_profile,
        max_abs_target=Decimal("2"),
    )
    short_reverse = apply_conditional_reversal(
        target=short_derisk.effective_target,
        position=Decimal("0"),
        trend_score=Decimal("0.9"),
        profile=short_profile,
        max_abs_target=Decimal("2"),
    )

    assert long_derisk.regime is DeRiskRegime.WARNING
    assert short_derisk.effective_target == -long_derisk.effective_target
    assert short_reverse.effective_target == -long_reverse.effective_target
    assert short_reverse.bearish_severity == long_reverse.bearish_severity


def test_both_reversal_directions_require_flat_first() -> None:
    long_profile = _long_profile()
    short_profile = replace(long_profile, baseline_target_fraction=Decimal("-0.5"))

    flatten_long = apply_conditional_reversal(
        target=Decimal("1"),
        position=Decimal("0.25"),
        trend_score=Decimal("-0.9"),
        profile=long_profile,
        max_abs_target=Decimal("2"),
    )
    flatten_short = apply_conditional_reversal(
        target=Decimal("-1"),
        position=Decimal("-0.25"),
        trend_score=Decimal("0.9"),
        profile=short_profile,
        max_abs_target=Decimal("2"),
    )

    assert flatten_long.requested_target < 0
    assert flatten_long.effective_target == Decimal("0")
    assert flatten_short.requested_target > 0
    assert flatten_short.effective_target == Decimal("0")


def test_flat_profile_stays_flat_without_later_adjustment() -> None:
    profile = replace(
        _long_profile(),
        baseline_target_fraction=Decimal("0"),
        max_opposite_target_fraction=Decimal("0"),
    )

    derisk = apply_directional_de_risk(
        profile=profile,
        max_abs_target=Decimal("2"),
        trend_score=Decimal("-1"),
        config=_derisk(),
    )
    reverse = apply_conditional_reversal(
        target=derisk.effective_target,
        position=Decimal("0"),
        trend_score=Decimal("-1"),
        profile=profile,
        max_abs_target=Decimal("2"),
    )

    assert derisk.effective_target == Decimal("0")
    assert reverse.effective_target == Decimal("0")


def test_profile_rejects_out_of_range_fractions() -> None:
    with pytest.raises(ValueError, match="baseline_target_fraction"):
        replace(_long_profile(), baseline_target_fraction=Decimal("1.1"))
    with pytest.raises(ValueError, match="max_opposite_target_fraction"):
        replace(_long_profile(), max_opposite_target_fraction=Decimal("-0.1"))
