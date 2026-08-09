# S2 Volatility-Adaptive Spacing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an S2 grid stage whose ladder spacing widens and narrows from causal realized volatility while enforcing a conservative execution-cost floor, without adding inventory targeting, short exposure, funding, order-book signals, or RL.

**Architecture:** Preserve S1 Dynamic Center as the center proposal source, add a pure volatility-spacing policy, and combine center plus spacing into one economic ladder decision so generation changes at most once per decision. Extract stage-independent Risk/reconciliation orchestration into an Application primitive so later S3-S7 stages do not duplicate cancel-before-replace and fail-closed semantics.

**Tech Stack:** Python 3.12, `Decimal`, dataclasses, pytest, Ruff, strict mypy, GitHub Actions, existing hftbacktest/Nautilus pinned research extras.

## Global Constraints

- License remains LGPL-3.0.
- Production status remains RESEARCH / NO-GO.
- S2 uses only the current causal `MarketSnapshot.realized_volatility`, current causal spread/mid fields already present, previous S2 state, and the already-approved S1 center proposal.
- S2 must not add trend/momentum, target inventory, inventory skew, partial de-risking, short exposure, funding bias, OFI/microprice, adaptive order sizing, or RL.
- S1 public behavior and deterministic Evidence must remain backward compatible.
- Volatility spacing is `realized_volatility * 10_000 * volatility_multiplier` basis points.
- Effective spacing is the ceiling of `min(max_spacing_bps, max(min_spacing_bps, execution_cost_floor_bps, volatility_spacing_bps))`; rounding upward is deliberate so the floor is never weakened by integer-bps geometry.
- Generation increments only when the tick-rounded executable economic ladder changes.
- Center and spacing changes in one decision increment generation at most once.
- Risk rejection must not commit candidate center, spacing, or generation.
- Cancel-before-replace remains authoritative; candidate state is committed only when cancellation is complete and submission is allowed.
- Risk is rechecked immediately before replacement submission.
- `hftbacktest==2.4.4` and `nautilus_trader==1.230.0` remain isolated to research/integration layers.
- Mechanical S2 Evidence does not establish profitability; promotion still requires later continuous Tier-2 L2 replay and sealed walk-forward/OOS evidence.

---

### Task 1: Stage-independent passive-policy transition primitive

**Files:**
- Create: `src/grid_trade/application/passive_policy.py`
- Modify: `src/grid_trade/application/dynamic_center.py`
- Modify: `src/grid_trade/application/__init__.py`
- Create: `tests/application/test_passive_policy.py`
- Modify: `tests/application/test_dynamic_center_application.py`

**Interfaces:**
- Produces `PassivePolicyTransition[StateT, DecisionT]` with `decision`, `previous_state`, `candidate_state`, `next_state`, `desired_ladder`, `risk_decision`, and `reconciliation`.
- Produces `transition_passive_policy(...) -> PassivePolicyTransition[StateT, DecisionT]`.
- Produces `continue_passive_policy_reconciliation(...) -> PassivePolicyTransition[StateT, DecisionT]`.
- S1 `transition_dynamic_center` and `continue_dynamic_center_reconciliation` become thin wrappers around the shared primitive.

- [ ] **Step 1: Write failing tests for generic state commit and risk rollback**

```python
@dataclass(frozen=True)
class _State:
    value: int


def test_cancel_only_keeps_previous_state_until_submit_phase() -> None:
    transition = transition_passive_policy(
        decision="candidate",
        previous_state=_State(1),
        candidate_state=_State(2),
        snapshot=_snapshot(),
        risk_limits=_limits(),
        risk_state=_risk_state(open_orders=1),
        working_orders=(_working_order(),),
        proposed_ladder=(_replacement_intent(),),
    )
    assert transition.reconciliation.cancel
    assert transition.reconciliation.submit == ()
    assert transition.next_state == _State(1)


def test_risk_failure_after_cancel_restores_previous_state() -> None:
    first = _cancel_only_transition()
    second = continue_passive_policy_reconciliation(
        first,
        snapshot=_snapshot(),
        risk_limits=_limits(),
        risk_state=_stale_risk_state(),
        working_orders=(),
    )
    assert second.risk_decision.allow_new_risk is False
    assert second.next_state == first.previous_state
    assert second.reconciliation.submit == ()
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `uv run --frozen --extra dev pytest -q tests/application/test_passive_policy.py tests/application/test_dynamic_center_application.py`

Expected: FAIL because `grid_trade.application.passive_policy` does not exist.

- [ ] **Step 3: Implement the generic transition primitive**

```python
StateT = TypeVar("StateT")
DecisionT = TypeVar("DecisionT")

@dataclass(frozen=True, slots=True)
class PassivePolicyTransition(Generic[StateT, DecisionT]):
    decision: DecisionT
    previous_state: StateT
    candidate_state: StateT
    next_state: StateT
    desired_ladder: tuple[PassiveOrderIntent, ...]
    risk_decision: RiskDecision
    reconciliation: ReconciliationPlan
```

The implementation must reuse `assess_passive_ladder_risk` and `reconcile_passive_orders`, preserve replacement-aware open-order accounting, retain only reduce-only orders after rejection, keep `previous_state` while cancels remain, and commit `candidate_state` only when accepted and no cancellation remains.

- [ ] **Step 4: Refactor S1 wrappers to delegate without changing S1 outcomes**

`transition_dynamic_center` computes the existing `CenterDecision`, candidate `DynamicCenterState`, and proposed ladder, then delegates to `transition_passive_policy`. `continue_dynamic_center_reconciliation` delegates to `continue_passive_policy_reconciliation` without recomputing the center proposal.

- [ ] **Step 5: Verify GREEN and S1 regression**

Run: `uv run --frozen --extra dev pytest -q tests/application/test_passive_policy.py tests/application/test_dynamic_center_application.py tests/strategy/test_dynamic_center.py tests/strategy/test_dynamic_center_ladder.py`

Expected: PASS.

- [ ] **Step 6: Verify the checked-in S1 digest is unchanged**

Run twice in separate processes:

```bash
PYTHONPATH=src uv run --frozen --extra dev --extra research python -m grid_trade.research.s1_runner
```

Expected digest both times: `f02dfda885d997886dffbdf77cca457989c5cf34066c3febae3f0873d9ca7873`.

### Task 2: Pure volatility-spacing policy

**Files:**
- Create: `src/grid_trade/strategy/volatility_spacing.py`
- Modify: `src/grid_trade/strategy/__init__.py`
- Create: `tests/strategy/test_volatility_spacing.py`

**Interfaces:**
- Produces `VolatilitySpacingConfig(min_spacing_bps: Decimal, max_spacing_bps: Decimal, volatility_multiplier: Decimal, execution_cost_floor_bps: Decimal)`.
- Produces `SpacingDecision(previous_spacing_bps: int, realized_volatility: Decimal, volatility_spacing_bps: Decimal, unclamped_spacing_bps: Decimal, effective_spacing_bps: int, changed: bool)`.
- Produces `propose_volatility_spacing(snapshot: MarketSnapshot, previous_spacing_bps: int, config: VolatilitySpacingConfig) -> SpacingDecision`.

- [ ] **Step 1: Write failing formula and validation tests**

```python
def test_volatility_component_widens_spacing() -> None:
    decision = propose_volatility_spacing(
        _snapshot(vol="0.004"),
        previous_spacing_bps=20,
        config=VolatilitySpacingConfig(
            min_spacing_bps=Decimal("10"),
            max_spacing_bps=Decimal("100"),
            volatility_multiplier=Decimal("0.5"),
            execution_cost_floor_bps=Decimal("12"),
        ),
    )
    assert decision.volatility_spacing_bps == Decimal("20.0000")
    assert decision.effective_spacing_bps == 20


def test_execution_floor_is_rounded_up_not_down() -> None:
    decision = propose_volatility_spacing(
        _snapshot(vol="0"),
        previous_spacing_bps=10,
        config=VolatilitySpacingConfig(
            min_spacing_bps=Decimal("8"),
            max_spacing_bps=Decimal("100"),
            volatility_multiplier=Decimal("1"),
            execution_cost_floor_bps=Decimal("12.1"),
        ),
    )
    assert decision.effective_spacing_bps == 13
```

Also test non-finite/negative values, `max < min`, `max >= 10000`, and non-positive previous spacing.

- [ ] **Step 2: Run focused test and verify RED**

Run: `uv run --frozen --extra dev pytest -q tests/strategy/test_volatility_spacing.py`

Expected: FAIL because the module is absent.

- [ ] **Step 3: Implement the exact Decimal formula**

Use `ROUND_CEILING` only at the final integer-bps conversion. Do not round realized volatility before multiplication.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `uv run --frozen --extra dev pytest -q tests/strategy/test_volatility_spacing.py`

Expected: PASS.

### Task 3: Combined S2 center-and-spacing economic decision

**Files:**
- Create: `src/grid_trade/strategy/s2_adaptive_grid.py`
- Modify: `src/grid_trade/strategy/grid_geometry.py`
- Modify: `src/grid_trade/strategy/__init__.py`
- Create: `tests/strategy/test_s2_adaptive_grid.py`
- Modify: `tests/strategy/test_grid_geometry.py`

**Interfaces:**
- `grid_geometry.py` exposes `ladder_economic_signature(ladder) -> tuple[...]`; S1 uses the same helper instead of a private duplicate.
- Produces `S2GridState(center: Decimal, spacing_bps: int, generation: int)`.
- Produces `S2GridDecision(previous_center, candidate_center, effective_center, previous_spacing_bps, candidate_spacing_bps, effective_spacing_bps, previous_generation, effective_generation, economic_ladder_changed, center_threshold_crossed, spacing_changed)`.
- Produces `initialize_s2_grid(snapshot, grid_config, spacing_config) -> S2GridState`.
- Produces `decide_s2_grid(snapshot, state, center_config, grid_config, spacing_config) -> tuple[S2GridDecision, S2GridState, tuple[PassiveOrderIntent, ...]]`.

- [ ] **Step 1: Write failing combined-decision tests**

```python
def test_spacing_only_change_advances_generation_once() -> None:
    state = S2GridState(center=Decimal("100"), spacing_bps=20, generation=4)
    decision, candidate_state, ladder = decide_s2_grid(
        _snapshot(mid="100", vol="0.006"),
        state,
        _center_config(),
        _grid_config(spacing_bps=20),
        _spacing_config(),
    )
    assert decision.center_threshold_crossed is False
    assert candidate_state.generation == 5
    assert candidate_state.spacing_bps > 20
    assert all(order.generation == 5 for order in ladder)


def test_center_and_spacing_change_still_advance_generation_once() -> None:
    state = S2GridState(center=Decimal("100"), spacing_bps=20, generation=7)
    _, candidate_state, _ = decide_s2_grid(
        _snapshot(mid="101", vol="0.006"),
        state,
        DynamicCenterConfig(Decimal("1"), Decimal("50")),
        _grid_config(spacing_bps=20),
        _spacing_config(),
    )
    assert candidate_state.generation == 8
```

Also test tick-equivalent candidate ladders keep previous center/spacing/generation, and low volatility can narrow spacing but never below the execution-cost floor.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `uv run --frozen --extra dev pytest -q tests/strategy/test_s2_adaptive_grid.py tests/strategy/test_grid_geometry.py`

Expected: FAIL because S2 module/helper is absent.

- [ ] **Step 3: Implement shared economic signature and S2 decision**

Build the current ladder with `state.center/state.spacing_bps/state.generation`, build the candidate ladder with the S1 center proposal plus the S2 spacing proposal and `generation + 1`, then compare tick-rounded economic signatures. Commit neither numerical center nor spacing if the executable ladder is unchanged.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `uv run --frozen --extra dev pytest -q tests/strategy/test_s2_adaptive_grid.py tests/strategy/test_grid_geometry.py tests/strategy/test_dynamic_center_ladder.py`

Expected: PASS.

### Task 4: S2 Risk/reconciliation orchestration

**Files:**
- Create: `src/grid_trade/application/s2_adaptive_grid.py`
- Modify: `src/grid_trade/application/__init__.py`
- Create: `tests/application/test_s2_adaptive_grid_application.py`

**Interfaces:**
- Produces `transition_s2_adaptive_grid(...) -> PassivePolicyTransition[S2GridState, S2GridDecision]`.
- Produces `continue_s2_adaptive_grid_reconciliation(...) -> PassivePolicyTransition[S2GridState, S2GridDecision]`.

- [ ] **Step 1: Write failing orchestration tests**

Tests must prove:

```python
def test_risk_rejection_does_not_commit_wider_spacing_or_center() -> None: ...

def test_old_generation_is_cancelled_before_new_spacing_generation_submits() -> None: ...

def test_risk_is_rechecked_after_cancel_without_recomputing_spacing() -> None: ...

def test_partial_fill_old_generation_never_coexists_with_new_generation_submission() -> None: ...
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `uv run --frozen --extra dev pytest -q tests/application/test_s2_adaptive_grid_application.py`

Expected: FAIL because the S2 Application wrapper is absent.

- [ ] **Step 3: Implement thin wrapper over Task 1 primitive**

The wrapper computes the S2 decision exactly once, passes previous/candidate state and desired ladder to `transition_passive_policy`, and continuation only advances the already-computed transition.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `uv run --frozen --extra dev pytest -q tests/application/test_s2_adaptive_grid_application.py tests/application/test_passive_policy.py`

Expected: PASS.

### Task 5: Canonical S2 Evidence and deterministic mechanical comparison

**Files:**
- Modify: `src/grid_trade/evidence/events.py`
- Create: `src/grid_trade/research/s2_runner.py`
- Create: `tests/evidence/test_spacing_decision.py`
- Create: `tests/research/test_s2_runner.py`
- Modify: `.github/workflows/research.yml`
- Modify: `README.md`

**Interfaces:**
- Adds `EvidenceKind.SPACING_DECISION` without changing Evidence schema version.
- Produces `S2ComparisonResult` with fixed-S1 and adaptive-S2 spacing paths, generation/reanchor/spacing-change counts, cancel/submit/queue-reset counts, risk rejection/reasons, deterministic flag, Evidence digest, zero PnL, `execution_scope="policy_reconciliation_only"`, `production_authorized=False`, `alpha_validated=False`.
- Produces `run_s2_comparison(...)` and `run_checked_in_comparison()`.

- [ ] **Step 1: Write failing Evidence/runner tests**

Use a deterministic fixture whose realized volatility transitions low → high → low while mid also contains one S1 center-threshold move. Assert S1 spacing remains fixed, S2 spacing widens in high volatility and narrows later, center+spacing changes do not double-increment generation, Risk rejection is explicit, PnL remains zero, and two identical runs compare exactly equal.

- [ ] **Step 2: Run Research tests and verify RED**

Run: `uv run --frozen --extra dev --extra research pytest -m research -q tests/research/test_s2_runner.py tests/evidence/test_spacing_decision.py`

Expected: FAIL because the S2 runner/Evidence kind are absent.

- [ ] **Step 3: Implement canonical events and runner**

Emit at minimum MARKET_SNAPSHOT, CENTER_DECISION, SPACING_DECISION, RISK_DECISION, RECONCILIATION_PLAN, and RUN_SUMMARY events. Do not synthesize fills or profitability.

- [ ] **Step 4: Add fresh-process S2 digest gate to Research CI**

Run the checked-in S2 runner twice in separate Python processes and require exact non-empty digest equality, alongside unchanged S0/S1 gates.

- [ ] **Step 5: Run full S2 verification**

```bash
uv lock --check
uv run --frozen --extra dev ruff format --check .
uv run --frozen --extra dev ruff check .
uv run --frozen --extra dev pytest -q --ignore=tests/research --ignore=tests/integrations
uv run --frozen --extra dev mypy src tests --exclude '^tests/(research|integrations)/'
uv run --frozen --extra dev --extra research pytest -m research -q tests/research tests/integrations
uv run --frozen --extra dev --extra research mypy src/grid_trade/research/s2_runner.py tests/research/test_s2_runner.py
```

Expected: all PASS.

### Task 6: Architecture/self-review and PR handoff

**Files:**
- Create: `docs/superpowers/reviews/2026-08-09-s2-volatility-adaptive-spacing-architecture-review.md`
- Modify tests/code only if review finds defects.

- [ ] **Step 1: Review the complete diff against the approved S2 scope**

Explicitly verify no trend, target inventory, short, funding, OFI/microprice, sizing, or RL logic entered S2.

- [ ] **Step 2: Re-run AST architecture boundaries**

Run: `uv run --frozen --extra dev pytest -q tests/architecture/test_boundaries.py`.

- [ ] **Step 3: Verify S0 and S1 Evidence digests remain unchanged**

Expected S0: `e0c78118d43dad6c52589e450cbd39069c9cf7636f8eb64a56278573617bd467`.
Expected S1: `f02dfda885d997886dffbdf77cca457989c5cf34066c3febae3f0873d9ca7873`.

- [ ] **Step 4: Record every review finding and resolution**

The review must state remaining economic limitation: S2 mechanical tests do not prove the high-volatility gate until continuous Tier-2 L2 replay measures fills, adverse-selection markout, turnover, and realized economics.

- [ ] **Step 5: Create a PR from `s2-volatility-spacing` to `grid-core` only after fresh Core and Research CI succeed on the final head**
