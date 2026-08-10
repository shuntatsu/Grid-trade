from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from grid_trade.datasets.manifest import DatasetManifest
from grid_trade.domain.instrument import InstrumentSpec, require_instruments_compatible
from grid_trade.domain.risk import RiskDecision
from grid_trade.evidence.events import EvidenceEvent
from grid_trade.research.replay_attribution import (
    FundingCashFlow,
    MarketImpactEligibilityConfig,
    OrderLiquidityEligibility,
    ReplayLiquiditySummary,
)

if TYPE_CHECKING:
    from grid_trade.research.hftbacktest_adapter import HftReplayConfig, ReplaySummary


def _require_non_empty(value: str, *, field: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field} must be non-empty")


def _require_finite(value: Decimal, *, field: str) -> None:
    if not value.is_finite():
        raise ValueError(f"{field} must be finite")


@dataclass(frozen=True, slots=True)
class Tier2ReplayManifest:
    dataset: DatasetManifest
    strategy_identity: str
    calibration_identity: str
    hft: HftReplayConfig
    market_impact: MarketImpactEligibilityConfig
    synthetic_receive_latency_ns: int
    instrument: InstrumentSpec | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.strategy_identity, field="strategy_identity")
        _require_non_empty(self.calibration_identity, field="calibration_identity")
        if self.synthetic_receive_latency_ns < 0:
            raise ValueError("synthetic_receive_latency_ns must be non-negative")
        if self.instrument is None:
            if self.hft.contract_multiplier != Decimal(1):
                raise ValueError("non-unit hft contract_multiplier requires InstrumentSpec")
            return

        require_instruments_compatible(
            self.dataset.instrument,
            self.instrument.instrument_id,
            context="Tier-2 dataset/spec",
        )
        if self.hft.tick_size != self.instrument.tick_size:
            raise ValueError("hft tick_size must match InstrumentSpec")
        if self.hft.lot_size != self.instrument.quantity_step:
            raise ValueError("hft lot_size must match InstrumentSpec quantity_step")
        if self.hft.contract_multiplier != self.instrument.contract_multiplier:
            raise ValueError("hft contract_multiplier must match InstrumentSpec")
        if self.instrument.funding_interval_seconds != 3_600:
            raise ValueError("Tier-2 replay currently requires an hourly funding interval")


@dataclass(frozen=True, slots=True)
class Tier2ReplayResult:
    evidence_events: tuple[EvidenceEvent, ...]
    evidence_digest: str
    decision_digest: str
    risk_decision: RiskDecision
    candidate_order_count: int
    risk_accepted_order_count: int
    eligible_order_count: int
    order_eligibility: tuple[OrderLiquidityEligibility, ...]
    liquidity_summary: ReplayLiquiditySummary
    replay_summary: ReplaySummary
    funding_cash_flows: tuple[FundingCashFlow, ...]
    funding_pnl: Decimal
    maker_fee_cash_flow: Decimal
    ending_position: Decimal
    production_authorized: bool = False
    alpha_validated: bool = False
    economics_validated: bool = False

    def __post_init__(self) -> None:
        for value in (
            self.candidate_order_count,
            self.risk_accepted_order_count,
            self.eligible_order_count,
        ):
            if value < 0:
                raise ValueError("replay counts must be non-negative")
        _require_finite(self.funding_pnl, field="funding_pnl")
        _require_finite(self.maker_fee_cash_flow, field="maker_fee_cash_flow")
        _require_finite(self.ending_position, field="ending_position")
        if self.production_authorized or self.alpha_validated or self.economics_validated:
            raise ValueError("Tier-2 research replay cannot authorize production or validate alpha")


__all__ = ["Tier2ReplayManifest", "Tier2ReplayResult"]
