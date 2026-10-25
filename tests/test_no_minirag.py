"""The harness is a separate product: it never imports the RAG repository.

Issue #16 removed the evaluation harness from kenmann01/minirag and left the two
products with no shared guts. This test fails if `src/evalharness` grows an
import of that repository, a reference to it by name, or if a test imports the
RAG package as a module instead of driving it over the published contract.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "evalharness"
TESTS = REPO / "tests"

FORBIDDEN_PACKAGES = ("app", "minirag")
"""Import names that would couple the harness to the RAG repository."""


def _python_files(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob("*.py") if "__pycache__" not in path.parts
    )


def _imported_modules(path: Path) -> list[str]:
    """Return every module name a file imports, absolute or relative."""
    modules: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def _forbidden(module: str) -> bool:
    return any(
        module == package or module.startswith(f"{package}.")
        for package in FORBIDDEN_PACKAGES
    )


def test_the_harness_package_imports_no_rag_module():
    offenders = [
        f"{path.relative_to(REPO).as_posix()} imports {module}"
        for path in _python_files(SRC)
        for module in _imported_modules(path)
        if _forbidden(module)
    ]
    assert offenders == []


def test_the_harness_package_never_names_the_rag_repository():
    offenders = [
        path.relative_to(REPO).as_posix()
        for path in _python_files(SRC)
        if "minirag" in path.read_text(encoding="utf-8").lower()
    ]
    assert offenders == []


def test_no_test_imports_the_rag_repository_as_a_module():
    offenders = [
        f"{path.relative_to(REPO).as_posix()} imports {module}"
        for path in _python_files(TESTS)
        for module in _imported_modules(path)
        if _forbidden(module)
    ]
    assert offenders == []
