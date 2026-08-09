# Universal Causal Market Calibration Design

Date: 2026-08-09
Status: Design for review
Repository: `shuntatsu/Grid-trade`
License: LGPL-3.0
Production status: RESEARCH / NO-GO

## 1. Purpose

Replace instrument-specific absolute strategy fixtures with a causal, reusable market-calibration layer that derives scale-aware inputs from the recent behavior of the traded instrument itself.

The goal is not to create `BTCConfig`, `ETHConfig`, or per-symbol hand-tuned tables. The same calibration code must be usable for any sufficiently liquid perpetual instrument whose required market data and venue metadata satisfy the data contract.

The existing S0-S7 grid mechanics remain the strategy under evaluation. This design changes how adaptive parameters and normalized signals are produced, not the hard Risk Controller or the sign-reversal safety contract.

## 2. Problem statement

The current research mechanics use absolute fixture values such as minimum spacing in basis points, a fixed coin-denominated inventory target, a fixed funding scale, and externally supplied trend scores. These values are useful for deterministic mechanics tests but are not acceptable as universal production/research strategy parameters.

A concrete BTC smoke test exposed the failure mode: the fixture minimum spacing could dominate recent realized volatility strongly enough that the strategy did not participate at all. That is not a BTC-specific defect; it shows that a mechanics fixture must not be mistaken for a calibrated market parameter.

The original long-biased adaptive-grid design already deferred the exact volatility estimator, long prior, trend estimator, short-entry threshold, OFI/microprice formulation, queue model, and leverage to measured experiments rather than treating them as fixed truths. This design resolves that deferred work at the architecture level.

## 3. Core principle

Only **meta-parameters and hard risk preferences** may be fixed across instruments.

Absolute market-scale quantities must be estimated causally from rolling market data.

Examples of fixed meta-parameters:

- estimator windows / half-lives,
- robust-estimator family,
- risk-aversion coefficient,
- normalized inventory fractions,
- maximum normalized funding impact,
- clipping bounds,
- calibration warm-up requirements,
- minimum evidence quality thresholds,
- hard risk budgets.

Examples that must not be fixed by symbol identity:

- absolute coin quantity targets,
- absolute quote widths chosen because the instrument is BTC/ETH/SOL,
- absolute funding thresholds chosen per symbol,
- raw price-change thresholds,
- raw order-book imbalance price impacts,
- symbol-name branches in strategy code.

No strategy or calibration module may branch on instrument symbol to select behavior.

## 4. Architecture

Introduce a new boundary:

`Market data -> Causal Calibration -> Calibrated Market State -> Adaptive Grid -> Risk -> Execution`

Conceptually:

```text
Trades / L2 / funding / spread / metadata
                    |
                    v
          Causal Market Calibration
     +--------------------------------+
     | volatility scale              |
     | order-arrival intensity A, k  |
     | execution-cost floor          |
     | trend normalization           |
     | funding normalization         |
     | OFI / microprice impact       |
     | liquidity/depth scale         |
     | inventory risk budget scale   |
     +--------------------------------+
                    |
            dimensionless state
                    |
                    v
             Adaptive Grid S0-S7
                    |
                    v
               Hard Risk
                    |
                    v
             Passive Execution
```

The Calibration layer is not allowed to submit orders or alter hard risk limits.

## 5. New domain contracts

### 5.1 `CalibrationObservation`

Immutable causal input at one decision timestamp.

Required fields are grouped rather than tied to a venue-specific payload:

- timestamp and source identity,
- best bid / best ask / mid,
- recent trades or aggregated trade-flow statistics,
- realized return observations required by the configured volatility/trend estimator,
- funding observations when available,
- L2 depth / OFI inputs when available,
- venue fee/rebate information when available,
- instrument tick / lot metadata,
- account-independent market metadata needed by the estimator.

Missing optional features must be represented explicitly as unavailable, never zero-filled as if observed.

### 5.2 `CalibrationState`

Rolling state carried forward causally.

Contains sufficient statistics / rolling windows for:

- volatility,
- robust return scale,
- funding median and robust scale,
- trade-arrival / quote-distance calibration,
- fill / markout statistics when supplied by replay/live evidence,
- OFI impact regression,
- liquidity/depth scale,
- calibration generation and last-valid timestamp.

The state must be serializable, hashable through Evidence, and deterministic for identical inputs.

### 5.3 `CalibratedMarketState`

Output consumed by strategy code.

Required normalized fields:

- `volatility_scale`,
- `trend_score` in a bounded normalized range,
- `funding_score` in a bounded normalized range,
- `order_book_score` in a bounded normalized range when available,
- `estimated_microprice_displacement` in relative-price units when available,
- `quote_distance_scale` / spacing recommendation in relative-price units,
- `execution_cost_floor` in relative-price units,
- `liquidity_score` or equivalent normalized depth state,
- calibration confidence / readiness flags,
- timestamp and source identity.

No field should contain an unexplained instrument-specific magic number.

## 6. Volatility calibration

Volatility remains causal and rolling.

The first maintained implementation should support a robust realized-volatility estimator using log returns, with an explicitly configured window or exponentially weighted half-life.

Raw volatility is converted to a dimensionless / relative-price scale before it reaches quote-spacing logic.

The existing S2 formula:

`volatility_spacing_bps = realized_volatility * 10_000 * multiplier`

is retained only as a compatibility baseline. The calibrated path instead receives a volatility-derived spacing component from the calibration layer, allowing estimator evolution without coupling strategy code to a particular bar cadence.

Warm-up failure is fail-closed: before enough causal observations exist, the calibration layer reports `not_ready` and strategy code may not silently substitute a symbol-specific default.

## 7. Quote-distance / fill-intensity calibration

The preferred universal spacing model is inspired by the Guéant-Lehalle-Fernandez-Tapia / Avellaneda-Stoikov family and practical GLFT-style calibration.

Model liquidity-taking arrival intensity as a function of quote distance:

`lambda(delta) = A * exp(-k * delta)`

where `A` and `k` are estimated from rolling market/replay observations rather than fixed per instrument.

The calibration layer should expose an estimated quote-distance scale derived from:

- realized volatility,
- estimated arrival intensity / distance sensitivity,
- configured risk aversion,
- current liquidity conditions.

The first implementation does not need a full HJB solver. It may use a documented closed-form / GLFT-style approximation as long as:

- `A`, `k`, and volatility are estimated causally,
- units are explicit,
- output is bounded,
- the approximation is independently tested against reference cases,
- fallback behavior is fail-closed when estimation quality is insufficient.

## 8. Execution-cost floor

Replace a static research-fixture cost floor with a causal economic floor.

Conceptually:

`spacing_floor = max(fee_cost, adverse_selection_cost, minimum_tick_cost, safety_buffer)`

Inputs:

- current or contract-bound maker fee / rebate,
- recent realized post-fill markout where available,
- tick-size induced minimum quote movement,
- configured conservative uncertainty buffer.

The calibration layer may consume execution Evidence from backtest/shadow/live observation, but it must not consume future fill outcomes when generating a historical decision.

If user-specific fee data is unavailable in an offline replay, the fee assumption must be bound to the dataset/experiment manifest rather than inferred from symbol identity.

## 9. Trend normalization

Remove externally supplied arbitrary trend scores as a required strategy input.

The first maintained trend estimator should be dimensionless. A baseline form is:

`z_trend = horizon_return / (volatility_scale * sqrt(horizon) + epsilon)`

followed by bounded transformation such as:

`trend_score = tanh(c * z_trend)`

Exact horizon and transform gain are meta-parameters selected on training/validation, not per-symbol constants.

Alternative robust momentum estimators may later be added behind the same interface and must be compared by ablation.

The directional signal must remain separate from inventory risk. Fodra-Labadie-style directional beliefs justify asymmetric quotes, but the signal may never bypass flat-before-reverse or Hard Risk.

## 10. Funding normalization

Replace fixed `funding_scale` with a rolling robust normalization.

Baseline:

`z_funding = (funding - rolling_median) / robust_scale`

then clip to a configured bounded interval before converting to `funding_score`.

The robust scale should use a method that remains defined under long stretches of nearly constant funding; zero-scale cases must explicitly produce neutral/unavailable state rather than divide-by-zero artifacts.

Funding remains an incremental S6 feature. The funding-aware market-making literature supports treating funding as an inventory-carry state, but observed gains are not universal across instruments. Therefore S6 must remain removable by ablation.

## 11. Order-flow / microprice calibration

Raw OBI must not be converted to a fixed price shift such as `10 bps * OBI` globally.

Instead estimate causal short-horizon impact, e.g.:

`relative_price_change ~= beta_OFI * OFI`

where `beta_OFI` is rolling and may depend on market depth.

This follows the empirical result that short-horizon price changes are strongly related to order-flow imbalance and that impact slope varies inversely with market depth.

The calibrated output is a bounded relative-price displacement or normalized order-book score.

If L2 data is unavailable, S7 is explicitly unavailable; it must not be synthesized from OHLC.

## 12. Inventory normalization and sizing

Remove coin-denominated strategic targets from the universal policy contract.

Represent strategy inventory in normalized units:

`q_norm = q / Q_max`

and define target / level sizes as fractions of `Q_max`.

Examples of universal meta-parameters:

- neutral long target fraction,
- max short target fraction,
- per-level risk fraction,
- skew strength.

`Q_max` itself is derived from hard risk/account state, not symbol identity:

`Q_max = min(Q_notional, Q_margin, Q_volatility_risk, Q_venue_limit)`

The exact risk-sizing formula belongs to a dedicated risk-sizing adapter because account equity and margin state are execution/account concerns. Strategy receives normalized capacity and may not enlarge it.

For pure research runs without account state, a normalized unit-capacity contract may be used, with notional conversion performed by the experiment harness.

## 13. Meta-parameter versus online-state boundary

Training/validation may select:

- estimator family,
- window lengths / EWMA half-lives,
- robust-scale method,
- risk-aversion coefficient,
- clipping bounds,
- normalized inventory fractions,
- trend transform gain,
- minimum confidence requirements,
- uncertainty buffers.

During sealed test / live shadow execution, these meta-parameters are frozen.

Only causal online state is updated:

- volatility estimate,
- `A`, `k`,
- funding distribution statistics,
- OFI impact,
- liquidity/depth state,
- execution markout statistics using only fills already observed by decision time.

A sealed test may not be used to retune meta-parameters.

## 14. Readiness and fail-closed behavior

Each calibration component emits readiness and quality.

Examples:

- insufficient volatility history -> not ready,
- unstable / unidentified `k` -> quote-distance model unavailable,
- funding robust scale degenerate -> funding neutral/unavailable,
- stale L2 -> order-book signal unavailable,
- markout sample too small -> adverse-selection floor uses a conservative bound declared by the experiment manifest,
- stale calibration timestamp -> no new passive risk.

The combined calibrator exposes a minimum readiness policy. Strategy may degrade from S7 to a lower explicitly supported stage only if that downgrade is predeclared in the experiment contract; otherwise it must fail closed.

Silent fallback to arbitrary fixed symbol values is forbidden.

## 15. Integration with existing S0-S7

S0 remains an intentionally fixed baseline.

S1 Dynamic Center remains structurally unchanged.

S2 changes from consuming only raw `MarketSnapshot.realized_volatility` plus fixture bounds to optionally consuming calibrated spacing components.

S3 inventory logic moves to normalized target/capacity units.

S4 de-risk thresholds consume normalized trend state.

S5 Short consumes normalized trend state and normalized inventory capacity while preserving Long -> Flat -> Short.

S6 consumes normalized funding score instead of raw funding divided by a globally fixed scale.

S7 consumes calibrated relative-price displacement / normalized book score rather than a globally fixed OBI-to-bps multiplier.

Backward-compatible fixture constructors may remain only for deterministic unit/mechanics tests. They must be clearly named as fixtures/test contracts and may not be used by production/shadow research orchestration.

## 16. Proposed module boundaries

New conceptual package:

- `grid_trade/calibration/contracts.py` — immutable observations/state/output contracts,
- `grid_trade/calibration/volatility.py` — causal volatility estimator,
- `grid_trade/calibration/trend.py` — normalized momentum/trend,
- `grid_trade/calibration/intensity.py` — GLFT-style `A, k` estimation,
- `grid_trade/calibration/execution_cost.py` — fees/tick/markout economic floor,
- `grid_trade/calibration/funding.py` — robust funding normalization,
- `grid_trade/calibration/order_flow.py` — OFI/depth/microprice calibration,
- `grid_trade/calibration/engine.py` — orchestration and readiness,
- `grid_trade/risk/sizing.py` or equivalent application boundary — derive normalized inventory capacity from account/risk constraints,
- `grid_trade/research/` — calibration ablation, walk-forward, evidence.

Dependency direction:

`domain -> calibration -> strategy -> application -> integrations/research`

Hard Risk remains independently authoritative. Calibration must not import execution adapters or research runners.

## 17. Evidence contract

Every calibration decision must be reproducible.

Evidence must include:

- estimator identities and schema versions,
- frozen meta-parameters,
- input time range / last observation timestamp,
- readiness flags,
- volatility estimate,
- `A`, `k` and fit-quality diagnostics when used,
- funding center/scale and normalized score,
- OFI/depth coefficients and quality when used,
- economic cost-floor components,
- normalized inventory capacity inputs,
- calibrated output consumed by strategy,
- state digest before/after update.

Identical ordered observations plus identical frozen meta-parameters must produce the same digest.

## 18. Research and ablation protocol

Do not compare only total S7 return.

Calibration ablations:

1. existing absolute fixture mechanics baseline,
2. causal volatility only,
3. + GLFT-style arrival/intensity calibration,
4. + execution-cost / adverse-selection floor,
5. + normalized trend,
6. + normalized inventory sizing,
7. + funding normalization,
8. + OFI/depth impact.

Each feature must justify itself in walk-forward validation and sealed OOS.

Required per-regime reporting remains bull, bear, sideways, crash, high-volatility, low-volatility.

A feature is removed if it improves one instrument only through symbol-specific retuning or if its normalized parameters fail to transfer across instruments.

## 19. Cross-instrument generalization test

The universal claim must be tested directly.

At minimum, research should include multiple sufficiently liquid perpetual instruments using the same frozen meta-parameter set where possible.

Acceptable experiment types:

- train meta-parameters on instrument A, validate on B, test on C,
- pooled training with symbol-disjoint validation/test,
- rolling per-instrument online calibration with globally frozen meta-parameters.

No symbol ID may be supplied as a feature to the rule-based calibrator.

The goal is not identical PnL across instruments. The goal is that scale adaptation occurs from market data rather than hand-authored symbol constants, with risk behavior remaining bounded.

## 20. Tier-2 requirement

OHLC remains insufficient for authoritative calibration of:

- `A`, `k`,
- queue/fill intensity,
- adverse-selection markout,
- OFI impact,
- microprice value.

Tier-1 bars may screen volatility/trend estimators only.

Promotion of the universal calibration layer requires Tier-2 L2/trade replay for microstructure-dependent components.

## 21. Literature basis

The design is informed by, but does not copy source code from:

- Guéant, Lehalle, Fernandez-Tapia, *Dealing with the Inventory Risk. A solution to the market making problem*, arXiv:1105.3115 — inventory-constrained market making with quote arrival rates depending on distance.
- Fodra and Labadie, *High-frequency market-making with inventory constraints and directional bets*, arXiv:1206.4810 — directional asymmetric quoting while retaining inventory-risk control.
- Cont, Kukanov, Stoikov, *The Price Impact of Order Book Events*, arXiv:1011.6402 — short-horizon price change relation to order-flow imbalance and market depth.
- Le, *Funding-Aware Optimal Market Making for Perpetual DEXs*, arXiv:2605.06405 — funding as an inventory-carry state with Hyperliquid BTC/ETH/SOL calibration; instrument-dependent gains justify retaining funding as an ablation.
- Chen, Chen, Jang, *Dynamic Grid Trading Strategy: From Zero Expectation to Market Outperformance*, arXiv:2506.11921 — motivation for dynamic resetting/adaptation instead of assuming static-grid positive expectancy.

These works justify model families and research hypotheses, not guaranteed profitability.

## 22. Testing strategy

TDD is required.

Unit/property tests:

- scale invariance under proportional price rescaling,
- no dependence on symbol string,
- deterministic rolling state,
- causal update ordering,
- no future observation access,
- robust behavior under zero/near-zero funding scale,
- bounded trend/funding/order-book outputs,
- `A,k` validation and fit-quality rejection,
- monotonic economic floor components,
- normalized inventory capacity never exceeds Hard Risk,
- no Long-to-Short direct reversal.

Integration tests:

- existing S0-S7 mechanics digests remain unchanged through compatibility fixture paths,
- calibrated path reproduces expected reference cases,
- hftbacktest Tier-2 replay supplies causal fill/markout observations,
- Nautilus integration preserves calibrated desired orders without embedding symbol calibration logic.

Metamorphic tests are mandatory: multiply all prices/ticks/notional scales by a positive constant and verify normalized strategy decisions remain equivalent after correct unit conversion.

## 23. Migration plan constraints

Implementation must not delete the current deterministic fixtures immediately.

Migration sequence:

1. add calibration contracts/engine with no strategy behavior change,
2. add causal volatility/trend outputs,
3. add normalized inventory capacity,
4. add GLFT-style intensity calibration,
5. add execution-cost floor,
6. add funding normalization,
7. add OFI/depth calibration,
8. add a calibrated policy-config adapter,
9. switch research orchestration from fixture config to calibrated config,
10. retain fixture constructors only under explicit mechanics-test/reproducibility paths.

At each step existing Evidence digests for S0-S7 mechanics fixtures remain regression gates unless a separately reviewed schema migration is required.

## 24. Success criteria

The calibration layer is architecturally complete when:

- no maintained research strategy requires a symbol-specific absolute parameter table,
- normalized decisions are scale-invariant in metamorphic tests,
- causal rolling updates are deterministic,
- every calibrated component has readiness/quality gates,
- Hard Risk remains independent and cannot be relaxed by calibration,
- mechanics fixture regressions remain stable,
- Tier-2 replay can supply intensity/markout/OFI inputs,
- walk-forward/sealed-OOS orchestration freezes meta-parameters correctly,
- cross-instrument experiments can run the same calibration code without symbol branches.

Profitability is a separate empirical gate and remains NO-GO until Tier-2 OOS and stress evidence support promotion.
