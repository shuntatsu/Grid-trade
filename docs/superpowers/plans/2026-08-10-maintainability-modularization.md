# Grid-trade Maintainability Modularization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the Dataset Audit, Hyperliquid Forward Recorder, and Tier-2 Replay maintainability hotspots into responsibility-scoped packages while preserving every public import, persistent byte format, fail-closed rule, and deterministic Evidence digest.

**Architecture:** Introduce one standard-library-only canonical serialization package, then perform three file-to-package conversions with explicit compatibility exports. Keep orchestration thin, keep optional runtimes confined to Research, and use existing behavioral suites plus new structure/compatibility tests as the migration contract.

**Tech Stack:** Python 3.12, frozen dataclasses, Decimal arithmetic, pytest 9.1.1, Hypothesis 6.165.2, strict mypy 2.3.0, Ruff 0.15.22, hftbacktest 2.4.4, GitHub Actions, uv 0.11.25.

## Global Constraints

- Production status remains exactly **RESEARCH / NO-GO FOR PRODUCTION**.
- Existing public imports from `grid_trade.datasets.audit`, `grid_trade.integrations.hyperliquid.forward_recorder`, and `grid_trade.research.tier2_replay` must remain valid.
- No strategy, Risk, execution, calibration, Dataset acceptance, replay, fee, funding, or Evidence behavior may change.
- Existing Dataset audit digests, forward segment/manifest bytes, and all seven fresh-process Evidence digests must remain unchanged.
- `grid_trade.serialization` may import only Python standard-library modules.
- `hftbacktest` and `nautilus_trader` must remain outside Core layers.
- Existing exception types and tested message text must remain unchanged.
- New production code must follow RED → observed failure → minimal GREEN → refactor.
- The refactor branch must not be merged into `main` without a separate merge decision.

---

## File Structure

### Create

- `src/grid_trade/serialization/__init__.py` — public canonical serialization API.
- `src/grid_trade/serialization/canonical.py` — generic canonical value, bytes, and digest implementation.
- `src/grid_trade/datasets/audit/__init__.py` — legacy Dataset Audit public API.
- `src/grid_trade/datasets/audit/models.py` — audit enums and immutable report contracts.
- `src/grid_trade/datasets/audit/quality.py` — range, overlap, gap, and alignment calculations.
- `src/grid_trade/datasets/audit/runner.py` — deterministic audit orchestration, digest, and promotion guard.
- `src/grid_trade/integrations/hyperliquid/forward_recorder/__init__.py` — legacy forward-recorder public API.
- `src/grid_trade/integrations/hyperliquid/forward_recorder/contracts.py` — immutable session/storage contracts and transport protocol.
- `src/grid_trade/integrations/hyperliquid/forward_recorder/manifest.py` — completed-segment manifest bytes.
- `src/grid_trade/integrations/hyperliquid/forward_recorder/segment.py` — durable segment writer and frame reader.
- `src/grid_trade/integrations/hyperliquid/forward_recorder/session.py` — connection/heartbeat/reference/reconnect state machine.
- `src/grid_trade/research/tier2_replay/__init__.py` — legacy Tier-2 replay public API.
- `src/grid_trade/research/tier2_replay/models.py` — replay manifest/result contracts.
- `src/grid_trade/research/tier2_replay/dataset.py` — audited Dataset binding and initial market context.
- `src/grid_trade/research/tier2_replay/liquidity.py` — participation and visibility-boundary policy.
- `src/grid_trade/research/tier2_replay/attribution.py` — position, funding, fee, and ending-position calculations.
- `src/grid_trade/research/tier2_replay/identity.py` — decision digest and run ID.
- `src/grid_trade/research/tier2_replay/evidence.py` — deterministic Tier-2 Evidence assembly.
- `src/grid_trade/research/tier2_replay/runner.py` — replay orchestration.
- `tests/serialization/test_canonical.py` — exact generic canonical serialization contract.
- `tests/architecture/test_module_ownership.py` — package ownership, compatibility, and dependency rules.

### Replace file with package

- Delete `src/grid_trade/datasets/audit.py` after its contents are moved.
- Delete `src/grid_trade/integrations/hyperliquid/forward_recorder.py` after its contents are moved.
- Delete `src/grid_trade/research/tier2_replay.py` after its contents are moved.

### Modify

- `src/grid_trade/datasets/manifest.py` — delegate generic canonical bytes to `grid_trade.serialization`.
- `src/grid_trade/research/tier2_calibrated_candidate.py` — delegate generic digest/time conversion where contract-equivalent.
- `.github/workflows/research.yml` — type-check the Tier-2 replay package directory.
- `tests/architecture/test_boundaries.py` — include `serialization` and narrower internal dependency gates.
- `README.md` — document responsibility-scoped package map and stable public imports.
- `docs/tier2-historical-forward-replay.md` — document Tier-2 replay pipeline ownership.

---

### Task 1: Canonical Serialization Contract

**Files:**
- Create: `tests/serialization/test_canonical.py`
- Create: `src/grid_trade/serialization/__init__.py`
- Create: `src/grid_trade/serialization/canonical.py`
- Modify: `src/grid_trade/datasets/manifest.py`
- Modify: `src/grid_trade/research/tier2_calibrated_candidate.py`

**Interfaces:**
- Consumes: Python dataclasses, `Enum`, `Decimal`, timezone-aware `datetime`, mappings, sequences, and JSON scalar values.
- Produces:
  - `canonical_value(value: object) -> JSONValue`
  - `canonical_json_bytes(value: object) -> bytes`
  - `canonical_json_digest(value: object) -> str`

- [ ] **Step 1: Write failing canonical serialization tests**

Create tests that assert exact bytes, UTC normalization, deterministic dict ordering, digest equality, unsupported-type failure, and standard-library-only imports:

```python
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256

import pytest

from grid_trade.serialization import canonical_json_bytes, canonical_json_digest


class Mode(StrEnum):
    ACTIVE = "active"


@dataclass(frozen=True)
class Payload:
    amount: Decimal
    timestamp: datetime
    mode: Mode
    values: tuple[int, ...]


def test_canonical_json_bytes_match_existing_contract() -> None:
    payload = Payload(
        amount=Decimal("1.2300"),
        timestamp=datetime(2026, 8, 10, 13, 0, tzinfo=timezone(timedelta(hours=9))),
        mode=Mode.ACTIVE,
        values=(2, 1),
    )

    rendered = canonical_json_bytes({"z": payload, "a": True})

    assert rendered == (
        b'{"a":true,"z":{"amount":"1.2300","mode":"active",'
        b'"timestamp":"2026-08-10T04:00:00Z","values":[2,1]}}\n'
    )
    assert canonical_json_digest({"z": payload, "a": True}) == sha256(rendered).hexdigest()


def test_canonical_json_rejects_unsupported_values() -> None:
    with pytest.raises(TypeError, match="unsupported canonical value: object"):
        canonical_json_bytes(object())


def test_canonical_json_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="datetime must be timezone-aware"):
        canonical_json_bytes(datetime(2026, 8, 10, 4, 0))
```

- [ ] **Step 2: Run tests and observe RED**

Run:

```bash
python -m pytest tests/serialization/test_canonical.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'grid_trade.serialization'`.

- [ ] **Step 3: Implement the minimal serialization package**

Implement type aliases and exact conversion/encoding rules. Ensure `datetime` rejects naive values and normalizes aware values to UTC `Z`. Export all three functions and aliases explicitly from `serialization/__init__.py`.

- [ ] **Step 4: Run serialization tests and observe GREEN**

Run:

```bash
python -m pytest tests/serialization/test_canonical.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Replace duplicate generic serializers without changing bytes**

In `datasets/manifest.py`, replace `_canonical_value` plus the local `json.dumps` call with `canonical_json_bytes(manifest)`. In `tier2_calibrated_candidate.py`, replace `_canonical_value`/`_digest` with `canonical_json_digest`; retain the existing nanosecond-to-datetime helper because it also defines timestamp truncation behavior.

- [ ] **Step 6: Run byte/digest regression tests**

Run:

```bash
python -m pytest -q \
  tests/serialization/test_canonical.py \
  tests/datasets/test_dataset_manifest.py \
  tests/datasets/test_dataset_audit.py \
  tests/research/test_tier2_calibrated_candidate.py
```

Expected: all locally runnable tests pass; any optional-runtime failure must be isolated and recorded rather than hidden.

- [ ] **Step 7: Commit Task 1**

```bash
git add src/grid_trade/serialization src/grid_trade/datasets/manifest.py \
  src/grid_trade/research/tier2_calibrated_candidate.py tests/serialization/test_canonical.py
git commit -m "refactor: centralize canonical serialization"
```

---

### Task 2: Dataset Audit Package

**Files:**
- Create: `src/grid_trade/datasets/audit/__init__.py`
- Create: `src/grid_trade/datasets/audit/models.py`
- Create: `src/grid_trade/datasets/audit/quality.py`
- Create: `src/grid_trade/datasets/audit/runner.py`
- Delete: `src/grid_trade/datasets/audit.py`
- Modify: `tests/architecture/test_module_ownership.py`

**Interfaces:**
- Consumes: `DatasetAuditExpectations`, canonical Dataset events, raw object references, required funding schedule, and optional warning gap.
- Produces unchanged public names:
  - `AuditSeverity`
  - `AuditFinding`
  - `DatasetAuditReport`
  - `audit_canonical_dataset(...) -> DatasetAuditReport`
  - `audit_report_digest(report: DatasetAuditReport) -> str`
  - `require_promoting_dataset(dataset_manifest: DatasetManifest) -> None`

- [ ] **Step 1: Write failing package-ownership and compatibility tests**

Create `tests/architecture/test_module_ownership.py` with assertions that the former hotspot paths are directories and that their public exports remain exact:

```python
from pathlib import Path


def test_maintainability_hotspots_are_responsibility_scoped_packages() -> None:
    assert Path("src/grid_trade/datasets/audit").is_dir()
    assert Path("src/grid_trade/integrations/hyperliquid/forward_recorder").is_dir()
    assert Path("src/grid_trade/research/tier2_replay").is_dir()


def test_dataset_audit_public_api_is_stable() -> None:
    from grid_trade.datasets.audit import (
        AuditFinding,
        AuditSeverity,
        DatasetAuditExpectations,
        DatasetAuditReport,
        audit_canonical_dataset,
        audit_report_digest,
        require_promoting_dataset,
    )

    assert all(
        value is not None
        for value in (
            AuditFinding,
            AuditSeverity,
            DatasetAuditExpectations,
            DatasetAuditReport,
            audit_canonical_dataset,
            audit_report_digest,
            require_promoting_dataset,
        )
    )
```

- [ ] **Step 2: Run the structure test and observe RED**

Run:

```bash
python -m pytest tests/architecture/test_module_ownership.py::test_maintainability_hotspots_are_responsibility_scoped_packages -q
```

Expected: failure because `src/grid_trade/datasets/audit` is not yet a directory.

- [ ] **Step 3: Move immutable report contracts to `models.py`**

Copy `AuditSeverity`, `AuditFinding`, and `DatasetAuditReport` without field, validation, property, default, or message changes. Import `DatasetAuditExpectations`, `CanonicalEventEnvelope`, and `DatasetAcceptance` directly.

- [ ] **Step 4: Move pure quality calculations to `quality.py`**

Move range, overlap, deterministic gap statistics, alignment test, and alignment finding construction. Keep private helper names and exact finding creation behavior. Export only the functions required by `runner.py` through an explicit `__all__`.

- [ ] **Step 5: Move audit orchestration to `runner.py`**

Preserve the original statement order inside `audit_canonical_dataset` so findings and digests remain byte-identical. Replace only the local generic serializer with `canonical_json_digest(report)`. Keep `require_promoting_dataset` unchanged.

- [ ] **Step 6: Add compatibility exports and remove the monolith**

Create `audit/__init__.py` with explicit imports and `__all__`, including `DatasetAuditExpectations` from `grid_trade.datasets.audit_contracts`. Delete `datasets/audit.py` only after the package imports cleanly.

- [ ] **Step 7: Run Dataset Audit RED/GREEN regressions**

Run:

```bash
python -m pytest -q \
  tests/architecture/test_module_ownership.py::test_dataset_audit_public_api_is_stable \
  tests/datasets/test_dataset_audit.py \
  tests/datasets/test_dataset_audit_quality.py \
  tests/research/test_tier2_audit_policy_propagation.py
```

Expected: all tests pass and existing digest assertions remain unchanged.

- [ ] **Step 8: Commit Task 2**

```bash
git add src/grid_trade/datasets/audit tests/architecture/test_module_ownership.py
git add -u src/grid_trade/datasets/audit.py
git commit -m "refactor: split dataset audit responsibilities"
```

---

### Task 3: Hyperliquid Forward Recorder Package

**Files:**
- Create: `src/grid_trade/integrations/hyperliquid/forward_recorder/__init__.py`
- Create: `src/grid_trade/integrations/hyperliquid/forward_recorder/contracts.py`
- Create: `src/grid_trade/integrations/hyperliquid/forward_recorder/manifest.py`
- Create: `src/grid_trade/integrations/hyperliquid/forward_recorder/segment.py`
- Create: `src/grid_trade/integrations/hyperliquid/forward_recorder/session.py`
- Delete: `src/grid_trade/integrations/hyperliquid/forward_recorder.py`
- Modify: `tests/architecture/test_module_ownership.py`

**Interfaces:**
- Consumes: filesystem `Path`, exact raw payload bytes, receive/exchange timestamps, Hyperliquid transport operations, and Dataset raw-object contracts.
- Produces unchanged forward-recorder public names and byte formats.

- [ ] **Step 1: Add public API and dependency tests before the move**

Add:

```python
def test_forward_recorder_public_api_is_stable() -> None:
    from grid_trade.integrations.hyperliquid.forward_recorder import (
        FORWARD_SEGMENT_MANIFEST_SCHEMA_VERSION,
        ContinuityRecord,
        ForwardCaptureResult,
        ForwardRecorderConfig,
        ForwardRecorderSession,
        ForwardSegment,
        ForwardSegmentWriter,
        HyperliquidForwardTransport,
        canonical_forward_segment_manifest_bytes,
        read_segment_records,
    )

    assert FORWARD_SEGMENT_MANIFEST_SCHEMA_VERSION == (
        "hyperliquid-forward-segment-manifest-v1"
    )
    assert all(
        value is not None
        for value in (
            ContinuityRecord,
            ForwardCaptureResult,
            ForwardRecorderConfig,
            ForwardRecorderSession,
            ForwardSegment,
            ForwardSegmentWriter,
            HyperliquidForwardTransport,
            canonical_forward_segment_manifest_bytes,
            read_segment_records,
        )
    )
```

Add an AST dependency assertion that `contracts.py`, `manifest.py`, and `segment.py` do not import `.session`.

- [ ] **Step 2: Run the forward-recorder package structure test and observe RED**

Run:

```bash
python -m pytest tests/architecture/test_module_ownership.py -q
```

Expected: the directory ownership assertion still fails for forward recorder.

- [ ] **Step 3: Move contracts without filesystem behavior**

Move the four dataclasses and transport protocol to `contracts.py`. Preserve all validation text. Keep shared non-negative timestamp validation private in this module for session use.

- [ ] **Step 4: Move manifest serialization**

Move the schema constant and `canonical_forward_segment_manifest_bytes` to `manifest.py`. Use shared canonical JSON bytes only after confirming the result is exactly equal to existing manifest fixtures; retain the explicit payload mapping because it defines the persisted schema.

- [ ] **Step 5: Move durable segment storage**

Move magic/header constants, directory sync, `ForwardSegmentWriter`, and `read_segment_records` to `segment.py`. Import contracts and manifest through narrow submodule paths. Preserve write, fsync, close, replace, and directory-sync order.

- [ ] **Step 6: Move session protocol**

Move `ForwardRecorderSession` and canonical outbound JSON-line helper to `session.py`. Preserve subscription payload ordering, heartbeat/reference timing, reconnect lifecycle errors, continuity records, and writer validation.

- [ ] **Step 7: Add compatibility exports and remove the monolith**

Create explicit exports in `forward_recorder/__init__.py`. Delete the old file after all existing imports succeed.

- [ ] **Step 8: Run durability and state-machine regressions**

Run:

```bash
python -m pytest -q \
  tests/architecture/test_module_ownership.py::test_forward_recorder_public_api_is_stable \
  tests/integrations/hyperliquid/test_forward_recorder.py \
  tests/integrations/hyperliquid/test_forward_recorder_session.py
```

Expected: all tests pass, including byte framing, atomic publication, abort behavior, heartbeat/reference cadence, and reconnect continuity.

- [ ] **Step 9: Commit Task 3**

```bash
git add src/grid_trade/integrations/hyperliquid/forward_recorder \
  tests/architecture/test_module_ownership.py
git add -u src/grid_trade/integrations/hyperliquid/forward_recorder.py
git commit -m "refactor: split forward recorder responsibilities"
```

---

### Task 4: Tier-2 Replay Package

**Files:**
- Create: `src/grid_trade/research/tier2_replay/__init__.py`
- Create: `src/grid_trade/research/tier2_replay/models.py`
- Create: `src/grid_trade/research/tier2_replay/dataset.py`
- Create: `src/grid_trade/research/tier2_replay/liquidity.py`
- Create: `src/grid_trade/research/tier2_replay/attribution.py`
- Create: `src/grid_trade/research/tier2_replay/identity.py`
- Create: `src/grid_trade/research/tier2_replay/evidence.py`
- Create: `src/grid_trade/research/tier2_replay/runner.py`
- Delete: `src/grid_trade/research/tier2_replay.py`
- Modify: `tests/architecture/test_module_ownership.py`

**Interfaces:**
- Consumes: accepted Dataset manifest/events, candidate orders, Risk limits/state, starting position, realized volatility, replay/runtime and market-impact configuration.
- Produces unchanged `Tier2ReplayResult` and deterministic Evidence.

- [ ] **Step 1: Add Tier-2 public API and optional-runtime ownership tests**

Add:

```python
def test_tier2_replay_public_api_is_stable() -> None:
    from grid_trade.research.tier2_replay import (
        Tier2ReplayManifest,
        Tier2ReplayResult,
        required_hourly_funding_timestamps,
        run_tier2_replay,
    )

    assert all(
        value is not None
        for value in (
            Tier2ReplayManifest,
            Tier2ReplayResult,
            required_hourly_funding_timestamps,
            run_tier2_replay,
        )
    )
```

Add AST checks that only `tier2_replay/runner.py` may import `grid_trade.research.hftbacktest_adapter`; Dataset, Liquidity, Attribution, Identity, Evidence, and Models must not import it.

- [ ] **Step 2: Run the Tier-2 structure test and observe RED**

Run:

```bash
python -m pytest tests/architecture/test_module_ownership.py -q
```

Expected: Tier-2 package ownership/dependency assertions fail against the monolith.

- [ ] **Step 3: Move replay contracts to `models.py`**

Copy `Tier2ReplayManifest` and `Tier2ReplayResult` exactly, including count/finite/NO-GO validation. Keep imports limited to Domain/Evidence/Research contract types needed for annotations.

- [ ] **Step 4: Move Dataset binding to `dataset.py`**

Move hourly funding schedule, event validation, re-audit binding, exact-hour funding validation, nanosecond conversion, and initial MarketSnapshot construction. Keep all exception messages and ordering rules identical.

- [ ] **Step 5: Move liquidity policy to `liquidity.py`**

Move initial-book participation calculation, visibility-boundary attachment, trusted-event truncation, and market-feed sufficiency. Continue to delegate formulas to `replay_attribution.py`.

- [ ] **Step 6: Move accounting to `attribution.py`**

Move signed fills, causal position, funding cash-flow construction, and add one `summarize_replay_cash_flows(...)` helper returning funding flows, funding PnL, maker fee cash flow, and ending position. The helper must use the exact existing formulas and Decimal zero seeds.

- [ ] **Step 7: Move identity to `identity.py`**

Move decision digest and run ID. Replace the monolithic canonical helper with `canonical_json_digest`. Preserve the exact identity dictionaries and Dataset Manifest SHA-256 input.

- [ ] **Step 8: Move Evidence assembly to `evidence.py`**

Move order/eligibility payloads and Evidence construction. Keep tie-break integers, sorting, event kinds, payload key names, Dataset identity fields, replay-model strings, NO-GO flags, and economic-validation note unchanged.

- [ ] **Step 9: Build the thin runner**

Create `run_tier2_replay` in `runner.py` by composing the extracted functions in the original order. Keep `transition_passive_policy` as the sole Hard Risk entry and keep hftbacktest conversion/execution only in this module.

- [ ] **Step 10: Add compatibility exports and remove the monolith**

Create `tier2_replay/__init__.py` with the four public names. Delete the old file only after import compatibility succeeds.

- [ ] **Step 11: Run all locally available Tier-2 regressions**

Run:

```bash
python -m pytest -q \
  tests/architecture/test_module_ownership.py::test_tier2_replay_public_api_is_stable \
  tests/research/test_tier2_audit_policy_propagation.py \
  tests/research/test_tier2_calibrated_candidate.py \
  tests/research/test_tier2_funding_completeness.py \
  tests/research/test_tier2_visibility_boundary.py
```

Then run the broader baseline command and confirm that the only failures are the same missing-local-`hftbacktest` failures recorded before the refactor.

- [ ] **Step 12: Commit Task 4**

```bash
git add src/grid_trade/research/tier2_replay tests/architecture/test_module_ownership.py
git add -u src/grid_trade/research/tier2_replay.py
git commit -m "refactor: split tier2 replay responsibilities"
```

---

### Task 5: Architecture Gates, CI, and Documentation

**Files:**
- Modify: `tests/architecture/test_boundaries.py`
- Modify: `tests/architecture/test_module_ownership.py`
- Modify: `.github/workflows/research.yml`
- Modify: `README.md`
- Modify: `docs/tier2-historical-forward-replay.md`
- Create: `docs/superpowers/reviews/2026-08-10-maintainability-modularization-review.md`

**Interfaces:**
- Consumes: final package layout and existing CI contract.
- Produces: enforceable dependency direction, updated type-check scope, and reviewer-readable architecture documentation.

- [ ] **Step 1: Add failing serialization boundary test**

Extend `test_boundaries.py` so `serialization` is scanned and any `grid_trade.*` import is a violation:

```python
def test_serialization_is_standard_library_only() -> None:
    violations = [
        f"{path}: {imported}"
        for path in _python_files("serialization")
        for imported in _imports(path)
        if imported.startswith("grid_trade")
    ]
    assert violations == []
```

Before implementation this test is absent; add it before final boundary cleanup and run it against the new package.

- [ ] **Step 2: Add explicit internal ownership checks**

Use AST imports to enforce:

- Forward contracts/manifest/segment do not import session.
- Tier-2 non-runner modules do not import `hftbacktest_adapter` or `hftbacktest`.
- Tier-2 runner does not import Hyperliquid integration modules directly.
- Dataset Audit modules do not import Research or Integration modules.

- [ ] **Step 3: Update Research mypy target**

Replace:

```text
src/grid_trade/research/tier2_replay.py
```

with:

```text
src/grid_trade/research/tier2_replay
```

Do not otherwise weaken or remove any CI step.

- [ ] **Step 4: Update architecture documentation**

README must describe the new subpackage boundaries and state that old import paths remain stable. The Tier-2 document must show:

```text
Dataset Binding → Hard Risk → Liquidity/Visibility → Replay Runtime
                → Attribution → Identity/Evidence → Result
```

It must retain all existing NO-GO wording.

- [ ] **Step 5: Run local format-independent checks**

Run:

```bash
python -m compileall -q src tests
python -m pytest -q tests/architecture tests/serialization \
  tests/datasets/test_dataset_audit.py \
  tests/datasets/test_dataset_audit_quality.py \
  tests/integrations/hyperliquid/test_forward_recorder.py \
  tests/integrations/hyperliquid/test_forward_recorder_session.py
```

Expected: all locally runnable tests pass.

- [ ] **Step 6: Run the recorded local Tier-2 baseline**

Run the exact pre-refactor baseline command. Expected result in the constrained local environment: 46 or more tests pass, and failures—if any—must be exclusively `PackageNotFoundError: hftbacktest`. Any new failure is a regression and must be fixed before publishing.

- [ ] **Step 7: Self-review the complete diff**

Review:

```bash
git status --short
git diff --check main...HEAD
git diff --stat main...HEAD
git diff main...HEAD -- src tests .github README.md docs
```

Check public exports, exception messages, persisted schemas, dependency direction, dead files, debug code, temporary workflows, and placeholder residue.

- [ ] **Step 8: Write the reviewer record**

Document design decisions, exact commands/results, local environment limitations, digest/CI expectations, and residual risks in the review file. Do not claim full verification until GitHub Actions has run on the exact remote head.

- [ ] **Step 9: Commit Task 5**

```bash
git add .github/workflows/research.yml README.md docs tests/architecture
git commit -m "docs: enforce modular architecture boundaries"
```

---

### Task 6: Publish, CI Verification, and Review

**Files:**
- No additional production files unless CI or review identifies a defect.

**Interfaces:**
- Consumes: verified local branch commits.
- Produces: remote `agent/maintainability-modularization` branch and Draft PR targeting `main`.

- [ ] **Step 1: Confirm exact scope and clean worktree**

Run:

```bash
git status -sb
git log --oneline --decorate main..HEAD
git diff --check main...HEAD
```

Expected: only intended refactor/design/plan/review changes; no untracked artifacts or temporary workflows.

- [ ] **Step 2: Publish the branch through the connected GitHub app**

Create the remote branch from current `main`, then apply the exact local file set through GitHub Contents operations. Verify remote compare metadata and changed filenames match the local diff.

- [ ] **Step 3: Open a Draft PR**

PR title:

```text
Refactor maintainability hotspots into scoped packages
```

PR body must include What, Why, design boundaries, compatibility, non-goals, local checks, CI gates, NO-GO status, and residual risks.

- [ ] **Step 4: Verify exact-head GitHub Actions**

Require both workflows on the same final head:

- `CI`: success
- `Research Integration`: success

Inspect workflow jobs, not merely aggregate status. Record Core test count, strict mypy result, Research test count, focused mypy results, and all seven fresh-process digest outputs.

- [ ] **Step 5: Request independent code review**

Review the remote diff against the design and plan. Fix all Critical and Important findings, rerun local affected tests, push, and require the full CI matrix again on the updated exact head.

- [ ] **Step 6: Final verification and report**

Confirm:

- remote branches are `main` plus the active review branch only
- no temporary workflow remains
- PR is Draft and unmerged
- exact-head CI is green
- public imports and all existing digests are unchanged
- status remains RESEARCH / NO-GO FOR PRODUCTION

Report changed architecture, tests, CI, unverified items, and remaining risks. Request only the final merge decision; do not merge automatically.
