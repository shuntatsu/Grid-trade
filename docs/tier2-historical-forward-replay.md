# Tier-2 Historical + Forward Replay Foundation

## Status

This subsystem is **RESEARCH / NO-GO FOR PRODUCTION**.

It does not establish profitability, alpha, sealed out-of-sample validity, live-trading readiness, or production authorization. Its purpose is to make the next economic-validation step causal, conservative, reproducible, and auditable.

## Data contract

Tier-2 accepts immutable raw objects identified by exact SHA-256, byte length, source family, dataset type, instrument, acquisition time, source locator, and collector/decoder schema versions.

Canonical events retain exchange time, actually observed receive time when available, raw-object SHA-256, raw record ordinal, normalization schema version, and stable source identity where available.

Dataset acceptance is fail-closed. The audit records and can enforce:

- exact raw-object resolvability;
- deterministic event ordering;
- conflicting versus exact duplicate trades;
- requested versus observed coverage;
- declared tick/lot alignment;
- book/trade observation overlap;
- exchange-time gap statistics and optional gap warnings;
- required hourly funding/reference boundaries;
- normalization schema identity.

The required funding schedule and audit expectations are part of DatasetManifest identity. Missing required funding/reference state is unavailable, never zero-filled or forward-filled into a promoting replay.

## Hyperliquid acquisition boundary

Hyperliquid-specific decoding stays under `grid_trade.integrations.hyperliquid`; canonical dataset and strategy/risk layers do not depend on exchange SDK types.

The forward recorder is single-instrument by construction. It supports deterministic `l2Book` and `trades` subscriptions, a default 60-second `metaAndAssetCtxs` reference cadence, heartbeat/ping scheduling, disconnect/reconnect continuity epochs, and an authoritative first post-reconnect book boundary.

Inbound segment payloads are persisted and fsynced before capture state advances. Completed segments are hashed and accompanied by an atomically published deterministic manifest. Interrupted segments remain explicitly incomplete and cannot enter an accepted dataset.

Network transports are injected behind a protocol. Blocking CI uses offline fixture transports; real network behavior remains a separate integration-validation gate.

## Top-N visibility contract

Hyperliquid `l2Book` observations are treated as snapshot-like MBP observations, not MBO data and not raw depth diffs.

When a previously visible level disappears:

- if its price remains inside the newly observable side range, its absence is a confirmed zero/removal;
- if its price falls outside the new top-N boundary, visibility is lost rather than a cancellation being invented;
- re-entry begins a new visibility epoch;
- a strategy order cannot claim a promoting fill after trustworthy visibility at its price is lost.

Canonical-to-hftbacktest conversion emits depth changes only where visibility is trustworthy.

## Causal calibration and candidate generation

`derive_tier2_calibrated_candidate` consumes only canonical events at or before the declared decision timestamp and only calibration Evidence frames whose `as_of` timestamp is at or before that decision.

Intensity buckets, matured markouts, and OFI impact samples must already be causally mature at their Evidence-frame time. Future frames cannot change the earlier candidate or its provenance digest.

The path is:

```text
accepted canonical pre-roll
  -> Universal Calibration
  -> calibrated market state
  -> risk-derived inventory capacity
  -> calibrated adaptive candidate
  -> existing Hard Risk gate
  -> visibility/market-impact eligibility
  -> conservative hftbacktest replay
  -> funding + Evidence
```

The candidate provenance SHA-256 is included in the replay calibration identity. Hard Risk remains authoritative and may eliminate all candidate orders.

## Replay model

Research replay pins `hftbacktest==2.4.4`, `PartialFillExchange`, and `RiskAverseQueueModel` with explicit tick size, lot size, latency assumptions, and fees.

Archive data without observed receive time uses explicitly labeled synthetic receive latency. Synthetic timing is never represented as observed timing.

A known pinned-runtime edge around an exact-terminal partial-fill transition is tested fail-closed. The implementation does not whitelist hftbacktest callback/status code 14 as success.

## Funding completeness

The replay extent deterministically derives every UTC hourly boundary that it crosses. A promoting DatasetManifest must declare exactly the same required funding schedule, and re-audit must confirm complete funding rate plus oracle/reference price at each boundary.

This catches both partially missing funding fields and a funding event that is missing entirely.

## Deterministic Evidence

Tier-2 Evidence records dataset/manifest identity, raw hashes, audit digest, required funding schedule, strategy/calibration identity, queue/exchange/runtime labels, latency assumptions, eligibility, visibility boundary, fills, funding cash flows, fee cash flow, and ending position.

CI runs the Tier-2 fixture in fresh Python processes and requires exact Evidence-digest equality. The Evidence explicitly keeps:

- `production_authorized=false`;
- `alpha_validated=false`;
- `economics_validated=false`.

Full economic PnL attribution remains disabled until a declared causal markout methodology is implemented and validated.

## Next promotion gates

The foundation is not the economic result. Before any production or alpha claim, the project still requires:

1. real Hyperliquid historical objects plus forward-recorded segments with accepted audit results;
2. continuous replay over declared train/calibration/OOS partitions;
3. a causal markout and full PnL-attribution methodology;
4. predeclared market-impact thresholds and sensitivity runs;
5. sealed OOS evaluation with no post-hoc tuning;
6. forward/shadow validation and operational fault testing;
7. a separate explicit production authorization decision.
