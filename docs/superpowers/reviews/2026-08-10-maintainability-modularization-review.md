# Maintainability Modularization Review

Date: 2026-08-10
Branch: `agent/maintainability-modularization`
Status: local implementation review complete; exact remote-head CI verification required
Production: **RESEARCH / NO-GO FOR PRODUCTION**

## Scope reviewed

This review covers the structural modularization of four areas:

1. standard-library-only generic canonical JSON serialization;
2. Dataset Audit models, quality rules, and deterministic aggregation;
3. Hyperliquid forward-recorder contracts, manifest, durable segment storage, and session protocol;
4. Tier-2 replay Dataset binding, liquidity/visibility policy, accounting, identity, Evidence, and orchestration.

The review also covers public import compatibility, architecture gates, Research CI type-check targeting, README/Tier-2 documentation, persisted byte formats, deterministic digests, and repository hygiene.

## Repository hygiene

Before this refactor, all ten obsolete non-`main` remote branches were deleted after confirming that nine were fully contained and the remaining branch was the explicitly superseded PR #9 implementation. The valid Decimal-context regression from that old path had already been rescued through PR #12.

The cleanup used a temporary self-delete workflow. Branch deletion succeeded; workflow self-deletion did not. The workflow file was then deleted directly and the remote branch list was rechecked. A later temporary source-export workflow was also deleted after its artifact was obtained. No temporary workflow is part of this refactor diff.

## Design assessment

### 1. Top-level layering remains appropriate

The existing Domain / Dataset / Calibration / Strategy / Risk / Execution / Application / Integration / Research separation was retained. The refactor targets responsibility concentration inside individual files rather than performing a broad top-level reorganization.

This is preferable to a repository-wide folder rewrite because current layer contracts are already enforced and aligned with the system's safety model.

### 2. Public module paths remain stable

The following files became same-named packages:

- `grid_trade.datasets.audit`
- `grid_trade.integrations.hyperliquid.forward_recorder`
- `grid_trade.research.tier2_replay`

Each package has an explicit `__init__.py` compatibility surface. Existing application, test, and research imports continue to use the same paths and names.

No parallel legacy implementation or duplicate forwarding file was retained.

### 3. Generic serialization is narrow and low-level

`grid_trade.serialization` owns only generic dataclass/Enum/Decimal/datetime/list/dict/scalar conversion, deterministic JSON bytes, and SHA-256. It imports no other `grid_trade` package.

The Evidence ledger remains separate. It has additional domain contracts—run identity, contiguous sequence numbers, timestamp monotonicity, and payload JSON parsing—that should not be weakened into a generic helper.

### 4. Dataset Audit decomposition preserves ordered findings

- `models.py` owns immutable findings and reports.
- `quality.py` owns pure range/overlap/gap/alignment calculations.
- `runner.py` preserves the original audit statement order, duplicate handling, finding order, accepted-event order, final acceptance, digest, and promotion guard.

A differential check loaded the pre-refactor module and compared three representative reports and digests. The reports were field-identical and the SHA-256 digests matched exactly.

### 5. Forward Recorder decomposition follows change reasons

- `contracts.py` has no filesystem behavior.
- `manifest.py` owns persisted manifest schema.
- `segment.py` owns frames, file/directory fsync, atomic publication, abort, and frame reading.
- `session.py` owns subscription, heartbeat, reference cadence, reconnect, and continuity epochs.

AST comparison confirmed that all thirteen moved functions/classes are mechanically equivalent to the pre-refactor implementation. Existing durability and state-machine tests passed.

### 6. Tier-2 Replay orchestration is now thin

The replay path is explicit:

```text
Dataset binding
  -> Hard Risk
  -> liquidity / visibility eligibility
  -> optional pinned runtime replay
  -> funding / fee / position attribution
  -> decision identity and Evidence
  -> result
```

The runner remains the only runtime importer of the hftbacktest adapter. Models, attribution, and Evidence use type-only imports. No Tier-2 replay module imports Hyperliquid integration code directly.

AST comparison confirmed mechanical equivalence for twenty unchanged contracts/functions, including the complete top-level `run_tier2_replay` body. Two no-runtime replay cases were also executed through both old and new modules; all result fields, decision digests, and Evidence digests matched exactly.

## TDD evidence

### Canonical serialization

RED:

- `tests/serialization/test_canonical.py` failed collection with `ModuleNotFoundError: grid_trade.serialization`.

GREEN:

- exact canonical bytes, digest, mapping-key conversion, unsupported-type failure, and naive-datetime failure: **4 passed**.

### Dataset Audit package

RED:

- package-ownership test failed because `src/grid_trade/datasets/audit` did not exist.

GREEN:

- package ownership, public API, Dataset Audit behavior/quality, and Tier-2 policy propagation: **16 passed**.
- broader Dataset tests excluding the unavailable local Hypothesis property suite: **31 passed**.

### Forward Recorder package

RED:

- package-ownership test failed because the package directory did not exist.

First GREEN attempt exposed a real extraction defect:

- four dataclass decorators were missing, producing eight constructor failures.

Fix:

- restored `@dataclass(frozen=True, slots=True)` to all four contracts.

GREEN:

- package ownership, public API, storage/session boundary, durability, and state-machine tests: **14 passed**.
- complete Hyperliquid integration directory plus ownership checks: **21 passed**.

### Tier-2 Replay package

RED:

- package-ownership test failed because the package directory did not exist.

First implementation checks exposed two extraction defects:

- an over-broad text replacement renamed `_decision_digest`;
- `_HOUR_NS` was not imported into the extracted attribution module.

Fixes:

- restored the exact function name;
- imported the shared Dataset funding-boundary constant.

GREEN/local constrained result:

- targeted Tier-2 and Hyperliquid scope: **40 passed**;
- **5 failures**, all with `PackageNotFoundError: hftbacktest`, matching the pre-refactor local environment limitation.

## Local verification evidence

Environment:

- Python available locally: 3.13.5;
- project contract: Python >=3.12,<3.13;
- unavailable locally: Hypothesis, Ruff, mypy, hftbacktest, NautilusTrader;
- therefore GitHub Actions on Python 3.12 is the authoritative complete gate.

Fresh local checks:

- `python -m compileall -q src tests`: **PASS**.
- configured 100-character Python line-length scan: **0 violations**.
- architecture tests: **24 passed**.
- Core tests excluding seven files that import unavailable Hypothesis and excluding Research/Integrations: **272 passed**.
- Dataset tests excluding the unavailable Hypothesis property file: **31 passed**.
- canonical serialization tests: **4 passed**.
- Hyperliquid integration tests and ownership checks: **27 passed**.
- focused structural AST equivalence:
  - Forward Recorder: **13 moved nodes equivalent**;
  - Tier-2 Replay: **20 moved nodes equivalent**.
- differential Dataset Audit reports/digests: **3 representative cases equivalent**.
- differential no-runtime Tier-2 results/digests: **2 representative cases equivalent**.

## Static boundary review

New architecture gates enforce:

- generic serialization implementation imports no `grid_trade` module;
- Dataset Audit remains runtime-neutral;
- forward-recorder storage/manifest/contracts do not depend on session orchestration;
- only the Tier-2 runner has a runtime import of the hftbacktest adapter;
- Tier-2 runner does not depend on exchange integration modules;
- Research CI type-checks the Tier-2 replay package directory rather than the removed single file;
- changes under `grid_trade.serialization` trigger Research Integration because canonical bytes and digests are research contracts.

Existing Core-layer and optional-runtime boundary tests remain intact and were not weakened.

## Exact remote-head merge gates

Before this branch is mergeable, the exact final remote head must pass:

1. dependency lock check;
2. Ruff format and lint;
3. all Core tests with Hypothesis installed;
4. strict Core mypy;
5. all Research/Integration tests with pinned runtimes;
6. focused S1, S2, Adaptive, Microstructure, Calibrated Adaptive, and Tier-2 mypy checks;
7. fresh-process equality for S0, S1, S2, Adaptive, Microstructure Calibration, Calibrated Adaptive, and Tier-2 Evidence digests.

The review record will be amended with exact workflow/head evidence before completion is claimed.

## Remaining risks

- Strict mypy and Ruff could identify issues not observable in the constrained local environment.
- Full hftbacktest execution and pinned Tier-2 Evidence reproduction require Research Integration CI.
- File-to-package conversion is atomic at merge time; partial cherry-picking of deletion and package creation commits is unsupported.
- Structural modularity improves change isolation but does not validate strategy economics, profitability, or live operation.

## Scope purity

This refactor does not change:

- Strategy or calibration formulas;
- Hard Risk authority or limits;
- Dataset acceptance policy;
- Hyperliquid wire payloads;
- forward segment or manifest schemas;
- replay queue/exchange models;
- funding or fee formulas;
- Evidence schema or intended digests;
- production authorization.

Status remains **RESEARCH / NO-GO FOR PRODUCTION**.
