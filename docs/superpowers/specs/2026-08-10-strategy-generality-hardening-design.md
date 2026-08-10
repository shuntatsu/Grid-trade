# Strategy Generality Hardening Design

Date: 2026-08-10
Status: Approved for implementation
Repository: `shuntatsu/Grid-trade`
Branch: `agent/generalize-strategy-core`
Production status: **RESEARCH / NO-GO FOR PRODUCTION**

## 1. Purpose

The current calibrated adaptive grid is intentionally research-first and already avoids many symbol-specific absolute constants. This change hardens the remaining generality boundaries before historical profitability and regime-robustness evaluation.

The goal is not to turn the repository into a universal trading framework. The supported operating model remains:

- one strategy state per instrument;
- one linear crypto perpetual contract per strategy instance;
- passive post-only ladder generation;
- deterministic calibration, risk, reconciliation, and Evidence;
- current long-biased behavior as the default compatibility profile.

The goal is to make the same core code safe to reuse across instruments, price scales, liquidity levels, feature combinations, and directional profiles without silently mixing identities or changing hidden time semantics.

## 2. Existing strengths

The present design already provides:

- relative-price volatility and trend normalization;
- symbol-rename and common price/size scale invariance checks;
- account/risk-derived inventory capacity;
- explicit tick and quantity steps;
- causal funding, OFI, microprice, arrival-intensity, and markout inputs;
- flat-before-reverse semantics;
- hard Risk veto authority;
- deterministic Decimal arithmetic and canonical Evidence;
- runtime-neutral Strategy/Application boundaries.

This change preserves those contracts.

## 3. Problems to solve

### 3.1 Instrument identity is incomplete

Calibration observations carry `instrument_id`, but core market snapshots, order intents, working orders, and fills do not. A one-instrument process is safe by convention, but a future multi-instance process can accidentally reconcile or assess orders from another instrument.

### 3.2 Contract assumptions are implicit

Notional sizing currently assumes a linear quantity-times-price contract. Tick and quantity steps are explicit, but minimum quantity, minimum notional, contract multiplier, maximum quantity, funding cadence, and supported contract type are not one validated contract.

### 3.3 Feature activation is coupled to research stage ordering

`AdaptiveStage` currently activates features through integer comparisons. It cannot naturally represent valid ablations such as inventory plus order-book reference without funding, or de-risking without conditional reversal.

### 3.4 Directional preference is embedded in the legacy pipeline

The default target starts from a non-negative `base_long_target`, then applies long-only de-risking and a short overlay. The current behavior is useful, but it should be an explicit profile rather than an assumption of the reusable core.

### 3.5 Observation counts hide elapsed-time semantics

Volatility windows, trend horizons, and matured-label windows use counts. Counts have different economic meaning when observation cadence changes. A strategy configuration must state and enforce the cadence and label horizons it assumes.

## 4. Considered approaches

### Approach A — compatibility-preserving explicit contracts (selected)

Add narrow contracts for instrument metadata, sampling cadence, independent features, and signed directional targets. Keep existing public APIs and S0–S7 behavior as defaults. New generalized paths must opt into explicit contracts and fail closed on mismatches.

Advantages:

- small migration surface;
- existing research fixtures remain usable;
- generality is testable without a framework rewrite;
- historical comparisons remain interpretable;
- future removal of compatibility paths is possible after evidence exists.

Trade-off:

- legacy `UNSPECIFIED` identity remains temporarily supported for existing fixtures;
- some compatibility names such as `ShortOverlayDecision.bearish_severity` remain even when the generalized path interprets the value as adverse-trend severity.

### Approach B — protocol/plugin architecture

Replace target, quote, risk, and execution policies with registries and runtime-loaded protocols.

Rejected because the repository has one maintained strategy family. The abstraction cost, configuration complexity, and test matrix would exceed the current need.

### Approach C — documentation-only constraints

Document that the system is single-instrument and linear-perpetual, but leave runtime contracts unchanged.

Rejected because identity and cadence mistakes would remain silent and could invalidate historical evidence.

## 5. Scope

### In scope

1. Explicit `InstrumentSpec` for supported linear perpetual contracts.
2. Instrument identity on market and order-domain contracts with compatibility defaults.
3. Cross-layer identity validation before calibration, risk, and reconciliation.
4. Explicit `SamplingSpec` with cadence and matured-label horizon validation.
5. Independent `AdaptiveFeatures`, with `AdaptiveStage` retained as a preset.
6. A signed `DirectionalTargetProfileConfig` supporting long, short, or flat baselines.
7. Sign-symmetric de-risk and conditional-reversal helpers.
8. Current long-biased S7 behavior as the default compatibility profile.
9. Generality, mismatch, fail-closed, and regression tests.
10. README and Research CI updates where required.

### Out of scope

- portfolio allocation across instruments;
- shared cross-instrument risk/netting;
- inverse perpetuals, dated futures, spot, options, or multi-currency collateral;
- dynamic plugin discovery;
- new alpha signals;
- parameter optimization;
- profitability claims;
- live-capital authorization.

Unsupported contract types must be rejected explicitly rather than approximated.

## 6. Architecture

```text
InstrumentSpec + SamplingSpec
          │
          v
Instrument-bound causal observations
          │
          v
Universal calibration
          │
          v
CalibratedMarketState
          │
          v
DirectionalTargetProfile + AdaptiveFeatures
          │
          v
Inventory/reference/spacing decisions
          │
          v
Instrument-bound passive ladder
          │
          v
Hard Risk + instrument validation
          │
          v
Cancel-before-replace reconciliation
```

### 6.1 `domain.instrument`

`InstrumentSpec` owns the venue-neutral economic contract required by the strategy:

- `instrument_id`;
- `contract_type`;
- `contract_multiplier`;
- `tick_size`;
- `quantity_step`;
- `min_quantity`;
- `min_notional`;
- `max_quantity`;
- `funding_interval_seconds`.

The first and only supported `ContractType` is `LINEAR_PERPETUAL`.

It provides deterministic helpers for:

- notional calculation;
- quantity floor-to-step;
- quantity and notional executability;
- validation that a `VenueGridConstraints` view matches the instrument.

A non-linear contract raises a clear error at construction or use.

### 6.2 Instrument identity propagation

The following contracts gain `instrument_id`:

- `MarketSnapshot`;
- `PassiveOrderIntent`;
- `WorkingOrder`;
- `FillEvent`;
- `AdaptiveGridState`.

For compatibility, existing constructors default to a named legacy sentinel. The sentinel is accepted only by legacy paths. Any path supplied with an `InstrumentSpec` must use an explicit matching identity.

Identity matching rules:

- two explicit identities must be equal;
- an explicit `InstrumentSpec` never matches the legacy sentinel;
- calibration and market snapshots must match;
- proposed and working orders must match the snapshot before Risk or reconciliation;
- Tier-2 snapshots and intents use the Dataset instrument explicitly;
- economic ladder signatures and reconciliation matches include identity.

Client-order IDs retain their historical shape for the legacy sentinel. Explicit instruments receive an instrument prefix to prevent cross-instrument collisions.

### 6.3 `calibration.sampling`

`SamplingSpec` declares:

- observation interval;
- allowed interval deviation;
- volatility window duration;
- trend horizon duration;
- markout horizon;
- OFI-label horizon.

When present on `CalibrationEngineConfig`, it must agree exactly with the count-based volatility window and trend horizon. Consecutive observations outside the allowed cadence fail closed.

`UniversalCalibration` additionally validates that matured markouts and OFI labels use the configured horizons within the same cadence tolerance. Future labels remain pending and cannot enter a causal fit.

Legacy configs may omit `SamplingSpec`; generalized historical evaluation must provide it.

### 6.4 Independent adaptive features

`AdaptiveFeatures` contains independent booleans for:

- inventory control;
- partial de-risking;
- conditional reversal;
- funding bias;
- order-book reference.

`AdaptiveStage` remains a reporting and compatibility preset through `AdaptiveFeatures.from_stage(stage)`. If no explicit feature set is provided, behavior is identical to the existing stage ordering.

Feature rules:

- target generation always occurs;
- disabling inventory control removes reservation/side skew but does not remove the target;
- de-risking, reversal, funding, and order-book reference can be enabled independently;
- readiness checks depend on active features, not ordinal stage value;
- Hard Risk remains independent and always active.

### 6.5 Signed directional target profile

`DirectionalTargetProfileConfig` contains:

- signed baseline target;
- whether an opposite target is allowed;
- adverse aligned-trend entry threshold;
- maximum opposite target.

A target may be positive, zero, or negative. For a non-zero baseline, trend is aligned to the preferred direction:

```text
aligned_trend = sign(baseline_target) × trend_score
```

Negative aligned trend is adverse for either a long or short baseline.

Generic de-risking multiplies the signed baseline by the configured warning/severe fraction. Generic conditional reversal maps sufficiently adverse aligned trend to the opposite sign. Existing flat-before-reverse enforcement remains authoritative.

The legacy path remains the default when no profile is supplied. The calibrated generalized path can supply an explicit profile. A long-biased profile using the current baseline, thresholds, and short limit must produce the same target sequence as the legacy pipeline.

### 6.6 Executable ladder constraints

`AdaptiveLadderConfig` receives optional instrument execution limits derived from `InstrumentSpec`:

- explicit identity;
- minimum quantity;
- minimum notional;
- contract multiplier.

Order generation floors quantities and omits an unexecutable residual level. Prepared inputs fail with `inventory_capacity_not_executable` when the normal per-level quantity cannot satisfy minimum quantity or notional.

No order may exceed the instrument maximum quantity or the strategy/risk inventory cap.

### 6.7 Calibrated application boundary

`prepare_calibrated_adaptive_inputs` receives optional explicit:

- `InstrumentSpec`;
- `AdaptiveFeatures`;
- `DirectionalTargetProfileConfig`.

When supplied, these are validated and embedded in the resulting policy configuration. The existing call form remains valid and keeps current stage-derived long-biased behavior.

The calibrated boundary validates timestamp, source, and instrument identity together.

## 7. Data flow and state ownership

- Instrument metadata is immutable and supplied by the integration/research boundary.
- Sampling metadata is immutable and frozen with calibration config.
- Calibration state remains per source and instrument.
- Strategy state remains per instrument and rejects identity changes.
- Risk state remains account-level; instrument correctness is checked from snapshot and orders before Risk projection.
- Working orders remain runtime observations and must carry the same instrument identity as the strategy snapshot.
- No global registry or mutable singleton is introduced.

## 8. Failure behavior

The generalized path fails closed for:

- unsupported contract type;
- empty or mismatched instrument identity;
- tick/quantity constraints inconsistent with `InstrumentSpec`;
- observation cadence outside tolerance;
- markout or OFI horizon inconsistent with `SamplingSpec`;
- per-level quantity below minimum quantity or notional;
- working orders from another instrument;
- target or opposite-target magnitude outside the inventory cap;
- direct long-to-short or short-to-long reversal while non-flat.

Readiness failures return explicit preparation reasons where the current API already supports them. Contract violations raise `ValueError` before order generation or reconciliation.

## 9. Compatibility

The following must remain true:

- existing imports remain valid;
- existing constructors remain valid through compatibility defaults;
- existing `AdaptiveStage` presets retain their current mechanics;
- current long-biased S0–S7 research runners remain deterministic;
- default policy behavior is unchanged unless new explicit contracts are supplied;
- Risk authority, cancel-before-replace, Evidence ordering, and Dataset acceptance are not relaxed.

Digest values may change when new dataclass fields become part of canonical Evidence. Determinism and semantic equivalence, rather than preservation of historical hash text, are the contract. Any changed digest must be regenerated only after the corresponding full runner remains deterministic across fresh processes.

## 10. Test strategy

### Contract tests

- valid linear perpetual construction;
- unsupported contract rejection;
- deterministic quantity rounding and notional calculation;
- minimum quantity/notional and maximum quantity enforcement;
- valid and invalid `SamplingSpec` count/duration relationships.

### Identity tests

- explicit snapshot/calibration mismatch fails;
- proposed-order mismatch fails before Risk;
- working-order mismatch fails before reconciliation;
- Tier-2 derived snapshot and intents carry Dataset identity;
- explicit `InstrumentSpec` rejects the legacy sentinel.

### Feature tests

- each feature can be independently enabled/disabled;
- order-book readiness is required only when order-book reference is active;
- funding readiness is required only when funding bias is active;
- disabling inventory control produces zero reservation shift and unit side scales;
- stage presets map to the historical feature combinations.

### Directional-profile tests

- the explicit long-biased profile matches legacy target behavior;
- short-biased behavior is the sign mirror under mirrored trend and position;
- flat baseline remains flat absent funding adjustment;
- both reversal directions require flat-before-reverse;
- target and opposite target cannot exceed capacity.

### Sampling tests

- exact cadence passes;
- cadence outside tolerance fails;
- matured markout/OFI horizon mismatch fails;
- future labels remain causally excluded.

### Regression and verification

- focused unit tests during implementation;
- full Core tests;
- architecture tests;
- strict mypy;
- Ruff format/lint;
- Research/Integration tests with pinned runtimes;
- all deterministic Evidence runners repeated across fresh processes;
- final PR head compared against `main` and checked for temporary files/workflows.

## 11. Migration plan

1. Add contract modules and failing tests.
2. Propagate compatibility identity fields.
3. Add instrument validation to ladder, Risk/Application, and Tier-2 boundaries.
4. Add sampling validation to foundation and universal calibration.
5. Add independent feature resolution while preserving stage presets.
6. Add signed profile helpers and calibrated-path opt-in.
7. Extend generalization and architecture tests.
8. Update README and CI type-check scope.
9. Run complete verification and review the full diff.

## 12. Success criteria

The change is complete when:

- the same core supports explicit long, flat, and short baseline profiles;
- features are independently ablatable without editing strategy code;
- explicit instrument identities cannot be mixed across calibration, strategy, Risk, or reconciliation;
- linear-perpetual execution constraints are validated before order submission;
- time-window semantics are explicit and enforced for generalized runs;
- current long-biased behavior remains the default compatibility behavior;
- all Core and Research gates pass on one final commit;
- the repository remains **RESEARCH / NO-GO FOR PRODUCTION**.
