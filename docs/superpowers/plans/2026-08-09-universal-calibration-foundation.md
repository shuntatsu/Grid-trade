# Universal Calibration Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the instrument-agnostic causal calibration foundation for volatility, trend, funding normalization, readiness, and risk-derived normalized inventory capacity without changing S0-S7 strategy mechanics yet.

**Architecture:** Add a new `grid_trade.calibration` package containing immutable causal contracts plus pure/stateful estimators. Keep account/risk sizing in `grid_trade.risk.sizing` so Calibration never sees account permissions or widens hard risk. This plan intentionally does not implement GLFT `A,k`, adverse-selection markout calibration, OFI/depth regression, or S2-S7 strategy integration; those are independent follow-on plans.

**Tech Stack:** Python 3.12, `Decimal`, frozen dataclasses, standard-library `statistics`/`decimal`/`datetime`, pytest, Hypothesis where invariants benefit, Ruff, strict mypy, existing GitHub Actions.

## Global Constraints

- License remains LGPL-3.0.
- Production status remains RESEARCH / NO-GO.
- No strategy/calibration behavior may branch on instrument symbol.
- Calibration consumes only observations available at or before the decision timestamp.
- Missing optional features are represented explicitly as unavailable, never zero-filled as observed values.
- Hard Risk and account-derived sizing remain outside `grid_trade.calibration`.
- Meta-parameters are frozen experiment inputs; rolling market state may update causally.
- No OHLC-derived substitute is allowed for L2-only or fill-only calibration components.
- Existing S0-S7 deterministic mechanics and public APIs must remain backward compatible in this plan.
- Calibration warm-up failure is fail-closed (`ready=False`); no symbol-specific default may be injected.
- Exact symbol strings may be retained for evidence identity, but changing only the symbol string with identical numeric observations must not alter numeric calibration outputs.

---

### Task 1: Immutable calibration contracts and readiness model

**Files:**
- Create: `src/grid_trade/calibration/__init__.py`
- Create: `src/grid_trade/calibration/contracts.py`
- Create: `tests/calibration/test_contracts.py`
- Modify: `tests/architecture/test_import_boundaries.py`

**Interfaces:**
- Produces `CalibrationReadiness(StrEnum)` with `NOT_READY`, `PARTIAL`, `READY`.
- Produces `CalibrationObservation(timestamp, source_id, instrument_id, mid, funding_rate)`.
- Produces `CalibrationComponentStatus(ready, sample_count, reason)`.
- Produces `CalibratedMarketState(timestamp, source_id, instrument_id, volatility_scale, trend_score, funding_score, quote_distance_scale, execution_cost_floor, order_book_score, estimated_microprice_displacement, volatility_status, trend_status, funding_status, microstructure_status)`.
- `funding_rate`, `quote_distance_scale`, `execution_cost_floor`, `order_book_score`, and `estimated_microprice_displacement` use `None` when unavailable.

- [ ] **Step 1: Write failing validation and invariance tests**

```python
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from grid_trade.calibration import CalibrationObservation, CalibratedMarketState


def test_observation_requires_positive_mid_and_aware_timestamp() -> None:
    with pytest.raises(ValueError, match="timestamp"):
        CalibrationObservation(
            timestamp=datetime(2026, 8, 9),
            source_id="fixture",
            instrument_id="AAA-PERP",
            mid=Decimal("100"),
            funding_rate=None,
        )


def test_unavailable_optional_components_remain_none() -> None:
    state = CalibratedMarketState.not_ready(
        timestamp=datetime(2026, 8, 9, tzinfo=UTC),
        source_id="fixture",
        instrument_id="AAA-PERP",
    )
    assert state.quote_distance_scale is None
    assert state.execution_cost_floor is None
    assert state.order_book_score is None
    assert state.estimated_microprice_displacement is None
```

Also assert non-finite `Decimal` values fail closed, empty source/instrument IDs fail, normalized scores must lie in `[-1, 1]`, and relative-price scales cannot be negative.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `uv run --frozen --extra dev pytest -q tests/calibration/test_contracts.py`

Expected: FAIL because `grid_trade.calibration` does not exist.

- [ ] **Step 3: Implement minimal frozen contracts**

Use `@dataclass(frozen=True, slots=True)` for every contract. `CalibratedMarketState.not_ready(...)` must construct a state with all optional calibrated values unavailable and component statuses explicitly `ready=False`.

- [ ] **Step 4: Extend architecture test**

Add AST assertions that modules under `grid_trade.calibration` do not import from `grid_trade.application`, `grid_trade.execution`, `grid_trade.integrations`, or `grid_trade.research`. Keep existing Strategy/Risk/Execution boundaries unchanged.

- [ ] **Step 5: Verify GREEN**

Run: `uv run --frozen --extra dev pytest -q tests/calibration/test_contracts.py tests/architecture/test_import_boundaries.py`

Expected: PASS.

- [ ] **Step 6: Commit**

Commit message: `feat: add universal calibration contracts`.

---

### Task 2: Robust rolling log-return volatility estimator

**Files:**
- Create: `src/grid_trade/calibration/volatility.py`
- Create: `tests/calibration/test_volatility.py`
- Modify: `src/grid_trade/calibration/__init__.py`

**Interfaces:**
- Consumes `CalibrationObservation` from Task 1.
- Produces `RobustVolatilityConfig(window: int, min_samples: int, mad_scale: Decimal)`.
- Produces `RobustVolatilityState(prices: tuple[Decimal, ...])`.
- Produces `VolatilityEstimate(scale: Decimal | None, sample_count: int, ready: bool)`.
- Produces `update_robust_volatility(state, observation, config) -> tuple[RobustVolatilityState, VolatilityEstimate]`.

**Estimator:** for positive mids `p_i`, use log returns `r_i = ln(p_i / p_{i-1})`; center by median return; compute `MAD = median(|r_i - median(r)|)`; relative volatility scale is `mad_scale * MAD`. `mad_scale` defaults through config tests to `Decimal("1.4826")`. If MAD is zero after warm-up, return `scale=Decimal(0)` and `ready=True`; consumers decide whether zero scale is sufficient.

- [ ] **Step 1: Write failing estimator tests**

```python
def test_volatility_is_scale_invariant_to_price_level() -> None:
    a = _run_vol(["100", "101", "100", "102", "101"])
    b = _run_vol(["1000", "1010", "1000", "1020", "1010"])
    assert a.scale == b.scale


def test_volatility_is_not_ready_before_min_samples() -> None:
    estimate = _run_vol(["100", "101"], min_samples=4)
    assert estimate.ready is False
    assert estimate.scale is None
```

Also test deterministic window truncation, invalid config, duplicate flat prices yielding zero scale, and non-monotonic timestamps rejected by a caller-facing update sequence helper.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `uv run --frozen --extra dev pytest -q tests/calibration/test_volatility.py`

Expected: FAIL because `volatility.py` is absent.

- [ ] **Step 3: Implement the exact Decimal estimator**

Use `Decimal.ln()` for log returns. Implement a deterministic `_median_decimal(values)` helper local to the calibration package (or a focused private utility if reused by Task 4); do not convert to float.

- [ ] **Step 4: Verify GREEN including property test**

Run: `uv run --frozen --extra dev pytest -q tests/calibration/test_volatility.py`

Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `feat: add robust causal volatility calibration`.

---

### Task 3: Dimensionless causal trend normalization

**Files:**
- Create: `src/grid_trade/calibration/trend.py`
- Create: `tests/calibration/test_trend.py`
- Modify: `src/grid_trade/calibration/__init__.py`

**Interfaces:**
- Consumes a causal price history plus current `VolatilityEstimate`.
- Produces `TrendCalibrationConfig(horizon: int, transform_gain: Decimal, min_volatility_scale: Decimal, max_abs_z: Decimal)`.
- Produces `TrendEstimate(z_score: Decimal | None, score: Decimal | None, ready: bool)`.
- Produces `estimate_normalized_trend(prices, volatility, config) -> TrendEstimate`.

**Formula:** `horizon_return = ln(p_t / p_{t-h})`; denominator is `max(volatility_scale, min_volatility_scale) * sqrt(horizon)`; `z = clip(horizon_return / denominator, -max_abs_z, max_abs_z)`; `score = tanh(transform_gain * z)` implemented deterministically with Decimal exponentials and bounded input.

- [ ] **Step 1: Write failing scale-invariance and sign tests**

```python
def test_trend_is_invariant_to_multiplying_all_prices() -> None:
    a = _trend(["100", "101", "102", "103"], vol="0.01")
    b = _trend(["1000", "1010", "1020", "1030"], vol="0.01")
    assert a.score == b.score


def test_trend_sign_tracks_direction() -> None:
    assert _trend(["100", "101", "102", "103"], vol="0.01").score > 0
    assert _trend(["103", "102", "101", "100"], vol="0.01").score < 0
```

Also test score bound `[-1,1]`, zero/near-zero volatility uses only the dimensionless configured floor, insufficient horizon is not ready, and no symbol input exists in the estimator signature.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `uv run --frozen --extra dev pytest -q tests/calibration/test_trend.py`

Expected: FAIL because `trend.py` is absent.

- [ ] **Step 3: Implement Decimal-only normalization and bounded tanh**

Implement `_decimal_tanh(x)` as `(exp(2x)-1)/(exp(2x)+1)` after clipping `x` to `[-20, 20]` to avoid unnecessary extreme exponentials. `20` is a dimensionless numerical stability bound, not a market threshold.

- [ ] **Step 4: Verify GREEN**

Run: `uv run --frozen --extra dev pytest -q tests/calibration/test_trend.py`

Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `feat: add normalized trend calibration`.

---

### Task 4: Robust rolling funding normalization

**Files:**
- Create: `src/grid_trade/calibration/funding.py`
- Create: `src/grid_trade/calibration/_robust.py`
- Create: `tests/calibration/test_funding.py`
- Modify: `src/grid_trade/calibration/volatility.py`
- Modify: `src/grid_trade/calibration/__init__.py`

**Interfaces:**
- `_robust.py` produces deterministic `median_decimal(values)` and `mad_decimal(values)` used by volatility and funding.
- Produces `FundingCalibrationConfig(window: int, min_samples: int, mad_scale: Decimal, clip_z: Decimal)`.
- Produces `FundingCalibrationState(values: tuple[Decimal, ...])`.
- Produces `FundingEstimate(center: Decimal | None, scale: Decimal | None, z_score: Decimal | None, score: Decimal | None, ready: bool, degenerate: bool)`.
- Produces `update_funding_calibration(state, funding_rate, config) -> tuple[FundingCalibrationState, FundingEstimate]`.

**Behavior:** use rolling median and scaled MAD. If insufficient observations: not ready. If robust scale is zero after warm-up: `ready=False`, `degenerate=True`, normalized score unavailable (`None`), never divide by zero and never silently substitute a fixed `funding_scale`.

- [ ] **Step 1: Write failing robust-normalization tests**

```python
def test_constant_funding_is_explicitly_degenerate() -> None:
    result = _run_funding(["0.0001"] * 8)
    assert result.degenerate is True
    assert result.ready is False
    assert result.score is None


def test_funding_score_is_location_and_scale_normalized() -> None:
    a = _run_funding(["0", "1", "2", "3", "4"], decimal_shift=3)
    b = _run_funding(["10", "12", "14", "16", "18"], decimal_shift=3)
    assert a.score == b.score
```

Also test clipping, missing funding (`None`) leaves rolling state unchanged, window truncation, and invalid config.

- [ ] **Step 2: Verify RED**

Run: `uv run --frozen --extra dev pytest -q tests/calibration/test_funding.py`

Expected: FAIL because funding calibration is absent.

- [ ] **Step 3: Extract robust helpers and implement funding estimator**

Refactor Task 2 to use `_robust.py` without changing Task 2 outputs.

- [ ] **Step 4: Verify GREEN and volatility regression**

Run: `uv run --frozen --extra dev pytest -q tests/calibration/test_funding.py tests/calibration/test_volatility.py`

Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `feat: add robust funding normalization`.

---

### Task 5: Risk-derived universal inventory capacity

**Files:**
- Create: `src/grid_trade/risk/sizing.py`
- Create: `tests/risk/test_sizing.py`
- Modify: `src/grid_trade/risk/__init__.py`
- Modify: `tests/architecture/test_import_boundaries.py`

**Interfaces:**
- Produces `RiskSizingConfig(max_notional_fraction: Decimal, max_single_move_loss_fraction: Decimal, volatility_floor: Decimal)`.
- Produces `RiskSizingInput(equity, reference_price, volatility_scale, max_margin_notional, venue_max_quantity)`.
- Produces `InventoryCapacity(q_notional, q_margin, q_volatility, q_venue, q_max, binding_constraint)`.
- Produces `derive_inventory_capacity(inputs, config) -> InventoryCapacity`.

**Formula:**
- `q_notional = equity * max_notional_fraction / reference_price`
- `q_margin = max_margin_notional / reference_price`
- `q_volatility = equity * max_single_move_loss_fraction / (reference_price * max(volatility_scale, volatility_floor))`
- `q_venue = venue_max_quantity`
- `q_max = min(q_notional, q_margin, q_volatility, q_venue)`

This plan intentionally does not model leverage optimization. All limits are upper bounds; Strategy may only consume normalized fractions of `q_max`.

- [ ] **Step 1: Write failing dimensional and binding-constraint tests**

```python
def test_quantity_capacity_scales_inverse_to_price() -> None:
    low = derive_inventory_capacity(_input(price="100"), _config())
    high = derive_inventory_capacity(_input(price="200"), _config())
    assert high.q_notional == low.q_notional / 2


def test_most_conservative_constraint_binds() -> None:
    result = derive_inventory_capacity(_input(venue_max_quantity="0.01"), _config())
    assert result.q_max == Decimal("0.01")
    assert result.binding_constraint == "venue"
```

Also test non-positive equity/price, non-finite values, zero venue capacity, and `q_max` never exceeds any component.

- [ ] **Step 2: Verify RED**

Run: `uv run --frozen --extra dev pytest -q tests/risk/test_sizing.py`

Expected: FAIL because `risk.sizing` is absent.

- [ ] **Step 3: Implement pure Decimal sizing**

Keep this module independent from calibration implementation details: it consumes only a numerical relative `volatility_scale` and account/risk inputs.

- [ ] **Step 4: Extend architecture boundaries**

Assert `grid_trade.calibration` never imports `grid_trade.risk.sizing`; application code will combine them in a later plan. `risk.sizing` may import only domain/value utilities, not Strategy/Application/Execution/Research.

- [ ] **Step 5: Verify GREEN**

Run: `uv run --frozen --extra dev pytest -q tests/risk/test_sizing.py tests/architecture/test_import_boundaries.py`

Expected: PASS.

- [ ] **Step 6: Commit**

Commit message: `feat: add risk-derived inventory capacity`.

---

### Task 6: Foundation calibration engine and metamorphic symbol-invariance gate

**Files:**
- Create: `src/grid_trade/calibration/engine.py`
- Create: `tests/calibration/test_engine.py`
- Modify: `src/grid_trade/calibration/__init__.py`
- Modify: `tests/architecture/test_import_boundaries.py`

**Interfaces:**
- Produces `CalibrationEngineConfig(volatility, trend, funding)`.
- Produces `CalibrationEngineState(observations, volatility_state, funding_state, generation, last_timestamp)` with bounded price history sufficient for the largest configured horizon/window.
- Produces `CalibrationUpdate(previous_state, next_state, market_state)`.
- Produces `update_calibration_engine(state, observation, config) -> CalibrationUpdate`.

**Foundation readiness:**
- Volatility and trend are mandatory for foundation `READY`.
- Funding is optional at the foundation level; missing/degenerate funding remains explicitly unavailable and yields component `ready=False` without blocking volatility/trend readiness.
- Microstructure fields remain `None` and `microstructure_status.ready=False` in this plan.
- Generation increments exactly once per accepted strictly newer observation.
- Equal/out-of-order timestamps fail closed.

- [ ] **Step 1: Write failing end-to-end causal tests**

```python
def test_engine_becomes_ready_after_warmup() -> None:
    update = _run_engine(_observations("AAA-PERP", prices=["100", "101", "100", "102", "103"]))
    assert update.market_state.volatility_status.ready is True
    assert update.market_state.trend_status.ready is True


def test_symbol_name_does_not_change_numeric_output() -> None:
    a = _run_engine(_observations("AAA-PERP", prices=["100", "101", "100", "102", "103"]))
    b = _run_engine(_observations("BTCUSDT-PERP", prices=["100", "101", "100", "102", "103"]))
    assert a.market_state.volatility_scale == b.market_state.volatility_scale
    assert a.market_state.trend_score == b.market_state.trend_score
    assert a.market_state.funding_score == b.market_state.funding_score
```

Also test same ordered observations produce identical frozen states, source/instrument identity is preserved only as metadata, stale timestamps fail, and microstructure values are never fabricated.

- [ ] **Step 2: Verify RED**

Run: `uv run --frozen --extra dev pytest -q tests/calibration/test_engine.py`

Expected: FAIL because engine module is absent.

- [ ] **Step 3: Implement orchestration using Tasks 2-4 only**

The engine must call existing estimator functions; do not duplicate their formulas. Keep price history capped to `max(volatility.window + 1, trend.horizon + 1)`.

- [ ] **Step 4: Verify GREEN**

Run: `uv run --frozen --extra dev pytest -q tests/calibration/test_engine.py tests/calibration/test_contracts.py tests/calibration/test_volatility.py tests/calibration/test_trend.py tests/calibration/test_funding.py`

Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `feat: add universal calibration foundation engine`.

---

### Task 7: Foundation regression, deterministic evidence compatibility, and documentation

**Files:**
- Create: `tests/calibration/test_symbol_invariance.py`
- Modify: `README.md`
- Create: `docs/superpowers/reviews/2026-08-09-universal-calibration-foundation-review.md`
- Modify: `.github/workflows/ci.yml` only if calibration paths are not already covered by the core test command.

**Interfaces:**
- No new runtime API beyond Tasks 1-6.
- Establishes regression gates and documentation for the next microstructure plan.

- [ ] **Step 1: Add metamorphic/property tests**

Test that for a fixed numeric observation stream:
- changing `instrument_id` changes identity fields only;
- multiplying every mid price by a positive constant leaves volatility/trend normalized outputs equal within exact Decimal arithmetic where the price ratios are exactly representable;
- risk sizing quantities change inversely with price while normalized fractions remain external strategy concerns;
- no calibration module contains a literal branch on known symbol strings such as `BTC`, `ETH`, or `SOL` (AST/string guard limited to production calibration modules, not docs/tests).

- [ ] **Step 2: Run full core suite**

Run: `uv run --frozen --extra dev pytest -q`

Expected: all existing S0-S7 plus new foundation tests PASS.

- [ ] **Step 3: Run static gates**

Run:
- `uv run --frozen --extra dev ruff format --check src tests`
- `uv run --frozen --extra dev ruff check src tests`
- `uv run --frozen --extra dev mypy src tests`

Expected: PASS / zero issues.

- [ ] **Step 4: Re-run existing research integration suite**

Run the repository's existing Research Integration workflow commands (including pinned `hftbacktest==2.4.4`, `nautilus_trader==1.230.0`, and S0/S1/S2/Adaptive fresh-process digest checks) without changing their expected historical digests in this foundation plan.

Expected: existing research tests PASS and historical mechanics digests remain unchanged.

- [ ] **Step 5: Update README**

Document:
- fixed S0-S7 fixtures are deterministic mechanics fixtures, not universal market parameters;
- `grid_trade.calibration` is the new instrument-agnostic causal layer;
- Foundation currently calibrates volatility/trend/funding only;
- GLFT intensity, execution markout floor, OFI/depth, and strategy consumption are explicitly next phases;
- Production remains NO-GO.

- [ ] **Step 6: Write architecture/self-review record**

Record verification of:
- no symbol branching;
- calibration/risk-sizing separation;
- no future-data inputs;
- no fabricated L2/fill features;
- existing mechanics unchanged;
- no TODO/TBD/FIXME/HACK in production changes;
- dependency direction preserved.

- [ ] **Step 7: Fresh final CI and commit**

Push the final branch head and require fresh Core CI and Research Integration success before opening/merging a PR.

Commit message: `docs: verify universal calibration foundation`.

---

## Follow-on plan boundaries

After this plan is GREEN, create and execute two separate plans rather than expanding this one:

1. **Universal Microstructure Calibration** — causal GLFT-style `A,k`, execution-cost/adverse-selection floor, OFI/depth/microprice estimation, readiness/fit quality, Tier-2 replay evidence.
2. **Calibrated Strategy Integration & Generalization** — map calibrated outputs and risk-derived `Q_max` into S2-S7, normalized inventory fractions, fixture-vs-calibrated orchestration separation, cross-instrument/symbol-disjoint walk-forward and sealed OOS research harness.

These plans may consume the APIs defined here but must not weaken Hard Risk or flat-before-reverse semantics.
