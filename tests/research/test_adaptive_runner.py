from decimal import Decimal

import pytest

from grid_trade.research.adaptive_runner import run_checked_in_comparison
from grid_trade.strategy.adaptive_grid import AdaptiveStage

pytestmark = pytest.mark.research


def test_checked_in_adaptive_comparison_is_deterministic_and_no_go() -> None:
    result = run_checked_in_comparison()

    assert len(result.evidence_digest) == 64
    assert result.deterministic
    assert not result.production_authorized
    assert not result.alpha_validated
    assert result.execution_scope == "policy_reconciliation_only"
    assert tuple(stage.stage for stage in result.stages) == tuple(AdaptiveStage)
    assert all(stage.pnl.total == Decimal(0) for stage in result.stages)
    assert all(stage.execution_scope == "policy_reconciliation_only" for stage in result.stages)


def test_ablation_keeps_later_features_out_of_earlier_stages() -> None:
    result = run_checked_in_comparison()
    by_stage = {stage.stage: stage for stage in result.stages}

    s3 = by_stage[AdaptiveStage.S3_INVENTORY]
    s4 = by_stage[AdaptiveStage.S4_DERISK]
    s5 = by_stage[AdaptiveStage.S5_SHORT]
    s6 = by_stage[AdaptiveStage.S6_FUNDING]
    s7 = by_stage[AdaptiveStage.S7_ORDER_BOOK]

    assert all(target >= 0 for target in s3.target_path)
    assert Decimal(0) in s4.target_path
    assert all(target >= 0 for target in s4.target_path)
    assert any(target < 0 for target in s5.target_path)
    assert min(s6.target_path) <= min(s5.target_path)
    assert s7.reference_path != s6.reference_path


def test_controlled_path_exercises_flat_before_short_and_execution_mechanics() -> None:
    result = run_checked_in_comparison()
    s5 = next(stage for stage in result.stages if stage.stage is AdaptiveStage.S5_SHORT)

    first_short = next(index for index, target in enumerate(s5.target_path) if target < 0)
    assert Decimal(0) in s5.target_path[:first_short]
    assert s5.cancel_count > 0
    assert s5.submit_count > 0
    assert s5.reduce_only_submit_count > 0
    assert s5.short_new_risk_submit_count > 0
    assert s5.ending_inventory == Decimal("-0.04")
