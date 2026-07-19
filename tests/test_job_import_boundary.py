"""JOB-04 reverse import-boundary enforcement test.

Scans in the reverse direction from ``tests/test_log_enforcement.py``:
that file proves framework modules avoid a specific call
(``logging.getLogger``); this file proves domain service modules never
reach *up* into the Job framework, the HTTP layer, or any scheduling
library -- services must remain callable without the Job framework
present (per PROJECT.md architecture invariant 8: "Domain services remain
infrastructure-independent").
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _ROOT / "src"
_SERVICES_DIR = _SRC_ROOT / "trading_platform" / "services"

# Auto-scoping: every *.py under services/, recursively, excluding
# __pycache__. Not hardcoded -- must grow automatically as services are
# added, which is what makes the boundary durable across future refactors.
SERVICE_MODULES: list[Path] = sorted(
    path for path in _SERVICES_DIR.rglob("*.py") if "__pycache__" not in path.parts
)

# JOB-04: no domain service may import job, HTTP, scheduling, or UI
# modules. This project has no separate UI Python package (the console is
# a Next.js/TypeScript app) so the UI half of JOB-04 is covered by the
# absence of any importable UI module, not by a scan entry here.
FORBIDDEN_IMPORT_ROOTS = (
    "trading_platform.jobs",
    "trading_platform.api",
    "trading_platform.worker",
    "fastapi",
    "starlette",
    "apscheduler",
    "celery",
)


def _dotted_module_name(path: Path) -> str:
    """Return the dotted module name for `path`, relative to `_SRC_ROOT`."""
    rel_parts = list(path.relative_to(_SRC_ROOT).with_suffix("").parts)
    if rel_parts[-1] == "__init__":
        rel_parts = rel_parts[:-1]
    return ".".join(rel_parts)


def _dotted_package_name(path: Path) -> str:
    """Return the dotted name of the package physically containing `path`.

    This is `__package__` for both a regular module file and its sibling
    `__init__.py` -- i.e. the dotted path of the enclosing directory.
    """
    dir_parts = list(path.relative_to(_SRC_ROOT).parent.parts)
    return ".".join(dir_parts)


def _resolve_relative_import(path: Path, *, level: int, module: str | None) -> str:
    """Resolve a relative `from . import x` / `from .mod import x` to a
    dotted absolute module name, so a relative import cannot evade the
    forbidden-root check."""
    package = _dotted_package_name(path)
    bits = package.rsplit(".", level - 1)
    base = bits[0]
    return f"{base}.{module}" if module else base


def _collect_imported_modules(path: Path) -> list[tuple[str, int]]:
    """Parse `path` and return every imported dotted module name with its
    source line number, resolving relative imports to absolute names."""
    tree = ast.parse(path.read_text(), filename=str(path))
    collected: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                collected.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                dotted = _resolve_relative_import(path, level=node.level, module=node.module)
            else:
                dotted = node.module or ""
            collected.append((dotted, node.lineno))
    return collected


def _forbidden_root_hit(module_name: str) -> str | None:
    for root in FORBIDDEN_IMPORT_ROOTS:
        if module_name == root or module_name.startswith(f"{root}."):
            return root
    return None


@pytest.mark.parametrize(
    "module_path",
    SERVICE_MODULES,
    ids=[p.relative_to(_SRC_ROOT).as_posix() for p in SERVICE_MODULES],
)
def test_domain_service_does_not_import_job_http_or_scheduling_modules(
    module_path: Path,
) -> None:
    offenders = [
        (module_name, lineno, root)
        for module_name, lineno in _collect_imported_modules(module_path)
        if (root := _forbidden_root_hit(module_name)) is not None
    ]
    assert not offenders, (
        f"{module_path.relative_to(_ROOT)} imports forbidden module(s): "
        + ", ".join(
            f"'{module_name}' at line {lineno} (forbidden root '{root}')"
            for module_name, lineno, root in offenders
        )
        + " -- domain services must not depend on Jobs, HTTP, scheduling, "
        "or UI concerns (JOB-04)."
    )


def test_service_module_scan_scope_is_not_empty() -> None:
    # Guards against a broken glob silently turning the parametrized test
    # above into a no-op. The repository currently contains 33 modules
    # under src/trading_platform/services/; a floor of 30 catches a broken
    # glob while tolerating normal refactoring churn.
    assert len(SERVICE_MODULES) >= 30


def test_forbidden_import_roots_cover_the_job_04_categories() -> None:
    # job, HTTP, scheduling -- explicit literal presence check. UI is
    # covered by the absence of any importable UI module (see module
    # docstring / FORBIDDEN_IMPORT_ROOTS comment above), not a scan entry.
    assert "trading_platform.jobs" in FORBIDDEN_IMPORT_ROOTS
    assert "fastapi" in FORBIDDEN_IMPORT_ROOTS
    assert "apscheduler" in FORBIDDEN_IMPORT_ROOTS
