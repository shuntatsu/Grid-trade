from decimal import Decimal
from pathlib import Path

import pytest

from grid_trade.domain.orders import OrderSide, PassiveOrderIntent
from grid_trade.research.hftbacktest_adapter import (
    HftReplayConfig,
    ReplayFill,
    ReplaySummary,
    load_microstructure_fixture,
    replay_passive_orders,
    require_hftbacktest_runtime,
)

pytestmark = pytest.mark.research

_FIXTURE = Path("tests/fixtures/microstructure/s0_round_trip.csv")
_SELL_FIXTURE = Path("tests/fixtures/microstructure/sell_round_trip.csv")


def _buy_intent() -> PassiveOrderIntent:
    return PassiveOrderIntent(
        client_order_id="s0:g0:buy:l1",
        generation=0,
        level=1,
        side=OrderSide.BUY,
        price=Decimal("99.0"),
        quantity=Decimal("0.02"),
        reduce_only=False,
    )


def _sell_intent() -> PassiveOrderIntent:
    return PassiveOrderIntent(
        client_order_id="adaptive:g1:sell:l1",
        generation=1,
        level=1,
        side=OrderSide.SELL,
        price=Decimal("101.0"),
        quantity=Decimal("0.02"),
        reduce_only=False,
    )


def _config(**overrides: object) -> HftReplayConfig:
    values: dict[str, object] = {
        "tick_size": Decimal("0.1"),
        "lot_size": Decimal("0.01"),
        "entry_latency_ns": 0,
        "response_latency_ns": 0,
        "maker_fee": Decimal("0"),
        "taker_fee": Decimal("0"),
    }
    values.update(overrides)
    return HftReplayConfig(**values)  # type: ignore[arg-type]


def test_pinned_hftbacktest_runtime_is_available() -> None:
    require_hftbacktest_runtime()


def test_runtime_identity_fails_closed_on_version_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "grid_trade.research.hftbacktest_adapter.distribution_version",
        lambda _: "9.9.9",
    )

    with pytest.raises(RuntimeError, match=r"2\.4\.4"):
        require_hftbacktest_runtime()


def test_fixture_load_is_deterministic_and_separates_snapshot_from_feed() -> None:
    left = load_microstructure_fixture(_FIXTURE)
    right = load_microstructure_fixture(_FIXTURE)

    assert left == right
    assert len(left.snapshot) == 2
    assert len(left.feed) == 5
    assert left.snapshot[0].kind == "snapshot_bid"
    assert left.feed[0].kind == "depth_ask"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tick_size", Decimal("0")),
        ("lot_size", Decimal("0")),
        ("entry_latency_ns", -1),
        ("response_latency_ns", -1),
        ("maker_fee", Decimal("NaN")),
        ("taker_fee", Decimal("NaN")),
    ],
)
def test_config_rejects_invalid_values(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        _config(**{field: value})


def test_replay_rejects_untick_aligned_price() -> None:
    intent = PassiveOrderIntent(
        client_order_id="bad-price",
        generation=0,
        level=1,
        side=OrderSide.BUY,
        price=Decimal("99.05"),
        quantity=Decimal("0.02"),
    )

    with pytest.raises(ValueError, match="tick"):
        replay_passive_orders(load_microstructure_fixture(_FIXTURE), (intent,), _config())


def test_replay_rejects_unlot_aligned_quantity() -> None:
    intent = PassiveOrderIntent(
        client_order_id="bad-quantity",
        generation=0,
        level=1,
        side=OrderSide.BUY,
        price=Decimal("99.0"),
        quantity=Decimal("0.015"),
    )

    with pytest.raises(ValueError, match="lot"):
        replay_passive_orders(load_microstructure_fixture(_FIXTURE), (intent,), _config())


def test_risk_adverse_queue_replay_observes_partial_then_full_fill() -> None:
    summary = replay_passive_orders(
        load_microstructure_fixture(_FIXTURE),
        (_buy_intent(),),
        _config(),
    )

    assert summary == ReplaySummary(
        fills=(
            ReplayFill(
                client_order_id="s0:g0:buy:l1",
                timestamp_ns=4_000_000_000,
                price=Decimal("99.0"),
                quantity=Decimal("0.01"),
                remaining_quantity=Decimal("0.01"),
            ),
            ReplayFill(
                client_order_id="s0:g0:buy:l1",
                timestamp_ns=5_000_000_000,
                price=Decimal("99.0"),
                quantity=Decimal("0.01"),
                remaining_quantity=Decimal("0.00"),
            ),
        ),
        ending_position=Decimal("0.02"),
        open_order_count=0,
    )


def test_sell_side_replay_can_fill_and_produces_negative_inventory() -> None:
    summary = replay_passive_orders(
        load_microstructure_fixture(_SELL_FIXTURE),
        (_sell_intent(),),
        _config(),
    )

    assert summary.fills
    assert sum((fill.quantity for fill in summary.fills), Decimal(0)) == Decimal("0.02")
    assert summary.ending_position == Decimal("-0.02")
    assert summary.open_order_count == 0


def test_first_fill_occurs_only_after_existing_queue_is_consumed() -> None:
    summary = replay_passive_orders(
        load_microstructure_fixture(_FIXTURE),
        (_buy_intent(),),
        _config(),
    )

    assert summary.fills[0].timestamp_ns > 3_000_000_000


def test_repeated_replay_is_exactly_deterministic() -> None:
    fixture = load_microstructure_fixture(_FIXTURE)
    intent = _buy_intent()
    config = _config()

    assert replay_passive_orders(fixture, (intent,), config) == replay_passive_orders(
        fixture,
        (intent,),
        config,
    )
