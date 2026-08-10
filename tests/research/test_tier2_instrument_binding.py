from datetime import UTC, datetime
from decimal import Decimal

import pytest

from grid_trade.datasets.contracts import DatasetAcceptance
from grid_trade.datasets.manifest import DatasetManifest
from grid_trade.domain.instrument import ContractType, InstrumentSpec
from grid_trade.research.hftbacktest_adapter import HftReplayConfig
from grid_trade.research.replay_attribution import MarketImpactEligibilityConfig
from grid_trade.research.tier2_replay import Tier2ReplayManifest

pytestmark = pytest.mark.research


def _dataset(instrument: str = "BTC-PERP") -> DatasetManifest:
    return DatasetManifest(
        instrument=instrument,
        raw_objects=(),
        normalization_schema_version="normalization-v1",
        ordering_schema_version="ordering-v1",
        audit_schema_version="audit-v1",
        acceptance=DatasetAcceptance.REJECTED,
        created_at=datetime(2026, 8, 11, tzinfo=UTC),
    )


def _instrument(
    *,
    instrument_id: str = "BTC-PERP",
    tick_size: str = "0.01",
    quantity_step: str = "0.001",
    contract_multiplier: str = "10",
    funding_interval_seconds: int = 3_600,
) -> InstrumentSpec:
    return InstrumentSpec(
        instrument_id=instrument_id,
        contract_type=ContractType.LINEAR_PERPETUAL,
        contract_multiplier=Decimal(contract_multiplier),
        tick_size=Decimal(tick_size),
        quantity_step=Decimal(quantity_step),
        min_quantity=Decimal("0.001"),
        min_notional=Decimal("1"),
        max_quantity=Decimal("100"),
        funding_interval_seconds=funding_interval_seconds,
    )


def _market_impact() -> MarketImpactEligibilityConfig:
    return MarketImpactEligibilityConfig(
        max_same_level_participation=Decimal("0.1"),
        max_top_n_participation=Decimal("0.1"),
    )


def _manifest(
    *,
    dataset: DatasetManifest | None = None,
    instrument: InstrumentSpec | None = None,
    tick_size: str = "0.01",
    lot_size: str = "0.001",
    contract_multiplier: str = "10",
) -> Tier2ReplayManifest:
    return Tier2ReplayManifest(
        dataset=dataset or _dataset(),
        strategy_identity="strategy-v1",
        calibration_identity="calibration-v1",
        hft=HftReplayConfig(
            tick_size=Decimal(tick_size),
            lot_size=Decimal(lot_size),
            contract_multiplier=Decimal(contract_multiplier),
        ),
        market_impact=_market_impact(),
        synthetic_receive_latency_ns=0,
        instrument=instrument or _instrument(),
    )


def _manifest_without_instrument(contract_multiplier: str) -> Tier2ReplayManifest:
    return Tier2ReplayManifest(
        dataset=_dataset(),
        strategy_identity="strategy-v1",
        calibration_identity="calibration-v1",
        hft=HftReplayConfig(
            tick_size=Decimal("0.01"),
            lot_size=Decimal("0.001"),
            contract_multiplier=Decimal(contract_multiplier),
        ),
        market_impact=_market_impact(),
        synthetic_receive_latency_ns=0,
        instrument=None,
    )


def test_tier2_manifest_accepts_matching_explicit_instrument() -> None:
    manifest = _manifest()

    assert manifest.instrument is not None
    assert manifest.instrument.instrument_id == manifest.dataset.instrument


def test_unit_multiplier_legacy_manifest_may_omit_instrument() -> None:
    manifest = _manifest_without_instrument("1")

    assert manifest.instrument is None


def test_non_unit_multiplier_requires_explicit_instrument() -> None:
    with pytest.raises(ValueError, match="requires InstrumentSpec"):
        _manifest_without_instrument("10")


def test_tier2_manifest_rejects_dataset_instrument_mismatch() -> None:
    with pytest.raises(ValueError, match="instrument"):
        _manifest(dataset=_dataset("ETH-PERP"))


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("tick_size", "0.1"),
        ("lot_size", "0.01"),
        ("contract_multiplier", "1"),
    ),
)
def test_tier2_manifest_rejects_hft_contract_mismatch(field: str, value: str) -> None:
    kwargs = {field: value}

    with pytest.raises(ValueError, match=field):
        _manifest(**kwargs)  # type: ignore[arg-type]


def test_tier2_manifest_rejects_non_hourly_funding_contract() -> None:
    with pytest.raises(ValueError, match="hourly funding"):
        _manifest(instrument=_instrument(funding_interval_seconds=28_800))
