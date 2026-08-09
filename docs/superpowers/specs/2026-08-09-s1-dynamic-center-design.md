# S1 Dynamic Center Design

Date: 2026-08-09
Status: Design for review
Repository: `shuntatsu/Grid-trade`
Branch: `s1-dynamic-center`
Base: `grid-core`
License: LGPL-3.0
Production status: NO-GO

## 1. Purpose

S1 tests one change only: whether a stateful, thresholded, bounded grid center improves fixed-grid drift without creating unacceptable cancel/replace churn.

S1 must not introduce trend, inventory skew, volatility-adaptive spacing, short exposure, funding bias, order-book imbalance, microprice, or RL. Those remain separate later ablations.

The causal question is deliberately narrow:

> Does a pure Dynamic Center improve execution robustness relative to an episode-fixed S0 center when every other grid parameter and risk rule is held constant?

## 2. S0 comparison semantics

The current S0 grid builder computes one ladder from the `MarketSnapshot.mid` supplied to that call. It is a single-snapshot primitive, not a stateful time-series center policy.

For S1 ablation, S0 is defined explicitly as:

- initialize the episode center from the first valid snapshot mid,
- hold that center fixed for the episode,
- keep S0 spacing, levels, quantity, tick rounding, risk checks, execution model, fee model, and evidence rules unchanged.

This removes ambiguity. A benchmark that jumps to every new mid would itself be a form of Dynamic Center and is not the S0 baseline.

Existing single-snapshot S0 behavior and deterministic fixture evidence must remain backward compatible.

## 3. Selected approach

Use thresholded plus maximum-step re-anchoring.

Let:

- `m_t` = current causal mid,
- `c_{t-1}` = previous effective center,
- `theta` = re-anchor threshold in basis points,
- `kappa` = maximum center movement in basis points per decision.

Deviation:

`d_t = (m_t - c_{t-1}) / c_{t-1} * 10_000`

If:

`abs(d_t) < theta`

then the proposal keeps the previous center.

If:

`abs(d_t) >= theta`

then compute the bounded movement:

`step_bps = sign(d_t) * min(abs(d_t), kappa)`

and:

`candidate_center = c_{t-1} * (1 + step_bps / 10_000)`

The rule is symmetric for upward and downward moves.

No directional forecast is present. The center moves only because current mid moved away from the previous center.

## 4. Configuration contract

Introduce immutable `DynamicCenterConfig` with:

- `reanchor_threshold_bps: Decimal`
- `max_step_bps: Decimal`

Validation:

- both values finite and strictly positive,
- `max_step_bps < 10_000` so a single downward proposal cannot make the center non-positive,
- no hidden defaults that imply market optimality.

Research parameter selection is training/validation-only. The sealed test cannot be used to tune either value.

Controlled mechanical fixtures may use explicit values chosen only to exercise invariants. They are not economic defaults.

## 5. State contract

Introduce immutable `DynamicCenterState`:

- `center: Decimal`
- `generation: int`

Validation:

- center finite and strictly positive,
- generation non-negative.

Initialization is explicit and separate from steady-state updates:

- when no prior state exists, initialize `center = snapshot.mid`,
- generation starts at `0`,
- initial S1 ladder must be economically identical to the S0 ladder for the same initial snapshot and fixed-grid configuration.

The state contains no trend score, inventory target, funding state, order-book signal, volatility multiplier, or learned state.

## 6. Proposal and effective-decision contracts

Responsibility is split so center arithmetic does not depend on grid geometry.

### 6.1 `CenterProposal`

A pure proposal function maps `(snapshot, prior_state_or_none, dynamic_center_config)` to a proposal.

Required proposal fields:

- previous center or `None` during initialization,
- market mid,
- signed deviation bps,
- proposed center,
- previous generation or `None`,
- proposal reason.

Proposal reasons:

- `INITIALIZED`
- `WITHIN_THRESHOLD`
- `BOUNDED_REANCHOR`

Exact threshold behavior is explicit: equality enters re-anchor consideration (`abs(d_t) >= theta`).

### 6.2 `CenterDecision`

A second pure resolution step combines the proposal with the fixed-grid configuration and the current economic ladder.

Required decision fields:

- proposal fields,
- effective center,
- effective generation,
- `reanchored: bool`,
- `economic_ladder_changed: bool`,
- final decision reason.

Final decision reasons:

- `INITIALIZED`
- `WITHIN_THRESHOLD`
- `BOUNDED_REANCHOR`
- `NO_EFFECTIVE_LADDER_CHANGE`

`NO_EFFECTIVE_LADDER_CHANGE` can only be produced by this grid-aware resolution step, never by the center-arithmetic proposal function.

## 7. Shared ladder geometry

S0 and S1 must share the same ladder geometry implementation so the center source is the only strategy difference.

Extract or introduce a pure primitive conceptually equivalent to:

`build_long_grid_at_center(center, fixed_grid_config, generation, client_order_prefix)`

Requirements:

- same level count as S0,
- same spacing formula as S0,
- same per-level quantity as S0,
- same tick-rounding rule as S0,
- same strictly descending price invariant,
- same positive-price invariant,
- existing `PassiveOrderIntent` domain shape remains unchanged unless a separately justified migration is required.

`build_fixed_long_grid(snapshot, ...)` remains a backward-compatible wrapper around `snapshot.mid` and the S0 client-order prefix.

S1 uses the shared primitive with its effective center and an S1-specific client-order prefix.

## 8. Effective-change suppression

A numerical center proposal must not automatically destroy queue priority.

After computing `candidate_center`, build a candidate ladder using `generation + 1` and compare its economic fields with the current effective ladder while ignoring identity-only fields such as client order ID and generation.

Economic comparison includes:

- side,
- level,
- price,
- quantity,
- reduce-only.

If the candidate center produces exactly the same economic ladder after tick rounding:

- keep the previous effective center,
- keep the previous generation,
- emit `NO_EFFECTIVE_LADDER_CHANGE`,
- emit no cancel/replace work.

The previous center is intentionally retained in this case. A later market move is therefore still measured from the last center that actually changed executable orders.

This prevents hidden center drift and queue resets caused by changes that have no executable price effect.

## 9. Generation and reconciliation semantics

A generation changes only when the executable ladder changes.

When a re-anchor is effective:

1. increment generation by exactly one,
2. build the new desired ladder with new generation IDs,
3. pass it through the existing deterministic reconciliation layer,
4. cancel stale/conflicting working orders first,
5. submit the new generation only after the cancellation phase has completed.

The existing cancel-before-replace safety contract is reused. S1 must not create a second reconciliation engine.

When no effective re-anchor occurs:

- existing working orders remain untouched,
- no generation bump occurs,
- no queue position is intentionally discarded.

## 10. Risk semantics

S1 cannot bypass or weaken any S0 risk control.

Before any replacement ladder becomes admissible, the existing RiskController must evaluate:

- current position,
- projected position if all proposed new risk-increasing orders fill,
- open-order budget,
- drawdown state,
- data freshness,
- all existing hard limits.

If a re-anchor candidate fails risk:

- do not submit the candidate generation,
- preserve explicit risk reasons in Evidence,
- do not claim the candidate center as successfully deployed,
- retain the last effective center/generation as the deployed state,
- fail closed.

S1 remains long-only. No short order is introduced here.

## 11. Evidence additions

Add a canonical center-decision evidence event for every decision step in the stateful S1 runner.

Required payload:

- previous center,
- market mid,
- deviation bps,
- threshold bps,
- max-step bps,
- proposed center,
- effective center,
- previous/effective generation,
- re-anchored flag,
- proposal reason,
- final decision reason,
- whether economic ladder prices changed,
- whether risk accepted deployment.

All Decimal values remain canonical string-preserving evidence values under the existing deterministic JSONL/SHA-256 ledger rules.

S0 evidence schema should not change solely to support S1 unless a shared schema migration is explicitly necessary and tested for backward compatibility.

## 12. Stateful ablation runner

Introduce a multi-step research runner that executes the same causal market sequence as two arms:

- S0 episode-fixed center,
- S1 thresholded/bounded Dynamic Center.

Both arms must use identical:

- initial snapshot,
- market event sequence,
- fixed grid parameters,
- fill/queue model,
- latency assumptions,
- fees,
- risk limits,
- starting inventory,
- evidence precision.

The runner records at minimum:

- center path,
- absolute center-to-mid error path,
- re-anchor count,
- generation count,
- cancel count,
- submit count,
- queue-reset count,
- order lifetime statistics where available,
- fill count and partial-fill behavior,
- ending inventory,
- fee PnL,
- mechanics-only PnL until full economic attribution exists.

The runner must not reuse future snapshots when computing a current center decision.

## 13. S1 controlled mechanical fixtures

Before historical study, deterministic fixtures must cover:

1. no movement below threshold -> no re-anchor,
2. exact threshold -> re-anchor consideration,
3. upward move below max step -> center catches current mid,
4. upward move above max step -> movement capped,
5. symmetric downward behavior,
6. repeated large drift -> center moves in bounded steps over successive decisions,
7. candidate center changes numerically but tick-rounded ladder is unchanged -> no generation change,
8. effective ladder change -> cancel-before-replace,
9. partial-fill working order during re-anchor -> no unsafe duplicate risk,
10. projected-position or order-count failure -> candidate generation rejected with explicit risk reason and prior deployed center retained,
11. same sequence in two independent Python processes -> identical Evidence digest.

## 14. Historical research parameterization

S1 adds exactly two tunable strategy parameters: `theta` and `kappa`.

Initial research search space should be expressed relative to S0 grid spacing to remain interpretable across spacing settings, with candidate ratios:

- threshold / spacing: `0.25`, `0.50`, `1.00`,
- max-step / spacing: `0.50`, `1.00`, `2.00`.

These are search candidates, not claims of optimal values.

Invalid or redundant combinations may be pruned before sealed evaluation, but parameter search must remain documented and reproducible.

## 15. S1 promotion gate

S1 is not promoted because it has a higher isolated total return.

Hard requirements:

- all S0 safety invariants remain green,
- deterministic state/replay/evidence,
- no future leakage,
- no increase in unauthorized exposure,
- no hidden strategy inputs beyond current mid and previous effective center state,
- realistic queue/partial-fill replay retained.

Comparative requirements versus S0 on validation/OOS research windows:

- lower center-to-mid drift during persistent movement,
- cancel/replace churn remains bounded and is explicitly reported,
- no material degradation in maker fill quality,
- no material worsening of inventory tail or drawdown,
- net economics are improved, or execution robustness improves materially at comparable economics,
- benefit survives latency/queue sensitivity rather than appearing only under optimistic fills.

A candidate that reduces drift only by constantly resetting orders and destroying queue priority is rejected.

A candidate that improves one regime but materially worsens the overall risk profile is rejected unless the trade-off is explicitly accepted in a later reviewed design.

## 16. Metrics specific to S1

In addition to existing metrics, report:

- mean absolute center error in bps,
- p95 absolute center error in bps,
- time-integrated absolute center error,
- re-anchors per hour/day,
- cancellations per re-anchor,
- submissions per re-anchor,
- unchanged-decision ratio,
- effective-ladder-change ratio,
- queue resets per unit time,
- average order age at cancellation,
- maker fills lost/retained around re-anchor where observable.

These metrics separate genuine drift improvement from brute-force order churn.

## 17. Explicit non-goals for S1

S1 does not implement:

- trend or momentum bias,
- EWMA center smoothing,
- volatility-dependent spacing,
- target inventory,
- inventory skew,
- staged de-risking,
- short grid or short overlay,
- funding-aware bias,
- OFI, imbalance, microprice,
- adaptive order sizing,
- RL control,
- live-capital authorization.

If any of these are required to make S1 look profitable, S1 is considered unsupported rather than silently expanding scope.

## 18. Expected implementation boundaries

Likely focused additions:

- `grid_trade/strategy/grid_geometry.py` — shared center-based ladder geometry,
- `grid_trade/strategy/dynamic_center.py` — config/state/proposal/decision logic,
- `grid_trade/research/s1_runner.py` — stateful S0-vs-S1 ablation orchestration,
- Evidence enum/schema extension for center decisions,
- mirrored unit/property/research tests.

Existing S0, risk, reconciliation, hftbacktest, Nautilus mapping, and Evidence code should be reused rather than duplicated.

## 19. Completion boundary

S1 implementation is complete only when:

- the approved mechanics are implemented with TDD,
- S0 regression behavior remains green,
- all new unit/property/research tests pass,
- strict type/lint/format gates pass,
- pinned `hftbacktest==2.4.4` and `nautilus_trader==1.230.0` research CI remains green,
- independent-process Evidence determinism remains green,
- the S1 research runner produces comparative S0/S1 evidence,
- documentation continues to state `NO-GO` and makes no alpha/profitability claim before historical OOS evaluation.
