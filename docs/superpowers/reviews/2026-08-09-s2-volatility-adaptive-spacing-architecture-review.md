# S2 Volatility-Adaptive Spacing Architecture Review

Date: 2026-08-09
Repository: `shuntatsu/Grid-trade`
Branch: `s2-volatility-spacing`
Base: `grid-core`
License: LGPL-3.0
Production status: RESEARCH / NO-GO

## Review scope

This review covers the complete S2 implementation after the TDD pass: causal spacing inputs, execution-cost-floor arithmetic, combined center/spacing generation semantics, Strategy/Application/Risk/Execution boundaries, cancel-before-replace state timing, S0/S1 compatibility, Evidence determinism, and CI coverage.

## Findings and resolutions

### 1. S1 orchestration would have been duplicated by every later ablation

**Finding:** S1 contained correct Risk and reconciliation semantics, but copying that orchestration into S2-S7 would create multiple authorities for replacement-aware open-order accounting, Risk rollback, and state commit timing.

**Resolution:** the behavior is extracted into `application/passive_policy.py` as a generic `PassivePolicyTransition`. S1 and S2 are thin wrappers around the same Application primitive.

**Result:** Strategy stages produce deterministic candidate state plus desired ladder; Application remains the sole coordinator of Strategy, Risk, and Execution.

### 2. Candidate state must not become authoritative during cancel-only phases

**Finding:** a center/spacing candidate can be valid but still require cancellation of the old generation before submission.

**Resolution:** `PassivePolicyTransition.next_state` remains `previous_state` while any cancellation remains. The candidate becomes authoritative only when cancellation has completed and the post-cancel Risk check still accepts the same desired ladder.

### 3. Post-cancel market-policy recomputation would double-advance generation

**Finding:** recomputing S2 after cancellation could observe the same snapshot again and produce an extra generation or a different spacing candidate.

**Resolution:** continuation carries the exact original decision, candidate state, and desired ladder. It re-evaluates **Risk only**, not Strategy.

### 4. Volatility spacing could accidentally round below the execution-cost floor

**Finding:** grid geometry uses integer basis-point spacing. Ordinary nearest/floor rounding can convert a fractional configured cost floor such as 12.1 bps into 12 bps.

**Resolution:** S2 uses exact `Decimal` arithmetic and `ROUND_CEILING` only at the final conversion to integer basis points. The configured floor is therefore never weakened by discretization.

### 5. Center and spacing changes must not create two queue resets in one decision

**Finding:** treating Dynamic Center and Volatility Spacing as independent state machines could increment generation twice when both move on the same market event.

**Resolution:** `decide_s2_grid` computes both candidates first, constructs one candidate executable ladder at `generation + 1`, and performs one economic-signature comparison against the current ladder.

**Result:** one market decision can advance generation by at most one.

### 6. Numerical parameter movement does not necessarily justify queue loss

**Finding:** either center or spacing can change numerically while tick-rounded executable order prices remain unchanged.

**Resolution:** `ladder_economic_signature` is now a shared Strategy primitive using only `(side, level, price, quantity, reduce_only)`. Client IDs and generation are intentionally excluded. If the candidate signature is unchanged, S2 retains the complete prior center/spacing/generation state and working ladder.

S1 was also moved to the shared helper without changing its Evidence digest.

### 7. Replacement open-order budgets must not double-count orders being cancelled

**Finding:** old strategy orders and their replacements are not simultaneously intended to remain live.

**Resolution:** the shared Application primitive subtracts known strategy working orders from the current open-order count before adding the desired replacement count. Non-strategy open orders remain counted.

### 8. Risk rejection must not masquerade as successful adaptation

**Finding:** a strategy candidate may request a wider/narrower grid, but hard Risk can reject the full ladder.

**Resolution:** S2 generation, spacing-change count, and queue-reset count depend on the actually committed state after Risk/reconciliation. Rejected candidates do not advance any of those counters, and Risk reasons are recorded explicitly.

### 9. S2 Evidence must not manufacture economic evidence

**Finding:** the controlled S2 fixture can validate state/reconciliation mechanics, but it has no continuous historical L2 queue context from which to infer realistic fills or profitability.

**Resolution:** `S2ComparisonResult` reports a zero `PnLBreakdown`, `execution_scope="policy_reconciliation_only"`, `production_authorized=False`, and `alpha_validated=False`. The runner emits market, center, spacing, Risk, reconciliation, and run-summary Evidence only.

### 10. Determinism must be measured, not asserted

**Resolution:** the public S2 runner executes the same controlled comparison twice and sets `deterministic` from exact result equality. Research CI separately executes the checked-in runner in independent Python processes and requires identical SHA-256 digests.

### 11. S2 scope purity

The reviewed S2 implementation contains no:

- trend or momentum forecast;
- target inventory or inventory skew;
- partial de-risking;
- short opening logic;
- funding bias;
- order-flow imbalance or microprice adjustment;
- adaptive order sizing;
- RL control.

Those remain isolated later ablations.

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

integrations remain external-runtime boundaries.
```

The existing AST architecture tests continue to enforce core-layer direction and prevent `hftbacktest` / `nautilus_trader` imports from entering core policy layers.

## Verification evidence

Fresh branch verification after the S2 code pass:

- Core CI: format PASS, Ruff lint PASS, core tests PASS, strict mypy PASS;
- Research Integration: **34 passed**;
- focused S1 research mypy: PASS;
- focused S2 research mypy: PASS;
- S0 independent-process Evidence equality: PASS;
- S1 independent-process Evidence equality: PASS;
- S2 independent-process Evidence equality: PASS.

Recorded deterministic digests:

- S0: `e0c78118d43dad6c52589e450cbd39069c9cf7636f8eb64a56278573617bd467`
- S1: `f02dfda885d997886dffbdf77cca457989c5cf34066c3febae3f0873d9ca7873`
- S2: `9478000d146bee86cc39ddff6ff6d7627c19bc38e05e9ac6a5bfc835621aae22`

## Remaining economic limitation

S2 is mechanically complete but **not economically promoted**. The high-volatility-spacing hypothesis still requires continuous Tier-2 L2 replay and sealed walk-forward/OOS evaluation that measures maker fill rate, queue resets, turnover, adverse-selection markout, fees, funding where applicable, inventory path, and realized PnL. Until those gates exist and pass, S2 remains RESEARCH / NO-GO.
