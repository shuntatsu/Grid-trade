import ast
from pathlib import Path

_SRC = Path("src/grid_trade")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def _runtime_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def test_dataset_audit_is_responsibility_scoped_package() -> None:
    assert Path("src/grid_trade/datasets/audit").is_dir()
    assert not Path("src/grid_trade/datasets/audit.py").exists()


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


def test_dataset_audit_package_stays_runtime_neutral() -> None:
    package = _SRC / "datasets" / "audit"
    forbidden = (
        "grid_trade.application",
        "grid_trade.calibration",
        "grid_trade.evidence",
        "grid_trade.execution",
        "grid_trade.integrations",
        "grid_trade.research",
        "grid_trade.risk",
        "grid_trade.strategy",
        "hftbacktest",
        "nautilus_trader",
    )
    violations = [
        f"{path}: {imported}"
        for path in sorted(package.rglob("*.py"))
        for imported in _imports(path)
        if imported.startswith(forbidden)
    ]
    assert violations == []


def test_forward_recorder_is_responsibility_scoped_package() -> None:
    assert Path("src/grid_trade/integrations/hyperliquid/forward_recorder").is_dir()
    assert not Path("src/grid_trade/integrations/hyperliquid/forward_recorder.py").exists()


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

    assert FORWARD_SEGMENT_MANIFEST_SCHEMA_VERSION == ("hyperliquid-forward-segment-manifest-v1")
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


def test_forward_recorder_storage_does_not_depend_on_session() -> None:
    package = _SRC / "integrations" / "hyperliquid" / "forward_recorder"
    violations = [
        f"{path}: {imported}"
        for name in ("contracts.py", "manifest.py", "segment.py")
        for path in (package / name,)
        if path.exists()
        for imported in _imports(path)
        if imported.startswith("grid_trade.integrations.hyperliquid.forward_recorder.session")
    ]
    assert violations == []


def test_tier2_replay_is_responsibility_scoped_package() -> None:
    assert Path("src/grid_trade/research/tier2_replay").is_dir()
    assert not Path("src/grid_trade/research/tier2_replay.py").exists()


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


def test_only_tier2_runner_owns_hftbacktest_adapter_dependency() -> None:
    package = _SRC / "research" / "tier2_replay"
    violations = [
        f"{path}: {imported}"
        for path in sorted(package.glob("*.py"))
        if path.name not in {"runner.py", "__init__.py"}
        for imported in _runtime_imports(path)
        if imported.startswith(("grid_trade.research.hftbacktest_adapter", "hftbacktest"))
    ]
    assert violations == []


def test_tier2_runner_does_not_depend_on_exchange_integration() -> None:
    runner = _SRC / "research" / "tier2_replay" / "runner.py"
    violations = [
        imported for imported in _imports(runner) if imported.startswith("grid_trade.integrations")
    ]
    assert violations == []


def test_research_ci_type_checks_tier2_package() -> None:
    workflow = Path(".github/workflows/research.yml").read_text(encoding="utf-8")
    assert "src/grid_trade/research/tier2_replay\n" in workflow
    assert "src/grid_trade/research/tier2_replay.py" not in workflow


def test_research_ci_runs_for_canonical_serialization_changes() -> None:
    workflow = Path(".github/workflows/research.yml").read_text(encoding="utf-8")
    assert workflow.count('"src/grid_trade/serialization/**"') == 2
