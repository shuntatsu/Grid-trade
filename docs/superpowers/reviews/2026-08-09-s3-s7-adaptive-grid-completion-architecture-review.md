# S3–S7 Adaptive Grid Completion — Architecture and Self-Review

Date: 2026-08-09

## Review conclusion

The S3–S7 implementation is internally consistent with the approved long-biased adaptive-grid design and preserves the repository's existing safety boundaries. The implementation is suitable to merge as **research mechanics infrastructure**.

It is **not** evidence that the strategy is profitable, not production authorization, and not permission to trade live capital.

The remaining economic gate is continuous Tier-2 microstructure replay with realistic fees/funding/queue/latency assumptions, followed by sealed walk-forward/OOS evaluation and stress testing.

## Scope reviewed

The review covers:

- S3 target inventory and inventory skew;
- S4 partial de-risking;
- S5 conditional short overlay;
- S6 funding-aware target bias;
- S7 order-book/microprice reference adjustment;
- adaptive long/short/flatten ladder construction;
- shared Application/Risk/Execution reconciliation behavior;
- deterministic S3–S7 ablation Evidence;
- NautilusTrader order construction;
- hftbacktest BUY/SELL research replay;
- architecture dependency tests and CI gates.

## Dependency direction

The implementation preserves the intended dependency direction:

```text
Domain
  ↑
Strategy      Risk      Execution
   \           |          /
        Application
             ↑
      Research / Integration
```

More precisely:

- `domain/` remains independent of higher layers;
- `strategy/` imports Domain contracts but not Risk, Execution, Integration, or Research;
- `risk/` owns hard veto/filter behavior and does not import Strategy;
- `execution/` owns runtime-neutral working-order reconciliation;
- `application/` coordinates Strategy, Risk, and Execution;
- architecture tests now explicitly prevent Application from importing Evidence, Integrations, or Research;
- `nautilus_trader` and `hftbacktest` remain outside core layers.

This keeps exchange/runtime concerns from becoming strategy inputs implicitly.

## S3 — Inventory Target and Skew

`inventory_target.py` owns target-inventory deviation, reservation-price skew, and side-intensity suppression.

The target is bounded independently of order reconciliation. The adaptive ladder separately enforces the same strategy inventory cap, and the Hard Risk layer remains authoritative after strategy construction.

The split is intentional:

- Strategy expresses desired bounded behavior;
- Risk decides whether the resulting ladder is permissible;
- Execution reconciles desired versus working orders.

No strategy component can raise the Hard Risk limit.

## S4 — Partial De-risking

`de_risk.py` can reduce an existing long target as the causal trend/risk signal worsens. It cannot generate a negative target by itself.

This is important because defensive risk reduction and short-alpha activation are different hypotheses and must remain independently ablatable.

A real boundary defect was found during TDD in the shared `PassivePolicyTransition`: the previous implementation treated every `allow_new_risk=False` decision as a reason to reject the candidate state, which incorrectly rolled back an entirely reduce-only de-risk candidate at the position limit.

The corrected rule is narrower and fail-closed:

1. Risk evaluates the complete proposed ladder.
2. The candidate may commit under `allow_new_risk=False` only when the filtered ladder is exactly equal to the proposed ladder.
3. Every proposed order must be `reduce_only`.
4. Risk must not request `cancel_all_passive` or `target_flat`.
5. If Risk truncates a mixed ladder to a reduce-only subset, the full candidate state is not committed.

This preserves Hard Risk authority while allowing legitimate exposure reduction.

## S5 — Conditional Short Overlay

`conditional_short.py` owns the directional short overlay and flat-before-reverse gate.

The maintained invariant is:

```text
Long → Flat → Short
Short → Flat → Long
```

The invariant is enforced twice:

- the target layer converts an opposite-sign request to flat while current inventory has the opposite sign;
- the adaptive ladder independently fails closed if an opposite-sign target is passed while non-flat.

This duplicate enforcement is deliberate defense-in-depth at two different abstraction boundaries, not duplicated alpha logic.

The adaptive ladder supports:

- new-risk BUY + reduce-only SELL for long orientation;
- new-risk SELL + reduce-only BUY for short orientation;
- reduce-only SELL only when flattening long;
- reduce-only BUY only when flattening short.

Nautilus integration tests cover all four BUY/SELL × reduce-only/non-reduce semantic combinations relevant to the strategy.

## S6 — Funding-Aware Bias

`funding_bias.py` applies a bounded target shift after the directional target is formed.

Funding cannot:

- exceed the absolute target cap;
- bypass flat-before-reverse;
- override Hard Risk;
- authorize production trading.

This keeps funding as an incremental ablation rather than a second execution authority.

## S7 — Order-Book / Microprice Reference

`order_book_reference.py` adjusts the quoting reference from causal microprice and order-book imbalance.

It changes reference price only. It does not increase inventory capacity, bypass target limits, or modify Hard Risk state.

The S7 stage can be disabled while retaining S3–S6 behavior, so any future economic contribution can be measured independently.

## Economic ladder identity and queue preservation

`adaptive_grid.py` combines center, volatility spacing, inventory skew, funding, and order-book reference into one candidate ladder and compares executable economic signatures.

Generation advances once only when executable side/price/quantity/reduce-only semantics change.

A numerical signal or parameter change which collapses to the same executable ladder does not force a generation change.

A coarse-tick edge case was found during TDD: multiple mathematical levels can round to the same exchange price. Emitting several logically distinct levels at one price would create false queue/lifecycle identity. The ladder now safely skips tick-collapsed duplicate levels while preserving strict side monotonicity.

## Application reconciliation

The Application layer preserves one policy decision across the cancellation boundary:

```text
policy decision
  → Risk
  → cancel stale working generation
  → wait until replacement boundary
  → Risk recheck
  → submit the same accepted candidate
```

Policy is not recomputed between cancel and replacement submission. This avoids decision drift and makes Evidence attributable to one candidate generation.

Candidate state remains uncommitted while required cancels are outstanding.

## S3–S7 ablation design

`AdaptiveStage` is an explicit ordered stage contract:

- `S3_INVENTORY`
- `S4_DERISK`
- `S5_SHORT`
- `S6_FUNDING`
- `S7_ORDER_BOOK`

Later diagnostics may be computed internally, but their effects are ignored until their stage is enabled. Tests verify that:

- S3 cannot de-risk or short from later-stage signals;
- S4 can reach flat but cannot open short;
- S5 can create a negative target but does not apply funding/order-book effects;
- S6 applies funding but leaves order-book adjustment disabled;
- S7 adds order-book/microprice reference adjustment.

This makes the intended future incremental economic gates executable rather than documentary only.

## Evidence and controlled runner

`adaptive_runner.py` runs the same stage-neutral controlled market/signal/position fixture through each S3–S7 stage.

The position path is explicitly exogenous. It is not inferred from fills and therefore cannot be used as PnL evidence.

The runner records canonical Evidence for:

- market state;
- center and spacing;
- de-risk;
- short overlay;
- funding;
- inventory target/skew;
- order-book reference;
- Risk;
- reconciliation;
- stage summary.

Each stage has a canonical SHA-256 digest. The aggregate digest is a deterministic SHA-256 envelope over the ordered per-stage digests.

The runner executes the same comparison twice and CI independently executes the module in fresh Python processes. PnL is fixed to zero and both `production_authorized` and `alpha_validated` remain false.

The checked-in adaptive Evidence proves mechanics reproducibility only.

## NautilusTrader boundary

The pinned runtime is `nautilus_trader==1.230.0`.

The construct-only adapter:

- rejects tick/lot misalignment rather than silently rounding;
- maps Domain BUY/SELL to Nautilus BUY/SELL;
- preserves the Domain `reduce_only` flag;
- constructs GTC post-only limit orders;
- does not submit orders during tests.

Tests cover:

- BUY new-risk;
- SELL reduce-only;
- SELL new-risk for the short overlay;
- BUY reduce-only for short de-risking.

This validates the order-construction boundary, not live Hyperliquid exchange behavior.

## hftbacktest boundary

The pinned runtime is `hftbacktest==2.4.4`.

The research adapter uses risk-adverse queueing, partial-fill exchange behavior, explicit tick/lot sizes, and finite latency-aware replay timeout logic. BUY and SELL passive replay paths are covered with deterministic fixtures.

Important limitation: the hftbacktest adapter submits side/price/quantity but does not model Hyperliquid/Nautilus exchange-side `reduce_only` enforcement. Therefore:

- hftbacktest is used for passive fill/queue/latency research;
- Strategy/Risk enforce the desired reduce-only contract before replay/runtime mapping;
- Nautilus construction preserves the flag;
- no statement is made that hftbacktest validates exchange reduce-only rejection/execution semantics.

This limitation is explicit rather than hidden behind a misleading test.

## Architecture-test coverage

Architecture tests enforce:

- Domain independence;
- Strategy independence from Risk/Execution/Integration/Research;
- Risk independence from Strategy/Execution/Integration/Research;
- Execution independence from Strategy/Risk/Integration/Research;
- Application independence from Evidence/Integration/Research;
- no `nautilus_trader` or `hftbacktest` imports in core layers.

The S3–S7 implementation stays inside these boundaries.

## Verification evidence before final PR

The implementation branch has already reached the following verified gates before documentation finalization:

- Core: 164 tests passed with strict mypy on 63 source files;
- Research/Integration: 37 tests passed before the final SELL replay addition;
- focused S1/S2/adaptive research mypy: success;
- fresh-process deterministic Evidence checks: S0, S1, S2, and adaptive all matched;
- adaptive aggregate digest at that checkpoint: `3af625539b90f53b0db34d3261f16669bd5618a6677bfa022a34df1f2b38d071`.

Final PR verification must be rerun after all review/documentation changes and is the authoritative completion evidence.

## Remaining limitations and NO-GO boundary

S3–S7 mechanics completion does not establish an economic edge.

Before any strategy promotion, the project still requires:

- continuous historical Tier-2 L2/trade replay;
- realistic maker/taker fees and rebates;
- historical funding;
- queue-position and cancel/replace timing assumptions;
- latency and stale-data stress;
- adverse-selection markouts;
- PnL attribution;
- rolling walk-forward validation;
- sealed final OOS evaluation;
- bull/bear/sideways/crash/high-vol/low-vol decomposition;
- explicit stage-by-stage incremental gates.

Production/live trading remains **NO-GO** even if those research gates later pass; production authorization is a separate execution/safety review.

## Self-review result

No architecture violation or unresolved correctness defect is known at this review point.

The two material defects discovered during TDD were corrected rather than hidden by weaker tests:

1. reduce-only candidate state rollback at a Hard Risk new-risk veto;
2. duplicate mathematical grid levels collapsing to one tick price.

The design remains intentionally conservative: ambiguous execution behavior fails closed, later features are removable, and controlled mechanics Evidence never masquerades as historical profitability.
