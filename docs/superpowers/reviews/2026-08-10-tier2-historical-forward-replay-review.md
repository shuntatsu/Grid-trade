# Tier-2 Historical + Forward Replay Review

Date: 2026-08-10

Status: **RESEARCH / NO-GO FOR PRODUCTION**

## Review scope

Reviewer-mode review of the `tier2-historical-forward-replay` branch covering dataset provenance, Hyperliquid acquisition/normalization, canonical visibility semantics, audit acceptance, calibrated candidate generation, Hard Risk integration, hftbacktest replay, funding treatment, deterministic Evidence, CI boundaries, and known runtime limitations.

## Material findings found during implementation review

### 1. Top-N zero state could lose later visibility semantics

A confirmed zero/removal must remain part of the known price-state domain. If the next top-N boundary moves past that price, the state transitions to visibility-lost; if the price becomes observable again, it begins a new epoch. Treating zero as simply inactive could incorrectly skip that later transition.

Fix: visibility is modeled explicitly as VISIBLE / ZERO / LOST, with deterministic epoch transitions and property tests.

### 2. Dataset audit digest was not initially bound to the exact replay event set

A manifest could have carried an audit digest while a different event tuple was supplied to replay.

Fix: Tier-2 re-audits supplied canonical events, compares the audit digest and acceptance, and replays only the audit's accepted/deduplicated event set.

### 3. Candidate orders were initially external to Tier-2 causal orchestration

The replay path originally accepted candidate orders instead of deriving them from the same causal data contract.

Fix: `derive_tier2_calibrated_candidate` consumes only decision-time-or-earlier canonical events and only already-matured calibration Evidence, runs Universal Calibration, derives inventory capacity, and produces the calibrated adaptive candidate. `run_calibrated_tier2_replay` then routes that candidate through the existing Hard Risk gate.

### 4. Forward recorder initially stopped at a durable segment writer

The design also required single-instrument session state, reference cadence, heartbeat, reconnect continuity, and a fresh authoritative book boundary after reconnect.

Fix: `ForwardRecorderSession` now provides deterministic subscriptions, default 60-second reference cadence, ping scheduling, reconnect continuity epochs, and first-post-reconnect book records while persisting bytes before capture-state advancement.

### 5. Completed forward segments initially lacked durable manifest publication

Returning an in-memory object after hashing was insufficient for crash-recoverable provenance.

Fix: completed segments and their deterministic manifests are separately atomically published, with file fsync and POSIX directory fsync. Interrupted segments remain incomplete and have no published completed manifest.

### 6. Funding validation originally covered only funding events that existed

A replay could cross a UTC hourly boundary while the funding event was entirely absent.

Fix: the replay extent derives all required hourly boundaries. The DatasetManifest records the required schedule, audit identity records the schedule, and a promoting replay rejects a schedule mismatch or missing funding/reference state.

### 7. Dataset-level audit completeness was narrower than the design

Raw identity, ordering, duplicates, funding, and optional gap warning were implemented, but requested coverage, declared tick/lot alignment, book/trade overlap, and deterministic gap statistics were missing.

Fix: optional `DatasetAuditExpectations` and report metrics now cover those dataset-level contracts. Expectations are stored in DatasetManifest identity so acquisition policy remains reproducible.

### 8. Pinned hftbacktest exact-terminal partial-fill edge

`hftbacktest==2.4.4` can surface an invalid-order-status path around a specific exact-terminal partial-fill transition. Treating callback/status 14 as benign would hide a runtime/model inconsistency.

Fix: the pinned limitation has a dedicated regression and remains fail-closed. The normal deterministic fixture avoids relying on that edge; no success whitelist was added.

## Causality and fabrication review

- Future canonical events can alter later Evidence but do not alter the earlier decision digest.
- Future calibration Evidence frames do not alter the earlier calibrated candidate or candidate provenance digest.
- Markout/OFI inputs must already be mature at the Evidence-frame timestamp.
- Missing L2 visibility is not converted to zero outside the observable top-N domain.
- Missing funding/reference state is not zero-filled.
- Archive receive latency is explicitly synthetic when receive time was not observed.
- Replay stops before the first untrusted order-price visibility boundary rather than claiming a fill through unknown queue state.

## Dependency review

- `datasets` is runtime-neutral and excludes exchange/runtime/strategy/risk/application dependencies.
- Hyperliquid integration may depend on datasets, but not Strategy, Risk, Application, Calibration, Research, hftbacktest, or NautilusTrader.
- hftbacktest remains confined to Research.
- Existing Hard Risk remains the authoritative pre-trade gate.

## CI and deterministic evidence contract

Research CI is offline and uses checked-in source-shape fixtures. It covers Hyperliquid normalization/recorder behavior, Tier-2 datasets/replay strict typing, and fresh-process Evidence equality for S0, S1, S2, adaptive, microstructure calibration, calibrated adaptive, and Tier-2 fixtures.

The final branch must not be reported complete until the same final HEAD passes Core and Research workflows after all review fixes and documentation updates.

## Intentionally unverified / remaining gates

The following are intentionally **not** claimed by this branch:

- real Hyperliquid network transport validation against a long-running live connection;
- accepted real historical + forward dataset coverage;
- complete causal economic PnL/markout attribution;
- sealed OOS alpha evidence;
- shadow/live operational performance;
- profitability;
- production authorization.

These are follow-on validation gates, not evidence that the foundation itself is production-ready.
