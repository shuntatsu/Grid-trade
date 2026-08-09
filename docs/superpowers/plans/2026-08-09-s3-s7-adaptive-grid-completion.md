# S3-S7 Adaptive Grid Completion Plan

> **Execution:** Inline TDD on `adaptive-grid-completion`, preserving S0-S2 digests and the existing Architecture/Risk/Execution boundaries.

**Goal:** Complete the approved rule-based strategy stack through S7 while keeping every component independently ablatable and keeping production/economic promotion fail-closed.

## Non-negotiable constraints

- LGPL-3.0.
- Single instrument.
- RESEARCH / NO-GO remains authoritative.
- No future leakage: all S3-S7 inputs are explicit current causal inputs.
- Hard Risk remains outside Strategy and can veto every desired ladder.
- Long/short sign reversal must pass through flat.
- Later stages may modify target/reference intent but never bypass `Q_max`, cancel-before-replace, post-cancel Risk recheck, or emergency authority.
- No RL in S3-S7.
- No hidden historical-profit claim from synthetic mechanics fixtures.
- Existing S0, S1, S2 deterministic digests must remain unchanged.

## Shared causal inputs

Create `AdaptiveSignals` rather than expanding `MarketSnapshot`, preserving existing contracts/digests:

- `trend_score ∈ [-1, 1]` (negative bearish),
- `funding_rate` finite Decimal (positive = long holding cost),
- `order_book_imbalance ∈ [-1, 1]` (positive buy-heavy),
- optional positive finite `microprice`.

## S3 — Inventory Target and Skew

Create `inventory_target.py`.

- Base target is configurable positive long quantity, bounded by `max_abs_target`.
- Inventory error: `(position - target) / max_abs_target`.
- Reservation shift: `-reservation_skew_bps × inventory_error`, clipped by configured maximum.
- Side skew is suppressive only: excess long suppresses bids; under-target long suppresses asks. Neither side is amplified above 1.0.
- Output includes target, normalized error, reservation shift, bid scale, ask scale.

Gate represented mechanically: excess inventory must reduce the dangerous side and move reservation price toward de-risking.

## S4 — Partial De-risking

Create `de_risk.py`.

- Use explicit causal `trend_score` supplied via `AdaptiveSignals`.
- Healthy regime keeps the S3 target.
- Warning bearish regime multiplies positive target by `warning_target_fraction`.
- Severe bearish regime multiplies positive target by `severe_target_fraction`, normally zero.
- S4 cannot create a negative target; it only shrinks long exposure.
- Output includes regime and requested/effective target.

## S5 — Conditional Short Overlay

Create `conditional_short.py`.

- Strong bearish trend can request a negative target, continuously increasing toward `max_short_target` as trend approaches -1.
- Short overlay is disabled above its entry threshold.
- Sign reversal is fail-closed through flat:
  - current long + requested short => effective target 0 / `FLATTEN_LONG`;
  - current flat/short + requested short => negative target / `SHORT`;
  - current short + requested long => effective target 0 / `FLATTEN_SHORT`.
- No direct positive-to-negative or negative-to-positive target transition is emitted while inventory has the opposite sign.

## S6 — Funding-aware Bias

Create `funding_bias.py`.

- Normalize `funding_rate / funding_scale` into `[-1, 1]`.
- Positive funding shifts target downward; negative funding shifts target upward.
- Maximum funding target shift is a configured fraction of absolute target capacity.
- Clip requested target to configured position target bounds.
- Reapply flat-before-reverse after funding adjustment so funding can never bypass S5 sign safety.

## S7 — Order-book / Microprice Reference

Create `order_book_reference.py`.

- Microprice, when present, is blended into the center by configurable weight `[0,1]` using its relative displacement from current mid.
- OBI shifts reference price by bounded basis points in the imbalance direction.
- Output reference must remain finite/positive.
- Missing microprice falls back to the center without inventing data.

## Two-sided inventory-aware ladder

Create a stage-neutral two-sided ladder primitive.

- BUY prices round down; SELL prices round up.
- Positive target: BUY can add long; SELL is reduce-only.
- Zero target: both sides reduce-only; no new directional risk.
- Negative target: SELL can add short; BUY is reduce-only.
- S3 side scales multiply base order quantity and can suppress a side to zero.
- Economic signature remains independent of generation/client IDs.

## Combined rule-based stack

Create `adaptive_grid.py` with `AdaptiveGridState` and `AdaptiveGridDecision`.

Decision sequence:

1. S1 causal Dynamic Center proposal.
2. S2 causal Volatility Spacing proposal.
3. S3 base target + inventory skew.
4. If enabled: S4 partial de-risk target.
5. If enabled: S5 conditional short target with flat-before-reverse.
6. If enabled: S6 funding target shift with flat-before-reverse.
7. If enabled: S7 book/microprice reference adjustment.
8. Apply inventory reservation shift to reference.
9. Build one candidate two-sided ladder at `generation + 1`.
10. Compare tick-rounded economic signature with current ladder.
11. Increment generation at most once and only for an actual executable change.

Expose `enabled_stage` in `[3,7]` so S3/S4/S5/S6/S7 are directly ablatable using the exact same engine.

## Application integration

Create a thin `application/adaptive_grid.py` wrapper over `PassivePolicyTransition`.

- Strategy is evaluated once per decision.
- Candidate state commits only after cancellation completes and Risk still accepts the same ladder.
- Partial old-generation fills never coexist with a new-generation submit in the same reconciliation phase.

## Evidence

Add enum kinds without schema bump:

- `INVENTORY_TARGET_DECISION`
- `DE_RISK_DECISION`
- `SHORT_OVERLAY_DECISION`
- `FUNDING_BIAS_DECISION`
- `ORDER_BOOK_DECISION`

Create `research/s7_runner.py` as a deterministic synthetic mechanics oracle. It must exercise:

- healthy long target,
- excess-long inventory skew,
- warning/severe de-risk,
- long→flat→short transition,
- positive/negative funding bias,
- microprice/OBI reference shift,
- Risk rejection,
- cancel-before-replace,
- exact duplicate-run equality.

Result remains `policy_reconciliation_only`, zero PnL, `production_authorized=False`, `alpha_validated=False`.

## Tests / TDD gates

Write all focused tests before implementation, then implement in this order:

1. `AdaptiveSignals` validation.
2. S3 inventory target/skew.
3. S4 de-risk.
4. S5 short overlay/flat gate.
5. S6 funding bias/flat gate.
6. S7 order-book reference.
7. Two-sided ladder geometry.
8. Combined stage-ablation engine.
9. Application Risk/reconciliation wrapper.
10. Evidence + deterministic S7 runner.

## Final verification

- `uv lock --check`
- Ruff format with diff
- Ruff lint
- full core pytest
- strict core mypy
- Research Integration full suite
- focused S7 runner mypy
- AST architecture tests
- independent-process S0/S1/S2/S7 digest equality
- branch comparison behind `grid-core` by 0
- complete architecture/self-review document
- PR-triggered Core + Research success before fast-forward integration

## Completion boundary

Rule-based S0-S7 implementation is considered mechanically complete only after the final verification above. Economic strategy promotion is a **separate** gate and remains NO-GO until continuous Tier-2 L2 historical replay and sealed walk-forward/OOS evaluation exist and pass. This distinction must remain visible in README and final report.
