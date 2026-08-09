import pytest

from grid_trade.research.calibrated_adaptive_runner import (
    CalibratedAdaptiveRunResult,
    run_checked_in_calibrated_adaptive,
)

pytestmark = pytest.mark.research


def _run() -> CalibratedAdaptiveRunResult:
    return run_checked_in_calibrated_adaptive()


def test_calibrated_adaptive_runner_is_exactly_deterministic() -> None:
    left = _run()
    right = _run()

    assert left == right
    assert left.deterministic is True
    assert len(left.evidence_digest) == 64
    assert left.calibration_generation >= 4
    assert left.adaptive_generation >= 1


def test_calibrated_adaptive_runner_is_symbol_and_scale_invariant() -> None:
    result = _run()

    assert result.symbol_invariant is True
    assert result.scale_invariant is True
    assert result.milestone_passed is True
    assert result.preparation_ready is True


def test_calibrated_adaptive_runner_keeps_economics_and_production_no_go() -> None:
    result = _run()

    assert result.economics_validated is False
    assert result.alpha_validated is False
    assert result.production_authorized is False
