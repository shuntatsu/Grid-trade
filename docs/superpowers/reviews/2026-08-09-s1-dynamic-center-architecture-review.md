# S1 Dynamic Center Architecture Review

Date: 2026-08-09
Repository: `shuntatsu/Grid-trade`
Branch: `s1-dynamic-center`
Base: `grid-core`
License: LGPL-3.0
Production status: NO-GO

## Review scope

This review was performed after the first complete S1 TDD pass and before PR handoff. It covers dependency direction, responsibility boundaries, S0 compatibility, Risk authority, reconciliation semantics, optional OSS dependency isolation, Evidence determinism, and CI coverage.

## Findings and resolutions

### 1. Shared Grid geometry depended on the S0 compatibility wrapper

**Finding:** the first extraction placed `FixedLongGridConfig` in `strategy/fixed_grid.py`, while the new shared `grid_geometry.py` imported that type. This made the shared S1 primitive conceptually depend on the S0 wrapper.

**Resolution:** `FixedLongGridConfig` and center-based ladder geometry now live in `strategy/grid_geometry.py`. `strategy/fixed_grid.py` is a thin backward-compatible S0 wrapper which re-exports the config and delegates to the shared primitive with `stage="s0"`.

**Result:** shared strategy primitives no longer depend on the S0 adapter/wrapper direction.

### 2. Dynamic Center orchestration crossed the Strategy boundary

**Finding:** the first implementation put Risk evaluation and Execution reconciliation in `strategy/dynamic_center_transition.py`. That made Strategy depend on Risk and Execution.

**Resolution:** orchestration moved to `application/dynamic_center.py`. `strategy/dynamic_center.py` now owns only deterministic center proposal and executable-ladder decisions. The Application layer coordinates Strategy, Risk, and Execution.

**Result:** Strategy is pure with respect to Risk/Execution orchestration.

### 3. Projected-position risk logic was duplicated between S0 and S1

**Finding:** S0 had private logic which converted a partially accepted passive ladder into an explicit `MAX_POSITION` rejection, while S1 required the same semantics.

**Resolution:** the rule is centralized as `risk.controller.assess_passive_ladder_risk`. S0 and S1 use the shared Risk API rather than owning strategy/research-specific copies.

**Result:** hard Risk semantics have one authority.

### 4. Cancel-before-replace could accidentally re-evaluate the Center

**Finding:** naively calling the Dynamic Center transition again after cancellation could observe the same market move and increment generation a second time.

**Resolution:** `continue_dynamic_center_reconciliation` advances the same accepted center decision and desired ladder through the later submission phase without recomputing Center state.

**Safety addition:** Risk is re-evaluated immediately before submission. If data becomes stale or another hard Risk condition appears during cancellation, the uncommitted replacement is suppressed and previous center/generation state is restored.

### 5. Replacement open-order budget could double-count old and new Grid orders

**Finding:** treating all currently working strategy orders plus all replacement orders as simultaneously outstanding would reject a safe cancel-before-replace solely due to transient accounting.

**Resolution:** prospective open-order count subtracts known strategy working orders which are being replaced, then adds the desired replacement count. Non-strategy outstanding orders remain counted.

### 6. Numerical center movement could reset queue priority without executable change

**Finding:** a center can move numerically while all Grid prices remain identical after tick rounding.

**Resolution:** S1 compares economic ladder signatures `(side, level, price, quantity, reduce_only)` before committing center/generation. If prices and quantities are unchanged, the old center and generation remain effective and no cancel/replace occurs.

### 7. Core architecture boundaries were previously convention-only

**Finding:** dependency direction was reviewed manually but had no permanent regression gate.

**Resolution:** `tests/architecture/test_boundaries.py` parses imports with Python AST and enforces:

- Domain cannot depend on higher layers;
- Strategy cannot depend on Application, Risk, Execution, Research, or Integrations;
- Risk cannot depend on Strategy/Execution/Application/Research/Integrations;
- Execution cannot depend on Strategy/Risk/Application/Research/Integrations;
- `hftbacktest` and `nautilus_trader` cannot be imported by core layers.

### 8. Research CI did not cover all S1 dependency paths

**Finding:** changes under Strategy/Application/Risk could previously pass Core CI without necessarily rerunning pinned research integrations.

**Resolution:** Research Integration path filters now cover S1 Strategy, Application, Risk, Execution, Evidence, Research, Integration, and matching tests. PRs targeting both `main` and `grid-core` run the gates.

Research CI now also performs:

- pinned-runtime research tests;
- focused mypy on the S1 research boundary;
- independent-process S0 Evidence digest equality;
- independent-process S1 Evidence digest equality.

## Dependency direction after review

```text
domain
  ↑
strategy      risk      execution
    \          |          /
     \         |         /
          application
              |
           research

integrations are external-runtime boundaries and are not imported by core policy layers.
```

`research` may orchestrate controlled experiments but is not a second Risk or Execution authority.

## S1 scope purity

The maintained S1 production logic contains no trend/momentum forecast, volatility-adaptive spacing, target inventory, inventory skew, short overlay, funding bias, OFI/microprice, adaptive sizing, or RL control.

S1 changes only center behavior relative to the episode-fixed S0 center.

## Evidence and economic boundary

The checked-in S1 runner uses `execution_scope="policy_reconciliation_only"`. It validates center movement, generation semantics, Risk/reconciliation interaction, queue-reset counts, and deterministic Evidence.

It does not infer historical fills or profitability from the controlled center path. A profitability/promotion claim requires later continuous Tier-2 L2 replay and sealed walk-forward/OOS evaluation.

## Verification evidence at review time

The reviewed branch passed:

- Core CI: Ruff format, Ruff lint, 106 core tests, mypy on 38 source files;
- Research Integration: 29 tests;
- focused S1 research mypy: no issues;
- S0 independent-process Evidence digest equality;
- S1 independent-process Evidence digest equality.

Recorded deterministic digests at this review point:

- S0: `e0c78118d43dad6c52589e450cbd39069c9cf7636f8eb64a56278573617bd467`
- S1: `759dc9f393e919f8aafe5c20f0898aa6c01762eae73bae00a9d2104137c4a189`

A fresh PR-triggered verification is still required on the final head before integration.
