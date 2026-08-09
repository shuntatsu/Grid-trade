from dataclasses import dataclass, replace
from decimal import Decimal

from grid_trade.datasets.audit import audit_canonical_dataset, audit_report_digest
from grid_trade.datasets.canonical import CanonicalEventEnvelope
from grid_trade.datasets.contracts import DatasetAcceptance
from grid_trade.datasets.manifest import DatasetManifest
from grid_trade.domain.risk import RiskLimits, RiskState
from grid_trade.research.hftbacktest_adapter import HftReplayConfig
from grid_trade.research.replay_attribution import MarketImpactEligibilityConfig
from grid_trade.research.tier2_calibrated_candidate import (
    Tier2CalibratedCandidateConfig,
    Tier2CalibratedCandidateResult,
    Tier2CalibrationEvidenceFrame,
    derive_tier2_calibrated_candidate,
)
from grid_trade.research.tier2_replay import (
    Tier2ReplayManifest,
    Tier2ReplayResult,
    run_tier2_replay,
)


def _require_non_empty(value: str, *, field: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field} must be non-empty")


@dataclass(frozen=True, slots=True)
class CalibratedTier2ReplayConfig:
    candidate: Tier2CalibratedCandidateConfig
    hft: HftReplayConfig
    market_impact: MarketImpactEligibilityConfig
    strategy_identity: str
    calibration_identity_prefix: str
    synthetic_receive_latency_ns: int

    def __post_init__(self) -> None:
        _require_non_empty(self.strategy_identity, field="strategy_identity")
        _require_non_empty(self.calibration_identity_prefix, field="calibration_identity_prefix")
        if self.synthetic_receive_latency_ns < 0:
            raise ValueError("synthetic_receive_latency_ns must be non-negative")
        if self.hft.tick_size != self.candidate.venue.tick_size:
            raise ValueError("hft tick_size must match calibrated candidate venue tick_size")
        if self.hft.lot_size != self.candidate.venue.quantity_step:
            raise ValueError("hft lot_size must match calibrated candidate venue quantity_step")


@dataclass(frozen=True, slots=True)
class CalibratedTier2ReplayResult:
    candidate: Tier2CalibratedCandidateResult
    replay_manifest: Tier2ReplayManifest
    replay: Tier2ReplayResult


def _replay_window(
    events: tuple[CanonicalEventEnvelope, ...],
    *,
    decision_exchange_ts_ns: int,
) -> tuple[CanonicalEventEnvelope, ...]:
    replay_events = tuple(
        event for event in events if event.exchange_ts_ns >= decision_exchange_ts_ns
    )
    if not replay_events:
        raise ValueError("Tier-2 replay window is empty at the calibrated decision timestamp")
    return replay_events


def _replay_dataset_manifest(
    dataset: DatasetManifest,
    replay_events: tuple[CanonicalEventEnvelope, ...],
) -> DatasetManifest:
    report = audit_canonical_dataset(
        replay_events,
        raw_objects=dataset.raw_objects,
        expected_normalization_schema_version=dataset.normalization_schema_version,
    )
    if report.acceptance is not DatasetAcceptance.ACCEPTED:
        raise ValueError("calibrated Tier-2 replay window is not audit ACCEPTED")
    return replace(
        dataset,
        acceptance=report.acceptance,
        audit_digest=audit_report_digest(report),
    )


def run_calibrated_tier2_replay(
    *,
    dataset: DatasetManifest,
    events: tuple[CanonicalEventEnvelope, ...],
    evidence_frames: tuple[Tier2CalibrationEvidenceFrame, ...],
    decision_exchange_ts_ns: int,
    config: CalibratedTier2ReplayConfig,
    risk_limits: RiskLimits,
    risk_state: RiskState,
    starting_position: Decimal,
) -> CalibratedTier2ReplayResult:
    candidate = derive_tier2_calibrated_candidate(
        dataset=dataset,
        events=events,
        evidence_frames=evidence_frames,
        decision_exchange_ts_ns=decision_exchange_ts_ns,
        config=config.candidate,
        equity=risk_state.equity,
        starting_position=starting_position,
    )
    replay_events = _replay_window(
        events,
        decision_exchange_ts_ns=decision_exchange_ts_ns,
    )
    replay_dataset = _replay_dataset_manifest(dataset, replay_events)
    replay_manifest = Tier2ReplayManifest(
        dataset=replay_dataset,
        strategy_identity=config.strategy_identity,
        calibration_identity=(
            f"{config.calibration_identity_prefix}:causal={candidate.provenance_digest}"
        ),
        hft=config.hft,
        market_impact=config.market_impact,
        synthetic_receive_latency_ns=config.synthetic_receive_latency_ns,
    )
    volatility = candidate.calibrated_market_state.volatility_scale
    if volatility is None:
        raise RuntimeError("ready calibrated candidate unexpectedly lacks volatility_scale")
    replay = run_tier2_replay(
        manifest=replay_manifest,
        events=replay_events,
        candidate_orders=candidate.candidate_orders,
        risk_limits=risk_limits,
        risk_state=risk_state,
        starting_position=starting_position,
        realized_volatility=volatility,
    )
    return CalibratedTier2ReplayResult(
        candidate=candidate,
        replay_manifest=replay_manifest,
        replay=replay,
    )


__all__ = [
    "CalibratedTier2ReplayConfig",
    "CalibratedTier2ReplayResult",
    "run_calibrated_tier2_replay",
]
