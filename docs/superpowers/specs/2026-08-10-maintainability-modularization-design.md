# Grid-trade Maintainability Modularization Design

Date: 2026-08-10
Status: approved by delegated design authority; implementation target is a review branch
Production status: **RESEARCH / NO-GO FOR PRODUCTION**

## 1. Objective

Improve maintainability without changing strategy economics, replay semantics, public import paths, deterministic Evidence, or production authorization. The change decomposes three oversized modules along stable responsibility boundaries and centralizes generic canonical JSON serialization used outside the Evidence ledger.

The implementation must preserve all existing externally consumed symbols and all existing deterministic digests. It must not add strategy behavior, alter risk authority, broaden runtime dependencies, or claim profitability.

## 2. Current constraints and findings

The existing top-level dependency direction is sound:

```text
domain
  ↑
calibration   datasets
  ↑             ↑
strategy      integrations
  ↑
application ← risk ← execution
  ↑
research → evidence
```

The exact arrows vary by use case, but the enforced principles remain:

- `domain` contains immutable contracts and numeric primitives.
- `datasets` is runtime-neutral and does not depend on Strategy, Risk, Application, Integrations, or Research.
- `calibration` cannot depend on Strategy, Risk, Application, Execution, Integrations, or Research.
- `strategy` is pure policy and cannot call Risk or Execution.
- `risk` owns hard vetoes and inventory capacity.
- `execution` owns runtime-neutral order reconciliation.
- `application` composes Strategy, Risk, and Execution.
- `integrations` owns venue/runtime adapters.
- `research` owns experiments, replay orchestration, and deterministic research Evidence.

The primary maintainability issue is not the top-level layering. It is concentration of unrelated responsibilities inside individual files:

- `datasets/audit.py`: models, duplicate handling, coverage/alignment rules, aggregation, digesting, and promotion guard.
- `integrations/hyperliquid/forward_recorder.py`: persistent segment storage, manifest serialization, frame decoding, and connection/session state machine.
- `research/tier2_replay.py`: dataset binding, risk gating, liquidity eligibility, runtime replay, economic attribution, identity, and Evidence assembly.

Generic dataclass/Enum/Decimal/datetime canonicalization is also duplicated in Dataset Manifest, Dataset Audit, Tier-2 candidate identity, and Tier-2 replay identity.

## 3. Design principles

### 3.1 Compatibility before cleanup

This is a structural refactor. Existing public imports remain valid:

```python
from grid_trade.datasets.audit import audit_canonical_dataset
from grid_trade.integrations.hyperliquid.forward_recorder import ForwardRecorderSession
from grid_trade.research.tier2_replay import run_tier2_replay
```

Each former module becomes a same-named package whose `__init__.py` explicitly re-exports the current public API. Consumers do not need migration changes.

### 3.2 Responsibility-based subpackages

Files are grouped by the reason they change, not merely by technical type. Storage durability, recorder protocol, replay liquidity, and replay accounting evolve independently and therefore receive independent modules.

### 3.3 Exact semantic preservation

The refactor preserves:

- Dataset finding order and accepted-event order.
- Audit acceptance and digest bytes.
- Forward segment bytes and manifest bytes.
- Connection, heartbeat, reference-cadence, and reconnect state transitions.
- Risk filtering and Hard Risk authority.
- Replay eligibility, visibility-boundary truncation, fill ordering, funding cash flow, maker-fee cash flow, and ending position.
- Tier-2 decision digest, run ID, Evidence event order, Evidence digest, and all NO-GO flags.

### 3.4 No compatibility layer beyond explicit package exports

No duplicate legacy implementation or long-lived forwarding module is retained. The package `__init__.py` is the single compatibility surface. Internal consumers import the narrow submodule they actually need where doing so improves dependency clarity.

### 3.5 Research runtime isolation

`hftbacktest` and `nautilus_trader` remain absent from core layers. The Tier-2 replay runner may call the existing research adapter, but lower-level Dataset, Domain, Integration, Serialization, Risk, Strategy, and Application modules may not import optional research runtimes.

## 4. Target structure

```text
src/grid_trade/
  serialization/
    __init__.py
    canonical.py

  datasets/
    audit/
      __init__.py
      models.py
      quality.py
      runner.py
    audit_contracts.py
    canonical.py
    contracts.py
    manifest.py

  integrations/
    hyperliquid/
      forward_recorder/
        __init__.py
        contracts.py
        manifest.py
        segment.py
        session.py
      archive.py
      node_data.py
      normalization.py

  research/
    tier2_replay/
      __init__.py
      attribution.py
      dataset.py
      evidence.py
      identity.py
      liquidity.py
      models.py
      runner.py
    hftbacktest_adapter.py
    replay_attribution.py
    tier2_calibrated_candidate.py
    tier2_calibrated_replay.py
    tier2_fixture_runner.py
```

Tests continue to mirror public feature ownership rather than every internal file. Architecture tests validate subpackage boundaries and public re-export contracts.

## 5. Shared canonical serialization

### 5.1 API

`grid_trade.serialization.canonical` provides:

```python
JSONScalar = None | bool | int | float | str
JSONValue = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]

def canonical_value(value: object) -> JSONValue: ...
def canonical_json_bytes(value: object) -> bytes: ...
def canonical_json_digest(value: object) -> str: ...
```

Behavior is deliberately equivalent to the existing Dataset/Tier-2 helpers:

- `Enum` becomes `.value`.
- `Decimal` becomes `str(value)`.
- timezone-aware `datetime` becomes UTC ISO-8601 with `Z`.
- dataclasses are serialized by declared field order before `sort_keys=True` normalizes object keys.
- tuple/list become JSON arrays.
- dict keys become strings.
- `None`, `str`, `int`, `float`, and `bool` pass through.
- unsupported values raise `TypeError` naming the concrete type.
- JSON uses `ensure_ascii=False`, `sort_keys=True`, `separators=(",", ":")`, and `allow_nan=False`.
- serialized bytes contain exactly one trailing newline.
- digest is SHA-256 of those exact bytes.

The Evidence ledger is intentionally excluded. It validates event sequence, run identity, timestamp monotonicity, and pre-canonicalized payload JSON; merging it with a generic serializer would weaken its domain contract.

### 5.2 Dependency rule

`serialization` depends only on the Python standard library. It may be imported by any layer, but it may not import `grid_trade.*` modules.

## 6. Dataset audit package

### 6.1 `models.py`

Owns:

- `AuditSeverity`
- `AuditFinding`
- `DatasetAuditReport`

These types keep the same field names, defaults, validation, and properties.

### 6.2 `quality.py`

Owns pure audit calculations:

- event-range calculation
- book/trade overlap duration
- deterministic maximum and p95 exchange gaps
- tick/lot alignment findings

Functions accept immutable tuples and return immutable results. They do not decide final Dataset acceptance and do not mutate manifests.

### 6.3 `runner.py`

Owns:

- raw-object resolution checks
- normalization-schema checks
- stable trade identity deduplication/conflict rejection
- deterministic finding aggregation in the existing order
- requested coverage, overlap, funding-completeness, and warning-gap policies
- final `DatasetAuditReport`
- `audit_report_digest`
- `require_promoting_dataset`

The existing one-pass ordering contract is preserved. Exact duplicates remain counted and omitted from `accepted_events`; conflicting stable identities remain errors and are omitted after the first occurrence.

### 6.4 `__init__.py`

Re-exports the exact pre-refactor public API, including `DatasetAuditExpectations` from `audit_contracts.py`.

## 7. Hyperliquid forward recorder package

### 7.1 `contracts.py`

Owns immutable transport/session contracts:

- `ForwardSegment`
- `ForwardRecorderConfig`
- `ContinuityRecord`
- `ForwardCaptureResult`
- `HyperliquidForwardTransport`

It also owns timestamp validation shared by session code. It contains no filesystem operations.

### 7.2 `manifest.py`

Owns:

- `FORWARD_SEGMENT_MANIFEST_SCHEMA_VERSION`
- `canonical_forward_segment_manifest_bytes`

Manifest bytes remain byte-for-byte identical.

### 7.3 `segment.py`

Owns:

- segment magic and frame encoding
- directory fsync behavior
- `ForwardSegmentWriter`
- `read_segment_records`

Durability order remains:

```text
append bytes → flush → fsync file
finalize: fsync partial → close → atomic replace data → fsync directory
         → write manifest partial → fsync manifest → atomic replace manifest → fsync directory
```

An interrupted segment remains `.partial` and cannot publish a completed manifest.

### 7.4 `session.py`

Owns `ForwardRecorderSession` and its protocol state:

- initial subscription
- heartbeat scheduling
- reference cadence and gap detection
- disconnect/reconnect continuity epochs
- first authoritative post-reconnect book boundary
- channel/writer validation

The session accepts `ForwardSegmentWriter` through its existing concrete API. Introducing a second writer protocol is unnecessary because there is only one durable implementation and tests already provide transport doubles.

### 7.5 `__init__.py`

Re-exports the exact pre-refactor forward-recorder public API.

## 8. Tier-2 replay package

### 8.1 `models.py`

Owns:

- `Tier2ReplayManifest`
- `Tier2ReplayResult`
- scalar validation used by those contracts

NO-GO invariants remain constructor-enforced: `production_authorized`, `alpha_validated`, and `economics_validated` cannot become true.

### 8.2 `dataset.py`

Owns:

- `required_hourly_funding_timestamps`
- deterministic event ordering/instrument validation
- DatasetManifest re-audit and audit-digest binding
- exact-hour funding completeness validation
- initial `MarketSnapshot` construction

Only `DatasetAcceptance.ACCEPTED` can enter promoting replay, through the existing `require_promoting_dataset` gate in the top-level runner.

### 8.3 `liquidity.py`

Owns:

- same-level and visible-top-N participation checks
- visibility-loss boundary attachment
- trusted replay truncation before the first unobservable order-price boundary
- market-feed sufficiency check

It delegates domain calculations to `replay_attribution.py` and does not invoke hftbacktest.

### 8.4 `attribution.py`

Owns:

- signed fill quantity
- causal position at a timestamp
- exact-hour funding cash-flow sequence
- funding PnL sum
- maker-fee cash flow
- ending position

It receives `ReplaySummary`; it does not decide whether or how replay runs.

### 8.5 `identity.py`

Owns:

- decision digest
- run ID

It uses shared canonical serialization and includes exactly the current identity inputs. Visibility boundaries remain excluded from the decision digest where the existing implementation uses decision-time eligibility; run Evidence still records replay-time visibility boundaries.

### 8.6 `evidence.py`

Owns:

- order payload conversion
- eligibility payload conversion
- deterministic Evidence event assembly

Event timestamps, tie-break ordering, sequence numbers, payload keys, Dataset Manifest digest, NO-GO flags, and economic-validation note remain unchanged.

### 8.7 `runner.py`

Owns only orchestration:

```text
promotion gate
  → validate and bind audited events
  → construct decision-time market snapshot
  → Hard Risk via application.passive_policy
  → liquidity and visibility eligibility
  → optional hftbacktest replay
  → funding/fee/position attribution
  → decision/run identity
  → Evidence assembly
  → Tier2ReplayResult
```

Risk remains authoritative before replay. The runner does not directly implement Dataset rules, liquidity formulas, accounting formulas, or Evidence serialization.

### 8.8 `__init__.py`

Re-exports exactly:

- `Tier2ReplayManifest`
- `Tier2ReplayResult`
- `required_hourly_funding_timestamps`
- `run_tier2_replay`

## 9. Error handling and fail-closed behavior

Existing exception types and message text remain unchanged wherever covered by tests or consumed as contract text. Structural helpers do not catch and translate errors unless the old module already did so.

The following remain hard failures:

- empty, unsorted, wrong-instrument, or manifest-unbound replay events
- incomplete exact-hour funding state
- unaccepted Dataset manifests
- non-zero initial working-order count
- invalid forward-recorder lifecycle transitions
- mismatched recorder writer channel/instrument/continuity epoch
- corrupt/truncated segment framing
- unsupported canonical serialization values
- pinned hftbacktest runtime/version failures

No fallback may silently reinterpret unavailable state as zero or successful replay.

## 10. Testing strategy

### 10.1 TDD contracts added before moving implementation

New tests first establish:

1. Generic canonical serialization produces the exact existing byte/digest contract.
2. Public APIs remain importable from the three historical module paths.
3. Internal subpackages obey dependency direction:
   - `serialization` imports no `grid_trade` module.
   - forward-recorder storage/manifest/contracts do not import session.
   - Tier-2 Dataset/Liquidity/Attribution/Identity/Evidence modules do not import `hftbacktest` directly.
   - only Tier-2 runner imports the research runtime adapter.
4. CI type-check targets the Tier-2 replay package, not a removed single file.

Each test is observed failing for the missing package or old monolithic layout before implementation.

### 10.2 Existing behavioral regression suites

Run all existing Dataset Audit, Hyperliquid Forward Recorder, Tier-2, architecture, Core, and Research Integration tests. Existing tests are the authoritative behavior contract; they are not weakened or rewritten to accommodate the refactor.

### 10.3 Determinism gates

GitHub Research Integration must reproduce all existing fresh-process digests:

- S0
- S1
- S2
- Adaptive S3-S7
- Microstructure Calibration
- Calibrated Adaptive
- Tier-2

No digest update is expected or permitted for this refactor.

### 10.4 Static verification

The exact final branch head must pass:

- dependency-lock check
- Ruff format check
- Ruff lint
- Core pytest
- strict Core mypy
- Research/Integration pytest
- all focused Research mypy commands
- architecture tests

## 11. Documentation and migration

README architecture documentation gains a package map and explains that public import paths are stable while internal modules are responsibility-scoped. The Tier-2 design document gains the replay pipeline/module ownership map.

No user migration is required. Internal imports may be narrowed, but external public imports remain unchanged.

## 12. Explicit non-goals

This change does not:

- modify S0-S7 strategy decisions or calibrated parameter formulas
- modify Hard Risk limits or acceptance rules
- add a live-trading runtime
- change Dataset acceptance policy
- change Hyperliquid payload formats or network behavior
- add a generalized plugin framework
- split every large research fixture runner
- change Evidence schemas or pinned digests
- validate alpha, profitability, economics, or production readiness
- merge the refactor branch into `main` without a separate merge decision

## 13. Rollout and rollback

The implementation is delivered as one reviewable branch because a Python path cannot simultaneously contain both `audit.py` and `audit/` (likewise for the other two file-to-package conversions). Commits remain logically separated by serialization, Dataset Audit, Forward Recorder, Tier-2 Replay, and documentation/CI.

Rollback is a normal PR revert: public API and persisted data formats do not change, so no data migration or compatibility rollback is required.

## 14. Repository hygiene

All obsolete non-`main` remote branches were removed before this refactor. The implementation uses a new dedicated review branch and a Draft PR. `main` remains protected from direct feature integration until the final branch head is reviewed and its complete CI matrix succeeds.
