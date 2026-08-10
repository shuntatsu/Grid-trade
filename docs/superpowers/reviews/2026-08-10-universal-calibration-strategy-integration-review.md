# Universal Calibration → Adaptive Grid Integration Review

Date: 2026-08-10
Branch: `universal-calibration-strategy-integration`
Status: implementation review complete; exact final-HEAD Core/Research verification required before merge
Production: **RESEARCH / NO-GO**

## Scope reviewed

This review covers the Phase C path which composes the verified Universal Foundation and Microstructure calibration with risk-derived inventory capacity and the existing S3–S7 adaptive Grid mechanics. The legacy fixed-fixture mechanics path remains a regression oracle and is not replaced.

Reviewed boundaries:

`Calibration → Application composition ← Risk sizing → Strategy → Hard Risk / Reconciliation`

Calibration remains market-only. Risk sizing remains account/risk-only. Application is the only layer allowed to combine calibrated market state, inventory capacity, and Strategy configuration.

## Architecture findings

### 1. Calibration / Risk / Strategy dependency direction — PASS

- `grid_trade.calibration` does not import Strategy, Risk, Application, Execution, Integrations, or Research.
- `grid_trade.risk` does not import Calibration.
- `grid_trade.application.calibrated_adaptive` owns composition and delegates actual policy decisions to the existing Strategy implementation.
- Hard Risk and generic cancel-before-replace reconciliation remain outside Strategy.
- Existing AST boundary tests continue to enforce these directions.

### 2. Symbol-independent calibration and runtime preparation — PASS

- No symbol-name switch or BTC/ETH/SOL constant is used in the calibrated path.
- Volatility, trend, GLFT quote-distance, execution cost, funding score, and OFI/order-book state are expressed in relative or normalized units before Strategy mapping.
- Metamorphic tests verify arbitrary instrument rename does not change numeric behavior.
- Common price/size scaling tests verify normalized outputs remain invariant while absolute executable prices scale appropriately.

### 3. Risk-derived inventory capacity — PASS

- `InventoryCapacity.q_max` is supplied by `grid_trade.risk.sizing`.
- Application rounds `q_max` **down** to venue quantity step; it never rounds up or enlarges risk capacity.
- Base long target, maximum short target, and per-level quantity are fractions of the effective risk capacity.
- Tests verify equity/risk capacity changes quantity while symbol identity does not.

### 4. Dynamic runtime config / generation semantics — FIXED AND PASS

Review found that a calibrated runtime can change order quantity or spacing while market state is otherwise unchanged. Rebuilding the currently working ladder with the new config would incorrectly hide an economic change and preserve a stale generation.

Fix:
- `decide_adaptive_grid(..., previous_config=...)` reconstructs the working ladder with the previously applied config.
- Candidate ladder is built with the new calibrated config.
- Legacy callers omit `previous_config`, retaining old behavior.
- `CalibratedAdaptiveState` stores the applied config.
- Cancel phase and Risk rejection keep the old applied config.
- Accepted replacement commits the new config only when the economic candidate becomes authoritative.

### 5. Foundation / L2 market consistency — FIXED AND PASS

Review found that Universal Calibration originally checked timestamp/source/instrument identity but could accept a Foundation mid that disagreed with the same-timestamp L2 top-of-book mid.

A RED regression test was added first. It failed with 297 existing Core tests passing. Universal Calibration now fails closed unless `CalibrationObservation.mid == TopOfBookObservation.mid` exactly for the composed observation.

This avoids silently combining contradictory market states at the same causal decision time.

### 6. Future-label causality — PASS

- OFI impact samples cannot affect a decision before `matured_at`.
- Future/pending samples cannot evict the current matured fit window.
- Markout cost uses matured samples only.
- Integrated tests verify adding an unmatured OFI label cannot change the current calibrated adaptive preparation.
- No OHLC substitute is used to fabricate L2, queue, fill, or markout evidence.

### 7. Funding and order-book normalization — PASS

- S6 consumes the already normalized funding score with `FundingBiasConfig.funding_scale = 1`.
- S7 consumes calibrated bounded order-book score and relative microprice displacement.
- Microprice is reconstructed from current mid and calibrated relative displacement only when S7 microstructure readiness is true.
- Missing required S6/S7 inputs fail closed rather than falling back to symbol constants.

### 8. Long → Flat → Short and Hard Risk authority — PASS

- Calibrated target sizing still delegates to the existing conditional-short logic.
- Integrated tests verify a bearish transition while long first targets Flat; only a later decision from Flat may become Short.
- Generic Hard Risk retains final veto authority.
- Fully reduce-only de-risk candidates remain permitted at a hard position limit under the existing reviewed Risk contract.

## Deterministic Evidence review

A new additive calibrated-adaptive research runner covers:

- causal Universal Calibration generation;
- risk-derived `q_max` and binding constraint;
- calibrated center/spacing/quantity parameters;
- normalized trend/funding/order-book signals;
- Adaptive generation and reconciliation;
- arbitrary-symbol rename invariance;
- common price/size scale invariance;
- explicit NO-GO flags.

The runner does not synthesize economic PnL or historical fills.

Pinned calibrated-adaptive Evidence digest:

`709481dcab22d0f611d89a5690f8fb28f3cc7f2a238f0c80e6a0e67d93606f63`

Existing historical digests must remain unchanged in the final Research run:

- S0: `e0c78118d43dad6c52589e450cbd39069c9cf7636f8eb64a56278573617bd467`
- S1: `f02dfda885d997886dffbdf77cca457989c5cf34066c3febae3f0873d9ca7873`
- S2: `9478000d146bee86cc39ddff6ff6d7627c19bc38e05e9ac6a5bfc835621aae22`
- Adaptive S3–S7: `3af625539b90f53b0db34d3261f16669bd5618a6677bfa022a34df1f2b38d071`
- Microstructure Calibration: `7e56c2e56b29c6ad15b2f5b2f8d6440169fad6bc7862f0085ccb5eb09a85e239`

## TDD / self-review findings fixed during implementation

- Public Universal Calibration API missing from package export — RED then fixed.
- Calibrated Application transition API missing from package export — RED then fixed.
- Existing adaptive working ladder could be reconstructed with new runtime config — RED then fixed with `previous_config`.
- Foundation/L2 mid disagreement could be silently composed — RED then fixed fail-closed.
- Several test fixture/API typing issues were corrected without weakening behavioral assertions.

## Scope purity

The calibrated path does **not**:

- add symbol-specific parameters;
- authorize live trading;
- claim profitability;
- infer fills from OHLC;
- weaken Hard Risk;
- replace or mutate legacy S0–S7 deterministic mechanics evidence;
- allow online calibration to expand the risk budget.

## Remaining economic gate

Phase C completes the reusable causal calibration-to-strategy mechanics, not strategy economics. Promotion still requires continuous real/historical Tier-2 replay with realistic maker fees/rebates, funding, queue position, partial fills, cancel latency, adverse-selection markout, outage/extreme-volatility stress, followed by symbol-disjoint walk-forward validation and a sealed OOS test.

Any S3–S7 increment which fails its ablation gate should be removed rather than rescued by extra complexity.

## Final merge gate

Before merge, verify the **exact final branch HEAD** with:

1. Core CI: lock, Ruff format, Ruff lint, all non-Research tests, strict mypy.
2. Research Integration: all Research/Integration tests and all focused mypy boundaries.
3. Separate-process equality for all six Evidence digests.
4. Full diff review: no temporary workflows, TODO/TBD/FIXME/HACK residue, secret material, symbol branches, or architecture-boundary violations.
5. PR remains Draft until these gates are green and the user explicitly requests merge.
