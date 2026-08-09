# Grid-trade

Research-first adaptive grid and market-making system for a single crypto perpetual instrument, initially targeting Hyperliquid.

## Status

**RESEARCH / NO-GO FOR PRODUCTION**

This repository is intentionally separated from `trade_rl`. Its first objective is to determine whether a long-biased adaptive grid has a reproducible out-of-sample edge after realistic execution costs and tail-risk controls. Live capital deployment is out of scope until explicit production controls and authorization are implemented.

## Core hypothesis

The baseline is not a permanently symmetric fixed grid. The candidate strategy is:

**Long-biased adaptive grid + inventory control + conditional short overlay**

The system maintains a continuous target inventory rather than switching directly between all-long and all-short states. Grid center, spacing, side intensity, and order size adapt to market state while a separate risk controller can reduce or flatten exposure.

Primary research sequence:

1. Fixed long grid baseline
2. Dynamic/re-centered grid
3. Volatility-adaptive spacing
4. Inventory-target / inventory-skew control
5. Partial de-risking
6. Conditional short overlay
7. Funding-aware bias
8. Order-flow / microprice-aware quoting

Every stage must demonstrate incremental value in walk-forward out-of-sample evaluation. Complexity that does not improve robust results is removed.

## Milestone 1 — deterministic S0 foundation

The `grid-core` implementation branch contains the first research foundation:

- immutable causal market and passive-order contracts;
- deterministic S0 fixed-long grid geometry;
- cancel-before-replace working-order reconciliation;
- partial-fill and duplicate-order fail-closed handling;
- an independent hard risk controller which can block new risk or require flattening;
- canonical JSONL evidence with SHA-256 run digests;
- a pinned `hftbacktest==2.4.4` microstructure replay adapter using risk-adverse queueing, partial fills, explicit tick/lot sizes, and finite latency-aware replay timeouts;
- a pinned `nautilus_trader==1.230.0` construct-only mapper for GTC post-only limit orders;
- a deterministic S0 runner which performs duplicate replay and refuses to claim production authorization or alpha validation.

The checked-in synthetic S0 fixture is execution-mechanics evidence only. It validates deterministic order generation, queue-sensitive partial fills, risk gating, evidence closure, and runtime integration.

## S1 — pure Dynamic Center mechanics

The `s1-dynamic-center` branch adds one isolated strategy change: a stateful center that re-anchors only after a configurable mid-price deviation threshold is reached, with a configurable maximum movement per decision.

S1 deliberately does **not** add trend prediction, volatility-adaptive spacing, inventory targeting/skew, short exposure, funding bias, order-book imbalance, microprice, adaptive sizing, or RL. Those remain separate later ablations.

Important S1 mechanics:

- the S0 comparison arm keeps the episode-initial center fixed;
- S1 uses only current causal mid plus previous effective center state;
- tick-rounded ladders are compared economically before a center change is committed;
- a numerical center change which produces the same executable prices does not increment generation or reset queue priority;
- effective re-anchors reuse the existing cancel-before-replace reconciler;
- the same center decision is retained across cancel and later submission phases instead of being recomputed;
- hard risk is rechecked before replacement submission, and a failed risk check rolls back the uncommitted center/generation;
- projected full-ladder position risk is owned by the shared Risk layer, not reimplemented by S0 or S1;
- an Application layer coordinates Strategy, Risk, and Execution so the Strategy layer remains pure.

The checked-in S1 comparison runner is explicitly `policy_reconciliation_only`. It measures center drift, generations, cancels, submissions, and queue-reset mechanics with deterministic Evidence. It does not infer fills or claim economic profitability from the controlled center path.

S1 is therefore still **NO-GO for strategy promotion**. Historical Tier-2 continuous L2 replay, realistic dynamic order lifecycles, and sealed walk-forward/OOS evidence are required before any claim that Dynamic Center improves Hyperliquid profitability or risk-adjusted returns.

Neither S0 nor S1 currently establishes:

- historical Hyperliquid profitability;
- a proven adaptive-grid edge;
- value from the conditional short overlay;
- realistic historical Hyperliquid queue position across dynamic re-anchors;
- production readiness or live-capital safety;
- permission to trade real funds.

## Execution and research architecture

Planned OSS reuse:

- **NautilusTrader** — primary trading/runtime framework and Hyperliquid data/execution integration.
- **hftbacktest** — high-frequency L2/L3 research, queue-position and latency-sensitive fill simulation.
- **Hyperliquid official Python SDK** — independent conformance/diagnostic oracle where useful.
- **Hummingbot** — reference implementation source for established market-making ideas such as Avellaneda–Stoikov; not intended as the authoritative runtime.

Core dependency direction is intentionally constrained:

- `domain/` contains immutable contracts and does not depend on higher layers;
- `strategy/` contains pure grid/center policy and does not call Risk or Execution;
- `risk/` owns hard veto logic;
- `execution/` owns runtime-neutral order reconciliation;
- `application/` coordinates Strategy, Risk, and Execution;
- `research/` runs controlled experiments and evidence workflows;
- `integrations/` contains external-runtime mappings.

Architecture tests prevent optional hftbacktest/Nautilus dependencies from leaking into core layers.

## Research principles

- Single instrument first.
- No future leakage.
- Deterministic and reproducible evidence.
- Realistic fees, funding, queueing, partial fills, latency, and adverse selection.
- Explicit PnL attribution: spread, directional exposure, funding, fees, inventory mark-to-market, adverse-selection markout, emergency execution cost.
- Walk-forward validation with a sealed final test.
- Bull, bear, sideways, crash, high-volatility, and low-volatility regime reporting.
- Buy-and-hold and fixed-grid baselines remain visible throughout research.
- Risk controls are outside the strategy policy and may veto or flatten it.

## Production safety

No strategy result alone authorizes live trading. Production requires separate execution reconciliation, secret management, margin controls, kill switches, monitoring, and reviewed authorization.

## Design

Design specifications are maintained under `docs/superpowers/specs/`. Implementation plans are maintained under `docs/superpowers/plans/`.

## License

GNU Lesser General Public License v3.0 (`LGPL-3.0`). See `LICENSE`.
