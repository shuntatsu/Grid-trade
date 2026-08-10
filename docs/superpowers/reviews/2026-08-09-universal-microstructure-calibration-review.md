# Universal Microstructure Calibration — Architecture & Self Review

Date: 2026-08-09
Branch: `universal-microstructure-calibration`
Foundation base: `a96e347487d718916dc540202fd4559cc23dbb17`
Scope: Phase B causal Tier-2 microstructure calibration only
Production status: RESEARCH / NO-GO

## 1. Reviewed scope

This review covers `docs/superpowers/plans/2026-08-09-universal-microstructure-calibration.md` and the Phase B changes built on the verified universal calibration foundation:

- venue-neutral L2/top-of-book, arrival, markout, and matured-label contracts;
- GLFT-style exposure-aware Poisson arrival-intensity calibration;
- best-level OFI, normalized OFI, microprice, and relative microprice displacement;
- causal matured-label OFI impact calibration;
- adverse-selection markout and execution-cost floor calibration;
- unified microstructure state, config freezing, readiness, and quality;
- symbol/price/size metamorphic invariance;
- future-label and future-markout non-interference;
- deterministic canonical microstructure Evidence;
- fresh-process Evidence digest checks without replacing existing S0-S7 digests.

Phase B intentionally does not wire calibrated outputs into S2-S7 Strategy mechanics. That integration belongs to Phase C.

## 2. Architecture findings

### Calibration remains market-only and venue-neutral

`grid_trade.calibration` consumes immutable venue-neutral observations and matured labels. It does not import Strategy, Risk, Application, Execution, Integrations, Research, NautilusTrader, or hftbacktest.

The existing AST architecture tests recursively inspect the entire `calibration/` subtree, so the Phase B modules are covered automatically. No new dependency exception was required.

Raw Hyperliquid/Nautilus/hftbacktest decoding remains outside Calibration. Phase B has no exchange-order submission path and no account/risk authority.

### No symbol-name calibration branches

The production calibration API does not use BTC, ETH, SOL, or any other instrument identity to choose numerical parameters. Instrument identity is continuity metadata only.

Tests verify that arbitrary instrument rename leaves the numerical microstructure estimate unchanged. Common price and L2-size scaling also leaves the relative outputs unchanged when tick size is scaled consistently.

### No OHLC substitute for microstructure

GLFT arrival intensity consumes explicit distance/exposure/arrival evidence. OFI and microprice consume top-of-book state. Adverse-selection cost consumes matured fill markouts. No OHLC bar is used to fabricate queue depth, arrival count, fill, or markout evidence.

The synthetic research runner is therefore a deterministic calibration/mechanics fixture, not a historical market replay or economic backtest.

## 3. Causality and maturity review

### Future labels cannot affect current fit

`OfiImpactSample` may be retained in state before maturity, but only samples satisfying `matured_at <= decision_time` enter the regression. Pending samples do not evict the causal matured window.

Property tests verify that adding an extreme future OFI label cannot change the current estimate or readiness and that the label becomes eligible exactly at its maturity boundary.

### Future markouts cannot affect current execution cost

Execution cost selects only markouts satisfying `matured_at <= decision_time`. An extreme future markout cannot change the current adverse-cost estimate, fallback state, or readiness.

### Input ordering is not authoritative

Matured/pending OFI samples and markouts are sorted by causal timestamps before rolling-window selection. The intensity fit is exposure/count based. The controlled causality tests verify that permuting the evidence inputs used by the fixture does not change the estimate or retained OFI state.

## 4. Numerical calibration review

### GLFT-style arrival intensity

The model is:

`count_i ~ Poisson(exposure_i * A * exp(-k * distance_i))`

where `distance_i` is already expressed in volatility units. For each configured deterministic `k` candidate, `A` is profiled analytically from total arrivals and weighted exposure. The selected fit is compared against a constant-intensity `k=0` null.

Zero-arrival buckets remain in the likelihood and are therefore informative. Insufficient buckets/arrivals or an insufficient likelihood improvement remain explicit not-ready states.

### OFI impact

OFI impact uses a deterministic through-origin fit:

`beta = sum(x*y) / sum(x*x)`

with `x = normalized_ofi` and `y = matured relative_price_change`. Feature-energy insufficiency remains not-ready. `beta` is bounded by the frozen config. Fit quality is recorded separately rather than hidden.

### Execution/adverse-selection cost

Relative adverse markout is side-aware:

- BUY is adverse when the later mark is below fill;
- SELL is adverse when the later mark is above fill.

The upper adverse-cost quantile is deterministic. Insufficient markout evidence uses the explicit configured conservative fallback and reports `used_fallback=True`; it does not masquerade as a fitted estimate.

The final relative floor is:

`max(0, tick_size/current_mid, 2*maker_fee_rate + adverse_cost + uncertainty_buffer)`

so a maker rebate cannot make the execution floor negative or lower than the tick floor.

### Volatility-normalized output

The GLFT e-fold distance is converted to a relative quote-distance scale by multiplying it by the causal volatility scale. Predicted OFI displacement is divided by the same volatility scale before conversion to the bounded order-book score. No symbol-specific basis-point multiplier is embedded in the Phase B calibration code.

## 5. Deterministic arithmetic review

All evidence-sensitive Phase B arithmetic uses the shared fixed-precision `deterministic_decimal_context()` boundary.

A self-review found one exception after the initial implementation: the public `TopOfBookObservation.mid` property used ambient Decimal precision. A regression test reproduced different mids under precision 10 and 50. The property and top-of-book depth arithmetic were moved under the deterministic Decimal context. The test now requires exact equality.

## 6. Public-contract consistency review

A second self-review found that `MicrostructureCalibrationEstimate` generated by the engine was internally consistent, but a caller could manually construct or `replace()` an estimate with `readiness.ready=True` while deleting a required component.

The public contract now fails closed: a READY estimate requires ready intensity, quote-distance scale, matured markout estimate, current OFI, ready OFI-impact estimate, predicted displacement, order-book score, readiness quality, and the canonical `ready` reason.

The microstructure engine also freezes its config after the first observation and rejects non-increasing timestamps or source/instrument identity discontinuities.

## 7. Evidence review

Phase B adds `EvidenceKind.MICROSTRUCTURE_CALIBRATION` without changing any pre-existing Evidence enum value or payload.

The controlled microstructure Evidence records:

- frozen config;
- state generation;
- intensity A/k, e-fold distance, fit improvement, quality, and sample counts;
- relative quote-distance scale;
- execution-cost floor, adverse cost, maker fee component, tick floor, markout readiness/fallback, and sample count;
- current normalized OFI;
- OFI beta, R2/quality, and sample count;
- predicted relative displacement;
- microprice relative displacement;
- bounded order-book score;
- combined readiness/quality;
- explicit NO-GO fields for economics, alpha, and production.

Fresh-process checks at reviewed code commit `6f298f9099ee9f6f3fabcbb070f1c7b46a7d9ba8` produced:

- S0: `e0c78118d43dad6c52589e450cbd39069c9cf7636f8eb64a56278573617bd467`
- S1: `f02dfda885d997886dffbdf77cca457989c5cf34066c3febae3f0873d9ca7873`
- S2: `9478000d146bee86cc39ddff6ff6d7627c19bc38e05e9ac6a5bfc835621aae22`
- Adaptive S3-S7: `3af625539b90f53b0db34d3261f16669bd5618a6677bfa022a34df1f2b38d071`
- Microstructure calibration: `7e56c2e56b29c6ad15b2f5b2f8d6440169fad6bc7862f0085ccb5eb09a85e239`

The four historical digests remain unchanged.

## 8. Verification evidence before final documentation commit

At reviewed code commit `6f298f9099ee9f6f3fabcbb070f1c7b46a7d9ba8`:

- Ruff format: success;
- Ruff lint: success;
- Core pytest: `275 passed`;
- Core mypy: no issues in `93 source files`;
- Research pytest: `43 passed`;
- S1/S2/Adaptive/Microstructure focused mypy: success;
- all five fresh-process Evidence checks: success.

The exact final branch HEAD containing this review document and the updated README must pass the same Core and Research workflows before Phase B is considered review-complete.

## 9. Third-party and production review

No hftbacktest, NautilusTrader, Hyperliquid SDK, or Hummingbot source code was copied into the Phase B core implementation. Literature and OSS implementations were used as model/behavior references only. Runtime-specific dependencies remain outside core Calibration.

Phase B does not authorize live trading and does not validate profitability. It has no live-order submission path, no production secrets, and no mechanism that can bypass Hard Risk.

## 10. Review conclusion

Phase B now provides a coherent causal microstructure-calibration boundary: normalized arrival intensity, OFI impact, microprice state, adverse-selection cost, explicit readiness, deterministic Evidence, and reproducibility gates exist without introducing symbol-specific calibration or leaking future labels.

The next implementation boundary is Phase C: compose Foundation + Microstructure estimates into S2-S7 while preserving the legacy deterministic fixture path, then test cross-instrument generalization, realistic Tier-2 replay, walk-forward validation, sealed OOS performance, and stress survival. Until those economic gates pass, the repository remains RESEARCH / NO-GO.
