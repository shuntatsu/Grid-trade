# Universal Microstructure Calibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans task-by-task with TDD. This plan builds on the verified Universal Calibration Foundation at `a96e347487d718916dc540202fd4559cc23dbb17`.

**Goal:** Add causal, instrument-agnostic Tier-2 calibration for liquidity-taking arrival intensity, execution/adverse-selection cost, and order-flow/depth impact without yet wiring those estimates into the live S2-S7 strategy path.

**Architecture:** Keep market-only microstructure estimation inside `grid_trade.calibration`. Raw exchange-format decoding remains outside this package. Calibration consumes immutable venue-neutral observations and matured labels. It produces deterministic normalized estimates/readiness which Phase C can compose into `CalibratedMarketState`. Hard Risk and order submission remain out of scope.

**Literature/Reference Basis:**
- Guéant–Lehalle–Fernandez-Tapia: arrival intensity decreases with quote distance and inventory risk is a separate control concern.
- hftbacktest GLFT tutorial: practical distance/intensity measurement and calibration of `A`, `k`; used as a behavioral reference, not copied source.
- Cont–Kukanov–Stoikov: short-horizon price changes relate approximately linearly to OFI and the impact slope varies with market depth.
- Hyperliquid official data contract: historical L2 snapshots and node trade/fill data can seed Tier-2 replay; WebSocket `l2Book` and `trades` can record forward data.

## Global Constraints

- LGPL-3.0; RESEARCH / NO-GO.
- No symbol-name branches or per-symbol constants.
- All evidence-sensitive Decimal arithmetic uses `deterministic_decimal_context()`.
- Every label with a future horizon is unusable until `decision_time >= matured_at`.
- L2-only features are never inferred from OHLC.
- `A`, `k`, OFI impact, markout cost, and fit quality have explicit readiness/sample counts.
- Missing/poor-quality microstructure calibration fails closed or remains explicitly unavailable; no hidden fixed-symbol fallback.
- Existing Foundation APIs and S0-S7 historical Evidence digests remain unchanged.
- This phase does not alter strategy target inventory, spacing, short, funding, Risk, or Execution behavior.

---

### Task 1: Venue-neutral Tier-2 microstructure contracts

**Files:**
- Create: `src/grid_trade/calibration/microstructure_contracts.py`
- Create: `tests/calibration/test_microstructure_contracts.py`
- Modify: `src/grid_trade/calibration/__init__.py`

**Contracts:**
- `IntensityBucket(distance_vol_units, exposure_seconds, arrival_count)`
- `TopOfBookObservation(timestamp, source_id, instrument_id, best_bid, bid_size, best_ask, ask_size)`
- `MarkoutSide(BUY, SELL)`
- `MaturedMarkout(fill_timestamp, matured_at, side, fill_price, mark_price)`
- `OfiImpactSample(feature_timestamp, matured_at, normalized_ofi, relative_price_change)`
- `MicrostructureReadiness(ready, sample_count, reason, quality)`

**TDD gates:** validation of positive prices/exposure, non-negative counts/sizes, aware timestamps, `matured_at >= feature/fill timestamp`, normalized quantities finite, quality in `[0,1]` when available.

---

### Task 2: GLFT-style arrival intensity via exposure-aware Poisson calibration

**Files:**
- Create: `src/grid_trade/calibration/intensity.py`
- Create: `tests/calibration/test_intensity.py`
- Modify: `src/grid_trade/calibration/__init__.py`

**Config:** `IntensityCalibrationConfig(min_buckets, min_total_arrivals, k_min, k_max, k_steps, min_log_likelihood_improvement)`.

**Model:** for bucket `i`,

`count_i ~ Poisson(exposure_i * A * exp(-k * distance_vol_units_i))`.

For fixed `k`:

`A(k) = sum(count) / sum(exposure * exp(-k*distance))`.

Evaluate a deterministic configured grid of `k` values and select maximum Poisson log-likelihood (constant factorial term omitted because it does not affect selection). Compare with a constant-intensity null model (`k=0`).

**Output:** `IntensityEstimate(A, k, e_fold_distance_vol_units=1/k, log_likelihood_improvement, sample_count, total_arrivals, ready)`.

**Why:** zero-arrival buckets remain informative instead of being discarded by `log(0)` handling. Distance is volatility-normalized rather than tick- or dollar-normalized.

**Tests:** synthetic recovery around known A/k; zero-count tails affect estimate; insufficient counts/buckets not ready; non-positive fitted k not ready; ambient Decimal precision independence; symbol identity absent from API.

---

### Task 3: Current top-of-book OFI and microprice primitives

**Files:**
- Create: `src/grid_trade/calibration/order_flow.py`
- Create: `tests/calibration/test_order_flow.py`

**Functions:**
- `compute_ofi(previous, current) -> Decimal` using Cont-style best-bid/best-ask event terms.
- `normalized_ofi(previous, current) -> Decimal` divides OFI by a conservative average top-of-book depth scale.
- `microprice(current) -> Decimal`
- `microprice_displacement(current) -> Decimal` relative to mid.

**Tests:** bid size increase positive OFI, ask size increase negative OFI, price-level changes use previous/current queue sizes correctly, symmetry under price/size scaling, microprice lies within spread for positive sizes, no division by zero.

---

### Task 4: Causal OFI impact calibration with matured labels

**Files:**
- Extend: `src/grid_trade/calibration/order_flow.py`
- Extend: `tests/calibration/test_order_flow.py`

**Config:** `OfiImpactConfig(window, min_samples, min_abs_feature_energy, max_abs_beta, score_scale_vol_units)`.

**State:** rolling `OfiImpactSample` values, but samples only enter the fit when `sample.matured_at <= decision_time`.

**Fit:** deterministic through-origin least squares:

`beta = sum(x*y) / sum(x*x)`

where `x=normalized_ofi`, `y=relative_price_change`.

**Output:** `OfiImpactEstimate(beta, fit_r2, sample_count, ready)` plus helper `predict_ofi_displacement(current_normalized_ofi, estimate)`.

**Tests:** future labels ignored; label becomes usable exactly at maturity; synthetic beta recovery; zero feature energy not ready; bounded beta; ambient context independence.

---

### Task 5: Execution and adverse-selection cost floor

**Files:**
- Create: `src/grid_trade/calibration/execution_cost.py`
- Create: `tests/calibration/test_execution_cost.py`

**Config:** `ExecutionCostConfig(markout_window, min_markout_samples, adverse_quantile, uncertainty_buffer, fallback_adverse_cost)`.

**Inputs:**
- maker fee rate (can be negative rebate),
- tick size and current mid,
- ordered matured markout samples,
- decision timestamp.

**Adverse markout:**
- BUY: adverse if future mark < fill;
- SELL: adverse if future mark > fill;
- relative adverse cost clipped at zero.

Only samples with `matured_at <= decision_time` can be used. Use a deterministic nearest-rank upper quantile. If fewer than `min_markout_samples`, use the manifest/config-declared conservative `fallback_adverse_cost`; report `markout_ready=False` and `used_fallback=True`.

**Floor:**

`round_trip_fee = 2 * maker_fee_rate`

`execution_cost_floor = max(tick_size / mid, round_trip_fee + adverse_cost + uncertainty_buffer, 0)`.

A maker rebate may reduce fee burden but can never make the floor negative.

**Tests:** future markout excluded; BUY/SELL sign; quantile; fallback; rebate handling; tick floor; deterministic Decimal context.

---

### Task 6: Universal microstructure calibration state and engine

**Files:**
- Create: `src/grid_trade/calibration/microstructure_engine.py`
- Create: `tests/calibration/test_microstructure_engine.py`
- Modify: `src/grid_trade/calibration/__init__.py`

**Config:** bundles intensity, OFI impact, execution-cost configs plus `min_microstructure_quality`.

**State:** frozen config, generation, identity, latest timestamp, rolling OFI-impact state; raw intensity buckets and markout samples are supplied as causally available snapshots from the replay/recording layer rather than duplicated internally.

**Output:** `MicrostructureCalibrationEstimate` containing:
- intensity estimate;
- `quote_distance_scale` in relative-price units computed as `volatility_scale * e_fold_distance_vol_units` when intensity+volatility are ready;
- execution cost floor;
- current normalized OFI;
- OFI impact estimate;
- predicted relative displacement;
- current microprice relative displacement;
- bounded order-book score expressed in volatility units when OFI impact + volatility are ready;
- combined readiness/quality.

The engine must not claim READY if its configured required components are unavailable.

**Tests:** frozen config; timestamp/identity continuity; no symbol effect; scale invariance; no microstructure fabrication; deterministic state/output across ambient Decimal contexts.

---

### Task 7: Tier-2 causality and maturity property tests

**Files:**
- Create: `tests/calibration/test_microstructure_causality.py`

**Properties:**
- adding a future (not-yet-matured) markout or OFI label cannot change a decision at time `t`;
- once `t == matured_at`, the label may affect the estimate;
- changing instrument identity only changes metadata;
- multiplying all prices/tick by a common factor leaves relative outputs invariant;
- multiplying all L2 sizes by a common factor leaves normalized OFI invariant;
- shuffled future labels cannot alter earlier state.

---

### Task 8: Deterministic microstructure Evidence and research fixture

**Files:**
- Create: `src/grid_trade/research/microstructure_calibration_runner.py`
- Create: `tests/research/test_microstructure_calibration_runner.py`
- Modify: `src/grid_trade/evidence/events.py` only if a calibration-specific Evidence kind is required; otherwise use existing generic payload mechanisms without changing old payloads.
- Modify: `.github/workflows/research.yml` to add a fresh-process digest gate for the new runner.

**Runner:** use synthetic but realistic venue-neutral L2/arrival/markout sequences with two arbitrary instrument IDs and scaled-price variants. It validates mechanics/determinism only; PnL remains out of scope.

**Evidence must include:** frozen config, readiness, A/k/fit improvement, quote-distance scale, markout/fallback components, OFI beta/quality, current predicted displacement, microprice displacement, state generation.

Existing S0/S1/S2/Adaptive digests MUST remain unchanged. Add a new microstructure digest rather than replacing them.

---

### Task 9: Architecture/self-review and final gates

**Files:**
- Update: `README.md`
- Create: `docs/superpowers/reviews/2026-08-09-universal-microstructure-calibration-review.md`
- Modify: `tests/architecture/test_boundaries.py` only if needed.

**Required review:**
- no symbol branches;
- no Strategy/Risk/Execution imports from Calibration;
- no future-label leakage;
- no OHLC substitute for L2/fill components;
- all evidence-sensitive Decimal math under deterministic context;
- config frozen after first update;
- zero/degenerate/poor-fit states explicit;
- old Evidence digests unchanged;
- no copied third-party source;
- no production authorization.

**Final verification:** fresh Core CI + Research Integration on exact branch HEAD, with old four digests and new microstructure digest all checked across separate Python processes.

---

## Phase C boundary

Do **not** wire these outputs into S2-S7 in this plan. After Phase B is verified, Phase C will:

- combine Foundation + Microstructure estimates;
- map relative quote-distance/cost outputs into executable spacing;
- convert risk-derived `Q_max` into normalized target/level quantity fractions;
- construct calibrated `AdaptiveSignals` without fixed funding/trend/OFI scales;
- preserve the legacy fixture mechanics path only for deterministic regression;
- add symbol-disjoint/cross-instrument walk-forward and sealed-OOS harnesses.
