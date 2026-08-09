import pytest

from grid_trade.research.microstructure_calibration_runner import (
    MicrostructureCalibrationRunResult,
    run_checked_in_microstructure_calibration,
)

pytestmark = pytest.mark.research

_EXPECTED_DIGEST = "409269fc188c2a66514133f0a4ccbf03c412ab6ed761d9068f818e6a5e9fd2bf"


def _run() -> MicrostructureCalibrationRunResult:
    return run_checked_in_microstructure_calibration()


def test_controlled_microstructure_runner_is_exactly_deterministic() -> None:
    left = _run()
    right = _run()

    assert left == right
    assert left.deterministic is True
    assert left.evidence_digest == _EXPECTED_DIGEST
    assert left.step_count == 2
    assert left.ready_step_count == 1
    assert left.final_ready is True


def test_controlled_runner_proves_symbol_and_scale_metamorphic_invariance() -> None:
    result = _run()

    assert result.symbol_invariant is True
    assert result.scale_invariant is True
    assert result.milestone_passed is True


def test_controlled_runner_keeps_economics_and_production_no_go() -> None:
    result = _run()

    assert result.production_authorized is False
    assert result.alpha_validated is False
    assert result.economics_validated is False
