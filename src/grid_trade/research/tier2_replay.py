import json
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from hashlib import sha256
from typing import Any

from grid_trade.application.passive_policy import transition_passive_policy
from grid_trade.datasets.audit import require_promoting_dataset
from grid_trade.datasets.canonical import (
    CanonicalBookSnapshot,
    CanonicalEventEnvelope,
    CanonicalEventType,
    CanonicalFundingReference,
    canonical_event_sort_key,
)
from grid_trade.datasets.manifest import DatasetManifest, canonical_manifest_bytes
from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.orders import OrderSide, PassiveOrderIntent
from grid_trade.domain.risk import RiskDecision, RiskLimits, RiskState
from grid_trade.evidence.events import EvidenceEvent, EvidenceKind
from grid_trade.evidence.ledger import EvidenceLedger
from grid_trade.research.hftbacktest_adapter import (
    HftReplayConfig,
    ReplaySummary,
    canonical_events_to_hftbacktest_fixture,
    replay_passive_orders,
)
from grid_trade.research.replay_attribution import (
    FundingCashFlow,
    MarketImpactEligibilityConfig,
    OrderLiquidityEligibility,
    assess_order_liquidity_eligibility,
    funding_cash_flow,
)

_HOUR_NS = 3_600_000_000_000
_HFTBACKTEST_IDENTITY = "hftbacktest==2.4.4"
_QUEUE_MODEL_IDENTITY = "RiskAverseQueueModel"
_EXCHANGE_MODEL_IDENTITY = "PartialFillExchange"


def _require_non_empty(value: str, *, field: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field} must be non-empty")


def _require_finite(value: Decimal, *, field: str) -> None:
    if not value.is_finite():
        raise ValueError(f"{field} must be finite")


def _canonical_value(value: object) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _canonical_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple | list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"unsupported canonical replay value: {type(value).__name__}")


def _digest(value: object) -> str:
    payload = json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return sha256(f"{payload}\n".encode()).hexdigest()


def _datetime_from_ns(timestamp_ns: int) -> datetime:
    if timestamp_ns < 0:
        raise ValueError("timestamp_ns must be non-negative")
    seconds, remainder_ns = divmod(timestamp_ns, 1_000_000_000)
    return datetime.fromtimestamp(seconds, tz=UTC) + timedelta(microseconds=remainder_ns // 1_000)


@dataclass(frozen=True, slots=True)
class Tier2ReplayManifest:
    dataset: DatasetManifest
    strategy_identity: str
    calibration_identity: str
    hft: HftReplayConfig
    market_impact: MarketImpactEligibilityConfig
    synthetic_receive_latency_ns: int

    def __post_init__(self) -> None:
        _require_non_empty(self.strategy_identity, field="strategy_identity")
        _require_non_empty(self.calibration_identity, field="calibration_identity")
        if self.synthetic_receive_latency_ns < 0:
            raise ValueError("synthetic_receive_latency_ns must be non-negative")


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


def _validate_events(
    manifest: Tier2ReplayManifest,
    events: tuple[CanonicalEventEnvelope, ...],
) -> CanonicalEventEnvelope:
    if not events:
        raise ValueError("Tier-2 replay requires canonical events")
    if events != tuple(sorted(events, key=canonical_event_sort_key)):
        raise ValueError("Tier-2 replay events must be in deterministic canonical order")
    if any(event.instrument != manifest.dataset.instrument for event in events):
        raise ValueError("canonical event instrument must match the dataset manifest")
    for event in events:
        if event.event_type is CanonicalEventType.BOOK_SNAPSHOT:
            if not isinstance(event.payload, CanonicalBookSnapshot):
                raise TypeError("validated book event must carry CanonicalBookSnapshot payload")
            if not event.payload.bids or not event.payload.asks:
                raise ValueError("Tier-2 replay requires two-sided initial visible depth")
            return event
    raise ValueError("Tier-2 replay requires an initial book snapshot")


def _market_snapshot(
    *,
    initial_event: CanonicalEventEnvelope,
    manifest: Tier2ReplayManifest,
    starting_position: Decimal,
    realized_volatility: Decimal,
) -> MarketSnapshot:
    book = initial_event.payload
    if not isinstance(book, CanonicalBookSnapshot):
        raise TypeError("initial event must carry CanonicalBookSnapshot payload")
    return MarketSnapshot(
        instrument=initial_event.instrument,
        timestamp=_datetime_from_ns(initial_event.exchange_ts_ns),
        best_bid=book.bids[0].price,
        best_ask=book.asks[0].price,
        position=starting_position,
        realized_volatility=realized_volatility,
        source_id=f"tier2:{manifest.dataset.audit_digest}",
    )


def _order_liquidity(
    *,
    book: CanonicalBookSnapshot,
    order: PassiveOrderIntent,
    config: MarketImpactEligibilityConfig,
) -> OrderLiquidityEligibility:
    levels = book.bids if order.side is OrderSide.BUY else book.asks
    visible_same_level_quantity = next(
        (level.quantity for level in levels if level.price == order.price),
        None,
    )
    visible_top_n_notional = sum(
        (level.price * level.quantity for level in levels),
        Decimal(0),
    )
    return assess_order_liquidity_eligibility(
        order_price=order.price,
        order_quantity=order.quantity,
        visible_same_level_quantity=visible_same_level_quantity,
        visible_top_n_notional=visible_top_n_notional,
        visibility_trusted=True,
        config=config,
    )


def _signed_fill_quantity(
    *,
    client_order_id: str,
    quantity: Decimal,
    side_by_client_id: dict[str, OrderSide],
) -> Decimal:
    side = side_by_client_id[client_order_id]
    return quantity if side is OrderSide.BUY else -quantity


def _position_at(
    *,
    timestamp_ns: int,
    starting_position: Decimal,
    replay_summary: ReplaySummary,
    side_by_client_id: dict[str, OrderSide],
) -> Decimal:
    position = starting_position
    for fill in replay_summary.fills:
        if fill.timestamp_ns > timestamp_ns:
            break
        position += _signed_fill_quantity(
            client_order_id=fill.client_order_id,
            quantity=fill.quantity,
            side_by_client_id=side_by_client_id,
        )
    return position


def _funding_cash_flows(
    *,
    events: tuple[CanonicalEventEnvelope, ...],
    starting_position: Decimal,
    replay_summary: ReplaySummary,
    side_by_client_id: dict[str, OrderSide],
) -> tuple[FundingCashFlow, ...]:
    flows: list[FundingCashFlow] = []
    for event in events:
        if event.event_type is not CanonicalEventType.FUNDING_REFERENCE:
            continue
        if event.exchange_ts_ns % _HOUR_NS != 0:
            continue
        reference = event.payload
        if not isinstance(reference, CanonicalFundingReference):
            raise TypeError("validated funding event must carry CanonicalFundingReference payload")
        position = _position_at(
            timestamp_ns=event.exchange_ts_ns,
            starting_position=starting_position,
            replay_summary=replay_summary,
            side_by_client_id=side_by_client_id,
        )
        flows.append(
            funding_cash_flow(
                timestamp_ns=event.exchange_ts_ns,
                position=position,
                reference=reference,
            )
        )
    return tuple(flows)


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
    return _digest(
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
    identity = _digest(
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


def _order_payload(order: PassiveOrderIntent) -> dict[str, object]:
    return {
        "client_order_id": order.client_order_id,
        "generation": order.generation,
        "level": order.level,
        "side": order.side,
        "price": order.price,
        "quantity": order.quantity,
        "reduce_only": order.reduce_only,
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
    risk_decision: RiskDecision,
    decision_digest: str,
    converted_receive_mode: str,
    replay_summary: ReplaySummary,
    funding_flows: tuple[FundingCashFlow, ...],
    maker_fee_cash_flow: Decimal,
    ending_position: Decimal,
) -> tuple[EvidenceEvent, ...]:
    ledger = EvidenceLedger()
    decision_time = _datetime_from_ns(initial_event.exchange_ts_ns)
    ledger.append(
        EvidenceEvent(
            timestamp=decision_time,
            kind=EvidenceKind.RISK_DECISION,
            event_id=f"{run_id}:risk",
            payload={
                "run_id": run_id,
                "decision_digest": decision_digest,
                "allow_new_risk": risk_decision.allow_new_risk,
                "cancel_all_passive": risk_decision.cancel_all_passive,
                "target_flat": risk_decision.target_flat,
                "reasons": tuple(reason.value for reason in risk_decision.reasons),
                "candidate_orders": tuple(_order_payload(order) for order in candidate_orders),
                "risk_orders": tuple(_order_payload(order) for order in risk_orders),
            },
        )
    )
    ledger.append(
        EvidenceEvent(
            timestamp=decision_time,
            kind=EvidenceKind.DESIRED_LADDER,
            event_id=f"{run_id}:eligible-ladder",
            payload={
                "run_id": run_id,
                "eligible_orders": tuple(_order_payload(order) for order in eligible_orders),
                "eligibility": eligibility,
                "market_impact_config": manifest.market_impact,
            },
        )
    )
    for index, fill in enumerate(replay_summary.fills):
        ledger.append(
            EvidenceEvent(
                timestamp=_datetime_from_ns(fill.timestamp_ns),
                kind=EvidenceKind.FILL,
                event_id=f"{run_id}:fill:{index:06d}",
                payload={
                    "run_id": run_id,
                    "client_order_id": fill.client_order_id,
                    "timestamp_ns": fill.timestamp_ns,
                    "price": fill.price,
                    "quantity": fill.quantity,
                    "remaining_quantity": fill.remaining_quantity,
                },
            )
        )
    for index, flow in enumerate(funding_flows):
        ledger.append(
            EvidenceEvent(
                timestamp=_datetime_from_ns(flow.timestamp_ns),
                kind=EvidenceKind.FUNDING_DECISION,
                event_id=f"{run_id}:funding:{index:06d}",
                payload={
                    "run_id": run_id,
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
    summary_timestamp_ns = max(event.exchange_ts_ns for event in events)
    ledger.append(
        EvidenceEvent(
            timestamp=_datetime_from_ns(summary_timestamp_ns),
            kind=EvidenceKind.RUN_SUMMARY,
            event_id=f"{run_id}:summary",
            payload={
                "run_id": run_id,
                "dataset": {
                    "manifest_digest": manifest_digest,
                    "acceptance": manifest.dataset.acceptance,
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
                    "hft_config": manifest.hft,
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
    return ledger.snapshot()


def run_tier2_replay(
    *,
    manifest: Tier2ReplayManifest,
    events: tuple[CanonicalEventEnvelope, ...],
    candidate_orders: tuple[PassiveOrderIntent, ...],
    risk_limits: RiskLimits,
    risk_state: RiskState,
    starting_position: Decimal,
    realized_volatility: Decimal,
) -> Tier2ReplayResult:
    require_promoting_dataset(manifest.dataset)
    _require_finite(starting_position, field="starting_position")
    _require_finite(realized_volatility, field="realized_volatility")
    if realized_volatility < 0:
        raise ValueError("realized_volatility must be non-negative")
    if risk_state.open_order_count != 0:
        raise ValueError("Tier-2 replay requires a clean initial working-order state")

    converted = canonical_events_to_hftbacktest_fixture(
        events,
        synthetic_receive_latency_ns=manifest.synthetic_receive_latency_ns,
    )
    initial_event = _validate_events(manifest, events)
    market_snapshot = _market_snapshot(
        initial_event=initial_event,
        manifest=manifest,
        starting_position=starting_position,
        realized_volatility=realized_volatility,
    )
    policy = transition_passive_policy(
        decision="tier2-pre-risk-candidate",
        previous_state=0,
        candidate_state=1,
        market_snapshot=market_snapshot,
        risk_limits=risk_limits,
        risk_state=risk_state,
        working_orders=(),
        proposed_ladder=candidate_orders,
    )
    risk_orders = policy.desired_ladder

    initial_book = initial_event.payload
    if not isinstance(initial_book, CanonicalBookSnapshot):
        raise TypeError("initial event must carry CanonicalBookSnapshot payload")
    eligibility = tuple(
        _order_liquidity(
            book=initial_book,
            order=order,
            config=manifest.market_impact,
        )
        for order in risk_orders
    )
    eligible_orders = tuple(
        order for order, decision in zip(risk_orders, eligibility, strict=True) if decision.eligible
    )

    if eligible_orders:
        replay_summary = replay_passive_orders(
            converted.fixture,
            eligible_orders,
            manifest.hft,
        )
    else:
        replay_summary = ReplaySummary(
            fills=(),
            ending_position=Decimal(0),
            open_order_count=0,
        )

    side_by_client_id = {order.client_order_id: order.side for order in eligible_orders}
    funding_flows = _funding_cash_flows(
        events=events,
        starting_position=starting_position,
        replay_summary=replay_summary,
        side_by_client_id=side_by_client_id,
    )
    funding_pnl = sum((flow.cash_flow for flow in funding_flows), Decimal(0))
    maker_fee_cash_flow = -sum(
        (fill.price * fill.quantity * manifest.hft.maker_fee for fill in replay_summary.fills),
        Decimal(0),
    )
    ending_position = starting_position + replay_summary.ending_position

    decision_digest = _decision_digest(
        initial_event=initial_event,
        manifest=manifest,
        candidate_orders=candidate_orders,
        risk_limits=risk_limits,
        risk_state=risk_state,
        starting_position=starting_position,
        realized_volatility=realized_volatility,
        risk_decision=policy.risk_decision,
        risk_orders=risk_orders,
        eligibility=eligibility,
    )
    run_id = _run_id(
        manifest=manifest,
        candidate_orders=candidate_orders,
        risk_limits=risk_limits,
        risk_state=risk_state,
        starting_position=starting_position,
        realized_volatility=realized_volatility,
    )
    evidence_events = _evidence(
        run_id=run_id,
        manifest=manifest,
        events=events,
        initial_event=initial_event,
        candidate_orders=candidate_orders,
        risk_orders=risk_orders,
        eligible_orders=eligible_orders,
        eligibility=eligibility,
        risk_decision=policy.risk_decision,
        decision_digest=decision_digest,
        converted_receive_mode=converted.receive_timestamp_mode.value,
        replay_summary=replay_summary,
        funding_flows=funding_flows,
        maker_fee_cash_flow=maker_fee_cash_flow,
        ending_position=ending_position,
    )
    ledger = EvidenceLedger()
    ledger.extend(evidence_events)

    return Tier2ReplayResult(
        evidence_events=evidence_events,
        evidence_digest=ledger.digest(),
        decision_digest=decision_digest,
        risk_decision=policy.risk_decision,
        candidate_order_count=len(candidate_orders),
        risk_accepted_order_count=len(risk_orders),
        eligible_order_count=len(eligible_orders),
        order_eligibility=eligibility,
        replay_summary=replay_summary,
        funding_cash_flows=funding_flows,
        funding_pnl=funding_pnl,
        maker_fee_cash_flow=maker_fee_cash_flow,
        ending_position=ending_position,
    )


__all__ = ["Tier2ReplayManifest", "Tier2ReplayResult", "run_tier2_replay"]
