from dataclasses import replace
from decimal import Decimal

from grid_trade.application.passive_policy import transition_passive_policy
from grid_trade.datasets.audit import require_promoting_dataset
from grid_trade.datasets.canonical import CanonicalBookSnapshot, CanonicalEventEnvelope
from grid_trade.domain.instrument import (
    LEGACY_UNSPECIFIED_INSTRUMENT,
    require_instruments_compatible,
)
from grid_trade.domain.orders import PassiveOrderIntent
from grid_trade.domain.risk import RiskLimits, RiskState
from grid_trade.evidence.ledger import evidence_digest as compute_evidence_digest
from grid_trade.research.hftbacktest_adapter import (
    ReplaySummary,
    canonical_events_to_hftbacktest_fixture,
    replay_passive_orders,
)
from grid_trade.research.replay_attribution import summarize_order_liquidity
from grid_trade.research.tier2_replay.attribution import _funding_cash_flows
from grid_trade.research.tier2_replay.dataset import (
    _market_snapshot,
    _validate_events,
    _validate_exact_hour_funding,
    _validated_audit_events,
)
from grid_trade.research.tier2_replay.evidence import _evidence
from grid_trade.research.tier2_replay.identity import _decision_digest, _run_id
from grid_trade.research.tier2_replay.liquidity import (
    _attach_visibility_boundary,
    _has_market_feed_after_initial,
    _order_liquidity,
    _trusted_replay_events,
)
from grid_trade.research.tier2_replay.models import (
    Tier2ReplayManifest,
    Tier2ReplayResult,
    _require_finite,
)


def _bind_candidate_orders(
    manifest: Tier2ReplayManifest,
    candidate_orders: tuple[PassiveOrderIntent, ...],
) -> tuple[PassiveOrderIntent, ...]:
    bound: list[PassiveOrderIntent] = []
    for order in candidate_orders:
        if order.instrument_id == LEGACY_UNSPECIFIED_INSTRUMENT:
            bound.append(replace(order, instrument_id=manifest.dataset.instrument))
            continue
        require_instruments_compatible(
            manifest.dataset.instrument,
            order.instrument_id,
            context="Tier-2 candidate/dataset",
        )
        bound.append(order)
    return tuple(bound)


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
    candidate_orders = _bind_candidate_orders(manifest, candidate_orders)
    _require_finite(starting_position, field="starting_position")
    _require_finite(realized_volatility, field="realized_volatility")
    if realized_volatility < 0:
        raise ValueError("realized_volatility must be non-negative")
    if risk_state.open_order_count != 0:
        raise ValueError("Tier-2 replay requires a clean initial working-order state")

    _validate_events(manifest, events)
    events = _validated_audit_events(manifest, events)
    initial_event = _validate_events(manifest, events)
    _validate_exact_hour_funding(events)
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
        snapshot=market_snapshot,
        risk_limits=risk_limits,
        risk_state=risk_state,
        working_orders=(),
        proposed_ladder=candidate_orders,
    )
    risk_orders = policy.desired_ladder

    initial_book = initial_event.payload
    if not isinstance(initial_book, CanonicalBookSnapshot):
        raise TypeError("initial event must carry CanonicalBookSnapshot payload")
    decision_eligibility = tuple(
        _order_liquidity(
            book=initial_book,
            order=order,
            config=manifest.market_impact,
        )
        for order in risk_orders
    )
    replay_eligibility = tuple(
        _attach_visibility_boundary(events=events, order=order, eligibility=eligibility)
        for order, eligibility in zip(risk_orders, decision_eligibility, strict=True)
    )
    eligible_orders = tuple(
        order
        for order, decision in zip(risk_orders, replay_eligibility, strict=True)
        if decision.eligible
    )
    liquidity_summary = summarize_order_liquidity(replay_eligibility)
    trusted_events = _trusted_replay_events(
        events,
        visibility_boundary_ts_ns=liquidity_summary.earliest_visibility_boundary_ts_ns,
    )

    if eligible_orders and _has_market_feed_after_initial(trusted_events):
        converted = canonical_events_to_hftbacktest_fixture(
            trusted_events,
            synthetic_receive_latency_ns=manifest.synthetic_receive_latency_ns,
        )
        replay_summary = replay_passive_orders(converted.fixture, eligible_orders, manifest.hft)
        converted_receive_mode = converted.receive_timestamp_mode.value
    else:
        replay_summary = ReplaySummary(
            fills=(),
            ending_position=Decimal(0),
            open_order_count=0,
        )
        converted_receive_mode = (
            "not_run_no_eligible_orders"
            if not eligible_orders
            else "not_run_no_trusted_feed_before_visibility_boundary"
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
        eligibility=decision_eligibility,
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
        eligibility=replay_eligibility,
        liquidity_summary=liquidity_summary,
        risk_decision=policy.risk_decision,
        decision_digest=decision_digest,
        converted_receive_mode=converted_receive_mode,
        replay_summary=replay_summary,
        funding_flows=funding_flows,
        maker_fee_cash_flow=maker_fee_cash_flow,
        ending_position=ending_position,
    )

    return Tier2ReplayResult(
        evidence_events=evidence_events,
        evidence_digest=compute_evidence_digest(evidence_events),
        decision_digest=decision_digest,
        risk_decision=policy.risk_decision,
        candidate_order_count=len(candidate_orders),
        risk_accepted_order_count=len(risk_orders),
        eligible_order_count=len(eligible_orders),
        order_eligibility=replay_eligibility,
        liquidity_summary=liquidity_summary,
        replay_summary=replay_summary,
        funding_cash_flows=funding_flows,
        funding_pnl=funding_pnl,
        maker_fee_cash_flow=maker_fee_cash_flow,
        ending_position=ending_position,
    )


__all__ = ["run_tier2_replay"]
