# Tier-2 Historical + Forward Replay Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic, evidence-first Hyperliquid Historical + Forward Recorder replay foundation that converts accepted canonical market data into conservative `hftbacktest==2.4.4` research replays without claiming profitability or production readiness.

**Architecture:** Add a runtime-neutral `grid_trade.datasets` package for immutable raw-object identity, canonical events, ordering, visibility epochs, manifests, and audit. Hyperliquid-specific acquisition/normalization stays under `integrations/hyperliquid`; optional replay/runtime code stays under `research`. Existing Strategy/Calibration/Risk/Application contracts remain unchanged and may only consume accepted canonical/replay inputs through explicit adapters.

**Tech Stack:** Python 3.12, standard library (`dataclasses`, `decimal`, `enum`, `hashlib`, `json`, `pathlib`), pytest 9.1.1, Hypothesis 6.165.2, mypy 2.3.0 strict, Ruff 0.15.22, optional `hftbacktest==2.4.4`.

## Global Constraints

- Repository: `shuntatsu/Grid-trade`.
- Branch: `tier2-historical-forward-replay`.
- Parent strategy/calibration head: `8b8c5e22445c3d028f572803fbd1789a90629048`.
- License remains LGPL-3.0.
- Production status remains **RESEARCH / NO-GO**.
- `datasets/` must not import Hyperliquid SDK, NautilusTrader, hftbacktest, Strategy, Risk, Application, or Calibration.
- `integrations/hyperliquid/` may decode/acquire exchange data but must emit runtime-neutral dataset contracts.
- `research/` owns hftbacktest/runtime-specific conversion and replay orchestration.
- Core and Research CI must remain network-independent; live Hyperliquid/S3/WebSocket access is never required by blocking CI.
- Raw payloads are immutable; exact bytes are SHA-256 identified before normalization.
- Missing L2/trade/funding/reference state is unavailable, never zero-filled or interpolated into fill evidence.
- Top-N snapshot visibility loss is not equivalent to cancellation.
- Economic promotion later requires `ACCEPTED`; `ACCEPTED_WITH_WARNINGS` is sensitivity-only and `REJECTED` is not replay-promoting.
- Existing `MicrostructureFixture` behavior and historical deterministic Evidence digests must remain regression-stable.

---

### Task 1: Runtime-neutral raw object and manifest contracts

**Files:**
- Create: `src/grid_trade/datasets/__init__.py`
- Create: `src/grid_trade/datasets/contracts.py`
- Create: `src/grid_trade/datasets/manifest.py`
- Create: `tests/datasets/test_contracts.py`
- Create: `tests/datasets/test_manifest.py`
- Modify: `tests/architecture/test_boundaries.py`

**Interfaces:**
- Produces `SourceFamily`, `DatasetType`, `RawObjectIdentity`, `RawObjectRef`, `DatasetAcceptance`, `DatasetManifest`, `sha256_bytes(payload: bytes) -> str`, and `canonical_manifest_bytes(manifest: DatasetManifest) -> bytes`.
- `RawObjectRef` carries source family/type, instrument, exact byte length/hash, acquisition timestamp, optional source/receive time bounds, source locator, and collector/decoder schema versions.
- `DatasetManifest` carries immutable raw refs plus normalization/order/audit schema versions and explicit acceptance state.

- [ ] **Step 1: Write failing contract tests**

```python
from datetime import UTC, datetime

import pytest

from grid_trade.datasets.contracts import DatasetType, RawObjectIdentity, SourceFamily


def test_raw_object_identity_rejects_non_sha256_digest() -> None:
    with pytest.raises(ValueError, match="sha256"):
        RawObjectIdentity(
            source_family=SourceFamily.ARCHIVE,
            dataset_type=DatasetType.L2_BOOK,
            instrument="BTC",
            sha256="abc",
        )


def test_raw_object_identity_normalizes_no_fields_implicitly() -> None:
    identity = RawObjectIdentity(
        source_family=SourceFamily.WEBSOCKET,
        dataset_type=DatasetType.TRADES,
        instrument="BTC",
        sha256="0" * 64,
    )
    assert identity.instrument == "BTC"
```

- [ ] **Step 2: Run focused test and verify RED**

Run: `pytest -q tests/datasets/test_contracts.py`
Expected: FAIL because `grid_trade.datasets` does not exist.

- [ ] **Step 3: Implement minimal immutable contracts**

Use frozen/slotted dataclasses and explicit validation. Require non-empty instrument/source locator/version fields where applicable; require timestamps to be timezone-aware UTC datetimes or integer nanoseconds consistently inside each contract; require exact 64-character lowercase hexadecimal SHA-256.

- [ ] **Step 4: Write failing manifest determinism test**

```python
from grid_trade.datasets.manifest import DatasetManifest, canonical_manifest_bytes


def test_manifest_serialization_is_deterministic(sample_manifest: DatasetManifest) -> None:
    first = canonical_manifest_bytes(sample_manifest)
    second = canonical_manifest_bytes(sample_manifest)
    assert first == second
    assert first.endswith(b"\n")
```

- [ ] **Step 5: Run manifest test and verify RED**

Run: `pytest -q tests/datasets/test_manifest.py`
Expected: FAIL because manifest serialization is not implemented.

- [ ] **Step 6: Implement canonical manifest serialization**

Serialize dataclasses/enums deterministically as UTF-8 JSON with `sort_keys=True`, separators `(",", ":")`, Decimal/string values kept textual, tuples rendered as arrays, and one trailing newline. No ambient locale or Decimal context may alter bytes.

- [ ] **Step 7: Extend architecture test**

Add `datasets` as a runtime-neutral layer and forbid imports from `grid_trade.application`, `grid_trade.calibration`, `grid_trade.execution`, `grid_trade.integrations`, `grid_trade.research`, `grid_trade.risk`, `grid_trade.strategy`, `hftbacktest`, and `nautilus_trader`.

- [ ] **Step 8: Verify Task 1**

Run:
```bash
pytest -q tests/datasets tests/architecture/test_boundaries.py
ruff check src/grid_trade/datasets tests/datasets tests/architecture/test_boundaries.py
mypy src/grid_trade/datasets tests/datasets tests/architecture/test_boundaries.py
```
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/grid_trade/datasets tests/datasets tests/architecture/test_boundaries.py
git commit -m "feat: add tier2 dataset provenance contracts"
```

---

### Task 2: Canonical events, deterministic ordering, and visibility epochs

**Files:**
- Create: `src/grid_trade/datasets/canonical.py`
- Create: `tests/datasets/test_canonical.py`
- Create: `tests/datasets/test_visibility.py`
- Modify: `src/grid_trade/datasets/__init__.py`

**Interfaces:**
- Produces `CanonicalEventType`, `CanonicalBookLevel`, `CanonicalBookSnapshot`, `CanonicalTrade`, `CanonicalFundingReference`, `CanonicalEventEnvelope`, `canonical_event_sort_key(...)`, `BookVisibilityTracker`, `VisibilityEpoch`, and `VisibleDepthUpdate`.
- Canonical events retain exchange timestamp, optional observed receive timestamp, optional source sequence/stable identity, raw object SHA-256, raw record ordinal, and normalization schema version.

- [ ] **Step 1: Write failing canonical validation tests**

Test positive finite price/quantity, ordered bids/asks, non-crossed books, required raw provenance, and distinct equal snapshots at distinct timestamps.

- [ ] **Step 2: Run focused canonical tests and verify RED**

Run: `pytest -q tests/datasets/test_canonical.py`
Expected: FAIL because canonical event contracts are absent.

- [ ] **Step 3: Implement canonical event dataclasses and sort key**

Ordering key is exactly: exchange timestamp, source sequence/block ID when present (with an explicit sentinel policy when absent), declared event-type precedence, raw object hash, raw record ordinal. Event-type precedence is a schema constant and must not depend on enum declaration accident.

- [ ] **Step 4: Write failing visibility tests**

Cover three cases: a missing level still inside the new observable side range becomes confirmed zero; a missing level outside the new top-N range becomes `VISIBILITY_LOST`; re-entry starts a new visibility epoch and cannot inherit old queue continuity.

- [ ] **Step 5: Run visibility tests and verify RED**

Run: `pytest -q tests/datasets/test_visibility.py`
Expected: FAIL because `BookVisibilityTracker` does not exist.

- [ ] **Step 6: Implement visibility tracker**

Track each side independently. Generate deterministic depth updates only where the new snapshot proves presence/zero inside the currently observable domain. Emit an epoch transition when trust is lost or re-established at a price. Never synthesize cancellation outside the visible boundary.

- [ ] **Step 7: Property tests**

Use Hypothesis to prove that applying the same snapshot sequence twice yields byte-for-byte equivalent visibility outputs and that reordering raw input before canonical sort produces the same ordered result when stable identities are unique.

- [ ] **Step 8: Verify Task 2 and commit**

Run focused pytest, Ruff, strict mypy on datasets/tests, then commit `feat: add canonical tier2 market events`.

---

### Task 3: Dataset quality audit and acceptance gate

**Files:**
- Create: `src/grid_trade/datasets/audit.py`
- Create: `tests/datasets/test_audit.py`
- Modify: `src/grid_trade/datasets/manifest.py`
- Modify: `src/grid_trade/datasets/__init__.py`

**Interfaces:**
- Produces `AuditSeverity`, `AuditFinding`, `DatasetAuditReport`, `audit_canonical_dataset(...)`, and `require_promoting_dataset(...)`.
- `require_promoting_dataset` accepts only `DatasetAcceptance.ACCEPTED` and raises on warning/rejected datasets.

- [ ] **Step 1: Write failing audit tests**

Cover conflicting duplicate stable identity => REJECTED; crossed book => REJECTED; missing funding interval required by manifest => REJECTED; benign duplicate exact raw record => counted/deduplicated without conflict; warning-only non-promoting dataset => `ACCEPTED_WITH_WARNINGS`.

- [ ] **Step 2: Verify RED**

Run: `pytest -q tests/datasets/test_audit.py`.

- [ ] **Step 3: Implement deterministic audit**

Audit identity consistency, numeric validity, book ordering/crossing, ordering monotonicity, duplicate/conflict counts, snapshot visibility gaps, timestamp gap distribution, reconnect intervals, requested-vs-observed coverage, trade/book overlap, funding/reference coverage, tick/lot metadata, raw hash resolvability, normalization version, and replay order-price visibility. Never repair data inside audit.

- [ ] **Step 4: Add manifest acceptance digest**

Canonical audit findings and acceptance state become part of the manifest/evidence identity so changing an audit rule changes the dataset identity.

- [ ] **Step 5: Verify Task 3 and commit**

Run datasets pytest + architecture + Ruff + mypy; commit `feat: add tier2 dataset acceptance audit`.

---

### Task 4: Hyperliquid historical/forward normalization boundary

**Files:**
- Create: `src/grid_trade/integrations/hyperliquid/__init__.py`
- Create: `src/grid_trade/integrations/hyperliquid/archive.py`
- Create: `src/grid_trade/integrations/hyperliquid/node_data.py`
- Create: `src/grid_trade/integrations/hyperliquid/normalization.py`
- Create: `src/grid_trade/integrations/hyperliquid/forward_recorder.py`
- Create: `tests/integrations/hyperliquid/test_archive.py`
- Create: `tests/integrations/hyperliquid/test_normalization.py`
- Create: `tests/integrations/hyperliquid/test_forward_recorder.py`
- Create: `tests/fixtures/hyperliquid/l2book.json`
- Create: `tests/fixtures/hyperliquid/trades.json`
- Create: `tests/fixtures/hyperliquid/meta_and_asset_ctxs.json`
- Modify: `tests/architecture/test_boundaries.py`

**Interfaces:**
- Historical loaders consume explicit payload bytes/path metadata and produce `RawObjectRef` plus decoded source records.
- Normalizers convert verified official-format source records into Task 2 canonical events.
- `ForwardSegmentWriter` persists exact inbound bytes before acknowledgement, finalizes by flush/close/hash/manifest publication, and marks interrupted segments incomplete.
- Network transport is injected behind protocols; blocking CI uses fixture transports only.

- [ ] **Step 1: Verify current official Hyperliquid source contracts before coding**

Confirm current official docs for `l2Book`, `trades`, `metaAndAssetCtxs`, historical archive location/limitations, node datasets, funding cadence, and rate limits. Record only source-shape fields used by tests; do not add SDK dependency unless separately justified.

- [ ] **Step 2: Write failing fixture normalization tests**

Assert official side mapping, Decimal-preserving price/size parsing, exchange timestamp preservation, raw hash/ordinal provenance, and missing funding/reference remaining unavailable rather than zero.

- [ ] **Step 3: Verify RED then implement pure normalizers**

No network calls in normalization. Unknown schema/version fails closed with `ValueError` carrying the decoder version/source family.

- [ ] **Step 4: Write failing durable-segment tests**

Use temporary directories and injected clock/transport. Assert exact bytes are persisted before acknowledgement; completed segment hash matches bytes; interrupted segment is not ACCEPTED; reconnect starts a new continuity/visibility boundary.

- [ ] **Step 5: Verify RED then implement recorder state machine**

Keep WebSocket/info protocol interfaces tiny and synchronous/asynchronous only as required by existing project conventions. Do not build an all-market crawler; recorder requires explicit single instrument configuration and default 60-second reference cadence.

- [ ] **Step 6: Architecture checks**

Ensure core layers do not import Hyperliquid integration modules. `integrations/hyperliquid` may import `datasets` but not Strategy/Risk/Application/Calibration.

- [ ] **Step 7: Verify Task 4 and commit**

Run integration fixture tests, core tests, Ruff, mypy; commit `feat: add hyperliquid tier2 normalization and recorder`.

---

### Task 5: Canonical-to-hftbacktest adapter and replay attribution

**Files:**
- Modify: `src/grid_trade/research/hftbacktest_adapter.py`
- Create: `src/grid_trade/research/replay_attribution.py`
- Create: `tests/research/test_canonical_hftbacktest_adapter.py`
- Create: `tests/research/test_replay_attribution.py`

**Interfaces:**
- Adds `canonical_events_to_hftbacktest_fixture(...) -> MicrostructureFixture` or an equally explicit typed adapter without changing existing fixture loader semantics.
- Produces `ReplayCostScenario`, `FundingCashFlow`, `ReplayPnlAttribution`, `OrderLiquidityEligibility`, and deterministic attribution helpers.

- [ ] **Step 1: Write failing conversion tests**

Assert only trustworthy visibility-domain depth updates are emitted; untrustworthy order-price visibility is replay-ineligible; existing synthetic fixture conversion remains unchanged.

- [ ] **Step 2: Verify RED then implement adapter**

Use MBP representation, existing `PartialFillExchange`, `RiskAverseQueueModel`, pinned `hftbacktest==2.4.4`, explicit tick/lot, and explicit latency/fee inputs. Archive receive latency remains synthetic and labeled as such.

- [ ] **Step 3: Write failing PnL/funding tests**

Assert additive identity:

```text
net_pnl = realized_spread_capture
        + directional_inventory_pnl
        + funding_pnl
        - fee_cost
        - adverse_selection_cost
        - emergency_execution_cost
```

Assert missing required funding/reference rejects a funding-complete promoting run.

- [ ] **Step 4: Implement attribution and market-impact eligibility**

Record order quantity / visible same-level quantity, order notional / visible top-N notional, maximum/high-quantile participation ratios, visibility-boundary time, and a predeclared hard eligibility threshold not tuned on sealed OOS PnL.

- [ ] **Step 5: Verify Task 5 and commit**

Run research-marked adapter/attribution tests plus existing research regression suite; commit `feat: add canonical tier2 replay adapter and attribution`.

---

### Task 6: Tier-2 replay orchestration, deterministic Evidence, CI, and self-review

**Files:**
- Create: `src/grid_trade/research/tier2_replay.py`
- Create: `tests/research/test_tier2_replay.py`
- Create: `tests/research/test_tier2_replay_determinism.py`
- Create: `docs/superpowers/reviews/2026-08-10-tier2-historical-forward-replay-review.md`
- Modify: `.github/workflows/research.yml`
- Modify: `README.md`

**Interfaces:**
- Produces frozen `Tier2ReplayManifest`, `Tier2ReplayResult`, and `run_tier2_replay(...)` that requires an ACCEPTED dataset and explicit latency/fee/funding/order-size assumptions.
- Emits canonical deterministic Evidence containing raw hashes, schema versions, audit digest, experiment manifest, strategy/calibration identity, queue/latency model labels, replay quality, fills, funding, costs, PnL attribution, and `production_authorized=false` / `alpha_validated=false`.

- [ ] **Step 1: Write failing orchestration tests**

Assert REJECTED/warning datasets cannot enter promoting replay; future events cannot change earlier decision digest; hard Risk remains authoritative; visibility-ineligible orders are skipped rather than guessed; funding is applied only at declared hourly boundaries with required reference state.

- [ ] **Step 2: Verify RED then implement minimal orchestration**

Drive accepted canonical events in replay-time order, update only matured calibration/markout evidence, derive risk capacity, obtain calibrated adaptive candidate, apply Hard Risk, reconcile, map eligible orders, ingest fills, apply funding, and append Evidence.

- [ ] **Step 3: Add deterministic independent-process Evidence check**

Add a tiny checked-in fixture runner whose SHA-256 output is run twice in fresh Python processes in Research CI and must be identical.

- [ ] **Step 4: Update Research CI**

Add Tier-2 fixture tests and focused mypy. CI stays offline and never accesses Hyperliquid network endpoints.

- [ ] **Step 5: Full verification**

Run on the same final HEAD:

```bash
uv lock --check
uv sync --extra dev --extra research --frozen
uv run --frozen --extra dev ruff format --check --diff .
uv run --frozen --extra dev ruff check .
uv run --frozen --extra dev pytest -q --ignore=tests/research --ignore=tests/integrations
uv run --frozen --extra dev mypy src tests --exclude '^tests/(research|integrations)/'
uv run --frozen --extra dev --extra research pytest -m research -q tests/research tests/integrations
uv run --frozen --extra dev --extra research mypy src/grid_trade/research tests/research src/grid_trade/integrations tests/integrations
```

Expected: all PASS, with all pre-existing Evidence digests unchanged and the new Tier-2 digest stable across fresh processes.

- [ ] **Step 6: Reviewer-mode self-review**

Review the full branch diff for causality leakage, snapshot/queue semantics, missing-data fabrication, Decimal precision, schema-version coupling, network-in-CI leakage, hidden production authorization, oversized files, dependency direction, duplicate logic, and stale/dead code. Fix findings and rerun the smallest impacted tests followed by the full verification set.

- [ ] **Step 7: Commit review and prepare Draft PR**

Commit `docs: review tier2 historical forward replay`, then open/update a Draft PR targeting the dependency head until PR #10 is integrated; retarget to `main` only when commit ancestry is safe. Do not merge without explicit user authorization.
