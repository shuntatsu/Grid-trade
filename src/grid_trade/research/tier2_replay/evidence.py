from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from typing import TYPE_CHECKING

from grid_trade.datasets.canonical import CanonicalEventEnvelope
from grid_trade.datasets.manifest import canonical_manifest_bytes
from grid_trade.domain.orders import PassiveOrderIntent
from grid_trade.domain.risk import RiskDecision
from grid_trade.evidence.events import EvidenceEvent, EvidenceKind
from grid_trade.research.replay_attribution import (
    FundingCashFlow,
    OrderLiquidityEligibility,
    ReplayLiquiditySummary,
)
from grid_trade.research.tier2_replay.dataset import _datetime_from_ns
from grid_trade.research.tier2_replay.models import Tier2ReplayManifest
from grid_trade.serialization import canonical_value

if TYPE_CHECKING:
    from grid_trade.research.hftbacktest_adapter import ReplaySummary

_HFTBACKTEST_IDENTITY = "hftbacktest==2.4.4"
_QUEUE_MODEL_IDENTITY = "RiskAverseQueueModel"
_EXCHANGE_MODEL_IDENTITY = "PartialFillExchange"


def _order_payload(order: PassiveOrderIntent) -> dict[str, object]:
    return {
        "client_order_id": order.client_order_id,
        "generation": order.generation,
        "level": order.level,
        "side": order.side.value,
        "price": order.price,
        "quantity": order.quantity,
        "reduce_only": order.reduce_only,
    }


def _eligibility_payload(
    eligibility: OrderLiquidityEligibility,
) -> dict[str, object]:
    return {
        "eligible": eligibility.eligible,
        "reason": eligibility.reason,
        "order_notional": eligibility.order_notional,
        "visible_same_level_quantity": eligibility.visible_same_level_quantity,
        "visible_top_n_notional": eligibility.visible_top_n_notional,
        "same_level_participation": eligibility.same_level_participation,
        "top_n_participation": eligibility.top_n_participation,
        "visibility_boundary_ts_ns": eligibility.visibility_boundary_ts_ns,
    }


def _evidence(
    *,
    run_id: str,
    manifest: Tier2ReplayManifest,
    events: tuple[CanonicalEventEnvelope, ...],
    initial_event: CanonicalEventEnvelope,
    candidate_orders: tuple[PassiveOrderIntent, ...],
    risk_orders: tuple[PassiveOrderIntent, ...],
    eligible_orders: tuple[PassiveOrderIntent, ...],
    eligibility: tuple[OrderLiquidityEligibility, ...],
    liquidity_summary: ReplayLiquiditySummary,
    risk_decision: RiskDecision,
    decision_digest: str,
    converted_receive_mode: str,
    replay_summary: ReplaySummary,
    funding_flows: tuple[FundingCashFlow, ...],
    maker_fee_cash_flow: Decimal,
    ending_position: Decimal,
) -> tuple[EvidenceEvent, ...]:
    decision_timestamp = _datetime_from_ns(initial_event.exchange_ts_ns)
    pending: list[tuple[datetime, int, EvidenceKind, dict[str, object]]] = [
        (
            decision_timestamp,
            0,
            EvidenceKind.RISK_DECISION,
            {
                "decision_digest": decision_digest,
                "allow_new_risk": risk_decision.allow_new_risk,
                "cancel_all_passive": risk_decision.cancel_all_passive,
                "target_flat": risk_decision.target_flat,
                "reasons": tuple(reason.value for reason in risk_decision.reasons),
                "candidate_orders": tuple(_order_payload(order) for order in candidate_orders),
                "risk_orders": tuple(_order_payload(order) for order in risk_orders),
            },
        ),
        (
            decision_timestamp,
            1,
            EvidenceKind.DESIRED_LADDER,
            {
                "eligible_orders": tuple(_order_payload(order) for order in eligible_orders),
                "eligibility": tuple(_eligibility_payload(item) for item in eligibility),
                "liquidity_summary": canonical_value(liquidity_summary),
                "market_impact_config": canonical_value(manifest.market_impact),
            },
        ),
    ]

    for index, fill in enumerate(replay_summary.fills):
        pending.append(
            (
                _datetime_from_ns(fill.timestamp_ns),
                10 + index,
                EvidenceKind.FILL,
                {
                    "client_order_id": fill.client_order_id,
                    "timestamp_ns": fill.timestamp_ns,
                    "price": fill.price,
                    "quantity": fill.quantity,
                    "remaining_quantity": fill.remaining_quantity,
                },
            )
        )
    for index, flow in enumerate(funding_flows):
        pending.append(
            (
                _datetime_from_ns(flow.timestamp_ns),
                1_000 + index,
                EvidenceKind.FUNDING_DECISION,
                {
                    "timestamp_ns": flow.timestamp_ns,
                    "position": flow.position,
                    "funding_rate": flow.funding_rate,
                    "reference_price": flow.reference_price,
                    "cash_flow": flow.cash_flow,
                },
            )
        )

    manifest_digest = sha256(canonical_manifest_bytes(manifest.dataset)).hexdigest()
    raw_hashes = tuple(raw.identity.sha256 for raw in manifest.dataset.raw_objects)
    summary_timestamp_ns = max(
        [
            *(event.exchange_ts_ns for event in events),
            *(fill.timestamp_ns for fill in replay_summary.fills),
            *(flow.timestamp_ns for flow in funding_flows),
        ],
        default=initial_event.exchange_ts_ns,
    )
    pending.append(
        (
            _datetime_from_ns(summary_timestamp_ns),
            10_000,
            EvidenceKind.RUN_SUMMARY,
            {
                "dataset": {
                    "manifest_digest": manifest_digest,
                    "acceptance": manifest.dataset.acceptance.value,
                    "audit_digest": manifest.dataset.audit_digest,
                    "raw_object_sha256": raw_hashes,
                    "normalization_schema_version": manifest.dataset.normalization_schema_version,
                    "ordering_schema_version": manifest.dataset.ordering_schema_version,
                    "audit_schema_version": manifest.dataset.audit_schema_version,
                },
                "strategy_identity": manifest.strategy_identity,
                "calibration_identity": manifest.calibration_identity,
                "replay_model": {
                    "runtime": _HFTBACKTEST_IDENTITY,
                    "exchange_model": _EXCHANGE_MODEL_IDENTITY,
                    "queue_model": _QUEUE_MODEL_IDENTITY,
                    "receive_timestamp_mode": converted_receive_mode,
                    "synthetic_receive_latency_ns": manifest.synthetic_receive_latency_ns,
                    "hft_config": canonical_value(manifest.hft),
                },
                "replay_quality": {
                    "liquidity_summary": canonical_value(liquidity_summary),
                    "visibility_boundary_policy": (
                        "stop_before_first_untrusted_order_price_boundary"
                    ),
                },
                "candidate_order_count": len(candidate_orders),
                "risk_accepted_order_count": len(risk_orders),
                "eligible_order_count": len(eligible_orders),
                "fill_count": len(replay_summary.fills),
                "funding_cash_flow": sum(
                    (flow.cash_flow for flow in funding_flows),
                    Decimal(0),
                ),
                "maker_fee_cash_flow": maker_fee_cash_flow,
                "ending_position": ending_position,
                "production_authorized": False,
                "alpha_validated": False,
                "economics_validated": False,
                "economic_validation_note": (
                    "Full PnL attribution stays disabled until a declared markout method exists."
                ),
            },
        )
    )

    pending.sort(key=lambda item: (item[0], item[1]))
    evidence: list[EvidenceEvent] = []
    for timestamp, _, kind, payload in pending:
        evidence.append(
            EvidenceEvent.create(
                run_id=run_id,
                sequence=len(evidence),
                timestamp=timestamp,
                kind=kind,
                payload=payload,
            )
        )
    return tuple(evidence)
