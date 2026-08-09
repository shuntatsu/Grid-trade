import re

import pytest

from grid_trade.research.tier2_fixture_runner import build_tier2_fixture_case

pytestmark = pytest.mark.research

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def test_tier2_fixture_replay_is_exactly_deterministic() -> None:
    case = build_tier2_fixture_case()

    first = case.run()
    second = case.run()

    assert first == second
    assert _SHA256.fullmatch(first.evidence_digest)
    assert first.production_authorized is False
    assert first.alpha_validated is False
    assert first.economics_validated is False
