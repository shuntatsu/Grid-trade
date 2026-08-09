# Universal Calibration Foundation — Architecture & Self Review

Date: 2026-08-09
Branch: `universal-calibration-final`
Scope: Universal causal calibration foundation only
Production status: RESEARCH / NO-GO

## 1. Reviewed scope

This review covers the foundation introduced by `docs/superpowers/specs/2026-08-09-universal-causal-market-calibration-design.md` and `docs/superpowers/plans/2026-08-09-universal-calibration-foundation.md`:

- immutable calibration contracts and readiness;
- robust rolling relative-price volatility;
- dimensionless normalized trend;
- robust rolling funding normalization;
- deterministic Decimal arithmetic for evidence-sensitive calculations;
- calibration engine causality, identity continuity, state restoration, and frozen meta-parameters;
- account/risk-derived inventory capacity in `grid_trade.risk.sizing`;
- symbol-invariance and price-scale metamorphic tests;
- architecture dependency gates;
- regression of existing S0-S7 mechanics and deterministic Evidence.

GLFT arrival-intensity calibration, execution/adverse-selection cost calibration, OFI/depth/microprice calibration, and calibrated S2-S7 strategy integration remain explicitly outside this foundation and are follow-on phases.

## 2. Architecture findings

### Calibration and Risk sizing are separated

`grid_trade.calibration` consumes market observations and produces normalized causal market state. It does not import Strategy, Risk, Application, Execution, Integrations, or Research.

`grid_trade.risk.sizing` owns account/risk-derived quantity capacity. Calibration cannot inspect account margin state and cannot widen `Q_max`. Strategy integration will consume the risk-derived capacity only in a later phase.

AST architecture tests enforce these directions and also prevent optional `hftbacktest`/`nautilus_trader` dependencies from leaking into core layers.

### Symbol identity is metadata only

Production calibration code contains no BTC/ETH/SOL-specific branches. Metamorphic tests verify that changing only `instrument_id` leaves numeric volatility, trend, and funding calibration unchanged.

A second metamorphic test verifies that multiplying every price in an otherwise identical stream by a positive constant leaves normalized volatility and trend outputs unchanged.

### Causality is fail-closed

The calibration engine rejects equal or older timestamps, source changes, and instrument changes inside one state. No L2/fill/microstructure value is fabricated in the foundation: quote-distance, execution-cost, order-book score, and microprice displacement remain unavailable until their Tier-2 calibration components exist.

Funding can be missing without blocking the volatility/trend foundation. A degenerate funding distribution is explicit and unavailable instead of being converted through a fixed fallback divisor.

### Meta-parameters are frozen

A self-review found that the first engine version accepted a new `CalibrationEngineConfig` on every update. That violated the sealed-test design because a caller could change estimator meta-parameters midstream.

The final engine binds its config on the first accepted observation. Any later config change fails closed. Restored positive-generation state also requires the frozen config.

## 3. Bugs found during self-review and fixed

### Ambient Decimal precision changed Evidence-sensitive output

Initial implementations used `Decimal.ln`, `sqrt`, `exp`, multiplication, and division under the ambient caller context. Tests with precision 10 versus 50 reproduced different Volatility, Trend, Funding, and `Q_max` values for identical inputs.

Fix: `grid_trade.domain.numeric.deterministic_decimal_context` establishes a fixed precision of 50 digits with `ROUND_HALF_EVEN`, and all evidence-sensitive foundation arithmetic uses that local context. Regression tests require exact equality across different ambient Decimal precisions rather than approximate equality.

### `CalibratedMarketState` could represent inconsistent readiness

Initial public contracts allowed manually constructed states where a value was present but its component status was not ready, or overall readiness contradicted volatility/trend readiness.

Fix: value availability and component status must match for volatility/trend/funding, and overall readiness is derived contractually from volatility/trend readiness.

### Restored engine state was under-validated

Initial engine state validation did not fully reject corrupted restoration states.

Fix: generation zero must be pristine; positive generation requires identity, aware timestamp, frozen config, price history, sufficient generation count, and an exact suffix relationship between retained engine prices and the volatility estimator state.

### Meta-parameter mutation was possible midstream

As noted above, config freezing was added after a RED test showed a changed trend gain was accepted on the second observation.

## 4. Compatibility review

The foundation does not change the existing adaptive-grid Strategy/Application/Execution mechanics. Existing S0, S1, S2, and S3-S7 controlled Research runners remain fixture-based and are intentionally preserved as deterministic mechanics baselines.

The Research Integration workflow continues to execute pinned `hftbacktest==2.4.4` and `nautilus_trader==1.230.0` boundaries plus the four existing fresh-process Evidence checks.

Expected historical digests remain:

- S0: `e0c78118d43dad6c52589e450cbd39069c9cf7636f8eb64a56278573617bd467`
- S1: `f02dfda885d997886dffbdf77cca457989c5cf34066c3febae3f0873d9ca7873`
- S2: `9478000d146bee86cc39ddff6ff6d7627c19bc38e05e9ac6a5bfc835621aae22`
- Adaptive S3-S7: `3af625539b90f53b0db34d3261f16669bd5618a6677bfa022a34df1f2b38d071`

## 5. Review conclusion

The foundation boundary is coherent and deliberately incomplete: market-scale normalization, readiness, deterministic arithmetic, and risk sizing exist without prematurely fabricating microstructure calibration or claiming economic alpha.

No production authorization is implied. Phase B must add causal Tier-2 microstructure calibration before calibrated S7 can exist; Phase C must then integrate calibrated outputs into S2-S7 and prove generalization through cross-instrument walk-forward and sealed OOS evidence.

The branch is eligible for merge only after fresh Core CI and Research Integration both succeed on the exact commit containing this review document.
