# Tier-2 Historical + Forward Replay Foundation Design

Date: 2026-08-10
Status: Design for review
Repository: `shuntatsu/Grid-trade`
Parent commit: `8b8c5e22445c3d028f572803fbd1789a90629048`
Branch: `tier2-historical-forward-replay`
License: LGPL-3.0
Production status: RESEARCH / NO-GO

## 1. Purpose

Build the data and replay foundation required to evaluate the calibrated adaptive Grid strategy against realistic Hyperliquid microstructure instead of deterministic mechanics fixtures.

The maintained approach is a hybrid:

1. seed historical research from Hyperliquid official historical archives and node datasets;
2. continuously record forward Hyperliquid WebSocket market data and periodic reference state into an immutable raw store;
3. normalize both sources into one canonical, causally ordered event contract;
4. audit gaps, duplicates, ordering, source identity, and provenance before replay;
5. convert accepted datasets into the existing pinned `hftbacktest==2.4.4` research boundary;
6. replay passive orders with explicit queue, latency, partial-fill, fee, and funding assumptions;
7. emit deterministic Evidence proving exactly which raw bytes, normalization version, assumptions, and strategy inputs produced each replay result.

This design does **not** claim profitability and does not authorize live trading.

## 2. Why the hybrid approach is required

Hyperliquid's official historical archive is useful but is explicitly not guaranteed to be complete or timely. Historical L2 book snapshots are available under `hyperliquid-archive/market_data`, asset contexts under `hyperliquid-archive/asset_ctxs`, and trade/fill data is available from Hyperliquid node datasets. The official documentation also states that additional historical datasets may need to be recorded through the API.

The forward recorder therefore has two purposes:

- close data-coverage gaps for future experiments;
- provide empirical feed-timing and continuity evidence that archive-only replay cannot supply.

The archive and recorder must converge into the same canonical contract so Strategy, Calibration, Risk, and replay code do not contain separate historical/live-data logic.

## 3. Scope and decomposition

This specification is intentionally the **Tier-2 data and replay foundation**, not the full economic research campaign.

### In scope

- official Hyperliquid historical L2 ingestion;
- official node trade/fill ingestion where available;
- asset-context / funding-state ingestion needed for replay;
- forward WebSocket recording for `l2Book` and `trades`;
- periodic/reference capture required to recover funding and venue metadata;
- immutable raw object storage layout;
- SHA-256 provenance manifests;
- canonical L2/trade/funding/reference event contracts;
- deterministic normalization;
- duplicate/order/gap/source audits;
- dataset acceptance/rejection policy;
- conversion to hftbacktest market events;
- queue-model and latency-assumption contracts;
- partial-fill replay;
- fee and funding accounting inputs;
- replay PnL attribution contracts;
- deterministic replay Evidence;
- small checked-in synthetic and recorded-format fixtures;
- architecture, causality, metamorphic, and regression tests.

### Deferred to the next specification

- broad parameter search;
- stage promotion S0 -> S7 based on economic metrics;
- symbol-disjoint train/validation/test orchestration;
- sealed final OOS campaign;
- production deployment;
- exchange-order submission;
- live capital allocation.

The next economic-evaluation specification may only start after this foundation can reproduce accepted datasets and replay them deterministically.

## 4. External contracts verified for this design

As of 2026-08-10:

- Hyperliquid historical asset data is uploaded to `hyperliquid-archive` approximately monthly; timely updates are not guaranteed and data may be missing.
- Historical L2 book snapshots are available in `market_data` and asset contexts in `asset_ctxs`.
- Node datasets expose historical fills/trades; current node schemas also document hourly trade data and raw book diffs.
- Hyperliquid WebSocket supports `l2Book` and `trades` subscriptions.
- `WsBook` carries `coin`, `levels`, and exchange `time`; each level contains price, size, and order count.
- the `l2Book` REST/info snapshot returns at most 20 levels per side.
- Hyperliquid funding is paid hourly.
- REST requests are rate limited; the recorder must not poll high-weight endpoints as a substitute for WebSocket data.
- hftbacktest is replay-based and cannot model the market impact caused by our own orders.
- `PartialFillExchange` can model partial execution under its replay assumptions.
- hftbacktest queue position must be modeled for Market-By-Price data; `RiskAverseQueueModel` is the conservative maintained baseline.

These are data/replay constraints, not alpha assumptions.

## 5. Architecture

```text
Historical source                          Forward source
+----------------------+                   +-----------------------+
| HL archive L2       |                   | WS l2Book            |
| HL node trades/fills|                   | WS trades            |
| asset_ctxs/funding  |                   | periodic reference   |
+----------+-----------+                   +-----------+-----------+
           |                                           |
           v                                           v
+-----------------------------------------------------------------+
|                     Immutable Raw Store                         |
| raw bytes + source URI/type + receive time + SHA-256           |
+-------------------------------+---------------------------------+
                                |
                                v
+-----------------------------------------------------------------+
|                    Deterministic Normalizer                     |
| decode -> validate -> canonical event -> sequence/provenance     |
+-------------------------------+---------------------------------+
                                |
                                v
+-----------------------------------------------------------------+
|                         Dataset Audit                           |
| identity | monotonicity | duplicates | gaps | cross-feed checks |
+-------------------------------+---------------------------------+
                                |
                     ACCEPTED only| 
                                v
+-----------------------------------------------------------------+
|                    Canonical Replay Dataset                     |
| book snapshots/events | trades | funding | venue metadata       |
+-------------------------------+---------------------------------+
                                |
                +---------------+----------------+
                |                                |
                v                                v
+-----------------------------+       +---------------------------+
| Universal Calibration       |       | hftbacktest Adapter       |
| volatility/A,k/OFI/markout  |       | queue/latency/partial fill|
+--------------+--------------+       +-------------+-------------+
               |                                    |
               +----------------+-------------------+
                                v
+-----------------------------------------------------------------+
| Calibrated Adaptive Grid -> Risk -> Reconciliation -> Replay     |
+-------------------------------+---------------------------------+
                                |
                                v
+-----------------------------------------------------------------+
| Evidence + PnL Attribution + Replay Quality Report               |
+-----------------------------------------------------------------+
```

The raw-data boundary is outside `strategy`, `risk`, `application`, and `calibration`. Exchange decoding may not leak into those core layers.

## 6. Package boundaries

Add research/data-specific packages rather than extending core Strategy types with Hyperliquid payload details.

Recommended boundaries:

```text
src/grid_trade/
  datasets/
    contracts.py
    manifest.py
    audit.py
    canonical.py
  integrations/
    hyperliquid/
      archive.py
      node_data.py
      forward_recorder.py
      normalization.py
  research/
    hftbacktest_adapter.py        # existing, extended through canonical input
    tier2_replay.py
    replay_attribution.py
```

### `datasets/`

Runtime-neutral immutable dataset and provenance contracts. It may use only standard-library/core domain-safe dependencies and must not import Hyperliquid SDK, hftbacktest, NautilusTrader, Strategy, Risk, or Application.

### `integrations/hyperliquid/`

Hyperliquid-specific payload decoding, transport, archive paths, WebSocket formats, and API/reference capture. It produces `datasets` canonical records and never calls Strategy.

### `research/`

May depend on canonical datasets, existing Application/Strategy/Risk contracts, and optional hftbacktest runtime. It owns replay orchestration and research-only PnL attribution.

## 7. Immutable raw store

The raw store is append-only from the perspective of research runs.

A raw object identity includes:

- source family: archive / node / websocket / info;
- dataset type: l2Book / trade / fill / asset_ctx / funding / metadata;
- instrument identity;
- source timestamp range if known;
- recorder receive-time range if applicable;
- byte length;
- SHA-256 of exact bytes;
- acquisition timestamp;
- source locator metadata;
- collector/decoder schema version.

Raw bytes are never rewritten to "fix" bad data. Corrections produce a new object and a new manifest.

Large external datasets are not committed to Git. Git contains schemas, tiny fixtures, manifests/examples, and deterministic code only.

## 8. Forward recorder design

### 8.1 Required subscriptions

For each explicitly configured single instrument:

- `l2Book`;
- `trades`.

The recorder does not subscribe to all instruments by default. Experiment configuration selects the recording universe.

### 8.2 Reference capture

The recorder additionally captures enough reference state to support later deterministic replay:

- instrument/venue metadata required for tick/lot and contract interpretation;
- funding/reference state at an explicitly configured cadence appropriate to the source;
- recorder clock/receive timestamp for every inbound payload.

Funding is not reconstructed from future information. Hourly realized funding applied to positions must come from observations that were available by the relevant funding boundary.

### 8.3 Recorder durability

Each received payload is written before downstream normalization acknowledges it.

The recorder uses segment files with deterministic rotation by time and/or byte count. A segment is finalized by:

1. flush;
2. close;
3. SHA-256;
4. manifest entry;
5. atomic publication of the finalized manifest record.

An interrupted open segment is marked incomplete and must not silently become an accepted replay segment.

### 8.4 Heartbeats and reconnects

Hyperliquid may close inactive WebSocket connections. Recorder logic must support heartbeat/ping handling and reconnect.

Reconnect creates an explicit continuity boundary. The recorder must capture:

- disconnect time;
- reconnect start/end;
- first post-reconnect exchange timestamp;
- whether a fresh reference/L2 snapshot was acquired;
- inferred uncovered interval.

A reconnect is never hidden by concatenating files as if no gap occurred.

## 9. Canonical event model

All accepted sources normalize into immutable canonical records.

### 9.1 Common envelope

Every event contains:

- `event_type`;
- `instrument_id`;
- `source_id`;
- `exchange_timestamp_ns`;
- `local_timestamp_ns` when observed;
- source sequence/block/trade identifier when available;
- raw object SHA-256;
- raw record ordinal/offset;
- normalization schema version.

### 9.2 Canonical book event

Contains at minimum:

- bids and asks as ordered `(price, quantity, order_count)` tuples;
- full-vs-aggregated precision metadata;
- snapshot/diff classification;
- source timestamp.

For the historical `l2Book` archive and WebSocket `WsBook`, the maintained first implementation treats each message as a snapshot-like MBP observation unless the source explicitly guarantees diff semantics.

Raw node book diffs are a separate source class and must not be mixed with snapshot semantics without an independently verified reconstruction path.

### 9.3 Canonical trade event

Contains:

- aggressor side using one repository-wide convention;
- price;
- quantity;
- globally stable source identifier when derivable;
- exchange timestamp;
- source metadata required to deduplicate replayed trades.

The trade-side mapping is explicitly tested against Hyperliquid source semantics before acceptance.

### 9.4 Canonical funding/reference event

Contains:

- applicable interval/boundary timestamp;
- funding rate observed for that interval;
- oracle/reference price when required for funding notional;
- source and observation timestamp;
- quality/readiness state.

Missing funding is `unavailable`, never zero-filled.

## 10. Time model and causality

Three time concepts are kept separate:

1. exchange/event time;
2. recorder local receive time;
3. strategy decision/replay time.

Historical archive data may not possess realistic local receive time. Such datasets must not fabricate measured feed latency.

For archive replay:

- exchange time is authoritative for event ordering;
- local time may be synthesized only through an explicit latency model;
- that synthetic latency is Evidence and never described as measured.

For forward recorder replay:

- actual recorder receive time is preserved;
- observed feed-delay distributions may be estimated from exchange vs local time;
- future observations may not alter latency assigned to earlier events except through a predeclared offline calibration split.

## 11. Deduplication and ordering

Normalization may collapse only **provably duplicate source records**.

Duplicate keys are source-specific and may include:

- trade/block identifiers;
- exact event identity plus source ordinal where no stable ID exists;
- raw object hash + record offset.

Numerically equal book snapshots at different timestamps are not duplicates.

Canonical output ordering uses a deterministic tie-break hierarchy:

1. exchange timestamp;
2. source-defined sequence/block identifier when available;
3. event-type precedence declared in the dataset schema;
4. raw object hash;
5. raw record ordinal.

The precedence is part of the schema version because changing it can change fills.

## 12. Dataset audit and acceptance

Every replay dataset passes a separate audit before Strategy is allowed to consume it.

### 12.1 Required checks

- instrument/source identity consistency;
- valid positive prices and non-negative/positive quantities as required by event type;
- book bid/ask ordering;
- no crossed book unless the source contract permits/transiently explains it;
- monotonic deterministic event ordering;
- duplicate classification;
- timestamp gap statistics;
- reconnect/gap boundaries for forward data;
- archive segment coverage against requested intervals;
- trade/book overlap coverage;
- funding coverage;
- tick/lot metadata availability;
- raw-object hashes resolvable from the manifest;
- normalization version fixed for the run.

### 12.2 Acceptance states

Use explicit states:

- `ACCEPTED`;
- `ACCEPTED_WITH_WARNINGS`;
- `REJECTED`.

Economic promotion runs require `ACCEPTED` unless an experiment explicitly declares a non-promoting sensitivity analysis.

Warnings may be used for non-critical sparse auxiliary state. Unknown book/trade gaps that can change fill interpretation are rejection conditions for promoting runs.

### 12.3 No silent interpolation

The audit layer does not invent L2 levels, trades, queue events, or funding observations.

Interpolation may be used only for explicitly continuous non-execution research features under a separate declared transformation and never to manufacture fill evidence.

## 13. Historical ingestion

### 13.1 Archive L2

Historical loader accepts explicit date/hour/instrument requests and imports official `market_data/.../l2Book/...` objects.

Each downloaded object is hashed before decoding. Decompression/decoding metadata is recorded separately so the compressed source bytes remain identifiable.

### 13.2 Node trades/fills

Use official node trade/fill datasets as the historical trade source when available for the requested interval.

Different historical node formats are separate decoder versions. Format detection must be explicit and fail closed on unknown schema.

### 13.3 Asset contexts / funding

Asset contexts and/or official funding-history/reference sources are ingested only through explicit decoder contracts. The replay dataset records exactly which source provided funding and oracle/reference state.

The implementation must not infer historical funding from current metadata.

## 14. hftbacktest conversion

The existing `grid_trade.research.hftbacktest_adapter` remains the optional-runtime boundary and remains pinned to `hftbacktest==2.4.4` until an independently reviewed dependency upgrade.

The existing synthetic `MicrostructureFixture` remains as a regression fixture.

A new canonical-to-hft conversion path maps accepted canonical events to hftbacktest events.

### 14.1 Maintained baseline model

- Market-By-Price replay;
- `PartialFillExchange`;
- `RiskAverseQueueModel`;
- explicit tick size;
- explicit lot size;
- linear contract model matching the normalized research representation;
- maker/taker fee rates from an experiment fee contract;
- explicit entry and response latency model.

### 14.2 Queue model sensitivity

`RiskAverseQueueModel` is the promotion baseline because it is conservative with respect to queue advancement.

Alternative probabilistic queue models may be used only as separately reported sensitivity analyses until forward observed fills justify calibration.

No favorable queue model may replace the conservative baseline merely because it improves PnL.

## 15. Order-size / market-impact eligibility

hftbacktest cannot change the replayed market in response to our orders, so the research system must gate replay order size.

Every promoting run records order-size diagnostics relative to contemporaneous visible depth, including at minimum:

- order quantity / same-level displayed quantity when defined;
- order notional / top-N visible notional;
- maximum and high-quantile participation ratios.

A hard research eligibility threshold prevents orders that are too large relative to visible liquidity from being treated as trustworthy passive-fill evidence.

The exact threshold is a predeclared research meta-parameter and must be stress-tested; it is not tuned on sealed OOS PnL.

## 16. Latency model

Latency is a first-class experiment contract.

### Historical archive

Archive-only data has no claim to measured local receive latency. Use named synthetic scenarios such as:

- zero-latency mechanics sanity check;
- conservative fixed latency;
- distribution sampled from a training-period forward-recorder empirical model.

### Forward recorder

Preserve exchange and receive timestamps. Estimate feed-latency distributions only from the allowed calibration split.

Order entry/response latency is not assumed equal to market-data latency. Until measured from testnet/live conformance, it remains an explicit scenario parameter.

Every replay report identifies whether each latency component is measured, estimated, or synthetic.

## 17. Funding accounting

Funding is separate from spread/directional PnL.

For each hourly funding boundary:

- determine signed position immediately before the applicable boundary according to the replay clock;
- use the funding rate for that interval;
- use the source-required oracle/reference price for notional when available under the verified Hyperliquid contract;
- record funding cash flow as its own attribution component.

Missing required funding/reference state fails the funding-complete promoting run rather than substituting zero.

## 18. Fee accounting

Fee configuration is an experiment input derived from a named fee schedule or explicit conservative scenario.

The research report separates:

- maker fees/rebates;
- taker/emergency fees;
- funding;
- gross spread capture;
- directional inventory mark-to-market;
- adverse-selection markout;
- emergency execution cost.

The first Tier-2 foundation need not predict future user fee tiers. It must support deterministic fee scenarios and preserve the scenario identity in Evidence.

## 19. PnL attribution contract

The replay foundation exposes additive attribution rather than a single opaque return number:

```text
net_pnl =
    realized_spread_capture
  + directional_inventory_pnl
  + funding_pnl
  - fee_cost
  - adverse_selection_cost
  - emergency_execution_cost
```

Accounting identities are tested exactly within the repository's Decimal precision contract where applicable.

Mark-to-market reference and closeout convention are explicit experiment metadata.

## 20. Adverse-selection evidence

For each passive fill, emit causal post-fill markout labels at configured horizons.

A markout becomes available only after its horizon matures. It may be used by later calibration decisions but never by the decision that generated the fill.

This extends the existing matured-markout causality rule to real replay data.

## 21. Replay orchestration

The Tier-2 runner operates on an `ACCEPTED` canonical dataset and a frozen experiment manifest.

Per event window:

1. advance canonical market events;
2. update causal calibration only with matured information;
3. derive risk capacity from the configured research account state;
4. obtain calibrated Adaptive Grid candidate;
5. apply Hard Risk;
6. reconcile cancel/replace orders;
7. submit/cancel through hftbacktest adapter with declared latency/queue assumptions;
8. ingest simulated fills;
9. update inventory and future markout queues;
10. apply funding at its event boundary;
11. emit canonical Evidence.

No future event is supplied to Strategy or Calibration before the replay clock reaches it.

## 22. Evidence and reproducibility

A Tier-2 Evidence root includes digests for:

- experiment manifest;
- every raw input object;
- canonical dataset manifest;
- normalization schema/version;
- dataset audit report;
- strategy/calibration config;
- risk config;
- fee scenario;
- funding source;
- latency scenario;
- queue model;
- hftbacktest version;
- event-ordering policy;
- replay result;
- PnL attribution;
- code revision.

Running the same accepted dataset and same frozen experiment manifest in separate Python processes must produce the same Evidence digest, subject to explicitly documented deterministic hftbacktest conversions.

Wall-clock acquisition timestamps may exist in provenance but must not contaminate the deterministic replay digest unless deliberately part of the dataset identity.

## 23. Storage and privacy/security

The recorder stores public market data only for the Tier-2 foundation.

It does not require private trading keys to record `l2Book` or `trades`.

Any future private user stream or testnet account data requires a separate security review and is outside this specification.

Secrets, credentials, signed requests, and wallet private keys must never be committed to Git or embedded in dataset manifests.

## 24. Failure handling

Fail closed on:

- unknown source schema;
- corrupt compressed/raw object;
- SHA mismatch;
- impossible/crossed book not permitted by source contract;
- non-monotonic source ordering that cannot be deterministically resolved;
- unresolved high-impact data gaps;
- missing required tick/lot metadata;
- required funding missing for a funding-complete run;
- duplicate event identity with conflicting payload;
- unsupported hftbacktest runtime version;
- order quantity/tick misalignment;
- replay order-size eligibility violation for promoting evidence.

A failure produces an audit/Evidence record where possible; it does not mutate or delete the source data.

## 25. Testing strategy

### Unit tests

- raw object/manifest validation;
- canonical event validation;
- source decoder schema validation;
- trade-side mapping;
- deterministic event ordering;
- duplicate detection;
- gap classification;
- funding boundary accounting;
- fee attribution;
- PnL identity;
- order-size eligibility;
- latency-scenario validation.

### Property/metamorphic tests

- instrument rename changes identity only, not normalized numeric behavior;
- common price/size scaling preserves relative calibration and replay geometry when tick/lot metadata scales consistently;
- input file enumeration order cannot change canonical dataset digest;
- duplicate identical source records cannot change output;
- conflicting duplicates always reject;
- future trade/book/funding/markout records cannot change an earlier decision digest;
- recorder segment rotation cannot change the normalized canonical stream.

### Integration tests

- tiny official-format historical L2 fixture -> raw manifest -> canonical dataset -> audit;
- tiny node trade fixture -> canonical trades;
- forward WebSocket message fixture -> same canonical form as historical equivalent;
- canonical dataset -> hftbacktest conversion;
- passive BUY and SELL partial-fill paths;
- cancel-before-replace under non-zero latency;
- funding boundary with held long/short inventory;
- deterministic replay in two fresh processes.

### Regression requirements

All existing Phase A/B/C Core and Research tests remain green.

Existing six deterministic Evidence digests remain unchanged because the previous synthetic runners are regression baselines and are not rewritten by this foundation.

## 26. Data-quality metrics

Every accepted dataset reports at minimum:

- requested vs observed time coverage;
- book message count;
- trade count;
- funding/reference count;
- duplicate count;
- conflicting duplicate count;
- maximum and quantile book-message gaps;
- maximum and quantile trade gaps where meaningful;
- reconnect intervals for forward data;
- crossed/invalid book count;
- source overlap intervals;
- percentage of replay time with funding/reference readiness;
- percentage of replay time with calibration readiness;
- raw bytes and object counts;
- canonical event count.

These metrics are Evidence and are visible before economic metrics.

## 27. Promotion gates for the next phase

The Tier-2 foundation is considered ready for the economic-evaluation specification only when:

1. official-format archive fixtures and forward fixtures normalize into the same canonical semantics;
2. raw object hashes and manifests are reproducible;
3. gap/duplicate/cross-source audit is deterministic and fail closed;
4. canonical replay preserves causality under future-data perturbation tests;
5. hftbacktest conversion supports both sides, partial fills, cancel/replace, and non-zero latency;
6. funding and fees are separately attributable;
7. replay order-size eligibility is enforced;
8. the same dataset/manifest yields the same replay Evidence digest in fresh processes;
9. Phase A/B/C regression digests remain unchanged;
10. Core and Research CI are green on the exact final branch head.

Meeting these gates means the infrastructure is suitable to **test** economic hypotheses. It does not mean the Grid strategy is profitable.

## 28. Next specification after approval and implementation

The follow-on design will define the actual economic campaign:

- explicit instrument universe selection by liquidity/data quality, not symbol preference;
- train/validation/sealed symbol-disjoint partitions;
- rolling time walk-forward within each partition;
- S0 -> S7 ablation promotion criteria;
- conservative queue/latency/fee/funding scenarios;
- bull/bear/range/crash/high-vol/low-vol reporting;
- B&H and simpler Grid baselines;
- return, drawdown, CVaR, inventory RMS, maker fill rate, adverse markout, funding, and emergency-cost gates;
- final sealed OOS decision with no parameter rescue after opening the seal.

## 29. Explicit non-goals

This specification does not:

- submit Hyperliquid orders;
- claim historical profitability;
- choose a final production leverage;
- tune parameters using sealed OOS data;
- infer fills from OHLC;
- use archive snapshots as if they were full Market-By-Order history;
- claim hftbacktest models our own market impact;
- treat missing funding or L2 data as zero;
- introduce per-symbol strategy branches;
- merge Phase C or this branch into `main` without explicit review/merge instruction.

## 30. Design decision summary

The maintained decision is **Historical + Forward Recorder hybrid** with an immutable evidence-first dataset pipeline.

Historical official data provides breadth; forward recording provides continuity and timing evidence. Both must normalize to one canonical event model before research. `RiskAverseQueueModel + PartialFillExchange` is the conservative hftbacktest baseline, while queue/latency alternatives remain sensitivity analyses. Replay order sizes are explicitly constrained because replay cannot model our own market impact. Funding, fees, adverse selection, and emergency execution remain separate PnL components.

The result of this phase is not a profitable strategy. It is a trustworthy enough replay substrate that a later sealed economic experiment can reject or support the strategy without confusing fixture mechanics with market evidence.
