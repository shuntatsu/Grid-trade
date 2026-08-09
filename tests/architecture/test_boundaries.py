import ast
from pathlib import Path

import pytest

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


def _python_files(layer: str) -> tuple[Path, ...]:
    return tuple(sorted((_SRC / layer).rglob("*.py")))


@pytest.mark.parametrize(
    ("layer", "forbidden_prefixes"),
    [
        (
            "domain",
            (
                "grid_trade.application",
                "grid_trade.evidence",
                "grid_trade.execution",
                "grid_trade.integrations",
                "grid_trade.research",
                "grid_trade.risk",
                "grid_trade.strategy",
            ),
        ),
        (
            "strategy",
            (
                "grid_trade.application",
                "grid_trade.execution",
                "grid_trade.integrations",
                "grid_trade.research",
                "grid_trade.risk",
            ),
        ),
        (
            "risk",
            (
                "grid_trade.application",
                "grid_trade.execution",
                "grid_trade.integrations",
                "grid_trade.research",
                "grid_trade.strategy",
            ),
        ),
        (
            "execution",
            (
                "grid_trade.application",
                "grid_trade.integrations",
                "grid_trade.research",
                "grid_trade.risk",
                "grid_trade.strategy",
            ),
        ),
    ],
)
def test_core_layer_dependency_direction(
    layer: str,
    forbidden_prefixes: tuple[str, ...],
) -> None:
    violations: list[str] = []
    for path in _python_files(layer):
        for imported in _imports(path):
            if imported.startswith(forbidden_prefixes):
                violations.append(f"{path}: {imported}")

    assert violations == []


def test_optional_runtime_dependencies_stay_out_of_core_layers() -> None:
    forbidden_runtime_prefixes = ("hftbacktest", "nautilus_trader")
    violations: list[str] = []

    for layer in ("application", "domain", "evidence", "execution", "risk", "strategy"):
        for path in _python_files(layer):
            for imported in _imports(path):
                if imported.startswith(forbidden_runtime_prefixes):
                    violations.append(f"{path}: {imported}")

    assert violations == []
