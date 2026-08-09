# Universal Calibrated Strategy Integration — Implementation Plan

Date: 2026-08-09
Branch: `universal-calibrated-strategy-integration`
Base: `f8ec9ce6736dfac236075a78a1adcd65228bdee9`
Scope: Phase C — Application composition of Foundation + Microstructure calibration + Risk sizing into existing S2-S7 mechanics
Production status: RESEARCH / NO-GO

## Goal

Complete migration steps 8-10 from the approved universal causal market calibration design without deleting or rewriting the deterministic mechanics fixtures:

1. add a calibrated policy-input adapter in Application;
2. make maintained calibrated research orchestration consume causal calibration + risk-derived capacity rather than symbol-specific absolute strategy values;
3. retain existing fixture constructors/runners as explicit mechanics/reproducibility regressions;
4. add cross-instrument/symbol-disjoint experiment contracts without claiming profitability;
5. preserve Hard Risk authority and the current Long -> Flat -> Short reversal contract.

This phase is an integration/generalization-mechanics phase. It does not establish historical alpha, sealed-OOS profitability, or production authorization.

## Architecture decision

Use Application as the composition boundary:

```text
Foundation Calibration ---------+
                                |
Microstructure Calibration -----+--> Application calibrated-input adapter
                                |              |
RiskSizing / InventoryCapacity -+              +--> existing Strategy API
                                               |
Venue metadata (tick/lot) ------+              +--> existing Hard Risk
```

Do not make Strategy import account/runtime adapters. Do not make Calibration import Strategy or Risk. Keep legacy Strategy functions pure and keep existing fixture paths intact.

### Why an Application adapter

The approved design explicitly assigns Application responsibility for composing calibrated market state, normalized risk capacity, Strategy, and Hard Risk veto. Existing Strategy APIs already encode flat-before-reverse, cancel-before-replace-compatible ladder semantics, stage gates, and deterministic fixture behavior. Reusing those APIs minimizes migration risk.

### Compatibility principle

Existing absolute fixture values are retained only as **dimensionless shape templates** when the calibrated path is used:

- `base_long_target / max_abs_target` becomes a target fraction;
- `order_quantity / max_abs_inventory` becomes a per-level quantity fraction;
- `max_short_target / max_abs_inventory` becomes a short-cap fraction.

The live/research absolute quantity is re-materialized from Risk-derived `InventoryCapacity.q_max` on every calibrated decision. Calibration never widens `q_max`.

## Stage-aware readiness

Fail closed according to the active stage rather than requiring every possible signal for every stage:

- S2: volatility + execution-cost floor;
- S3: S2 + risk-derived inventory capacity;
- S4: S3 + normalized trend;
- S5: S4 + normalized trend (short decision remains flat-before-reverse);
- S6: S5 + normalized funding score;
- S7: S6 + ready microstructure estimate / order-book score / microprice displacement.

A missing component required by the selected stage returns an explicit not-ready composition result; it does not substitute a symbol-specific constant.

## Canonical calibrated mapping

### Market snapshot

Create a Strategy-facing `MarketSnapshot` from the current causal top-of-book/position input but replace `realized_volatility` with Foundation `volatility_scale`.

### S2 spacing

Keep globally frozen, dimensionless/relative meta-parameters such as volatility multiplier and min/max relative spacing bounds. Replace the fixture execution floor with:

`execution_cost_floor_bps = microstructure.execution.execution_cost_floor * 10_000`

The existing `propose_volatility_spacing()` then remains the authoritative spacing mechanics implementation.

### Inventory quantity

Let:

`usable_capacity = InventoryCapacity.q_max * capacity_utilization_fraction`

with `capacity_utilization_fraction in (0, 1]` globally frozen.

Preserve template ratios while replacing absolute coin quantities:

- `ladder.max_abs_inventory = usable_capacity`
- `inventory.max_abs_target = usable_capacity`
- `funding.max_abs_target = usable_capacity`
- `inventory.base_long_target = usable_capacity * template_base_long_fraction`
- `short.max_short_target = usable_capacity * template_short_fraction`
- `ladder.order_quantity = usable_capacity * template_order_fraction`

All resulting quantities must be positive, internally consistent, and never exceed `InventoryCapacity.q_max`.

### Funding

Foundation already produces a normalized funding score in `[-1, 1]`. To preserve the existing funding-bias equation without a symbol-specific divisor:

- Strategy `AdaptiveSignals.funding_rate = calibrated funding_score`
- materialized `FundingBiasConfig.funding_scale = 1`

This makes the existing normalization an identity mapping while preserving its clipping and flat-before-reverse behavior.

### Order book / microprice

For S7:

- `AdaptiveSignals.order_book_imbalance = microstructure.order_book_score`
- reconstruct a Strategy-facing absolute microprice as `mid * (1 + microprice_relative_displacement)`
- derive the maximum imbalance shift in relative units from the same volatility scale used by the microstructure score:

`imbalance_shift_bps = volatility_scale * score_scale_vol_units * 10_000`

The product `order_book_score * imbalance_shift_bps` therefore represents a volatility-scaled impact rather than a symbol-specific fixed bps constant. Existing `microprice_weight` remains a globally frozen dimensionless meta-parameter.

## Task 1 — Deterministic Strategy-facing market primitive

**Files**

- Modify: `src/grid_trade/domain/market.py`
- Modify: `tests/domain/test_market.py` or the existing MarketSnapshot test file

**RED**

Add a regression proving `MarketSnapshot.mid` is exactly invariant to ambient Decimal precision, using a long-decimal bid/ask pair.

**GREEN**

Use `deterministic_decimal_context()` in `MarketSnapshot.mid` and any evidence-sensitive midpoint arithmetic added by Phase C.

**Verify**

`uv run --frozen --extra dev pytest -q tests/domain -k market`

## Task 2 — Calibrated composition contracts and readiness

**Files**

- Create: `src/grid_trade/application/calibrated_policy_inputs.py`
- Create: `tests/application/test_calibrated_policy_inputs.py`
- Modify: `src/grid_trade/application/__init__.py`

**Contracts**

- `CalibratedPolicyInputConfig`
  - globally frozen `capacity_utilization_fraction`;
  - optional global dimensionless/relative stage meta-parameters only;
- `CalibratedPolicyInputStatus`
  - ready flag;
  - explicit reason;
  - required stage;
- `CalibratedAdaptiveInputs`
  - Strategy-facing `MarketSnapshot`;
  - `AdaptiveSignals`;
  - materialized `AdaptiveGridPolicyConfig`;
  - `InventoryCapacity` trace;
  - status/readiness provenance.

**RED cases**

- S2 refuses missing volatility or execution-cost calibration;
- S4/S5 refuse missing trend;
- S6 refuses missing funding score;
- S7 refuses incomplete microstructure readiness;
- changing only instrument ID cannot change numeric composed inputs;
- config must be finite/bounded and frozen by the caller/research orchestration.

**GREEN**

Implement only validation/readiness and immutable output contracts first. No Strategy call in this task.

## Task 3 — Risk-derived quantity materialization

**Files**

- Modify: `src/grid_trade/application/calibrated_policy_inputs.py`
- Modify: `tests/application/test_calibrated_policy_inputs.py`

**RED cases**

- materialized max inventory equals `q_max * utilization`;
- max inventory never exceeds q_max;
- template base-long/order/short ratios are preserved;
- `ladder.max_abs_inventory == inventory.max_abs_target == funding.max_abs_target`;
- short cap never exceeds max inventory;
- price rescaling plus corresponding equity/notional/venue-unit conversion preserves normalized target/order fractions;
- ambient Decimal precision cannot change quantities.

**GREEN**

Add one pure materialization helper. Do not change `risk.sizing` or Hard Risk authority.

## Task 4 — Calibrated spacing and AdaptiveSignals mapping

**Files**

- Modify: `src/grid_trade/application/calibrated_policy_inputs.py`
- Modify: `tests/application/test_calibrated_policy_inputs.py`

**RED cases**

- calibrated volatility replaces fixture `MarketSnapshot.realized_volatility`;
- execution floor equals relative execution cost converted to bps;
- normalized funding score maps through `funding_scale=1` exactly;
- S7 order-book signal equals calibrated bounded score;
- reconstructed microprice uses relative displacement;
- derived order-book shift equals `volatility_scale * score_scale_vol_units * 10_000`;
- no BTC/ETH/SOL branch or symbol-based parameter lookup exists.

**GREEN**

Materialize Strategy-facing inputs by `dataclasses.replace()` of the mechanics template. Keep global dimensionless policy behavior settings unchanged.

## Task 5 — Application calibrated transitions

**Files**

- Create: `src/grid_trade/application/calibrated_adaptive_grid.py`
- Create: `tests/application/test_calibrated_adaptive_grid.py`
- Modify: `src/grid_trade/application/__init__.py`

**API**

Add calibrated wrappers rather than changing legacy transition signatures:

- `transition_calibrated_adaptive_grid(...)`
- optional S2-focused wrapper if needed by existing S2 research boundary.

The wrapper:

1. composes calibrated inputs;
2. fails closed before Strategy if stage readiness is insufficient;
3. calls the existing `transition_adaptive_grid()` when ready;
4. leaves Hard Risk evaluation in the existing Application/passive-policy path;
5. returns composition provenance together with the existing transition.

**RED cases**

- not-ready calibration submits/cancels nothing and does not advance Strategy state;
- ready calibrated path produces the same mechanical decision as manually supplying the equivalent derived inputs;
- Hard Risk veto remains authoritative;
- `Long -> Short` cannot occur without a Flat decision even through the calibrated wrapper;
- capacity materialization cannot override Risk limits.

## Task 6 — Calibrated Evidence contract

**Files**

- Modify: `src/grid_trade/evidence/events.py`
- Create: `src/grid_trade/research/calibrated_adaptive_runner.py`
- Create: `tests/research/test_calibrated_adaptive_runner.py`

Add an append-only Evidence kind such as `CALIBRATED_POLICY_INPUT`; do not rename or change existing Evidence values.

Evidence must record:

- instrument/source identity for traceability;
- Foundation readiness and normalized values consumed;
- Microstructure readiness and values consumed;
- full `RiskSizingInput`, `InventoryCapacity`, and binding constraint;
- globally frozen adapter config;
- template quantity ratios and final materialized quantities;
- derived spacing floor and S7 shift scale;
- Strategy stage and exact `AdaptiveSignals` consumed;
- before/after strategy state digest or canonical state payload;
- explicit economics/alpha/production NO-GO flags.

The calibrated synthetic runner must replay the same observations twice and produce an identical SHA-256 digest.

## Task 7 — Compatibility and metamorphic gates

**Files**

- Create: `tests/application/test_calibrated_policy_metamorphic.py`
- Modify: `tests/research/test_calibrated_adaptive_runner.py`
- Modify: `.github/workflows/research.yml`

Required gates:

1. old S0/S1/S2/Adaptive fixture digests remain byte-for-byte unchanged;
2. Phase B microstructure digest remains unchanged;
3. calibrated path is deterministic across fresh processes;
4. changing only symbol ID changes trace identity only, never numeric policy inputs/decisions;
5. proportional price/tick/notional/quantity-unit rescaling produces economically equivalent normalized decisions after unit conversion;
6. config permutation/input evidence ordering cannot change a causal result;
7. Hard Risk cannot be widened by calibration.

Pin the new calibrated-integration digest only after the complete Evidence payload is reviewed.

## Task 8 — Symbol-disjoint / sealed split research harness

**Files**

- Create: `src/grid_trade/research/generalization.py`
- Create: `tests/research/test_generalization.py`

This task builds experiment orchestration, not performance claims.

Contracts:

- immutable `InstrumentSplit(train, validation, test)` with pairwise-disjoint instrument IDs;
- immutable globally frozen meta-parameter identity/digest;
- train may fit globally allowed meta-parameters;
- validation may select among already-defined candidates;
- sealed test accepts a frozen selected config only;
- per-instrument online calibration state remains separate;
- instrument ID is never a predictive numerical feature.

**RED cases**

- overlap across train/validation/test fails closed;
- changing test data cannot affect selected config before test evaluation;
- symbol reorder does not change split digest;
- a per-symbol override table is rejected from maintained calibrated orchestration;
- same frozen adapter/calibration config can instantiate all split instruments.

No Tier-1/OHLC result may promote microstructure-dependent components.

## Task 9 — Architecture and self-review

**Files**

- Modify only if needed: `tests/architecture/test_boundaries.py`
- Update: `README.md`
- Create: `docs/superpowers/reviews/2026-08-09-universal-calibrated-strategy-integration-review.md`

Review questions:

- Does Strategy remain free of account/runtime adapter dependencies?
- Does Application own Calibration + Risk composition as approved?
- Can any symbol string alter numerical composition?
- Can missing calibration silently fall back to fixture absolute constants?
- Can quantity materialization exceed `InventoryCapacity.q_max` or Hard Risk?
- Can S5 bypass flat-before-reverse?
- Can future/unmatured data alter current inputs?
- Are all Evidence-sensitive Decimal operations context-independent?
- Are fixture paths explicitly mechanics-only?
- Is any historical/economic claim being made from synthetic data?

Any reproducible issue found during self-review receives a failing regression test before the fix.

## Task 10 — Final verification

Run on the exact final branch HEAD including README/review docs:

### Core

```bash
uv lock --check
uv run --frozen --extra dev ruff format --check --diff .
uv run --frozen --extra dev ruff check .
uv run --frozen --extra dev pytest -q --ignore=tests/research --ignore=tests/integrations
uv run --frozen --extra dev mypy src tests --exclude '^tests/(research|integrations)/'
```

### Research

```bash
uv sync --extra dev --extra research --frozen
uv run --frozen --extra dev --extra research pytest -m research -q tests/research tests/integrations
```

Research workflow must also verify all historical fresh-process Evidence digests plus the new calibrated-integration digest.

## Phase C exit criteria

Phase C is review-complete only if all are true:

- a maintained Application path consumes Foundation + Microstructure + RiskSizing outputs;
- Strategy receives no symbol-specific absolute calibration table;
- absolute inventory/order quantities are re-materialized from risk-derived capacity;
- S2 execution floor and S7 order-book shift are data-derived relative values;
- funding uses the normalized calibrated score rather than a fixed symbol divisor;
- stage-aware readiness fails closed;
- Hard Risk remains authoritative and independent;
- Long -> Flat -> Short remains mandatory;
- old five Evidence digests remain unchanged;
- new calibrated integration Evidence is deterministic;
- symbol/scale metamorphic tests pass;
- a symbol-disjoint sealed-split harness exists with frozen meta-parameter boundaries;
- exact final Core and Research workflows pass;
- production, economics, and alpha remain explicitly NO-GO.
