# Grid-trade

Research-first adaptive grid and market-making system for a single crypto perpetual instrument, initially targeting Hyperliquid.

## Status

**RESEARCH / NO-GO FOR PRODUCTION**

This repository is intentionally separated from `trade_rl`. Its objective is to determine whether a long-biased adaptive grid has a reproducible out-of-sample edge after realistic execution costs and tail-risk controls. Completing a strategy mechanic does not establish profitability. Live capital deployment remains out of scope until explicit production controls, realistic historical evidence, and reviewed authorization exist.

## Core hypothesis

The baseline is not a permanently symmetric fixed grid. The candidate strategy is:

**Long-biased adaptive grid + inventory control + conditional short overlay**

The system maintains a continuous target inventory rather than switching directly between all-long and all-short states. Grid center, spacing, side intensity, and order direction adapt to causal market state while a separate hard-risk layer retains veto authority.

Research sequence:

1. S0 — Fixed long grid baseline
2. S1 — Dynamic/re-centered grid
3. S2 — Volatility-adaptive spacing
4. S3 — Inventory-target / inventory-skew control
5. S4 — Partial de-risking
6. S5 — Conditional short overlay
7. S6 — Funding-aware bias
8. S7 — Order-flow / microprice-aware reference

Every stage remains independently ablatable. Complexity that does not improve robust walk-forward/OOS results is intended to be removed.

## Generality boundary

The reusable core is deliberately narrower than a universal trading framework.

- Only linear perpetual contracts are supported. `InstrumentSpec` makes the contract
  multiplier, tick size, quantity step, minimum quantity, minimum notional, maximum
  quantity, and funding cadence explicit. Unsupported contract types fail closed.
- Runtime ownership remains one strategy and calibration state per explicit instrument.
  Instrument identity is carried by snapshots, strategy state, candidate orders, working
  orders, fills, Risk assessment, reconciliation, and Tier-2 replay.
- InstrumentSpec and SamplingSpec are required for generalized historical evaluation.
  Sampling cadence, volatility/trend elapsed-time windows, and matured markout/OFI horizons
  are validated rather than inferred from observation counts.
- AdaptiveStage is a compatibility and reporting preset. Independent `AdaptiveFeatures`
  select inventory control, partial de-risking, conditional reversal, funding bias, and
  order-book reference without requiring ordinal S3–S7 activation.
- Long bias is the default compatibility profile, not a core invariant. An explicit
  `DirectionalTargetProfileConfig` can use a long, flat, or short signed baseline while the
  same flat-before-reverse and Hard Risk contracts remain authoritative. Explicit profiles
  override the compatibility baseline/opposite-target fractions.
- Portfolio allocation and cross-instrument netting remain out of scope, as do inverse
  perpetuals, spot, dated futures, options, and multi-currency collateral.

## S0 — deterministic fixed-grid foundation

The initial foundation provides:

- immutable causal market and passive-order contracts;
- deterministic fixed-long grid geometry;
- cancel-before-replace working-order reconciliation;
- partial-fill and duplicate-order fail-closed handling;
- an independent hard Risk controller which can block new risk or require flattening;
- defense-in-depth validation of reduce-only direction and cumulative flattening capacity, so malformed reduce-only ladders fail closed even if Strategy or a venue adapter is wrong;
- canonical JSON Evidence with SHA-256 run digests;
- pinned `hftbacktest==2.4.4` microstructure replay using risk-adverse queueing, partial fills, explicit tick/lot sizes, and finite latency-aware replay timeouts;
- pinned `nautilus_trader==1.230.0` construct-only mapping for GTC post-only limit orders;
- deterministic research runners which refuse to claim production authorization or alpha validation.

The checked-in synthetic fixtures are execution-mechanics evidence only. They do not represent historical Hyperliquid profitability.

## S1 — Dynamic Center mechanics

S1 adds a stateful center which re-anchors only after a configurable mid-price deviation threshold is reached and is bounded by a configurable maximum movement per decision.

Important mechanics:

- S1 uses only current causal mid plus previous effective center state;
- tick-rounded ladders are compared economically before a center change is committed;
- numerical movement which produces the same executable prices does not increment generation or reset queue priority;
- re-anchors reuse cancel-before-replace reconciliation;
- the same policy decision survives cancel and later submission instead of being recomputed;
- hard Risk is rechecked before replacement submission;
- rejected candidates never become authoritative state.

The S1 comparison runner is `policy_reconciliation_only`: it measures center drift, generations, cancels, submissions, and queue-reset mechanics without inferring fills or economic PnL.

## S2 — Volatility-Adaptive Spacing mechanics

S2 changes spacing on top of S1 Dynamic Center. Current causal realized volatility is mapped to integer basis-point spacing, bounded by explicit minimum/maximum limits and a conservative execution-cost floor.

```text
volatility_spacing_bps = realized_volatility × 10,000 × volatility_multiplier
spacing_bps = ceil(min(max_spacing_bps,
                       max(min_spacing_bps,
                           execution_cost_floor_bps,
                           volatility_spacing_bps)))
```

Important mechanics:

- `Decimal` arithmetic is used and spacing rounds upward only at final integer-bps conversion;
- low volatility cannot narrow below the configured execution-cost floor;
- high volatility widens spacing up to the configured maximum;
- center and spacing are evaluated as one executable economic ladder, so simultaneous changes advance generation at most once;
- tick-equivalent changes do not reset queue priority;
- a shared Application primitive owns Risk evaluation, replacement-aware open-order accounting, cancel-before-replace, post-cancel Risk recheck, and state-commit timing;
- canonical Evidence includes `SPACING_DECISION`.

## S3–S7 — adaptive inventory, defense, short, funding, and order-book mechanics

The controlled research fixtures use `AdaptiveStage` as S3–S7 presets. Runtime policy uses
independent `AdaptiveFeatures`, so each mechanism can be ablated without inheriting every
earlier stage.

### S3 — Inventory Target and Skew

S3 introduces a bounded target inventory and current-inventory deviation. The deviation can shift the reservation/reference price and suppress the side of the ladder that would worsen inventory imbalance.

The adaptive ladder supports all required passive orientations:

- long orientation: new-risk BUYs plus reduce-only SELLs;
- short orientation: new-risk SELLs plus reduce-only BUYs;
- target flat while long: reduce-only SELLs only;
- target flat while short: reduce-only BUYs only.

Strategy-level inventory capacity is enforced before Hard Risk evaluates the candidate ladder.

### S4 — Partial De-risking

S4 can progressively shrink the long target as the causal trend/risk signal deteriorates. The de-risk component itself is not permitted to create a short target. This keeps defensive exposure reduction separate from short alpha assumptions.

A Hard Risk decision with `allow_new_risk=False` may still accept and commit an **exact, entirely reduce-only candidate** when Risk did not request cancel-all or target-flat. A mixed candidate which Risk truncates is not allowed to smuggle an unapproved state commit through its reduce-only subset.

### S5 — Conditional Short Overlay

S5 adds negative target inventory only after the flat-before-reverse contract is satisfied.

```text
Long → Flat → Short
Short → Flat → Long
```

Passing an opposite-sign target while non-flat fails closed. In strong bearish conditions a long position first receives only reduce-only flattening intents; a later decision from flat may create new-risk passive SELL intents.

### S6 — Funding-Aware Bias

S6 applies a bounded funding-derived target shift after the S5 directional target. Funding can strengthen or weaken an existing directional target, but it cannot bypass the absolute inventory cap or flat-before-reverse rule.

### S7 — Order-Book / Microprice Reference

S7 adjusts the quoting reference from causal microprice and order-book imbalance. It does not alter Hard Risk limits or directly create additional inventory capacity. Its effect remains independently removable through the stage gate.

### Shared adaptive mechanics

- center, spacing, inventory, funding, and order-book changes are collapsed into one economic-ladder comparison;
- generation advances only when executable side/price/quantity/reduce-only semantics change;
- tick-collapsed duplicate levels are safely skipped rather than emitting multiple logically distinct levels at the same price;
- cancel-before-replace retains the original candidate decision across the cancellation boundary;
- Risk is re-evaluated before replacement submission;
- optional runtime dependencies remain outside core Strategy/Application layers.

## Deterministic adaptive Evidence

The checked-in S3–S7 comparison runner executes the same controlled, stage-neutral exogenous position path through Strategy → Risk → Execution reconciliation for every stage.

It records:

- center and spacing decisions;
- de-risk and conditional-short decisions;
- funding and inventory decisions;
- order-book reference decisions;
- Risk decisions;
- cancel/submit mechanics;
- reduce-only and new-risk short submissions;
- canonical per-stage and aggregate SHA-256 evidence digests.

The controlled runner deliberately does **not** infer fills from its exogenous position path and sets PnL to zero. It is a deterministic mechanics/ablation proof, not a profitability backtest.

## Universal causal market calibration foundation

The absolute values used by the checked-in S0–S7 controlled runners are deterministic **mechanics fixtures**, not universal market parameters. Strategy research must not infer that a fixed `40 bps` floor, a fixed coin quantity, a fixed funding divisor, or a symbol-specific threshold is appropriate for a live or historical instrument.

The `grid_trade.calibration` boundary provides an instrument-agnostic causal foundation:

- robust rolling log-return volatility in relative-price units;
- dimensionless trend normalization using the instrument's own volatility scale;
- rolling robust funding normalization with explicit unavailable/degenerate states instead of a fixed global funding scale;
- strict timestamp and source/instrument continuity in the calibration engine;
- frozen estimator meta-parameters after the first accepted observation;
- explicit readiness for every calibration component;
- deterministic evidence-sensitive Decimal arithmetic independent of the caller's Decimal context;
- metamorphic tests proving that changing only the symbol identity cannot change numeric calibration output;
- price-scale tests proving that multiplying all prices by a constant does not change normalized volatility/trend output.

Account/risk capacity remains outside Calibration. `grid_trade.risk.sizing` derives `Q_max` from the most conservative of notional, margin, volatility-risk, and venue quantity caps. Calibration cannot increase Hard Risk limits, and Strategy may only consume a fraction of the capacity supplied to it.

## Universal causal microstructure calibration

Phase B adds venue-neutral Tier-2 calibration without changing the existing S2–S7 strategy mechanics:

- GLFT-style arrival-intensity calibration fits `λ(δ) = A exp(-kδ)` with exposure-aware Poisson likelihood; quote distance is expressed in volatility units rather than symbol-specific dollars or ticks, and zero-arrival buckets remain informative;
- best-level OFI follows price/queue event changes and is normalized by observed top-of-book depth;
- microprice and its displacement are represented in relative-price units;
- OFI impact is fitted from matured short-horizon labels only; a future label may be retained as pending state but cannot enter the fit or evict the current causal window before `matured_at`;
- adverse-selection cost uses matured BUY/SELL markouts and a deterministic upper quantile, with an explicit configured conservative fallback while markout evidence is insufficient;
- execution-cost floor combines maker fee/rebate, adverse markout, uncertainty buffer, and tick/mid floor, and can never become negative;
- the microstructure engine freezes config after its first observation, enforces timestamp and identity continuity, and reports explicit readiness/quality instead of fabricating unavailable L2 state;
- order-book impact is normalized into volatility units before conversion to a bounded score;
- public top-of-book and calibration calculations use the deterministic Decimal context.

The Tier-2 inputs are L2/top-of-book observations, distance/exposure/arrival evidence, and matured OFI/markout labels. OHLC data is not used as a substitute for queue, depth, arrival, fill, or markout evidence.

The checked-in microstructure calibration research runner is a **deterministic synthetic calibration/mechanics fixture**. It records frozen config, state generation, `A/k` and fit improvement, quote-distance scale, markout/fallback components, OFI beta/quality, predicted displacement, microprice displacement, and readiness in canonical Evidence. It also checks arbitrary-symbol rename and common price/size scale invariance. This runner does not establish historical performance, economic alpha, or production authorization.

## Universal calibration → adaptive strategy integration

Phase C adds a separate calibrated Application path while retaining the checked-in S0–S7 controlled fixture path unchanged for deterministic regression. The new path:

- composes Foundation and Microstructure calibration with exact timestamp/source/instrument/mid consistency;
- derives executable inventory capacity from `grid_trade.risk.sizing`, including the linear contract multiplier, and only rounds `Q_max` downward to the venue quantity step;
- derives center thresholds, spacing limits, reservation skew, and order-book shift from volatility units rather than symbol-specific basis-point constants;
- combines the volatility floor, GLFT quote-distance estimate, and calibrated execution/adverse-selection cost when deriving spacing;
- consumes normalized funding with unit scale and normalized order-book/OFI state without symbol-specific divisors;
- reconstructs calibrated microprice from relative displacement only when S7 microstructure evidence is ready;
- compares an existing working ladder with the **previous applied runtime config** and the candidate ladder with the newly calibrated config, preventing dynamic parameter changes from being mistaken for queue-equivalent state;
- keeps the previous config through cancel-before-replace and Risk rejection, committing the candidate config only when the candidate economic state is accepted;
- preserves the Long → Flat → Short contract and leaves Hard Risk as the final veto authority;
- emits additive deterministic calibrated-adaptive Evidence and proves arbitrary-symbol rename and common price/size scale invariance.

The calibrated integration runner still does not infer historical fills or PnL and explicitly records `economics_validated=false`, `alpha_validated=false`, and `production_authorized=false`. Strategy economics must still pass realistic continuous Tier-2 replay, symbol-disjoint walk-forward evaluation, sealed OOS tests, and stress gates before any production consideration.

## Execution and research architecture

OSS reuse:

- **NautilusTrader** — primary event-driven runtime and intended Hyperliquid data/execution integration. The maintained construct-only adapter preserves BUY/SELL and reduce-only semantics when producing GTC post-only limit orders; it does not submit orders in tests.
- **hftbacktest** — L2/L3-oriented research oracle for queue-sensitive passive fills and latency assumptions. Both BUY and SELL replay paths are tested. The current replay adapter does **not** model exchange-side `reduce_only` enforcement, so reduce-only safety remains a Strategy/Risk/Nautilus boundary, not an hftbacktest claim.
  `HftReplayConfig.contract_multiplier` is applied to the linear replay asset and to Tier-2 liquidity, funding, and fee attribution. Legacy synthetic fixtures default to multiplier `1`; explicit generalized replay binds and validates an `InstrumentSpec`.
- **Hyperliquid official Python SDK** — optional independent conformance/diagnostic oracle.
- **Hummingbot** — reference source for established market-making ideas; not an authoritative runtime.

Dependency direction is intentionally constrained:

- `serialization/` contains standard-library-only canonical encoding and hashing;
- `domain/` contains immutable contracts and does not depend on higher layers;
- `datasets/` contains runtime-neutral acquisition/audit contracts and canonical market events;
- `calibration/` contains causal instrument-agnostic estimators and cannot depend on Strategy, Risk, Application, Execution, Integrations, or Research;
- `strategy/` contains pure policy and does not call Risk or Execution;
- `risk/` owns hard veto logic and account/risk-derived capacity sizing;
- `execution/` owns runtime-neutral reconciliation;
- `application/` coordinates Strategy, Risk, and Execution and is prevented from depending back on Evidence, Integrations, or Research;
- `evidence/` owns ordered, canonical research records and run digests;
- `research/` owns controlled experiments and evidence workflows;
- `integrations/` owns external-runtime mappings.

Architecture tests prevent optional hftbacktest/Nautilus dependencies from leaking into core layers.

### Responsibility-scoped package map

High-change subsystems are divided by the reason they change while keeping their historical
public import paths stable:

```text
grid_trade.serialization
  canonical.py                  # generic canonical JSON bytes and SHA-256

grid_trade.datasets.audit
  models.py                     # findings and immutable audit reports
  quality.py                    # coverage, overlap, gap, and alignment calculations
  runner.py                     # ordered audit aggregation and promotion guard

grid_trade.integrations.hyperliquid.forward_recorder
  contracts.py                  # recorder/session contracts and transport protocol
  manifest.py                   # completed-segment manifest schema
  segment.py                    # durable frames, fsync, atomic publication
  session.py                    # subscriptions, heartbeat, reference, reconnect state

grid_trade.research.tier2_replay
  dataset.py                    # manifest binding and exact-hour funding validation
  liquidity.py                  # participation and visibility-boundary policy
  attribution.py                # fills, funding, fees, and ending position
  identity.py                   # decision and run identities
  evidence.py                   # deterministic Evidence assembly
  runner.py                     # Hard Risk and replay orchestration
```

Existing callers continue to import from `grid_trade.datasets.audit`,
`grid_trade.integrations.hyperliquid.forward_recorder`, and
`grid_trade.research.tier2_replay`; package `__init__.py` files explicitly own those public APIs.
The generic serializer is standard-library-only. The Evidence ledger remains separate because it
also enforces run identity, contiguous sequence numbers, and monotonic event timestamps.

## What S0–S7 mechanics do not establish

The current implementation does **not** establish:

- historical Hyperliquid profitability;
- a proven adaptive-grid alpha edge;
- that any S3–S7 increment improves risk-adjusted returns;
- realistic historical queue position throughout dynamic cancel/requote lifecycles;
- robustness to real fees, rebates, funding, latency, adverse selection, outages, or liquidation pressure;
- sealed walk-forward/OOS success;
- production readiness or permission to trade real funds.

The next economic gate is continuous Tier-2 microstructure replay with realistic fees, funding, queue and latency assumptions, followed by sealed walk-forward/OOS evaluation and stress testing. A stage which fails its incremental gate should be removed rather than rescued by adding more complexity.

## Research principles

- Single instrument first.
- No future leakage.
- Deterministic and reproducible evidence.
- Realistic fees, funding, queueing, partial fills, latency, and adverse selection before economic promotion.
- Explicit PnL attribution: spread, directional exposure, funding, fees, inventory mark-to-market, adverse-selection markout, emergency execution cost.
- Walk-forward validation with a sealed final test.
- Bull, bear, sideways, crash, high-volatility, and low-volatility regime reporting.
- Buy-and-hold and simpler grid baselines remain visible throughout research.
- Hard Risk is outside the strategy policy and may veto or flatten it.

## Production safety

No strategy result alone authorizes live trading. Production requires separate exchange reconciliation, secret management, margin controls, kill switches, monitoring, fault recovery, and reviewed authorization.

## Design

Design specifications are maintained under `docs/superpowers/specs/`. Implementation plans are maintained under `docs/superpowers/plans/`. Architecture/self-review records are maintained under `docs/superpowers/reviews/`.

## License

GNU Lesser General Public License v3.0 (`LGPL-3.0`). See `LICENSE`.
