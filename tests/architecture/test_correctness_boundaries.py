from dataclasses import fields
from decimal import Decimal
from pathlib import Path

from grid_trade.domain.risk import RiskReason
from grid_trade.research.hftbacktest_adapter import HftReplayConfig
from grid_trade.research.tier2_replay import Tier2ReplayManifest


def test_hard_risk_exposes_invalid_reduce_only_reason() -> None:
    assert RiskReason.INVALID_REDUCE_ONLY.value == "invalid_reduce_only"


def test_hft_replay_config_has_explicit_contract_multiplier() -> None:
    config = HftReplayConfig(
        tick_size=Decimal("0.01"),
        lot_size=Decimal("0.001"),
    )

    assert config.contract_multiplier == Decimal(1)


def test_hftbacktest_asset_uses_configured_contract_multiplier() -> None:
    source = Path("src/grid_trade/research/hftbacktest_adapter.py").read_text(encoding="utf-8")

    assert ".linear_asset(float(config.contract_multiplier))" in source
    assert ".linear_asset(1.0)" not in source


def test_tier2_manifest_owns_optional_explicit_instrument_binding() -> None:
    assert "instrument" in {field.name for field in fields(Tier2ReplayManifest)}


def test_calibrated_replay_propagates_explicit_instrument_binding() -> None:
    source = Path("src/grid_trade/research/tier2_calibrated_replay.py").read_text(
        encoding="utf-8"
    )

    assert "instrument=config.candidate.instrument" in source
    assert "self.hft.contract_multiplier != instrument.contract_multiplier" in source
