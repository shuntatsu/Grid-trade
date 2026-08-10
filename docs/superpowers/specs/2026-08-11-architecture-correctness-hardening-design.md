# Architecture Correctness Hardening Design

## Objective

Close the two correctness gaps found in the post-generalization architecture review before economic walk-forward and multi-regime evaluation:

1. Hard Risk must independently validate `reduce_only` semantics rather than trusting Strategy or the venue.
2. Linear-contract `contract_multiplier` must be applied consistently through Tier-2 sizing, liquidity, funding, fees, replay runtime configuration, and evidence identity.

The change also tightens executable architecture contracts around these paths without introducing portfolio allocation, live order submission, or a generalized broker framework.

## Scope

### Included

- Validate reduce-only side, flat-position use, and cumulative quantity against the current position.
- Keep new-risk position projection conservative and independent from reduce-only fills.
- Add explicit Risk reasons for malformed reduce-only ladders.
- Add `contract_multiplier` to the pinned hftbacktest configuration and use it when constructing the linear asset.
- Apply multiplier-aware notional calculations to liquidity participation, funding cash flow, and maker-fee attribution.
- Allow `Tier2ReplayManifest` to bind an explicit `InstrumentSpec` and validate instrument, tick, lot, multiplier, and supported funding cadence consistency.
- Require non-unit replay multipliers and the calibrated Tier-2 path to provide an explicit instrument specification.
- Add architecture and regression tests for the new contracts.
- Update README architecture notes and research limitations.

### Excluded

- Portfolio allocation or cross-instrument netting.
- Inverse perpetuals, spot, futures, options, or multi-currency collateral.
- Generalized non-hourly funding replay; the current Tier-2 funding schedule remains explicitly hourly.
- Live exchange acknowledgements and production order lifecycle state machines.
- New alpha signals or profitability claims.
- Broad package reorganization unrelated to the correctness gaps.

## Hard Risk design

`filter_passive_orders` will evaluate two independent worst-case capacities:

- **New-risk projection:** starts from the actual current position and ignores reduce-only fills, because risk-increasing orders may fill while reduce-only orders do not.
- **Reduce-only capacity:** starts from the absolute current position and is consumed only by correctly oriented reduce-only orders.

A reduce-only order is valid only when:

- current position is positive and the order is `SELL`, or current position is negative and the order is `BUY`;
- current position is non-zero;
- cumulative accepted reduce-only quantity does not exceed the absolute current position.

Malformed reduce-only orders are filtered and cause the exact candidate ladder to be rejected with `RiskReason.INVALID_REDUCE_ONLY`. Existing valid reduce-only behavior at a position limit remains unchanged.

## Contract multiplier design

`HftReplayConfig.contract_multiplier` becomes the authoritative replay economic multiplier, defaulting to `1` for legacy deterministic fixtures. A non-unit multiplier requires `Tier2ReplayManifest.instrument`; when supplied, it must match:

- `dataset.instrument`;
- `hft.tick_size`;
- `hft.lot_size`;
- `hft.contract_multiplier`;
- the currently supported hourly Tier-2 funding cadence.

All Tier-2 notional calculations use:

```text
notional = abs(quantity) × price × contract_multiplier
```

This applies to:

- hftbacktest `.linear_asset(...)` configuration;
- same-level and top-N liquidity participation;
- funding cash flow;
- maker-fee attribution;
- deterministic manifest/evidence identity through serialized config.

The calibrated Tier-2 path already owns an explicit `InstrumentSpec`; it must propagate that specification into `Tier2ReplayManifest` and reject hft configuration mismatches.

## Compatibility

- Existing unit-multiplier fixtures continue to work through a default multiplier of `1` without an explicit spec.
- Public function signatures gain keyword-only/defaulted multiplier parameters where practical.
- Non-unit and calibrated generalized paths require `InstrumentSpec` validation.
- No existing strategy economics or stage presets are changed.

## Failure behavior

The system remains fail-closed:

- malformed reduce-only ladders cannot commit candidate Strategy state;
- non-unit multiplier paths without an `InstrumentSpec` raise before replay;
- multiplier/spec and funding-cadence mismatches raise before replay;
- unsupported contract types remain rejected by `InstrumentSpec`;
- Tier-2 results continue to prohibit production, alpha, or economics authorization.

## Verification

Required evidence:

- RED→GREEN regression tests for wrong-side, flat, oversize, and cumulative reduce-only orders;
- multiplier metamorphic tests proving equivalent economic notional produces equal liquidity, funding, and fee attribution;
- hftbacktest configuration test proving the configured multiplier reaches `.linear_asset`;
- explicit manifest and calibrated replay propagation tests;
- architecture tests and all existing Core/Research CI checks;
- deterministic Evidence reproduction on the final PR head.
