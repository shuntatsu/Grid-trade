# Universal Calibration → Adaptive Grid Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans task-by-task with TDD. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the research-only fixed BTC-like fixture inputs in the new strategy path with causal, symbol-agnostic Foundation + Microstructure calibration and risk-derived inventory capacity, while preserving the legacy S0-S7 path byte-for-byte for regression evidence.

**Architecture:** `grid_trade.calibration` remains market-only and composes Foundation + Microstructure into one `CalibratedMarketState`. `grid_trade.risk.sizing` remains account/risk-only and derives `Q_max`. `grid_trade.application.calibrated_adaptive` is the composition boundary: it converts calibrated relative values and risk capacity into the existing `AdaptiveGridPolicyConfig`, `AdaptiveSignals`, and `MarketSnapshot`, then delegates to the existing Strategy and generic PassivePolicy/Risk/Reconciliation machinery. Strategy receives no symbol-specific information and Hard Risk retains final veto authority.

**Tech Stack:** Python 3.12, Decimal fixed-precision arithmetic, pytest, mypy strict, Ruff, existing GitHub Actions Core/Research workflows.

## Global Constraints

- LGPL-3.0; RESEARCH / NO-GO FOR PRODUCTION.
- No BTC/ETH/SOL or other symbol-name branches or per-symbol constants.
- No Calibration → Strategy/Risk/Application/Execution dependency.
- No Risk → Calibration dependency.
- All calibration-sensitive Decimal arithmetic uses `deterministic_decimal_context()`.
- Legacy S0/S1/S2/Adaptive/Microstructure Evidence digests must remain unchanged.
- Calibrated path fails closed when required Foundation/Microstructure components are not ready.
- Calibrated strategy can never enlarge `InventoryCapacity.q_max`; venue quantity alignment rounds down only.
- Funding score is already normalized; calibrated path uses unit funding scale rather than a raw absolute funding-rate constant.
- OFI/order-book influence is in volatility units; no fixed per-symbol basis-point multiplier.
- Long → Short sign reversal still requires Flat first.
- A changed runtime calibration/config must be compared against the previously applied config; never reconstruct the old working ladder with the new config.

---

### Task 1: Compose Foundation + Microstructure into one universal calibration state

**Files:**
- Create: `src/grid_trade/calibration/universal_engine.py`
- Create: `tests/calibration/test_universal_engine.py`
- Modify: `src/grid_trade/calibration/__init__.py`

**Interfaces:**
- `UniversalCalibrationConfig(foundation: CalibrationEngineConfig, microstructure: MicrostructureCalibrationConfig)`
- `UniversalCalibrationState(foundation_state: CalibrationEngineState, microstructure_state: MicrostructureCalibrationState)`
- `UniversalCalibrationUpdate(previous_state, next_state, market_state, foundation, microstructure)`
- `update_universal_calibration(...) -> UniversalCalibrationUpdate`

The update requires the Foundation `CalibrationObservation` and Microstructure `TopOfBookObservation` to have identical timestamp/source/instrument identity. Foundation updates first; its causal `volatility_scale` is passed to the Microstructure engine. The resulting `CalibratedMarketState` is created from the Foundation state with Microstructure quote-distance, execution-cost, order-book score, microprice displacement, and a `CalibrationComponentStatus` derived from Microstructure readiness.

- [ ] Write tests that identity/timestamp mismatch fails closed, Foundation-only warmup remains explicit, ready Microstructure fields are composed, config/state determinism holds, symbol rename changes metadata only, and common price/size scaling preserves relative outputs.
- [ ] Run Core CI and confirm the tests fail because `universal_engine` is missing.
- [ ] Implement the minimal universal engine without importing Strategy/Risk/Application.
- [ ] Run Core CI through strict mypy.

### Task 2: Derive adaptive runtime inputs from dimensionless meta-parameters

**Files:**
- Create: `src/grid_trade/application/calibrated_adaptive.py`
- Create: `tests/application/test_calibrated_adaptive.py`

**Interfaces:**
- `VenueGridConstraints(tick_size: Decimal, quantity_step: Decimal)`
- `CalibratedAdaptiveMetaConfig(...)` containing only dimensionless fractions/volatility-unit multipliers plus stage/level counts.
- `CalibratedAdaptiveInputs(snapshot, signals, policy_config, effective_q_max)`
- `CalibratedAdaptivePreparation(inputs: CalibratedAdaptiveInputs | None, reason: str)`
- `prepare_calibrated_adaptive_inputs(...) -> CalibratedAdaptivePreparation`

Derivation rules:
- Require Foundation volatility+trend READY.
- Require Microstructure READY for calibrated spacing/execution economics; S7 additionally requires order-book score and microprice displacement; S6+ requires funding READY.
- `effective_q_max = floor(InventoryCapacity.q_max / quantity_step) * quantity_step`; never round up.
- Base long target, short cap, and level quantity are fractions of `effective_q_max`, rounded down to the venue quantity step.
- Dynamic-center thresholds, reservation skew, order-book shift and spacing bounds are `volatility_scale × configured_vol_units × 10_000` bps.
- Economic spacing floor is `max(volatility floor, quote_distance_scale × intensity multiplier, execution_cost_floor × execution multiplier)` and must not exceed the configured volatility-unit max spacing.
- Calibrated `funding_score` is passed as the signal with `FundingBiasConfig.funding_scale = 1`.
- Calibrated `order_book_score` is passed directly as normalized imbalance.
- Microprice is reconstructed as `mid * (1 + estimated_microprice_displacement)` only from the calibrated microprice displacement; no OHLC substitute.

- [ ] Write RED tests for readiness gating, q_max never increasing, quantity-step alignment, price-scale invariance, symbol invariance, dynamic spacing/funding/order-book mapping, and invalid economics (`floor > max spacing`) failing closed.
- [ ] Implement minimal preparation logic under deterministic Decimal context.
- [ ] Run Core CI through strict mypy.

### Task 3: Make AdaptiveGrid config changes generation-safe

**Files:**
- Modify: `src/grid_trade/strategy/adaptive_grid.py`
- Extend: `tests/strategy/test_adaptive_grid.py`

**Interface change:** add optional keyword `previous_config: AdaptiveGridPolicyConfig | None = None` to `decide_adaptive_grid`. Legacy callers omit it. Current ladder is reconstructed with `previous_config or config`; candidate ladder is always built with the new `config`.

- [ ] Write a failing regression where only `order_quantity` or spacing bounds change and prove the new economic ladder is compared against the ladder produced by the old config.
- [ ] Verify RED on current implementation.
- [ ] Implement the optional previous-config comparison with no behavior change when omitted.
- [ ] Verify legacy adaptive tests and old Evidence digest remain unchanged.

### Task 4: Add calibrated application state and Risk/Reconciliation transition

**Files:**
- Extend: `src/grid_trade/application/calibrated_adaptive.py`
- Extend: `tests/application/test_calibrated_adaptive.py`
- Modify: `src/grid_trade/application/__init__.py`

**Interfaces:**
- `CalibratedAdaptiveState(policy_state: AdaptiveGridState, applied_config: AdaptiveGridPolicyConfig)`
- `initialize_calibrated_adaptive_grid(inputs) -> tuple[CalibratedAdaptiveState, tuple[PassiveOrderIntent, ...]]`
- `transition_calibrated_adaptive_grid(...) -> PassivePolicyTransition[CalibratedAdaptiveState, AdaptiveGridDecision]`
- `continue_calibrated_adaptive_reconciliation(...)`

The transition passes `state.applied_config` as `previous_config` and the newly prepared config as `config`. The candidate wrapper stores the new config only if the economic ladder changed. Generic `transition_passive_policy` controls commit timing: cancel phase keeps the previous wrapper; accepted submit completion commits the candidate wrapper. Risk rejection never commits calibration-derived config/state.

- [ ] RED tests: config changes cause one generation; tick-equivalent changes cause none; cancel-before-replace retains old applied config; Risk rejection retains old config; reduce-only de-risk still commits when allowed; continuation never recomputes calibration/policy.
- [ ] Implement with existing generic PassivePolicy/Risk/Reconciliation APIs only.
- [ ] Run Core CI through strict mypy and architecture tests.

### Task 5: Generalization and causality gates for the integrated path

**Files:**
- Create: `tests/application/test_calibrated_adaptive_generalization.py`

- [ ] Verify arbitrary instrument rename leaves all numerical prepared inputs identical.
- [ ] Verify multiplying price/tick by a common factor leaves normalized decisions invariant and only scales absolute prices.
- [ ] Verify multiplying equity/margin/venue quantity consistently changes quantities via risk capacity, not symbol identity.
- [ ] Verify an unmatured OFI/markout label cannot change an earlier integrated decision.
- [ ] Verify S6 is unavailable without calibrated funding and S7 is unavailable without ready microstructure/order-book state.
- [ ] Verify Long → Flat → Short remains enforced after calibrated target sizing.

No production code is added unless these tests reveal a contract bug; any bug fix follows RED → minimal fix → GREEN.

### Task 6: Deterministic calibrated-adaptive Evidence runner

**Files:**
- Create: `src/grid_trade/research/calibrated_adaptive_runner.py`
- Create: `tests/research/test_calibrated_adaptive_runner.py`
- Modify: `.github/workflows/research.yml`
- Modify: `src/grid_trade/evidence/events.py` only if a new additive Evidence kind is necessary.

The runner uses the same causal Foundation/Microstructure synthetic sequence with two arbitrary instrument IDs and a common price/size-scaled copy. It derives `InventoryCapacity` from normalized risk inputs, prepares calibrated adaptive inputs, then exercises initialization and one Risk/Reconciliation transition. It does not invent fills or PnL.

Evidence includes calibration generation/readiness, q_max/binding constraint, derived center/spacing/quantity parameters, normalized signals, adaptive generation/reconciliation, and explicit `economics_validated=False`, `alpha_validated=False`, `production_authorized=False`.

- [ ] Test deterministic repeat, symbol invariance, scale invariance, fail-closed readiness and NO-GO flags.
- [ ] Add focused mypy and two-fresh-process digest check to Research workflow.
- [ ] Verify existing five digests are unchanged and add a sixth calibrated-adaptive digest.

### Task 7: Architecture/self-review, docs and final gates

**Files:**
- Update: `README.md`
- Create: `docs/superpowers/reviews/2026-08-10-universal-calibration-strategy-integration-review.md`
- Extend `tests/architecture/test_boundaries.py` only if the new Application bridge exposes a missing boundary rule.

Required review:
- no symbol-specific constants/branches;
- Calibration remains market-only;
- Risk sizing remains calibration-independent;
- Application is the only Calibration+Risk+Strategy composition boundary;
- q_max only rounds down;
- runtime config changes use previous applied config for ladder comparison;
- future labels cannot affect current decisions;
- legacy fixture path and five historical digests unchanged;
- no OHLC fabrication of L2/fill evidence;
- no production authorization or profitability claim.

Final verification on exact branch HEAD: fresh Core CI and Research Integration, all legacy tests, strict/focused mypy, five existing fresh-process digests plus the new calibrated-adaptive digest.
