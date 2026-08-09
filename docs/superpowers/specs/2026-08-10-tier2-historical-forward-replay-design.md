# Tier-2 Historical + Forward Replay Foundation Design

Date: 2026-08-10
Status: Design for review
Repository: `shuntatsu/Grid-trade`
Parent commit: `8b8c5e22445c3d028f572803fbd1789a90629048`
Branch: `tier2-historical-forward-replay`
License: LGPL-3.0
Production status: RESEARCH / NO-GO

## 1. Purpose

Build the evidence-first data and replay foundation required to evaluate the calibrated adaptive Grid strategy against realistic Hyperliquid microstructure rather than deterministic mechanics fixtures.

The maintained approach is **Historical + Forward Recorder hybrid**:

1. seed historical research from Hyperliquid official L2 archives, node trade/fill datasets, and asset-context/funding sources;
2. record forward Hyperliquid `l2Book` and `trades` WebSocket messages plus periodic reference state;
3. preserve source payloads in an immutable raw store with SHA-256 provenance;
4. normalize historical and forward sources into one canonical causal event model;
5. reject unresolved gaps, conflicting duplicates, impossible books, or missing required funding/metadata before replay;
6. convert accepted canonical events through the existing pinned `hftbacktest==2.4.4` research boundary;
7. replay with explicit queue, latency, partial-fill, fee, funding, and order-size assumptions;
8. emit deterministic Evidence identifying exactly which source bytes and assumptions produced each result.

This phase proves replay infrastructure only. It does not establish profitability and does not authorize live trading.

## 2. Why a hybrid source is required

Hyperliquid officially states that historical archive uploads are approximately monthly, are not guaranteed to be timely, and may contain missing data. Historical L2 snapshots are provided in `market_data`, asset contexts in `asset_ctxs`, and node datasets provide trade/fill history. Hyperliquid also explicitly recommends recording additional datasets through the API when needed.

Therefore:

- historical sources provide breadth and older regimes;
- the forward recorder provides future continuity, receive-time evidence, and a reference for archive assumptions;
- both must normalize into the same canonical event model so Strategy/Calibration never branches on historical-vs-forward origin.

## 3. Scope and decomposition

This specification intentionally stops at the **Tier-2 Data & Replay Foundation**.

In scope:

- Hyperliquid archive L2 ingestion;
- node trade/fill ingestion;
- asset-context and funding/reference ingestion;
- forward `l2Book` and `trades` recording;
- periodic `metaAndAssetCtxs` reference capture;
- immutable raw storage and provenance manifests;
- canonical book/trade/funding/reference contracts;
- deterministic ordering, deduplication, and gap audit;
- snapshot-visibility rules for top-N books;
- canonical-to-hftbacktest conversion;
- conservative queue and partial-fill baseline;
- latency scenario contracts;
- fee/funding/adverse-selection/PnL attribution contracts;
- replay order-size / market-impact eligibility;
- deterministic Evidence;
- unit/property/integration/regression tests.

Deferred to the next specification:

- broad parameter search;
- S0-S7 economic promotion;
- symbol-disjoint train/validation/sealed-test orchestration;
- full walk-forward campaign;
- final sealed OOS decision;
- live/testnet order submission and production deployment.

## 4. Verified external constraints as of 2026-08-10

The design treats the following as external contracts, not strategy assumptions:

- official Hyperliquid historical asset data may be missing or delayed;
- historical L2 snapshots are available under `hyperliquid-archive/market_data`;
- historical asset contexts are available under `hyperliquid-archive/asset_ctxs`;
- official node datasets expose historical trades/fills and node schemas document raw book diffs;
- WebSocket supports `l2Book` and `trades`;
- `WsBook` carries `coin`, `levels`, and exchange `time` and is snapshot-oriented;
- REST/info `l2Book` returns at most 20 levels per side;
- `metaAndAssetCtxs` exposes current perpetual asset context including current funding and price context;
- funding is applied hourly;
- REST requests are rate limited and must not replace streaming market data;
- hftbacktest is market-data replay and does not model the market impact created by our own orders;
- `PartialFillExchange` supports partial-fill simulation under replay assumptions;
- Market-By-Price replay requires an explicit queue model;
- `RiskAverseQueueModel` is the maintained conservative baseline.

## 5. Architecture

```text
Official historical sources                 Forward recorder
archive L2 / node trades / asset_ctxs        WS l2Book / WS trades
                |                            metaAndAssetCtxs
                +-------------+--------------+
                              v
                  Immutable Raw Object Store
                  exact payload + SHA-256
                              |
                              v
                  Deterministic Normalizer
                              |
                              v
                    Dataset Quality Audit
                   ACCEPTED / WARN / REJECT
                              |
                       ACCEPTED only
                              v
                   Canonical Replay Dataset
             book + trades + funding + metadata
                    |                   |
                    v                   v
          Universal Calibration   hftbacktest Adapter
                    |                   |
                    +---------+---------+
                              v
                 Calibrated Adaptive Grid
                    -> Hard Risk
                    -> Reconciliation
                    -> Replay fills
                              |
                              v
                Evidence + PnL Attribution
```

Exchange decoding remains outside `strategy`, `risk`, `application`, and `calibration`.

## 6. Package boundaries

Recommended boundaries:

```text
src/grid_trade/
  datasets/
    contracts.py
    manifest.py
    canonical.py
    audit.py
  integrations/
    hyperliquid/
      archive.py
      node_data.py
      forward_recorder.py
      normalization.py
  research/
    hftbacktest_adapter.py
    tier2_replay.py
    replay_attribution.py
```

`datasets/` is runtime-neutral and must not import Hyperliquid SDK, NautilusTrader, hftbacktest, Strategy, Risk, or Application.

`integrations/hyperliquid/` owns source-specific acquisition and decoding and produces canonical dataset contracts only.

`research/` may use datasets plus existing Application/Strategy/Risk and optional hftbacktest dependencies.

## 7. Immutable raw object store

Every acquired object/segment has immutable identity containing:

- source family: archive / node / websocket / info;
- dataset type;
- instrument identity;
- source timestamp range when known;
- recorder receive-time range when applicable;
- exact byte length;
- SHA-256 of the exact stored payload;
- acquisition timestamp;
- source locator metadata;
- collector/decoder schema version.

Raw data is never edited to repair errors. A correction becomes a new raw object and new manifest.

Large datasets are not committed to Git. Git stores only code, schemas, tiny source-format fixtures, example manifests, and expected deterministic digests.

## 8. Forward recorder

### 8.1 Streaming inputs

For each explicitly configured single instrument, subscribe to:

- `l2Book`;
- `trades`.

The recorder is not an all-market crawler by default.

### 8.2 Reference inputs

On connection, record current perpetual metadata/context. While connected, record `metaAndAssetCtxs` with a **default 60-second cadence**. The cadence is an explicit recorder meta-parameter and is included in provenance.

This periodic source supplies current funding/reference context for later replay and audit. A gap larger than two configured reference intervals is recorded as a reference-data gap; it is never silently forward-filled as measured state.

Static/slow-changing venue metadata is also snapshotted at recorder startup and whenever an explicit metadata refresh is requested.

### 8.3 Durable segments

Inbound payload is persisted before downstream normalization acknowledges it. Segment rotation is deterministic by configured time/size policy.

Finalization order:

1. flush;
2. close;
3. SHA-256 exact segment bytes;
4. manifest entry;
5. atomic publication of the finalized manifest entry.

Interrupted segments are explicitly incomplete and cannot enter an `ACCEPTED` dataset.

### 8.4 Reconnect and heartbeat

Recorder supports Hyperliquid heartbeat/ping requirements and reconnects.

Every disconnect/reconnect produces a continuity record containing disconnect time, reconnect times, first post-reconnect exchange timestamp, and uncovered interval.

The first valid post-reconnect `WsBook` establishes a new authoritative visible-book snapshot boundary. Data before and after the gap is not concatenated as if queue continuity were known.

## 9. Canonical event model

Every canonical event contains:

- event type;
- instrument/source identity;
- exchange timestamp;
- local receive timestamp when actually observed;
- source sequence/block/trade identity when available;
- raw object SHA-256;
- raw record ordinal/offset;
- normalization schema version.

### 9.1 Canonical book snapshot

Contains ordered bid/ask tuples of `(price, quantity, order_count)` plus source precision/aggregation metadata.

Historical `l2Book` and WebSocket `WsBook` are treated as **snapshot-like MBP observations**, not Market-By-Order data and not raw diffs.

### 9.2 Top-N visibility and snapshot-to-depth conversion

This is a hard replay contract.

A level that disappears between two top-N snapshots is considered a confirmed zero/removal only when its price remains inside the newly observable price range for that side and is absent from the new snapshot.

If a previously visible level merely falls outside the new top-N observable boundary, the system records **visibility lost**, not cancellation.

Consequences:

- no queue advancement or cancellation evidence is invented outside the visible range;
- a strategy order whose price loses trustworthy book visibility becomes replay-ineligible until continuity is re-established;
- promoting replay cannot claim fills while queue state at the order price is unknown;
- a fresh snapshot/re-entry starts a new visibility epoch rather than pretending the old queue survived;
- raw node book diffs, if supported later, are a distinct higher-fidelity source and may not be mixed into snapshot semantics without a verified reconstruction contract.

Canonical-to-hftbacktest conversion derives deterministic depth updates only inside the trustworthy observable domain.

### 9.3 Canonical trade

Contains aggressor side under one repository convention, price, quantity, stable source identity when available, and exchange timestamp.

Hyperliquid side mapping is tested against official source semantics before dataset acceptance.

### 9.4 Canonical funding/reference

Contains observation timestamp, applicable funding boundary/interval, current/applied funding value as appropriate to the source, oracle/reference price when required for accounting, and readiness/quality state.

Missing funding/reference is unavailable, never zero-filled.

## 10. Time and causality

Keep separate:

1. exchange/event time;
2. recorder local receive time;
3. strategy/replay decision time.

Archive data must not fabricate measured local latency. Archive local time may be synthesized only through an explicitly named latency scenario and is labeled synthetic in Evidence.

Forward data preserves observed receive time. Feed-latency models may be learned only from a declared calibration split and cannot use future sealed observations to rewrite earlier latency.

Future book/trade/funding/markout records cannot alter an earlier strategy decision digest.

## 11. Ordering and deduplication

Collapse only provably duplicate source records.

Numerically identical snapshots at different timestamps are distinct observations.

Conflicting records with the same stable identity reject the dataset.

Deterministic tie-break ordering is schema-versioned:

1. exchange timestamp;
2. source sequence/block ID when available;
3. declared event-type precedence;
4. raw object hash;
5. raw record ordinal.

Changing ordering policy changes dataset schema identity because fills may change.

## 12. Dataset audit

Acceptance states:

- `ACCEPTED`;
- `ACCEPTED_WITH_WARNINGS`;
- `REJECTED`.

Economic promotion later requires `ACCEPTED`; warning datasets may be used only for explicitly non-promoting sensitivity work.

Required audit checks include:

- identity consistency;
- valid prices/quantities;
- bid/ask ordering;
- crossed/impossible books;
- deterministic monotonic ordering;
- duplicate/conflict counts;
- top-N visibility continuity;
- timestamp-gap distributions;
- reconnect intervals;
- archive requested-vs-observed coverage;
- trade/book overlap;
- funding/reference coverage;
- tick/lot metadata;
- raw-hash resolvability;
- normalization version;
- replay order-price visibility.

Unknown book/trade gaps capable of changing fill interpretation are rejection conditions for promoting datasets.

The audit layer never interpolates L2 levels, trades, queue events, or funding to manufacture fill evidence.

## 13. Historical ingestion

### 13.1 L2 archive

Loader accepts explicit date/hour/instrument requests for official `market_data/.../l2Book/...` objects.

Hash compressed source bytes before decoding. Decompression and decoder versions are separate provenance fields.

### 13.2 Node trades/fills

Use official node trade/fill datasets when available. Different historical formats use explicit decoder versions; unknown format fails closed.

### 13.3 Asset contexts / funding

Use official asset-context and/or historical funding sources through explicit source contracts. Record exactly which source supplied current funding, applied funding, and oracle/reference state.

Never infer historical funding from current metadata.

### 13.4 Network policy

Bulk archive acquisition is an explicit operator/research command and may incur requester-pays transfer cost.

Normal Core/Research CI must **not** depend on live Hyperliquid API, WebSocket, or S3 availability. CI uses tiny checked-in fixtures that preserve official payload shapes. Optional network conformance tests are separate and non-blocking until a dedicated policy promotes them.

## 14. hftbacktest conversion

Keep the existing optional-runtime boundary pinned to `hftbacktest==2.4.4` unless a separate dependency review approves an upgrade.

The existing synthetic `MicrostructureFixture` remains unchanged as regression evidence.

New canonical conversion uses:

- Market-By-Price representation;
- `PartialFillExchange`;
- `RiskAverseQueueModel` baseline;
- explicit tick/lot sizes;
- linear contract representation matching current research model;
- explicit maker/taker fees;
- explicit feed/entry/response latency scenarios;
- visibility epochs from Section 9.2.

Alternative probabilistic queue models are sensitivity analyses only until forward evidence justifies calibration. A more profitable queue model cannot replace the conservative baseline merely because it improves PnL.

## 15. Market-impact / order-size eligibility

hftbacktest cannot alter replayed market depth in response to our orders. Therefore promoting replay must constrain our simulated order size.

Record at minimum:

- order quantity / same-level visible quantity when defined;
- order notional / visible top-N notional;
- maximum and high-quantile participation ratios;
- time spent near the visibility boundary.

A predeclared hard eligibility threshold blocks evidence where our order is too large relative to visible liquidity. The threshold is a research meta-parameter and is not tuned on sealed OOS PnL.

## 16. Latency scenarios

Latency is frozen experiment metadata.

Archive scenarios may include:

- zero-latency mechanics sanity check;
- conservative fixed latency;
- distribution estimated from training-period forward recorder data.

Forward replay preserves observed exchange/receive timestamps for feed latency.

Order entry/response latency is distinct from feed latency. Until measured via a separate testnet/live conformance exercise, entry/response latency remains synthetic or externally measured scenario input.

Every report labels each latency component as measured, estimated, or synthetic.

## 17. Funding accounting

Funding is a separate PnL component.

At each hourly funding boundary:

- use signed position immediately before the applicable boundary;
- use the applicable official funding rate event;
- use the required oracle/reference price for notional under the verified Hyperliquid contract;
- emit the funding cash flow separately.

A funding-complete promoting run rejects missing required funding/reference state instead of substituting zero.

## 18. Fees and PnL attribution

Fee scenario is frozen experiment input from a named fee schedule or conservative scenario.

Required additive attribution:

```text
net_pnl =
    realized_spread_capture
  + directional_inventory_pnl
  + funding_pnl
  - fee_cost
  - adverse_selection_cost
  - emergency_execution_cost
```

Report maker fees/rebates, taker/emergency fees, funding, spread capture, inventory mark-to-market, adverse-selection markout, and emergency cost independently.

Mark-to-market reference and closeout convention are explicit manifest fields.

## 19. Adverse-selection labels

Every passive fill may schedule post-fill markout labels at configured horizons.

A markout becomes visible to Calibration only after its horizon matures. Pending future markouts cannot affect the decision that generated the fill or evict current causal samples prematurely.

## 20. Replay orchestration

The Tier-2 runner consumes an `ACCEPTED` canonical dataset and frozen experiment manifest.

At replay time:

1. advance only events whose replay time has arrived;
2. update Calibration with currently matured evidence;
3. derive risk capacity from configured research account state;
4. obtain calibrated Adaptive Grid candidate;
5. apply Hard Risk;
6. reconcile cancel/replace intents;
7. map eligible orders into hftbacktest with declared latency/queue assumptions;
8. ingest simulated fills;
9. update inventory and pending markout queues;
10. apply funding at the correct boundary;
11. emit Evidence and attribution.

An order at an untrustworthy visibility price is not submitted as promoting replay evidence.

## 21. Evidence

Tier-2 Evidence root digests:

- experiment manifest;
- every raw input object;
- raw acquisition manifest;
- canonical dataset manifest;
- normalization schema;
- audit report;
- Strategy/Calibration config;
- Risk config;
- fee scenario;
- funding source;
- latency scenario;
- queue model;
- visibility policy;
- hftbacktest version;
- event-ordering policy;
- replay result;
- PnL attribution;
- code revision.

Same accepted dataset + same manifest + same code must reproduce the same replay Evidence digest in separate Python processes.

Wall-clock acquisition metadata may be retained in provenance but does not contaminate deterministic replay output unless deliberately declared part of dataset identity.

## 22. Security

This foundation records public market/reference data and requires no trading private key for `l2Book` or `trades`.

Private user streams, signed trading requests, wallet keys, and live account reconciliation are outside this specification.

Secrets or wallet material must never appear in Git, raw manifests, or replay Evidence.

## 23. Fail-closed conditions

Reject or fail closed on:

- unknown decoder schema;
- corrupt/decompression failure;
- SHA mismatch;
- invalid/crossed book not permitted by source semantics;
- unresolved event ordering;
- conflicting duplicates;
- high-impact coverage gap;
- lost book visibility at an active strategy order price;
- missing required tick/lot metadata;
- missing required funding/reference data;
- unsupported hftbacktest runtime version;
- tick/lot misaligned strategy orders;
- market-impact eligibility violation for promoting evidence.

Failures produce audit/Evidence records where possible and never mutate source data.

## 24. Testing strategy

Unit tests:

- manifests and raw hashes;
- canonical contracts;
- Hyperliquid decoder validation;
- aggressor-side mapping;
- snapshot top-N visibility transitions;
- snapshot-to-depth conversion;
- deterministic ordering/deduplication;
- gap classification;
- funding boundary accounting;
- fee/PnL identities;
- order-size eligibility;
- latency scenario validation.

Property/metamorphic tests:

- instrument rename changes identity only;
- common price/size scale leaves relative behavior invariant when venue metadata scales consistently;
- file enumeration order cannot change canonical digest;
- identical duplicates cannot change output;
- conflicting duplicates always reject;
- future book/trade/funding/markout data cannot change earlier decisions;
- segment rotation cannot change canonical stream;
- a level leaving top-N range cannot be reclassified as a confirmed cancel;
- replay cannot submit promoting orders into unknown visibility epochs.

Integration tests:

- official-format historical L2 fixture -> raw -> canonical -> audit;
- node trade fixture -> canonical trades;
- forward `WsBook`/`WsTrade` fixtures -> same canonical semantics;
- `metaAndAssetCtxs` fixture -> reference/funding state;
- canonical snapshot sequence -> trustworthy depth events;
- canonical events -> hftbacktest;
- passive BUY/SELL partial fills;
- cancel-before-replace under non-zero latency;
- funding with long and short positions;
- two fresh-process replay digest equality.

Regression gates:

- all existing Phase A/B/C Core and Research tests remain green;
- all six existing deterministic Evidence digests remain unchanged.

## 25. Data-quality report

Every dataset reports before economic metrics:

- requested vs observed coverage;
- raw bytes/object count;
- canonical event count;
- book/trade/reference counts;
- duplicates/conflicts;
- max/quantile book gaps;
- reconnect intervals;
- visibility-loss intervals;
- invalid/crossed book count;
- trade/book overlap;
- funding/reference readiness percentage;
- calibration readiness percentage;
- replay-order visibility eligibility percentage.

## 26. Promotion gates to the economic-evaluation phase

This foundation is ready for the next spec only when:

1. historical and forward fixtures normalize to the same canonical semantics;
2. raw hashes/manifests are reproducible;
3. gap/duplicate/visibility audit is deterministic and fail closed;
4. future-data perturbation cannot alter earlier decisions;
5. hftbacktest conversion supports both sides, partial fill, cancel/replace, and non-zero latency;
6. top-N visibility loss cannot create fake cancellation/fill evidence;
7. funding and fees are separately attributable;
8. market-impact/order-size eligibility is enforced;
9. fresh processes reproduce the same Tier-2 Evidence digest;
10. existing six Evidence digests remain unchanged;
11. exact-head Core and Research CI are green.

Passing these gates means the system is suitable to **test** economic hypotheses. It does not mean the Grid strategy is profitable.

## 27. Follow-on economic specification

After this foundation is approved and implemented, the next design will define:

- instrument universe by liquidity/data quality rather than symbol preference;
- symbol-disjoint train/validation/sealed-test partitioning;
- rolling time walk-forward;
- S0-S7 ablation promotion criteria;
- conservative queue/latency/fee/funding scenarios;
- bull/bear/range/crash/high-vol/low-vol reporting;
- B&H and simpler Grid baselines;
- return, drawdown, CVaR, inventory RMS, maker fill rate, adverse markout, funding, and emergency-cost gates;
- final sealed OOS decision with no parameter rescue after opening the seal.

## 28. Explicit non-goals

This specification does not:

- submit Hyperliquid orders;
- claim historical profitability;
- infer fills from OHLC;
- treat top-20 snapshots as full Market-By-Order history;
- invent cancellations outside observable book range;
- claim hftbacktest models our market impact;
- treat missing funding/L2 as zero;
- introduce per-symbol strategy branches;
- tune on sealed OOS;
- merge Phase C or this branch into `main` without explicit review/merge instruction.

## 29. Decision summary

Use official historical breadth plus forward recorder continuity, normalize both into one immutable canonical dataset, reject unresolved execution-data uncertainty, and replay through a conservative hftbacktest baseline.

The critical safety rule is that **unknown microstructure remains unknown**: missing book range, gaps, funding, latency provenance, or market-impact eligibility cannot be converted into favorable fill evidence.

The output of this phase is a trustworthy replay substrate for later economic rejection/promotion, not a profitability result.
