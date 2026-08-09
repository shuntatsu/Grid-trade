# Long-Biased Adaptive Grid Design

Date: 2026-08-09
Status: Design for review
Repository: `shuntatsu/Grid-trade`
License: LGPL-3.0
Production status: NO-GO

## 1. Purpose

This project evaluates whether a single-instrument, long-biased adaptive grid can produce robust out-of-sample returns after realistic execution costs while avoiding the main failure mode of classic grid trading: accumulating inventory into a persistent adverse trend.

The strategy is intentionally separated from `trade_rl` so grid/market-making research can evolve independently without coupling passive-liquidity assumptions to the existing target-exposure RL system.

The initial venue target is Hyperliquid perpetual futures. The first maintained research instrument should be one highly liquid perpetual, with BTC as the default research candidate unless later evidence justifies another instrument.

This project does not begin with live capital deployment. Research, replay, shadow/testnet execution, and evidence generation come first.

## 2. Core hypothesis

The candidate is not a permanently symmetric fixed grid.

The maintained hypothesis is:

> A long-biased dynamic grid with explicit inventory control and a conditional short overlay can retain range-market spread capture while reducing the left-tail inventory blow-up of a classic long grid.

The strategy should express directional preference as a continuous target inventory rather than a discrete Bull/Neutral/Bear switch.

Let:

- `q_t` = current signed inventory,
- `q*_t` = desired signed inventory,
- `Q_max` = hard absolute inventory limit.

Then:

`q*_t ∈ [-Q_max, Q_max]`

A normal regime is intentionally long-biased. Weak bearish evidence first reduces desired long inventory. A short position becomes admissible only after the target crosses through flat; the execution layer must not cross directly from long to short in one unsafe child order.

## 3. Non-goals

The initial design does not include:

- multi-asset portfolio allocation,
- unrestricted leverage optimization,
- end-to-end RL that emits raw buy/sell actions,
- a custom Hyperliquid networking stack,
- live-capital authorization,
- opaque parameter search against the sealed test period,
- claims of alpha based only on total portfolio return.

RL may be introduced only after a rule-based controller demonstrates stable incremental value. If introduced, RL controls bounded strategy parameters such as target inventory, spacing multiplier, or skew; hard risk constraints remain deterministic.

## 4. Architecture

The design separates market state, policy intent, passive order generation, execution, risk, and evidence.

### 4.1 MarketState

Produces a causal immutable snapshot from information available at decision time.

Initial fields:

- best bid / best ask / mid,
- realized volatility,
- recent directional trend or momentum score,
- current position and normalized inventory,
- funding rate and next known funding boundary where causally available,
- spread,
- optional order-book imbalance,
- optional microprice,
- timestamp and source identity.

Order-book/microprice features are not mandatory in the first strategy stage; the schema reserves them so later ablations do not require an architectural rewrite.

### 4.2 TargetInventoryPolicy

Maps MarketState to `q*_t`.

The first implementation should be deterministic and interpretable. It must support:

- a positive long prior in neutral conditions,
- gradual reduction of long target as bearish evidence increases,
- optional negative target only when bearish evidence exceeds a reviewed threshold,
- inventory-aware damping,
- bounded output in `[-Q_max, Q_max]`,
- hysteresis / rate limiting to reduce rapid long-short flip-flopping.

The target should be continuous. A one-step `+100% → -100%` reversal is explicitly disallowed by policy contract.

### 4.3 GridPolicy

Maps MarketState plus target inventory to a desired passive order ladder.

Each desired order contains at minimum:

- side,
- price,
- quantity,
- reduce-only intent,
- logical level index,
- generation identifier,
- reason / strategy stage identity.

Core parameters:

- center / reservation price,
- spacing,
- number of levels,
- per-level size curve,
- bid-side intensity,
- ask-side intensity.

Conceptual center:

`r_t = mid_t + alpha_t - lambda_q * (q_t - q*_t)`

where `alpha_t` is an optional bounded directional adjustment and `lambda_q` controls inventory skew.

Conceptual spacing:

`delta_t = max(delta_min, k_sigma * sigma_t, execution_cost_floor_t)`

where `execution_cost_floor_t` must prevent grids so narrow that expected fees and adverse-selection burden mechanically dominate the nominal captured spread.

The exact functional forms are research parameters, not hard-coded claims of optimality.

### 4.4 InventoryController

Owns inventory discipline independently from the alpha/grid policy.

Responsibilities:

- enforce `Q_max`,
- reject ladder orders that increase a prohibited side,
- reduce bid intensity when long inventory is above target,
- reduce ask intensity when short inventory is below target,
- generate controlled de-risk intents,
- require flat-before-reverse semantics for sign changes,
- distinguish passive rebalancing from urgent de-risking.

The controller is the primary defense against classic grid accumulation during persistent trends.

### 4.5 RiskController

Hard safety layer with veto authority over strategy intent.

Initial hard controls:

- maximum absolute position,
- maximum notional exposure,
- minimum margin buffer,
- maximum drawdown / loss budget for the research episode,
- maximum stale-order age,
- maximum outstanding-order count,
- data freshness requirement,
- exchange/runtime health requirement,
- emergency flatten command,
- halt state that prevents new risk after a critical fault.

A strategy parameter or future RL policy may never override these controls.

### 4.6 PassiveLiquidityController

Maintains the working passive ladder and reconciles desired versus actual open orders.

Responsibilities:

- post-only / ALO submission,
- deterministic desired-vs-working diff,
- cancel-before-replace when required,
- partial-fill accounting,
- stale-generation cancellation,
- order lifecycle identity,
- quantity rounding and tick rounding,
- idempotent reconciliation,
- emergency handoff to aggressive reduce-only execution when risk requires it.

This component must not contain trend, alpha, or funding strategy logic.

### 4.7 Execution adapters

Execution must be behind a narrow interface so research and live/testnet runtimes are replaceable without changing strategy logic.

Planned authoritative runtime: NautilusTrader.

Planned Hyperliquid route: NautilusTrader Hyperliquid integration rather than a new custom exchange client.

The official Hyperliquid Python SDK may be used as an independent diagnostic/conformance oracle for account state, order state, positions, fills, and metadata where useful. It is not the primary runtime.

### 4.8 Evidence and PnL attribution

Every experiment must emit enough evidence to answer why a strategy made or lost money.

Required attribution buckets:

- spread / grid capture,
- directional mark-to-market PnL,
- funding PnL,
- trading fees / rebates,
- adverse-selection markout,
- emergency / aggressive execution cost,
- realized and unrealized inventory PnL.

Required operational metrics:

- maker fill rate,
- maker/taker ratio,
- cancel/replace count,
- average and tail order lifetime,
- inventory RMS,
- maximum inventory,
- turnover,
- time at risk limits,
- liquidation or margin proximity,
- stale-order incidents.

## 5. OSS reuse policy

The project should reuse mature OSS where it lowers implementation risk without surrendering strategy transparency.

### NautilusTrader — primary runtime

Use for:

- event-driven trading runtime,
- instrument/order abstractions,
- backtest/testnet/live execution infrastructure,
- Hyperliquid data and execution integration,
- order lifecycle handling where supported.

Do not duplicate its networking stack unless a specific measured gap requires a reviewed adapter.

### hftbacktest — microstructure research oracle

Use for:

- full-tick / L2-L3 replay research,
- queue-sensitive passive fill assumptions,
- latency modeling,
- market-making/grid reference experiments,
- sensitivity tests for passive execution realism.

It is not the Hyperliquid live execution authority.

### Hyperliquid official Python SDK — conformance oracle

Use selectively to cross-check:

- instrument/account metadata,
- positions,
- open orders,
- fills,
- funding/account information,
- exchange-specific behavior.

### Hummingbot — reference implementation source

Use as a readable comparison source for established market-making techniques, especially Avellaneda-Stoikov-style reservation-price and inventory logic.

Do not use Hummingbot as a second competing runtime in production architecture.

### License rule

No third-party source is copied into this repository until its license and attribution requirements are recorded. Prefer normal dependency linking or independent reimplementation from published algorithms. Vendored or substantially copied code requires explicit notice and source-level attribution.

## 6. Research stages and ablation gates

The project progresses only when each stage demonstrates incremental value or a clearly documented risk benefit.

### S0 — Fixed Long Grid

Purpose: establish the simplest baseline and expose classic failure modes.

No adaptive center, no volatility adaptation, no short overlay.

### S1 — Dynamic Center

Adds center re-anchoring / reservation-price behavior.

Gate: must improve robustness against grid drift without materially increasing churn.

### S2 — Volatility-Adaptive Spacing

Spacing responds to causal realized volatility and a minimum execution-cost floor.

Gate: must improve high-volatility results or reduce adverse-selection/turnover burden without unacceptable loss of range capture.

### S3 — Inventory Target and Skew

Adds `q*_t`, inventory deviation, and side skew.

This is a core gate.

Gate: should materially reduce inventory RMS / tail inventory and improve risk-adjusted outcomes relative to S2.

### S4 — Partial De-risking

Adds staged exposure reduction when risk/trend evidence worsens.

Gate: must reduce left-tail drawdown or inventory blow-up without destroying normal-range economics.

### S5 — Conditional Short Overlay

Allows negative target inventory after long exposure has been reduced through flat.

Short is introduced first as a defensive/hedging overlay, not assumed to be a separate alpha source.

Gate: must improve drawdown, Calmar, CVaR, or bear-regime behavior without creating excessive whipsaw cost.

### S6 — Funding-Aware Bias

Funding affects target inventory and/or side intensity.

Gate: retain only if the incremental OOS contribution survives cost and regime decomposition.

### S7 — Order-Book / Microprice Signal

Adds OFI, imbalance, microprice, or related short-horizon state.

Gate: must improve passive-fill quality or adverse-selection markout in realistic L2 replay. Remove if incremental value is unstable.

## 7. Backtest tiers

### Tier 1 — fast strategy screening

Purpose: parameter and structural screening only.

May use simplified touch/next-trade fill rules but results cannot authorize strategy promotion.

### Tier 2 — microstructure replay

Requires sufficiently granular trade/order-book data to model:

- passive fill eligibility,
- partial fills,
- queue assumptions,
- cancel/replace timing,
- order latency,
- exchange tick/lot rules.

This is the primary strategy-evaluation tier.

### Tier 3 — execution stress

Re-evaluate finalists under pessimistic perturbations:

- higher latency,
- worse queue position,
- missing/late data,
- wider spread,
- lower fill probability,
- fee changes,
- funding shocks,
- violent volatility,
- cancel delays,
- forced aggressive flattening.

A strategy that only works under optimistic fills is rejected.

## 8. Data contract

OHLC bars alone are insufficient for authoritative grid evaluation because intrabar path and passive queue position materially change fills.

Tier 2 should use, where available:

- trades,
- L2/L3 order-book events or snapshots at adequate frequency,
- funding history,
- mark/oracle/reference prices needed for correct perpetual accounting,
- exchange metadata such as tick and quantity constraints.

Every dataset artifact must bind source identity, time range, symbol, schema version, completeness checks, and digest.

All feature construction must be causal at the decision timestamp.

## 9. Evaluation protocol

Primary comparison set:

- Buy and Hold / equivalent directional baseline,
- S0 fixed grid,
- previous accepted stage,
- new candidate stage.

Required metrics:

- net return / log growth,
- Sharpe,
- Sortino,
- max drawdown,
- Calmar,
- CVaR / tail loss,
- turnover,
- inventory RMS and max inventory,
- maker fill rate,
- maker/taker ratio,
- adverse-selection markout at defined horizons,
- funding PnL,
- fee PnL,
- emergency-execution cost,
- liquidation/margin proximity.

Report results separately for bull, bear, sideways, crash, high-volatility, and low-volatility regimes. Regime labels used for analysis must not leak future information into strategy decisions.

## 10. Walk-forward and sealed test

Parameter tuning occurs only in training windows.

Model/strategy-stage selection uses validation windows.

The final sealed test is not inspected until:

- the strategy stage is frozen,
- all parameters and thresholds are frozen,
- the fill model is frozen,
- fee/funding assumptions are frozen,
- risk settings are frozen.

Repeated tuning against the final test invalidates that test and requires a new sealed period.

## 11. Success and rejection criteria

No single total-return threshold is sufficient.

A promoted candidate must satisfy all hard conditions:

- no causal leakage,
- deterministic replay within defined tolerances,
- no liquidation in accepted scenarios,
- realistic fee and funding accounting,
- Tier-2 OOS profitability or a documented risk-adjusted improvement over the accepted predecessor,
- no dependence on one isolated bull period,
- survivable Tier-3 stress behavior,
- bounded inventory and margin usage.

A feature is removed if its incremental contribution is not robust. The project must not rescue a failing simple hypothesis by indefinitely adding complexity.

## 12. Sign reversal semantics

A requested transition from long to short must be represented as:

1. stop increasing long exposure,
2. cancel working orders that conflict with de-risking,
3. reduce long exposure to flat,
4. observe terminal flat state,
5. only then open short exposure.

The same rule applies symmetrically from short to long.

This rule applies in backtest, shadow/testnet, and any future live runtime.

## 13. Error handling and fail-closed behavior

Critical data or execution ambiguity should halt new risk rather than infer success.

Examples:

- stale market data → cancel or suppress new passive risk,
- unknown order state → do not duplicate replacement risk,
- position mismatch → halt and reconcile,
- funding/accounting mismatch → mark evidence invalid,
- runtime/API failure → fail closed and preserve evidence,
- risk-limit breach → cancel passive orders and execute the reviewed de-risk path.

## 14. Testing strategy

Development uses TDD for each component.

Test layers:

1. pure unit tests for state, target inventory, grid geometry, rounding, risk gates;
2. property tests for invariants such as bounded exposure and no unsafe cross-through-flat reversal;
3. deterministic order-lifecycle tests for partial fill, cancel/replace, stale generation, duplicate events;
4. integration tests against NautilusTrader;
5. differential tests between simplified/Tier-1 and microstructure/Tier-2 assumptions where meaningful;
6. Hyperliquid testnet/shadow conformance checks before any live-capital discussion;
7. regression evidence for every promoted strategy stage.

## 15. Initial repository boundaries

Proposed conceptual modules for the later implementation plan:

- `grid_trade/domain/` — immutable contracts and value objects,
- `grid_trade/market_state/` — causal state construction,
- `grid_trade/strategy/` — target inventory and grid policy,
- `grid_trade/risk/` — hard safety and de-risk policy,
- `grid_trade/execution/` — runtime-neutral passive-liquidity reconciliation,
- `grid_trade/integrations/nautilus/` — Nautilus mapping,
- `grid_trade/integrations/hyperliquid/` — only exchange-specific glue not already owned by Nautilus,
- `grid_trade/research/` — ablation and walk-forward orchestration,
- `grid_trade/evidence/` — PnL attribution and immutable evidence schemas,
- `tests/` — mirrors production boundaries.

These are design boundaries, not permission to create implementation files before the implementation plan is approved.

## 16. First implementation milestone after plan approval

The first code milestone should not be the full adaptive strategy.

It should establish a trustworthy passive-liquidity research foundation:

1. immutable passive order-ladder contracts,
2. desired-vs-working reconciliation,
3. post-only order semantics,
4. partial fill and cancel/replace behavior,
5. deterministic evidence,
6. one minimal fixed-grid S0 baseline,
7. realistic replay path using an OSS-backed execution/microstructure engine.

Only after S0 execution evidence is trustworthy should S1-S7 strategy intelligence be added.

## 17. Open design decisions deferred to implementation planning

The following are intentionally not selected by this design because they require measured prototypes rather than speculation:

- exact decision cadence,
- exact number of ladder levels,
- exact volatility estimator and horizon,
- exact long-prior target,
- exact trend/momentum estimator,
- exact short-entry threshold,
- exact OFI/microprice formulation,
- exact queue model used for Hyperliquid historical replay,
- exact production leverage.

Each must be introduced as a bounded experiment with a documented baseline rather than silently treated as a fixed truth.
