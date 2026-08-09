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

## S0 — deterministic fixed-grid foundation

The initial foundation provides:

- immutable causal market and passive-order contracts;
- deterministic fixed-long grid geometry;
- cancel-before-replace working-order reconciliation;
- partial-fill and duplicate-order fail-closed handling;
- an independent hard Risk controller which can block new risk or require flattening;
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

The adaptive policy is staged by `AdaptiveStage`, so S3 through S7 can be enabled independently on the same causal fixture.

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

## Execution and research architecture

OSS reuse:

- **NautilusTrader** — primary event-driven runtime and intended Hyperliquid data/execution integration. The maintained construct-only adapter preserves BUY/SELL and reduce-only semantics when producing GTC post-only limit orders; it does not submit orders in tests.
- **hftbacktest** — L2/L3-oriented research oracle for queue-sensitive passive fills and latency assumptions. Both BUY and SELL replay paths are tested. The current replay adapter does **not** model exchange-side `reduce_only` enforcement, so reduce-only safety remains a Strategy/Risk/Nautilus boundary, not an hftbacktest claim.
- **Hyperliquid official Python SDK** — optional independent conformance/diagnostic oracle.
- **Hummingbot** — reference source for established market-making ideas; not an authoritative runtime.

Dependency direction is intentionally constrained:

- `domain/` contains immutable contracts and does not depend on higher layers;
- `strategy/` contains pure policy and does not call Risk or Execution;
- `risk/` owns hard veto logic;
- `execution/` owns runtime-neutral reconciliation;
- `application/` coordinates Strategy, Risk, and Execution and is prevented from depending back on Evidence, Integrations, or Research;
- `research/` owns controlled experiments and evidence workflows;
- `integrations/` owns external-runtime mappings.

Architecture tests prevent optional hftbacktest/Nautilus dependencies from leaking into core layers.

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
