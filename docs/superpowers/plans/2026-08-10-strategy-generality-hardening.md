# Strategy Generality Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing calibrated adaptive grid safely reusable across explicit linear-perpetual instruments, observation cadences, independent feature combinations, and signed directional profiles while preserving current long-biased behavior as the default.

**Architecture:** Add four narrow contracts—`InstrumentSpec`, `SamplingSpec`, `AdaptiveFeatures`, and `DirectionalTargetProfileConfig`—then propagate them through existing Domain, Calibration, Strategy, Application, Risk/reconciliation, and Tier-2 boundaries. Compatibility constructors and stage presets remain available, but explicit generalized runs fail closed on identity, cadence, contract, and minimum-order violations.

**Tech Stack:** Python 3.12, frozen/slotted dataclasses, `Decimal`, pytest 9.1.1, Hypothesis 6.165.2, Ruff 0.15.22, strict mypy 2.3.0, pinned `hftbacktest==2.4.4`, pinned `nautilus-trader==1.230.0`.

## Global Constraints

- Supported contract type is exactly `LINEAR_PERPETUAL`; unsupported types fail closed.
- One strategy/calibration state owns exactly one explicit instrument.
- Existing constructors and imports remain valid through compatibility defaults.
- Existing `AdaptiveStage` behavior remains the default when no explicit feature set is supplied.
- Current long-biased behavior remains the default when no explicit directional profile is supplied.
- Hard Risk, flat-before-reverse, Dataset acceptance, cancel-before-replace, and deterministic Evidence contracts are never relaxed.
- Generalized historical runs must supply explicit instrument and sampling contracts.
- No portfolio allocator, plugin registry, inverse contract, spot, option, new alpha signal, or production authorization is introduced.

---

## File map

### New files

- `src/grid_trade/domain/instrument.py` — linear-perpetual instrument contract and identity helpers.
- `src/grid_trade/calibration/sampling.py` — observation cadence and matured-label horizon contract.
- `src/grid_trade/strategy/features.py` — independent adaptive feature flags and stage presets.
- `src/grid_trade/strategy/target_profile.py` — signed baseline, de-risk, and conditional-reversal policy.
- `tests/domain/test_instrument.py`
- `tests/calibration/test_sampling.py`
- `tests/strategy/test_adaptive_features.py`
- `tests/strategy/test_directional_target_profile.py`
- `tests/application/test_instrument_bound_policy.py`
- `tests/research/test_tier2_instrument_sampling.py`

### Modified files

- `src/grid_trade/domain/__init__.py`
- `src/grid_trade/domain/market.py`
- `src/grid_trade/domain/orders.py`
- `src/grid_trade/calibration/__init__.py`
- `src/grid_trade/calibration/engine.py`
- `src/grid_trade/calibration/universal_engine.py`
- `src/grid_trade/strategy/__init__.py`
- `src/grid_trade/strategy/adaptive_grid.py`
- `src/grid_trade/strategy/adaptive_ladder.py`
- `src/grid_trade/strategy/inventory_target.py`
- `src/grid_trade/strategy/grid_geometry.py`
- `src/grid_trade/execution/reconcile.py`
- `src/grid_trade/application/passive_policy.py`
- `src/grid_trade/application/calibrated_adaptive.py`
- `src/grid_trade/research/tier2_calibrated_candidate.py`
- `tests/architecture/test_boundaries.py`
- `tests/architecture/test_module_ownership.py`
- `README.md`
- `.github/workflows/research.yml`

---

### Task 1: Explicit linear-perpetual instrument contract

**Files:**
- Create: `src/grid_trade/domain/instrument.py`
- Modify: `src/grid_trade/domain/__init__.py`
- Test: `tests/domain/test_instrument.py`

**Interfaces:**
- Produces:
  - `LEGACY_UNSPECIFIED_INSTRUMENT: str`
  - `ContractType(StrEnum)` with `LINEAR_PERPETUAL`
  - `InstrumentSpec`
  - `instruments_compatible(left: str, right: str) -> bool`
  - `require_instruments_compatible(left: str, right: str, *, context: str) -> None`
- `InstrumentSpec.floor_quantity(quantity: Decimal) -> Decimal`
- `InstrumentSpec.notional(quantity: Decimal, price: Decimal) -> Decimal`
- `InstrumentSpec.is_executable(quantity: Decimal, price: Decimal) -> bool`

- [ ] **Step 1: Write the failing contract tests**

```python
from decimal import Decimal

import pytest

from grid_trade.domain.instrument import ContractType, InstrumentSpec


def test_linear_perpetual_rounding_and_notional() -> None:
    spec = InstrumentSpec(
        instrument_id="BTC-PERP",
        contract_type=ContractType.LINEAR_PERPETUAL,
        contract_multiplier=Decimal("1"),
        tick_size=Decimal("0.1"),
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.002"),
        min_notional=Decimal("10"),
        max_quantity=Decimal("5"),
        funding_interval_seconds=3_600,
    )
    assert spec.floor_quantity(Decimal("0.0029")) == Decimal("0.002")
    assert spec.notional(Decimal("0.002"), Decimal("60000")) == Decimal("120.000")
    assert spec.is_executable(Decimal("0.002"), Decimal("60000"))


def test_instrument_rejects_unexecutable_and_unsupported_contracts() -> None:
    with pytest.raises(ValueError, match="instrument_id"):
        InstrumentSpec(
            instrument_id=" ",
            contract_type=ContractType.LINEAR_PERPETUAL,
            contract_multiplier=Decimal("1"),
            tick_size=Decimal("0.1"),
            quantity_step=Decimal("0.001"),
            min_quantity=Decimal("0.001"),
            min_notional=Decimal("1"),
            max_quantity=Decimal("1"),
            funding_interval_seconds=3_600,
        )
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
uv run --frozen --extra dev pytest -q tests/domain/test_instrument.py
```

Expected: collection fails with `ModuleNotFoundError: grid_trade.domain.instrument`.

- [ ] **Step 3: Implement the contract**

Use deterministic Decimal arithmetic. Require finite positive multiplier/tick/step/min/max values, `min_quantity <= max_quantity`, and `funding_interval_seconds > 0`. `notional` is `abs(quantity) * price * contract_multiplier`. `is_executable` requires step alignment, minimum quantity/notional, and maximum quantity.

- [ ] **Step 4: Export and run GREEN**

Run:

```bash
uv run --frozen --extra dev pytest -q tests/domain/test_instrument.py
uv run --frozen --extra dev mypy src/grid_trade/domain tests/domain/test_instrument.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/grid_trade/domain tests/domain/test_instrument.py
git commit -m "feat: add explicit linear perpetual instrument contract"
```

---

### Task 2: Instrument identity propagation and reconciliation guard

**Files:**
- Modify: `src/grid_trade/domain/market.py`
- Modify: `src/grid_trade/domain/orders.py`
- Modify: `src/grid_trade/strategy/adaptive_ladder.py`
- Modify: `src/grid_trade/strategy/adaptive_grid.py`
- Modify: `src/grid_trade/strategy/grid_geometry.py`
- Modify: `src/grid_trade/execution/reconcile.py`
- Modify: `src/grid_trade/application/passive_policy.py`
- Test: `tests/application/test_instrument_bound_policy.py`
- Test: `tests/execution/test_reconcile.py`

**Interfaces:**
- `MarketSnapshot.instrument_id: str = LEGACY_UNSPECIFIED_INSTRUMENT`
- `PassiveOrderIntent.instrument_id: str = LEGACY_UNSPECIFIED_INSTRUMENT`
- `WorkingOrder.instrument_id: str = LEGACY_UNSPECIFIED_INSTRUMENT`
- `FillEvent.instrument_id: str = LEGACY_UNSPECIFIED_INSTRUMENT`
- `AdaptiveGridState.instrument_id: str = LEGACY_UNSPECIFIED_INSTRUMENT`
- `AdaptiveLadderConfig.instrument_id`, `min_quantity`, `min_notional`, and `contract_multiplier` use compatibility defaults.
- `build_adaptive_ladder` binds every order to `config.instrument_id`.

- [ ] **Step 1: Write failing identity tests**

```python
import datetime as dt
from decimal import Decimal

import pytest

from grid_trade.application.passive_policy import transition_passive_policy
from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.orders import OrderSide, PassiveOrderIntent, WorkingOrder


def test_policy_rejects_proposed_order_from_another_instrument() -> None:
    snapshot = MarketSnapshot(
        dt.datetime(2026, 8, 10, tzinfo=dt.UTC),
        Decimal("99"),
        Decimal("101"),
        Decimal("0.01"),
        Decimal("0"),
        "fixture",
        instrument_id="BTC-PERP",
    )
    proposed = (
        PassiveOrderIntent(
            "eth-order",
            0,
            1,
            OrderSide.BUY,
            Decimal("98"),
            Decimal("0.01"),
            instrument_id="ETH-PERP",
        ),
    )
    with pytest.raises(ValueError, match="instrument"):
        transition_passive_policy(
            decision="fixture",
            previous_state="old",
            candidate_state="new",
            snapshot=snapshot,
            risk_limits=_limits(),
            risk_state=_risk_state(snapshot),
            working_orders=(),
            proposed_ladder=proposed,
        )


def test_reconciliation_identity_is_economic() -> None:
    desired = PassiveOrderIntent(
        "same-id",
        0,
        1,
        OrderSide.BUY,
        Decimal("98"),
        Decimal("0.01"),
        instrument_id="BTC-PERP",
    )
    working = WorkingOrder(
        "same-id",
        0,
        1,
        OrderSide.BUY,
        Decimal("98"),
        Decimal("0.01"),
        Decimal("0"),
        instrument_id="ETH-PERP",
    )
    assert reconcile_passive_orders(desired=(desired,), working=(working,)).cancel == ("same-id",)
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
uv run --frozen --extra dev pytest -q \
  tests/application/test_instrument_bound_policy.py \
  tests/execution/test_reconcile.py
```

Expected: constructors reject unknown `instrument_id` or mismatched orders are not rejected.

- [ ] **Step 3: Add compatibility identity fields**

Append identity fields so old positional constructors retain their meaning. Validate non-empty identity strings. Update economic signatures and reconciliation matching to include identity.

For explicit instruments, client order IDs use:

```text
{instrument_id}:{stage}:g{generation}:{side}:l{level}
```

For the legacy sentinel, preserve the historical ID text exactly.

- [ ] **Step 4: Add Application identity validation**

Before prospective Risk assessment, validate every working and proposed order against `snapshot.instrument_id`. Two explicit identities must match. Generalized paths reject the legacy sentinel; legacy-to-legacy remains compatible.

- [ ] **Step 5: Run focused and related tests**

Run:

```bash
uv run --frozen --extra dev pytest -q \
  tests/domain tests/execution tests/application \
  tests/strategy/test_adaptive_ladder.py \
  tests/strategy/test_adaptive_grid_policy.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/grid_trade/domain src/grid_trade/strategy \
  src/grid_trade/execution/reconcile.py src/grid_trade/application/passive_policy.py \
  tests/application/test_instrument_bound_policy.py tests/execution/test_reconcile.py
git commit -m "feat: bind passive policy state and orders to instruments"
```

---

### Task 3: Explicit sampling and matured-label horizons

**Files:**
- Create: `src/grid_trade/calibration/sampling.py`
- Modify: `src/grid_trade/calibration/__init__.py`
- Modify: `src/grid_trade/calibration/engine.py`
- Modify: `src/grid_trade/calibration/universal_engine.py`
- Test: `tests/calibration/test_sampling.py`

**Interfaces:**
- `SamplingSpec(observation_interval_ms, interval_tolerance_ms, volatility_window_ms, trend_horizon_ms, markout_horizon_ms, ofi_horizon_ms)`
- `SamplingSpec.validate_engine_counts(*, volatility_window: int, trend_horizon: int) -> None`
- `SamplingSpec.validate_observation_delta(previous: datetime, current: datetime) -> None`
- `SamplingSpec.validate_markout(markout: MaturedMarkout) -> None`
- `SamplingSpec.validate_ofi_sample(sample: OfiImpactSample) -> None`
- `CalibrationEngineConfig.sampling: SamplingSpec | None = None`

- [ ] **Step 1: Write failing sampling tests**

```python
import datetime as dt
from decimal import Decimal

import pytest

from grid_trade.calibration.sampling import SamplingSpec


def test_sampling_spec_binds_count_windows_to_elapsed_time() -> None:
    spec = SamplingSpec(1_000, 50, 4_000, 2_000, 5_000, 5_000)
    spec.validate_engine_counts(volatility_window=4, trend_horizon=2)
    with pytest.raises(ValueError, match="volatility"):
        spec.validate_engine_counts(volatility_window=5, trend_horizon=2)


def test_sampling_spec_rejects_off_cadence_observation() -> None:
    spec = SamplingSpec(1_000, 50, 4_000, 2_000, 5_000, 5_000)
    previous = dt.datetime(2026, 8, 10, tzinfo=dt.UTC)
    spec.validate_observation_delta(previous, previous + dt.timedelta(milliseconds=1_040))
    with pytest.raises(ValueError, match="cadence"):
        spec.validate_observation_delta(previous, previous + dt.timedelta(milliseconds=1_100))
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run --frozen --extra dev pytest -q tests/calibration/test_sampling.py
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `SamplingSpec`**

Use integer microseconds for comparisons; do not compare floating-point seconds. Require positive durations, non-negative tolerance, and tolerance smaller than the observation interval.

- [ ] **Step 4: Integrate with Calibration**

`CalibrationEngineConfig.__post_init__` validates configured counts. `_validate_observation_sequence` checks cadence after the first observation. `update_universal_calibration` validates every supplied matured markout and OFI sample before updating either engine.

- [ ] **Step 5: Run calibration suite**

Run:

```bash
uv run --frozen --extra dev pytest -q tests/calibration
uv run --frozen --extra dev mypy src/grid_trade/calibration tests/calibration
```

Expected: PASS, including legacy configs with `sampling=None`.

- [ ] **Step 6: Commit**

```bash
git add src/grid_trade/calibration tests/calibration/test_sampling.py
git commit -m "feat: make calibration time semantics explicit"
```

---

### Task 4: Independent adaptive features

**Files:**
- Create: `src/grid_trade/strategy/features.py`
- Modify: `src/grid_trade/strategy/__init__.py`
- Modify: `src/grid_trade/strategy/inventory_target.py`
- Modify: `src/grid_trade/strategy/adaptive_grid.py`
- Modify: `src/grid_trade/application/calibrated_adaptive.py`
- Test: `tests/strategy/test_adaptive_features.py`
- Test: `tests/application/test_calibrated_adaptive.py`

**Interfaces:**
- `AdaptiveFeatures`
- `AdaptiveFeatures.from_stage(stage: AdaptiveStage) -> AdaptiveFeatures`
- `AdaptiveGridPolicyConfig.features: AdaptiveFeatures | None = None`
- `AdaptiveGridPolicyConfig.active_features -> AdaptiveFeatures`
- `decide_inventory_target(..., enabled: bool = True)`
- `prepare_calibrated_adaptive_inputs(..., features: AdaptiveFeatures | None = None)`

- [ ] **Step 1: Write failing feature tests**

```python
from grid_trade.strategy.adaptive_grid import AdaptiveStage
from grid_trade.strategy.features import AdaptiveFeatures


def test_stage_presets_preserve_historical_activation() -> None:
    assert AdaptiveFeatures.from_stage(AdaptiveStage.S3_INVENTORY) == AdaptiveFeatures(
        inventory_control=True,
        partial_derisk=False,
        conditional_reversal=False,
        funding_bias=False,
        order_book_reference=False,
    )
    assert AdaptiveFeatures.from_stage(AdaptiveStage.S7_ORDER_BOOK) == AdaptiveFeatures(
        inventory_control=True,
        partial_derisk=True,
        conditional_reversal=True,
        funding_bias=True,
        order_book_reference=True,
    )


def test_order_book_can_be_enabled_without_funding() -> None:
    features = AdaptiveFeatures(
        inventory_control=True,
        partial_derisk=False,
        conditional_reversal=False,
        funding_bias=False,
        order_book_reference=True,
    )
    config = replace(_policy(), features=features)
    decision, _, _ = decide_adaptive_grid(_snapshot(), _signals(), _state(), config)
    assert decision.order_book_applied
    assert not decision.funding_applied
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
uv run --frozen --extra dev pytest -q tests/strategy/test_adaptive_features.py
```

Expected: missing module/field.

- [ ] **Step 3: Implement feature resolution**

Replace every ordinal `stage >= ...` behavior check with `config.active_features`. Keep `stage` only as reporting/preset metadata. When inventory control is disabled, return zero reservation shift and unit bid/ask scales while retaining the target.

- [ ] **Step 4: Make readiness feature-aware**

Funding readiness is required only when `funding_bias` is active. Order-book readiness is required only when `order_book_reference` is active. Existing calls without explicit features resolve from stage and remain unchanged.

- [ ] **Step 5: Run Strategy/Application suites**

Run:

```bash
uv run --frozen --extra dev pytest -q tests/strategy tests/application
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/grid_trade/strategy src/grid_trade/application/calibrated_adaptive.py \
  tests/strategy/test_adaptive_features.py tests/application/test_calibrated_adaptive.py
git commit -m "feat: decouple adaptive features from stage ordering"
```

---

### Task 5: Signed directional target profile

**Files:**
- Create: `src/grid_trade/strategy/target_profile.py`
- Modify: `src/grid_trade/strategy/__init__.py`
- Modify: `src/grid_trade/strategy/adaptive_grid.py`
- Modify: `src/grid_trade/application/calibrated_adaptive.py`
- Test: `tests/strategy/test_directional_target_profile.py`
- Test: `tests/application/test_calibrated_adaptive_generalization.py`

**Interfaces:**
- `DirectionalTargetProfileConfig`
- `apply_directional_de_risk(target, trend_score, config) -> DeRiskDecision`
- `apply_conditional_reversal(*, target, position, trend_score, profile) -> ShortOverlayDecision`
- `AdaptiveGridPolicyConfig.target_profile: DirectionalTargetProfileConfig | None = None`
- `prepare_calibrated_adaptive_inputs(..., target_profile: DirectionalTargetProfileConfig | None = None)`

- [ ] **Step 1: Write failing sign-symmetry tests**

```python
from decimal import Decimal

from grid_trade.strategy.target_profile import (
    DirectionalTargetProfileConfig,
    apply_conditional_reversal,
    apply_directional_de_risk,
)


def test_short_profile_is_sign_mirror_of_long_profile() -> None:
    long_profile = DirectionalTargetProfileConfig(
        baseline_target=Decimal("0.5"),
        allow_opposite=True,
        opposite_entry_aligned_trend_threshold=Decimal("-0.6"),
        max_opposite_target=Decimal("0.4"),
    )
    short_profile = replace(long_profile, baseline_target=Decimal("-0.5"))
    long = apply_conditional_reversal(
        target=Decimal("0.5"),
        position=Decimal("0"),
        trend_score=Decimal("-0.9"),
        profile=long_profile,
    )
    short = apply_conditional_reversal(
        target=Decimal("-0.5"),
        position=Decimal("0"),
        trend_score=Decimal("0.9"),
        profile=short_profile,
    )
    assert short.effective_target == -long.effective_target


def test_both_reversal_directions_require_flat_first() -> None:
    # Long position plus requested short => zero target.
    # Short position plus requested long => zero target.
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
uv run --frozen --extra dev pytest -q \
  tests/strategy/test_directional_target_profile.py \
  tests/application/test_calibrated_adaptive_generalization.py
```

Expected: missing module/field.

- [ ] **Step 3: Implement generic aligned-trend helpers**

For non-zero baseline targets:

```python
preferred_sign = Decimal(1) if baseline_target > 0 else Decimal(-1)
aligned_trend = preferred_sign * trend_score
```

Use existing warning/severe thresholds against `aligned_trend`. Conditional reversal maps adverse severity to `-preferred_sign * max_opposite_target`. Call the existing `enforce_flat_before_reverse` before returning the effective target.

For a zero baseline, de-risk and conditional reversal return zero unless later funding adjustment changes the target.

- [ ] **Step 4: Integrate profile opt-in**

When `target_profile is None`, retain the exact legacy `_target_pipeline`. When present, use the signed profile with active de-risk/reversal feature flags, then apply funding and inventory control.

- [ ] **Step 5: Prove legacy equivalence**

Add a test comparing the existing long-biased pipeline with an explicit long profile under healthy, warning, severe-long, flatten-long, and flat-to-short cases. Compare target, phase, bid/ask scales, and emitted ladder economic signatures.

- [ ] **Step 6: Run related tests and commit**

Run:

```bash
uv run --frozen --extra dev pytest -q tests/strategy tests/application
```

Expected: PASS.

```bash
git add src/grid_trade/strategy src/grid_trade/application/calibrated_adaptive.py \
  tests/strategy/test_directional_target_profile.py \
  tests/application/test_calibrated_adaptive_generalization.py
git commit -m "feat: isolate directional bias behind a signed target profile"
```

---

### Task 6: Instrument-aware calibrated preparation and Tier-2 propagation

**Files:**
- Modify: `src/grid_trade/application/calibrated_adaptive.py`
- Modify: `src/grid_trade/research/tier2_calibrated_candidate.py`
- Modify: `src/grid_trade/strategy/adaptive_ladder.py`
- Test: `tests/application/test_instrument_bound_policy.py`
- Test: `tests/research/test_tier2_instrument_sampling.py`

**Interfaces:**
- `VenueGridConstraints.from_instrument(spec: InstrumentSpec) -> VenueGridConstraints`
- `prepare_calibrated_adaptive_inputs(..., instrument: InstrumentSpec | None = None)`
- `Tier2CalibratedCandidateConfig.instrument: InstrumentSpec | None = None`
- Tier-2 `_book_context` creates `MarketSnapshot(..., instrument_id=event.instrument)`.

- [ ] **Step 1: Write failing Application/Tier-2 tests**

```python

def test_explicit_instrument_rejects_legacy_or_mismatched_snapshot() -> None:
    spec = _instrument("BTC-PERP")
    with pytest.raises(ValueError, match="instrument"):
        prepare_calibrated_adaptive_inputs(
            snapshot=_snapshot(instrument_id="ETH-PERP"),
            calibrated=_market(instrument_id="BTC-PERP"),
            capacity=_capacity("100"),
            meta=_meta(),
            venue=VenueGridConstraints.from_instrument(spec),
            instrument=spec,
        )


def test_minimum_notional_makes_tiny_capacity_not_executable() -> None:
    result = prepare_calibrated_adaptive_inputs(...)
    assert result.inputs is None
    assert result.reason == "inventory_capacity_not_executable"
```

Tier-2 test asserts that candidate orders and the decision snapshot carry the Dataset instrument and that a mismatched config instrument fails.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
uv run --frozen --extra dev --extra research pytest -q \
  tests/application/test_instrument_bound_policy.py \
  tests/research/test_tier2_instrument_sampling.py
```

Expected: missing arguments/helpers or mismatches are accepted.

- [ ] **Step 3: Validate explicit instrument boundary**

Check:

- snapshot, calibrated state, and spec identity;
- venue tick and quantity step against spec;
- capacity venue cap against instrument maximum;
- per-level minimum quantity/notional;
- final ladder identity and executable residual levels.

- [ ] **Step 4: Propagate Tier-2 identity**

Bind snapshots and candidate orders to `DatasetManifest.instrument`. Pass explicit instrument and sampling config through the candidate configuration without importing exchange-specific code.

- [ ] **Step 5: Run Dataset/Application/Tier-2 suites**

Run:

```bash
uv run --frozen --extra dev --extra research pytest -q \
  tests/domain tests/application tests/datasets tests/research \
  tests/integrations/hyperliquid
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/grid_trade/application src/grid_trade/research \
  src/grid_trade/strategy/adaptive_ladder.py \
  tests/application/test_instrument_bound_policy.py \
  tests/research/test_tier2_instrument_sampling.py
git commit -m "feat: enforce instrument contracts in calibrated Tier-2 flow"
```

---

### Task 7: Architecture, exports, documentation, and CI contracts

**Files:**
- Modify: `src/grid_trade/domain/__init__.py`
- Modify: `src/grid_trade/calibration/__init__.py`
- Modify: `src/grid_trade/strategy/__init__.py`
- Modify: `tests/architecture/test_boundaries.py`
- Modify: `tests/architecture/test_module_ownership.py`
- Modify: `.github/workflows/research.yml`
- Modify: `README.md`

**Interfaces:**
- Public exports include all four new contracts.
- Domain instrument code imports only standard library plus `grid_trade.domain.numeric` if deterministic Decimal context is needed.
- Sampling code remains Calibration-owned and imports only Domain numeric plus microstructure contracts.
- Strategy features/target profile import no Application, Risk, Execution, Integration, Evidence, or Research modules.

- [ ] **Step 1: Add failing architecture/export tests**

Assert:

- public imports resolve;
- `domain.instrument` has no higher-layer imports;
- `strategy.features` and `strategy.target_profile` remain pure Strategy modules;
- Research CI includes the new generalized tests and type-checks the new modules;
- no optional runtime import leaks into the new modules.

- [ ] **Step 2: Run architecture tests and verify RED**

Run:

```bash
uv run --frozen --extra dev pytest -q tests/architecture
```

Expected: missing exports/CI targets.

- [ ] **Step 3: Update exports, workflow, and README**

README must state:

- supported contract scope is linear perpetual only;
- explicit instrument and sampling contracts are required for generalized historical evaluation;
- stage is a preset, not a feature dependency;
- long bias is the default profile, not a core invariant;
- multi-asset portfolio operation remains out of scope.

- [ ] **Step 4: Run architecture and formatting checks**

Run:

```bash
uv run --frozen --extra dev pytest -q tests/architecture
uv run --frozen --extra dev ruff format --check --diff .
uv run --frozen --extra dev ruff check .
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/grid_trade/*/__init__.py tests/architecture \
  .github/workflows/research.yml README.md
git commit -m "docs: define generalized strategy contracts and boundaries"
```

---

### Task 8: Full verification, self-review, and PR publication

**Files:**
- Create: `docs/superpowers/reviews/2026-08-10-strategy-generality-hardening-review.md`
- Review: all branch changes

- [ ] **Step 1: Run complete Core verification**

```bash
uv lock --check
uv sync --extra dev --frozen
uv run --frozen --extra dev ruff format --check --diff .
uv run --frozen --extra dev ruff check .
uv run --frozen --extra dev pytest -q --ignore=tests/research --ignore=tests/integrations
uv run --frozen --extra dev mypy src tests --exclude '^tests/(research|integrations)/'
```

Expected: all PASS.

- [ ] **Step 2: Run complete Research verification**

```bash
uv sync --extra dev --extra research --frozen
uv run --frozen --extra dev --extra research pytest -m research -q \
  tests/research tests/integrations
uv run --frozen --extra dev --extra research mypy \
  src/grid_trade/datasets \
  src/grid_trade/integrations/hyperliquid \
  src/grid_trade/research \
  tests/datasets \
  tests/integrations/hyperliquid \
  tests/research
```

Expected: all PASS.

- [ ] **Step 3: Repeat deterministic Evidence runners**

Run each twice in fresh Python processes and require exact equality:

```bash
for module in \
  grid_trade.research.s0_runner \
  grid_trade.research.s1_runner \
  grid_trade.research.s2_runner \
  grid_trade.research.adaptive_runner \
  grid_trade.research.microstructure_calibration_runner \
  grid_trade.research.calibrated_adaptive_runner \
  grid_trade.research.tier2_fixture_runner; do
  first="$(PYTHONPATH=src uv run --frozen --extra dev --extra research python -m "$module")"
  second="$(PYTHONPATH=src uv run --frozen --extra dev --extra research python -m "$module")"
  test -n "$first"
  test "$first" = "$second"
done
```

Expected: all equality checks PASS.

- [ ] **Step 4: Review the diff as a reviewer**

Check:

- no explicit identity path accepts the legacy sentinel;
- no stage ordinal remains as a feature activation condition;
- no signed target path can bypass flat-before-reverse;
- no minimum-order check is performed with float arithmetic;
- no future markout/OFI label enters a causal fit;
- no exchange-specific dependency enters Domain/Strategy/Application;
- no temporary workflow, cache, generated artifact, or debug code is tracked;
- documentation does not claim profitability or production readiness.

- [ ] **Step 5: Write review record**

Record exact commands, counts, CI run IDs, final head SHA, changed files, design decisions, remaining limitations, and **RESEARCH / NO-GO FOR PRODUCTION** status.

- [ ] **Step 6: Compare branch to main**

```bash
git diff --check main...HEAD
git status --short
git log --oneline --decorate main..HEAD
```

Expected: no whitespace errors or untracked files; branch is not behind `main`.

- [ ] **Step 7: Open a draft PR**

PR description must include:

- What / Why;
- explicit supported scope and non-goals;
- compatibility decisions;
- identity, sampling, feature, and signed-target contracts;
- exact local and CI verification;
- changed Evidence digests if any;
- risks and residual limitations;
- statement that profitability remains unvalidated.

- [ ] **Step 8: Require exact final-head CI**

Do not mark ready or merge until Core CI and Research Integration both succeed on the same final head. Resolve any failure by reproducing the failing contract, applying the smallest fix, and rerunning the affected plus full gates.

- [ ] **Step 9: Mark ready after verification**

Keep the branch for review. Do not merge into `main` without explicit user authorization.
