from decimal import Decimal
from hashlib import sha256

from grid_trade.datasets.canonical import CanonicalBookSnapshot, CanonicalEventEnvelope
from grid_trade.datasets.manifest import canonical_manifest_bytes
from grid_trade.domain.orders import PassiveOrderIntent
from grid_trade.domain.risk import RiskDecision, RiskLimits, RiskState
from grid_trade.research.replay_attribution import OrderLiquidityEligibility
from grid_trade.research.tier2_replay.models import Tier2ReplayManifest
from grid_trade.serialization import canonical_json_digest


def _decision_digest(
    *,
    initial_event: CanonicalEventEnvelope,
    manifest: Tier2ReplayManifest,
    candidate_orders: tuple[PassiveOrderIntent, ...],
    risk_limits: RiskLimits,
    risk_state: RiskState,
    starting_position: Decimal,
    realized_volatility: Decimal,
    risk_decision: RiskDecision,
    risk_orders: tuple[PassiveOrderIntent, ...],
    eligibility: tuple[OrderLiquidityEligibility, ...],
) -> str:
    book = initial_event.payload
    if not isinstance(book, CanonicalBookSnapshot):
        raise TypeError("initial event must carry CanonicalBookSnapshot payload")
    return canonical_json_digest(
        {
            "instrument": initial_event.instrument,
            "decision_exchange_ts_ns": initial_event.exchange_ts_ns,
            "visible_book": book,
            "strategy_identity": manifest.strategy_identity,
            "calibration_identity": manifest.calibration_identity,
            "candidate_orders": candidate_orders,
            "risk_limits": risk_limits,
            "risk_state": risk_state,
            "starting_position": starting_position,
            "realized_volatility": realized_volatility,
            "risk_decision": risk_decision,
            "risk_orders": risk_orders,
            "market_impact": manifest.market_impact,
            "order_eligibility": eligibility,
        }
    )


def _run_id(
    *,
    manifest: Tier2ReplayManifest,
    candidate_orders: tuple[PassiveOrderIntent, ...],
    risk_limits: RiskLimits,
    risk_state: RiskState,
    starting_position: Decimal,
    realized_volatility: Decimal,
) -> str:
    manifest_digest = sha256(canonical_manifest_bytes(manifest.dataset)).hexdigest()
    identity = canonical_json_digest(
        {
            "dataset_manifest_digest": manifest_digest,
            "strategy_identity": manifest.strategy_identity,
            "calibration_identity": manifest.calibration_identity,
            "hft": manifest.hft,
            "market_impact": manifest.market_impact,
            "synthetic_receive_latency_ns": manifest.synthetic_receive_latency_ns,
            "candidate_orders": candidate_orders,
            "risk_limits": risk_limits,
            "risk_state": risk_state,
            "starting_position": starting_position,
            "realized_volatility": realized_volatility,
        }
    )
    return f"tier2-{identity[:20]}"
