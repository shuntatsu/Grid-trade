# S1 Dynamic Center Architecture Review

Date: 2026-08-09
Repository: `shuntatsu/Grid-trade`
Branch: `s1-dynamic-center`
Base: `grid-core`
License: LGPL-3.0
Production status: NO-GO

## Review scope

This review was performed after the complete S1 TDD pass and before PR handoff. It covers dependency direction, responsibility boundaries, S0 compatibility, Risk authority, reconciliation semantics, state-commit timing, optional OSS dependency isolation, Evidence determinism, and CI coverage.

## Findings and resolutions

### 1. Shared Grid geometry depended on the S0 compatibility wrapper

**Finding:** the first extraction placed `FixedLongGridConfig` in `strategy/fixed_grid.py`, while the shared `grid_geometry.py` imported that type. This made the shared S1 primitive conceptually depend on the S0 wrapper.

**Resolution:** `FixedLongGridConfig` and center-based ladder geometry now live in `strategy/grid_geometry.py`. `strategy/fixed_grid.py` is a thin backward-compatible S0 wrapper which re-exports the config and delegates to the shared primitive with `stage="s0"`.

### 2. Dynamic Center orchestration crossed the Strategy boundary

**Finding:** the first implementation put Risk evaluation and Execution reconciliation in `strategy/dynamic_center_transition.py`.

**Resolution:** orchestration moved to `application/dynamic_center.py`. `strategy/dynamic_center.py` owns only deterministic center proposal and executable-ladder decisions. The Application layer coordinates Strategy, Risk, and Execution.

### 3. Projected-position risk logic was duplicated between S0 and S1

**Finding:** S0 had private logic which converted a partially accepted passive ladder into an explicit `MAX_POSITION` rejection, while S1 required identical semantics.

**Resolution:** the rule is centralized as `risk.controller.assess_passive_ladder_risk`. S0 and S1 use the shared Risk API.

### 4. Cancel-before-replace could accidentally re-evaluate the Center

**Finding:** calling the Dynamic Center transition again after cancellation could observe the same market move and increment generation a second time.

**Resolution:** `continue_dynamic_center_reconciliation` advances the same accepted center decision and desired ladder through the later submission phase without recomputing Center state.

**Safety addition:** Risk is re-evaluated immediately before submission. If data becomes stale or another hard Risk condition appears during cancellation, the replacement is suppressed and previous center/generation state is retained.

### 5. Center state was initially committed too early

**Finding:** the first accepted re-anchor implementation exposed the candidate center/generation as `next_state` during a cancel-only phase, even though only old-generation orders still existed at the venue boundary.

**Resolution:** a cancel-only transition now keeps the previous state. The accepted candidate center/generation is committed only when reconciliation reaches a no-cancel submission phase. A Risk failure during that interval leaves the previous state authoritative.

### 6. Replacement open-order budget could double-count old and new Grid orders

**Finding:** treating current strategy orders plus all replacements as simultaneously outstanding could reject a safe cancel-before-replace.

**Resolution:** prospective open-order count subtracts known strategy working orders being replaced, then adds the desired replacement count. Non-strategy outstanding orders remain counted.

### 7. Numerical center movement could reset queue priority without executable change

**Finding:** a center can move numerically while all Grid prices remain identical after tick rounding.

**Resolution:** S1 compares economic ladder signatures `(side, level, price, quantity, reduce_only)` before committing center/generation. If the executable ladder is unchanged, center/generation stay unchanged and no cancel/replace occurs.

### 8. Risk rejection could be misclassified as a queue reset

**Finding:** an early S1 runner counted a cancel triggered by rejected candidate risk as a Dynamic Center queue reset because the strategy proposal itself was a re-anchor candidate.

**Resolution:** re-anchor and queue-reset counts now depend on an actual generation transition after Risk/reconciliation completion. A Risk-rejected candidate does not increment generation, re-anchor count, or queue-reset count.

### 9. Risk/reconciliation evidence was incomplete

**Finding:** CenterDecision evidence existed, but stepwise Risk reasons and reconciliation phases were not explicitly recorded in the S1 trace.

**Resolution:** S1 now emits canonical `RISK_DECISION` and `RECONCILIATION_PLAN` events for initial, decision, and post-cancel phases where applicable. `risk_rejection_count` and unique `risk_reasons_seen` are also included in the research result and run summary.

### 10. Determinism was initially asserted rather than measured inside a run

**Finding:** the first `S1ComparisonResult` returned `deterministic=True` as a fixed mechanics flag.

**Resolution:** the public runner executes the same pure comparison twice and sets `deterministic` from exact result equality. CI independently executes the checked-in S1 runner in separate Python processes and requires equal SHA-256 Evidence digests.

### 11. Core architecture boundaries were convention-only

**Resolution:** `tests/architecture/test_boundaries.py` parses imports with Python AST and enforces:

- Domain cannot depend on higher layers;
- Strategy cannot depend on Application, Risk, Execution, Research, or Integrations;
- Risk cannot depend on Strategy/Execution/Application/Research/Integrations;
- Execution cannot depend on Strategy/Risk/Application/Research/Integrations;
- `hftbacktest` and `nautilus_trader` cannot be imported by core layers.

### 12. Research CI did not cover all S1 dependency paths

**Resolution:** Research Integration path filters cover S1 Strategy, Application, Risk, Execution, Evidence, Research, Integrations, architecture tests, and matching tests. Pull requests targeting `grid-core` also run both Core and Research workflows.

Research CI performs:

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

The checked-in S1 runner uses `execution_scope="policy_reconciliation_only"`. It validates center movement, generation semantics, Risk/reconciliation interaction, queue-reset counts, Risk rejection evidence, and determinism.

It does not infer historical fills or profitability from the controlled center path. A profitability/promotion claim requires later continuous Tier-2 L2 replay and sealed walk-forward/OOS evaluation.

## Verification evidence at final self-review

The final code-bearing head before PR handoff passed:

- Core CI: Ruff format, Ruff lint, **106 core tests**, mypy on **38 source files**;
- Research Integration: **30 tests**;
- focused S1 research mypy: no issues;
- S0 independent-process Evidence digest equality;
- S1 independent-process Evidence digest equality.

Recorded deterministic digests:

- S0: `e0c78118d43dad6c52589e450cbd39069c9cf7636f8eb64a56278573617bd467`
- S1: `f02dfda885d997886dffbdf77cca457989c5cf34066c3febae3f0873d9ca7873`

A fresh PR-triggered verification is required on the final PR head before integration.
