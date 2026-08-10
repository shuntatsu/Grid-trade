# Architecture Correctness Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Hard Risk independently enforce reduce-only semantics and propagate linear-contract multipliers consistently through Tier-2 economic evaluation.

**Architecture:** Keep Strategy, Risk, Execution, Application, and Research ownership unchanged. Add conservative validation inside Risk; use `HftReplayConfig.contract_multiplier` as the replay economic unit; bind optional explicit `InstrumentSpec` at the Tier-2 manifest boundary and require it in calibrated replay.

**Tech Stack:** Python 3.12, frozen dataclasses, `Decimal`, pytest, Hypothesis, Ruff, strict mypy, GitHub Actions, pinned `hftbacktest==2.4.4`.

## Global Constraints

- Preserve `RESEARCH / NO-GO FOR PRODUCTION` status.
- Preserve public imports and default unit-multiplier fixtures.
- No production code before a failing regression test.
- Hard Risk remains authoritative and fail-closed.
- Generalized contract support remains limited to linear perpetuals.
- Do not add portfolio, live execution, or broker-framework scope.

---

### Task 1: Reduce-only Hard Risk semantics

**Files:**
- Create: `tests/risk/test_reduce_only_semantics.py`
- Modify: `src/grid_trade/domain/risk.py`
- Modify: `src/grid_trade/risk/controller.py`

**Interfaces:**
- Produces: `RiskReason.INVALID_REDUCE_ONLY`.
- Preserves: `filter_passive_orders(...) -> tuple[PassiveOrderIntent, ...]` and `assess_passive_ladder_risk(...)`.

- [ ] **Step 1: Add failing tests**

Cover wrong-side reduce-only, flat-position reduce-only, cumulative oversize, exact cumulative flattening, and conservative separation from new-risk fills.

- [ ] **Step 2: Run Core CI and verify RED**

Expected: new tests fail because malformed reduce-only orders currently survive and the new Risk reason does not exist.

- [ ] **Step 3: Implement conservative filtering**

Maintain independent `projected_new_risk_position` and `remaining_reduce_only_capacity`. Valid reduce-only orders consume only the latter; malformed orders are removed and reported as `INVALID_REDUCE_ONLY`.

- [ ] **Step 4: Run focused and full Core tests**

Expected: new regression tests and existing Risk/Application tests pass.

- [ ] **Step 5: Commit**

Commit message: `fix: validate reduce-only semantics in hard risk`

### Task 2: Multiplier-aware replay economics

**Files:**
- Create: `tests/research/test_contract_multiplier_economics.py`
- Modify: `src/grid_trade/research/hftbacktest_adapter.py`
- Modify: `src/grid_trade/research/replay_attribution.py`
- Modify: `src/grid_trade/research/tier2_replay/attribution.py`
- Modify: `src/grid_trade/research/tier2_replay/liquidity.py`
- Modify: `src/grid_trade/research/tier2_replay/runner.py`
- Modify: `src/grid_trade/research/tier2_replay/models.py`

**Interfaces:**
- Produces: `HftReplayConfig.contract_multiplier: Decimal = Decimal(1)`.
- Extends: `funding_cash_flow(..., contract_multiplier=Decimal(1))`.
- Extends: `assess_order_liquidity_eligibility(..., contract_multiplier=Decimal(1))`.

- [ ] **Step 1: Add failing multiplier metamorphic tests**

Prove that multiplier `10` with quantity `0.1` matches multiplier `1` with quantity `1` for funding, order notional, liquidity participation, and maker-fee attribution.

- [ ] **Step 2: Run Research Integration and verify RED**

Expected: missing keyword parameters or unequal economics.

- [ ] **Step 3: Implement multiplier propagation**

Use `abs(quantity) × price × contract_multiplier` consistently and configure hftbacktest with `.linear_asset(float(contract_multiplier))`.

- [ ] **Step 4: Run focused Research tests**

Expected: multiplier tests and existing Tier-2/hftbacktest tests pass.

- [ ] **Step 5: Commit**

Commit message: `fix: propagate contract multiplier through tier2 economics`

### Task 3: Explicit Tier-2 instrument binding

**Files:**
- Create: `tests/research/test_tier2_instrument_binding.py`
- Modify: `src/grid_trade/research/tier2_replay/models.py`
- Modify: `src/grid_trade/research/tier2_calibrated_replay.py`

**Interfaces:**
- Extends: `Tier2ReplayManifest.instrument: InstrumentSpec | None = None`.
- Requires calibrated replay to provide the candidate `InstrumentSpec` and matching hft multiplier.

- [ ] **Step 1: Add failing manifest tests**

Cover dataset/spec identity mismatch, tick mismatch, lot mismatch, multiplier mismatch, and calibrated replay propagation.

- [ ] **Step 2: Run Research Integration and verify RED**

Expected: manifest accepts mismatches and calibrated replay omits the instrument.

- [ ] **Step 3: Implement manifest validation and propagation**

Validate explicit specs in `Tier2ReplayManifest.__post_init__`; add calibrated-config multiplier validation; pass `candidate.instrument` into replay manifest.

- [ ] **Step 4: Run focused and full Research tests**

Expected: all Tier-2 tests and deterministic Evidence checks pass.

- [ ] **Step 5: Commit**

Commit message: `fix: bind tier2 replay to explicit instrument specs`

### Task 4: Architecture contracts and documentation

**Files:**
- Create: `tests/architecture/test_correctness_boundaries.py`
- Modify: `README.md`

**Interfaces:**
- Ensures: Hard Risk owns reduce-only validation.
- Ensures: Tier-2 replay configuration contains multiplier/spec contracts.
- Documents: unit-multiplier legacy fixtures and explicit generalized paths.

- [ ] **Step 1: Add architecture assertions**

Assert the new public contracts are exported/available and calibrated replay cannot omit explicit instrument propagation.

- [ ] **Step 2: Run architecture tests**

Expected: PASS after Tasks 1-3.

- [ ] **Step 3: Update README**

Document reduce-only defense-in-depth and end-to-end multiplier economics.

- [ ] **Step 4: Run all Core and Research verification**

Commands represented by the permanent `CI` and `Research Integration` workflows must pass on the same final head.

- [ ] **Step 5: Self-review and commit**

Commit message: `docs: record risk and multiplier architecture contracts`

### Task 5: Final review and integration

**Files:**
- Review all branch changes.

- [ ] **Step 1: Inspect diff and temporary files**

Confirm no temporary workflows, scripts, generated caches, or unrelated refactors remain.

- [ ] **Step 2: Verify final PR head**

Require Core CI, Research Integration, strict mypy, Ruff, all tests, and deterministic Evidence reproduction on the exact head.

- [ ] **Step 3: Update PR description**

Record What, Why, design decisions, scope, verification, risks, and remaining production-only limitations.

- [ ] **Step 4: Request review and leave PR ready**

Do not merge until the user authorizes integration.
