from datetime import UTC, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from grid_trade.domain.market import MarketSnapshot
from grid_trade.strategy.dynamic_center import (
    DynamicCenterConfig,
    DynamicCenterState,
    initialize_dynamic_center,
    propose_dynamic_center,
)

_NOW = datetime(2026, 8, 9, 9, 0, tzinfo=UTC)


def _snapshot(*, mid: str) -> MarketSnapshot:
    value = Decimal(mid)
    half_spread = Decimal("0.01")
    return MarketSnapshot(
        timestamp=_NOW,
        best_bid=value - half_spread,
        best_ask=value + half_spread,
        realized_volatility=Decimal("0.01"),
        position_quantity=Decimal("0"),
        source_id="fixture:s1-center",
    )


def test_initialization_uses_current_causal_mid() -> None:
    assert initialize_dynamic_center(_snapshot(mid="100")) == DynamicCenterState(
        center=Decimal("100.00"),
        generation=0,
    )


def test_below_threshold_keeps_previous_center() -> None:
    proposal = propose_dynamic_center(
        _snapshot(mid="100.20"),
        DynamicCenterState(center=Decimal("100"), generation=3),
        DynamicCenterConfig(
            reanchor_threshold_bps=Decimal("25"),
            max_step_bps=Decimal("50"),
        ),
    )

    assert proposal.deviation_bps == Decimal("20.00")
    assert proposal.proposed_center == Decimal("100")
    assert proposal.threshold_crossed is False
    assert proposal.previous_generation == 3


def test_exact_threshold_is_eligible() -> None:
    proposal = propose_dynamic_center(
        _snapshot(mid="100.25"),
        DynamicCenterState(center=Decimal("100"), generation=0),
        DynamicCenterConfig(
            reanchor_threshold_bps=Decimal("25"),
            max_step_bps=Decimal("50"),
        ),
    )

    assert proposal.deviation_bps == Decimal("25.00")
    assert proposal.threshold_crossed is True
    assert proposal.proposed_center == Decimal("100.25")


def test_large_move_is_capped_symmetrically() -> None:
    config = DynamicCenterConfig(
        reanchor_threshold_bps=Decimal("25"),
        max_step_bps=Decimal("50"),
    )
    state = DynamicCenterState(center=Decimal("100"), generation=1)

    up = propose_dynamic_center(_snapshot(mid="102"), state, config)
    down = propose_dynamic_center(_snapshot(mid="98"), state, config)

    assert up.proposed_center == Decimal("100.5")
    assert down.proposed_center == Decimal("99.5")


@pytest.mark.parametrize(
    ("threshold", "max_step"),
    [
        (Decimal("0"), Decimal("1")),
        (Decimal("-1"), Decimal("1")),
        (Decimal("1"), Decimal("0")),
        (Decimal("1"), Decimal("10000")),
        (Decimal("NaN"), Decimal("1")),
    ],
)
def test_config_rejects_invalid_values(threshold: Decimal, max_step: Decimal) -> None:
    with pytest.raises(ValueError):
        DynamicCenterConfig(threshold, max_step)


@pytest.mark.parametrize(
    ("center", "generation"),
    [
        (Decimal("0"), 0),
        (Decimal("-1"), 0),
        (Decimal("NaN"), 0),
        (Decimal("100"), -1),
    ],
)
def test_state_rejects_invalid_values(center: Decimal, generation: int) -> None:
    with pytest.raises(ValueError):
        DynamicCenterState(center=center, generation=generation)


@given(
    previous=st.decimals(min_value="10", max_value="100000", places=2, allow_nan=False),
    deviation_bps=st.integers(min_value=-5000, max_value=5000),
    max_step_bps=st.integers(min_value=1, max_value=9999),
)
def test_proposal_step_is_bounded_and_center_stays_positive(
    previous: Decimal,
    deviation_bps: int,
    max_step_bps: int,
) -> None:
    target = previous * (Decimal(1) + Decimal(deviation_bps) / Decimal(10_000))
    if target <= Decimal("0.01"):
        return
    proposal = propose_dynamic_center(
        _snapshot(mid=str(target)),
        DynamicCenterState(center=previous, generation=0),
        DynamicCenterConfig(
            reanchor_threshold_bps=Decimal("1"),
            max_step_bps=Decimal(max_step_bps),
        ),
    )
    applied_bps = abs((proposal.proposed_center - previous) / previous * Decimal(10_000))

    assert proposal.proposed_center > 0
    assert applied_bps <= Decimal(max_step_bps)
