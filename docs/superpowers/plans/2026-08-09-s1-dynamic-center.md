# S1 Dynamic Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a stateful thresholded/bounded Dynamic Center to S1 while preserving S0 behavior and isolating the ablation to center movement only.

**Architecture:** Extract shared long-grid geometry from the S0 wrapper, add a pure center proposal policy, then add an S1 decision layer that suppresses re-anchors when tick-rounded executable prices do not change. Reuse existing RiskController, reconciliation, hftbacktest replay, Nautilus order mapping, and canonical Evidence rather than duplicating them.

**Tech Stack:** Python 3.12, Decimal, pytest, Hypothesis, Ruff, strict mypy, hftbacktest==2.4.4, nautilus_trader==1.230.0, GitHub Actions.

## Global Constraints

- License remains LGPL-3.0.
- Production status remains NO-GO; no live-capital authorization.
- S1 may use only current causal mid plus previous DynamicCenterState for center logic.
- S1 must not add trend, momentum, volatility-adaptive spacing, target inventory, inventory skew, short exposure, funding bias, OFI, microprice, adaptive size, or RL.
- Existing single-snapshot S0 behavior and checked-in S0 Evidence determinism must remain backward compatible.
- Re-anchor equality rule: `abs(deviation_bps) >= reanchor_threshold_bps` is eligible for movement.
- `max_step_bps` must be finite, positive, and strictly below 10,000.
- Generation changes only when the executable economic ladder changes after tick rounding.
- Risk rejection must not commit a proposed center or generation.
- Existing cancel-before-replace semantics remain authoritative.
- Research CI must continue to pin hftbacktest==2.4.4 and nautilus_trader==1.230.0.

---

### Task 1: Shared center-based long-grid geometry

**Files:**
- Create: `src/grid_trade/strategy/grid_geometry.py`
- Modify: `src/grid_trade/strategy/fixed_grid.py`
- Create: `tests/strategy/test_grid_geometry.py`
- Modify: `tests/strategy/test_fixed_grid.py`

**Interfaces:**
- Consumes: `FixedLongGridConfig`, `PassiveOrderIntent`, `OrderSide`.
- Produces: `build_long_grid_at_center(center: Decimal, config: FixedLongGridConfig, generation: int, stage: str) -> tuple[PassiveOrderIntent, ...]`.
- `build_fixed_long_grid(snapshot, config, generation)` remains public and calls the shared primitive with `center=snapshot.mid` and `stage="s0"`.

- [ ] **Step 1: Write failing shared-geometry tests**

```python
def test_center_geometry_matches_existing_s0_prices() -> None:
    orders = build_long_grid_at_center(
        Decimal("100"),
        _config(),
        generation=7,
        stage="s1",
    )
    assert [order.price for order in orders] == [
        Decimal("99.00"),
        Decimal("98.00"),
        Decimal("97.00"),
    ]
    assert [order.client_order_id for order in orders] == [
        "s1:g7:buy:l1",
        "s1:g7:buy:l2",
        "s1:g7:buy:l3",
    ]


def test_geometry_rejects_empty_stage() -> None:
    with pytest.raises(ValueError, match="stage"):
        build_long_grid_at_center(Decimal("100"), _config(), generation=0, stage=" ")
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `uv run --frozen --extra dev pytest -q tests/strategy/test_grid_geometry.py tests/strategy/test_fixed_grid.py`

Expected: FAIL because `grid_trade.strategy.grid_geometry` does not exist.

- [ ] **Step 3: Implement the minimal shared primitive**

```python
def build_long_grid_at_center(
    center: Decimal,
    config: FixedLongGridConfig,
    generation: int,
    stage: str,
) -> tuple[PassiveOrderIntent, ...]:
    if not center.is_finite() or center <= 0:
        raise ValueError("center must be finite and positive")
    if generation < 0:
        raise ValueError("generation must be non-negative")
    if not stage.strip():
        raise ValueError("stage must be non-empty")
    # Reuse the exact S0 spacing, tick-floor, positive-price, and strict-descending rules.
```

Change `build_fixed_long_grid` to delegate to this function while retaining the exact existing `s0:g{generation}:buy:l{level}` IDs.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `uv run --frozen --extra dev pytest -q tests/strategy/test_grid_geometry.py tests/strategy/test_fixed_grid.py`

Expected: PASS with all pre-existing S0 tests unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/grid_trade/strategy/grid_geometry.py src/grid_trade/strategy/fixed_grid.py tests/strategy/test_grid_geometry.py tests/strategy/test_fixed_grid.py
git commit -m "refactor: share center-based grid geometry"
```

### Task 2: Pure Dynamic Center proposal policy

**Files:**
- Create: `src/grid_trade/strategy/dynamic_center.py`
- Create: `tests/strategy/test_dynamic_center.py`
- Modify: `src/grid_trade/strategy/__init__.py`

**Interfaces:**
- Produces `DynamicCenterConfig(reanchor_threshold_bps: Decimal, max_step_bps: Decimal)`.
- Produces `DynamicCenterState(center: Decimal, generation: int)`.
- Produces `CenterProposal(previous_center: Decimal, market_mid: Decimal, deviation_bps: Decimal, proposed_center: Decimal, previous_generation: int, threshold_crossed: bool)`.
- Produces `initialize_dynamic_center(snapshot: MarketSnapshot) -> DynamicCenterState`.
- Produces `propose_dynamic_center(snapshot: MarketSnapshot, state: DynamicCenterState, config: DynamicCenterConfig) -> CenterProposal`.

- [ ] **Step 1: Write failing policy tests**

```python
def test_below_threshold_keeps_previous_center() -> None:
    state = DynamicCenterState(center=Decimal("100"), generation=3)
    proposal = propose_dynamic_center(
        _snapshot(mid="100.20"),
        state,
        DynamicCenterConfig(Decimal("25"), Decimal("50")),
    )
    assert proposal.deviation_bps == Decimal("20")
    assert proposal.proposed_center == Decimal("100")
    assert proposal.threshold_crossed is False


def test_exact_threshold_is_eligible() -> None:
    state = DynamicCenterState(center=Decimal("100"), generation=0)
    proposal = propose_dynamic_center(
        _snapshot(mid="100.25"),
        state,
        DynamicCenterConfig(Decimal("25"), Decimal("50")),
    )
    assert proposal.threshold_crossed is True
    assert proposal.proposed_center == Decimal("100.25")


def test_large_move_is_capped_symmetrically() -> None:
    config = DynamicCenterConfig(Decimal("25"), Decimal("50"))
    up = propose_dynamic_center(_snapshot(mid="102"), DynamicCenterState(Decimal("100"), 1), config)
    down = propose_dynamic_center(_snapshot(mid="98"), DynamicCenterState(Decimal("100"), 1), config)
    assert up.proposed_center == Decimal("100.5")
    assert down.proposed_center == Decimal("99.5")
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `uv run --frozen --extra dev pytest -q tests/strategy/test_dynamic_center.py`

Expected: FAIL because module/types do not exist.

- [ ] **Step 3: Implement exact Decimal proposal arithmetic**

```python
_BPS = Decimal(10_000)

def propose_dynamic_center(snapshot, state, config):
    deviation_bps = (snapshot.mid - state.center) / state.center * _BPS
    crossed = abs(deviation_bps) >= config.reanchor_threshold_bps
    if not crossed:
        proposed = state.center
    else:
        step_bps = min(abs(deviation_bps), config.max_step_bps)
        signed_step = step_bps if deviation_bps > 0 else -step_bps
        proposed = state.center * (Decimal(1) + signed_step / _BPS)
    return CenterProposal(...)
```

Validate finite/positive config and state values, `max_step_bps < 10_000`, timezone-aware snapshot through existing `MarketSnapshot`, and non-negative generation.

- [ ] **Step 4: Add property tests**

Use Hypothesis to prove:
- proposed center remains positive,
- `abs((proposed - previous) / previous * 10_000) <= max_step_bps`,
- below-threshold proposals equal previous center,
- up/down arithmetic is symmetric within exact Decimal inputs.

- [ ] **Step 5: Run and verify GREEN**

Run: `uv run --frozen --extra dev pytest -q tests/strategy/test_dynamic_center.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/grid_trade/strategy/dynamic_center.py src/grid_trade/strategy/__init__.py tests/strategy/test_dynamic_center.py
git commit -m "feat: add pure dynamic center proposal policy"
```

### Task 3: Executable-ladder change decision and queue-preserving generation semantics

**Files:**
- Modify: `src/grid_trade/strategy/dynamic_center.py`
- Create: `tests/strategy/test_dynamic_center_ladder.py`

**Interfaces:**
- Produces `CenterDecisionReason` values `WITHIN_THRESHOLD`, `BOUNDED_REANCHOR`, `NO_EFFECTIVE_LADDER_CHANGE`.
- Produces `CenterDecision` with previous/proposed/effective center, previous/effective generation, deviation, reason, `reanchored`, and `economic_ladder_changed`.
- Produces `decide_dynamic_center(snapshot, state, center_config, grid_config) -> tuple[CenterDecision, tuple[PassiveOrderIntent, ...]]`.

- [ ] **Step 1: Write failing decision tests**

```python
def test_tick_equivalent_candidate_preserves_generation_and_center() -> None:
    state = DynamicCenterState(center=Decimal("100"), generation=4)
    decision, ladder = decide_dynamic_center(
        _snapshot(mid="100.05"),
        state,
        DynamicCenterConfig(Decimal("1"), Decimal("10")),
        _grid_config(tick_size=Decimal("1")),
    )
    assert decision.reason is CenterDecisionReason.NO_EFFECTIVE_LADDER_CHANGE
    assert decision.effective_center == Decimal("100")
    assert decision.effective_generation == 4
    assert decision.reanchored is False


def test_effective_price_change_increments_generation_once() -> None:
    state = DynamicCenterState(center=Decimal("100"), generation=4)
    decision, ladder = decide_dynamic_center(...)
    assert decision.reason is CenterDecisionReason.BOUNDED_REANCHOR
    assert decision.effective_generation == 5
    assert all(order.generation == 5 for order in ladder)
```

- [ ] **Step 2: Verify RED**

Run: `uv run --frozen --extra dev pytest -q tests/strategy/test_dynamic_center_ladder.py`

Expected: FAIL because decision API does not exist.

- [ ] **Step 3: Implement economic-ladder comparison**

Compare only `(side, level, price, quantity, reduce_only)` between current-generation ladder at the previous effective center and candidate-generation ladder at the proposed center. Ignore client IDs and generation when deciding whether executable economics changed.

When equal, return the previous ladder/state semantics. When different, adopt candidate center and increment generation exactly once.

- [ ] **Step 4: Run and verify GREEN**

Run: `uv run --frozen --extra dev pytest -q tests/strategy/test_dynamic_center_ladder.py tests/strategy/test_dynamic_center.py tests/strategy/test_fixed_grid.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/grid_trade/strategy/dynamic_center.py tests/strategy/test_dynamic_center_ladder.py
git commit -m "feat: suppress non-economic dynamic center resets"
```

### Task 4: Risk-aware state transition and cancel-before-replace orchestration

**Files:**
- Create: `src/grid_trade/strategy/dynamic_center_transition.py`
- Create: `tests/strategy/test_dynamic_center_transition.py`
- Reuse unchanged: `src/grid_trade/risk/controller.py`, `src/grid_trade/execution/reconcile.py`

**Interfaces:**
- Produces `DynamicCenterTransition` containing `decision`, `next_state`, `desired_ladder`, `risk_decision`, and `reconciliation`.
- Produces `transition_dynamic_center(snapshot, state, center_config, grid_config, risk_limits, risk_state, working_orders) -> DynamicCenterTransition`.

- [ ] **Step 1: Write failing transition tests**

```python
def test_risk_rejection_does_not_commit_proposed_center() -> None:
    transition = transition_dynamic_center(...position_near_limit...)
    assert transition.risk_decision.allow_new_risk is False
    assert RiskReason.MAX_POSITION in transition.risk_decision.reasons
    assert transition.next_state == DynamicCenterState(center=Decimal("100"), generation=2)
    assert transition.desired_ladder == ()


def test_effective_reanchor_cancels_before_submitting_new_generation() -> None:
    first = transition_dynamic_center(...working_old_generation...)
    assert first.reconciliation.cancel
    assert first.reconciliation.submit == ()
```

Also test a second cycle with old working orders removed produces the new-generation submissions.

- [ ] **Step 2: Verify RED**

Run: `uv run --frozen --extra dev pytest -q tests/strategy/test_dynamic_center_transition.py`

Expected: FAIL because transition module does not exist.

- [ ] **Step 3: Implement using existing risk and reconcile functions**

Algorithm:
1. `decide_dynamic_center`.
2. If no executable change, preserve state and reconcile current desired ladder against working orders without creating new generation.
3. For a candidate new ladder, construct prospective open-order count and call existing `evaluate_risk`.
4. Call existing `filter_passive_orders`; accept transition only if full candidate remains admissible.
5. On risk rejection, preserve previous state and return no new-risk desired ladder.
6. On acceptance, call existing `reconcile_passive_orders`; do not bypass its cancel-only first phase.

- [ ] **Step 4: Add partial-fill regression**

Verify a partially filled old-generation working order is cancelled without simultaneous submit and no desired quantity is ever below its filled quantity.

- [ ] **Step 5: Run and verify GREEN**

Run: `uv run --frozen --extra dev pytest -q tests/strategy/test_dynamic_center_transition.py tests/risk tests/execution`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/grid_trade/strategy/dynamic_center_transition.py tests/strategy/test_dynamic_center_transition.py
git commit -m "feat: integrate dynamic center with risk and reconciliation"
```

### Task 5: Canonical S1 center Evidence and deterministic comparison runner

**Files:**
- Modify: `src/grid_trade/evidence/events.py`
- Create: `src/grid_trade/research/s1_runner.py`
- Create: `tests/evidence/test_center_decision.py`
- Create: `tests/research/test_s1_runner.py`
- Add fixture if needed: `tests/fixtures/microstructure/s1_center_path.csv`

**Interfaces:**
- Add `EvidenceKind.CENTER_DECISION = "center_decision"` without changing schema version 1.
- Produces `S1ComparisonResult` with S0/S1 center-error metrics, re-anchor/generation/cancel/submit counts, ending inventory, mechanics PnL, evidence digest, determinism, `production_authorized=False`, `alpha_validated=False`.
- Produces `run_s1_comparison(...) -> S1ComparisonResult`.

- [ ] **Step 1: Write failing Evidence tests**

```python
def test_center_decision_decimal_payload_is_canonical() -> None:
    event = EvidenceEvent.create(
        run_id="s1",
        sequence=0,
        timestamp=_NOW,
        kind=EvidenceKind.CENTER_DECISION,
        payload={"previous_center": Decimal("100.00"), "deviation_bps": Decimal("25.0")},
    )
    assert '"previous_center":"100.00"' in event.payload_json
```

- [ ] **Step 2: Write failing comparison-runner tests**

Use a deterministic mid path that contains: below-threshold movement, capped movement, tick-equivalent movement, and effective re-anchor. Assert:
- S0 center remains episode-initial mid,
- S1 center moves only on effective re-anchor,
- S1 generation count equals executable ladder changes,
- identical input run twice gives identical result and digest,
- result never authorizes production or claims alpha.

- [ ] **Step 3: Verify RED**

Run: `uv run --frozen --extra dev --extra research pytest -q tests/evidence/test_center_decision.py tests/research/test_s1_runner.py`

Expected: FAIL because CENTER_DECISION and S1 runner do not exist.

- [ ] **Step 4: Implement Evidence event and runner**

Record each decision with previous/market/proposed/effective center, deviation, threshold, max-step, generations, reason, `reanchored`, and `economic_ladder_changed`.

Compute center error in bps against each causal snapshot mid. S0 uses the initial center for the entire episode. S1 uses effective state center. Mechanics PnL remains explicitly incomplete; do not infer profitability.

- [ ] **Step 5: Verify deterministic fresh-process entrypoint**

Add a `__main__` path that prints the checked-in S1 comparison digest. CI will invoke it twice with `PYTHONPATH=src` and require exact equality.

- [ ] **Step 6: Run and verify GREEN**

Run: `uv run --frozen --extra dev --extra research pytest -q tests/evidence/test_center_decision.py tests/research/test_s1_runner.py tests/research/test_s0_runner.py`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/grid_trade/evidence/events.py src/grid_trade/research/s1_runner.py tests/evidence/test_center_decision.py tests/research/test_s1_runner.py tests/fixtures/microstructure
git commit -m "feat: add deterministic S1 comparison evidence"
```

### Task 6: CI, architecture review, documentation, and final verification

**Files:**
- Modify: `.github/workflows/research.yml`
- Modify: `README.md`
- Modify only if architecture findings require it: focused production/test files from Tasks 1-5.

**Interfaces:**
- Research CI additionally proves independent-process S1 Evidence determinism.
- No public production-trading interface is added.

- [ ] **Step 1: Extend Research CI fresh-process gate**

Add commands equivalent to:

```bash
first="$(PYTHONPATH=src uv run --frozen --extra dev --extra research python -m grid_trade.research.s1_runner)"
second="$(PYTHONPATH=src uv run --frozen --extra dev --extra research python -m grid_trade.research.s1_runner)"
test -n "$first"
test "$first" = "$second"
echo "S1 evidence digest: $first"
```

- [ ] **Step 2: Update README scope**

Document S1 as implemented mechanics only, explicitly retaining `NO-GO` and stating no historical OOS alpha/profitability claim exists yet.

- [ ] **Step 3: Run focused architecture review**

Verify all of the following from the actual diff:
- `strategy/` owns center/ladder policy only and does not import hftbacktest/Nautilus.
- `risk/` remains independent and authoritative; strategy cannot override it.
- `execution/` contains no trend/center/alpha logic.
- `research/` orchestrates but does not become a second execution/risk engine.
- hftbacktest and Nautilus remain optional research/integration dependencies, not core imports.
- S0 wrapper compatibility remains intact.
- no S2+ state (`trend`, adaptive volatility spacing, inventory target/skew, short, funding, OFI/microprice, RL) appears in S1 production code.
- no duplicate grid geometry or reconciliation implementation remains.
- files remain focused; split any file whose responsibilities become mixed.

If a violation is found, add a regression test first, observe RED, apply the smallest architecture correction, and rerun the affected suite.

- [ ] **Step 4: Run full fresh Core CI equivalent**

Run:
```bash
uv lock --check
uv sync --extra dev --frozen
uv run --frozen --extra dev ruff format --check .
uv run --frozen --extra dev ruff check .
uv run --frozen --extra dev pytest -m "not research" -q
uv run --frozen --extra dev mypy --strict src tests
```

Expected: all PASS.

- [ ] **Step 5: Run full fresh Research CI equivalent**

Run:
```bash
uv sync --extra dev --extra research --frozen
uv run --frozen --extra dev --extra research pytest -m research -q tests/research tests/integrations
```

Expected: all PASS with pinned hftbacktest==2.4.4 and nautilus_trader==1.230.0.

- [ ] **Step 6: Final diff self-review**

Search for and reject:
- `TODO`, `TBD`, `FIXME`, `HACK`,
- temporary diagnostics,
- secrets/API keys/private keys,
- production/live authorization claims,
- duplicated center/grid/risk/reconcile logic,
- unrelated refactors.

Compare `grid-core...s1-dynamic-center`; require `behind_by == 0` before PR handoff.

- [ ] **Step 7: Commit final docs/CI corrections**

```bash
git add .github/workflows/research.yml README.md src tests
git commit -m "chore: verify S1 dynamic center research gate"
```

- [ ] **Step 8: Create a Draft PR against `grid-core`**

PR title: `feat: add S1 thresholded dynamic center`

The PR body must report exact fresh Core/Research test counts and the final S1 Evidence digest from CI. Keep it Draft and do not merge automatically.
