"""LOG-01 / LOG-06 enforcement tests.

LOG-01: execution, reconciliation, and config/startup/control path modules
must obtain loggers ONLY through `trading_platform.core.logging.get_logger`
-- direct `logging.getLogger(...)` is forbidden there. Enforced below by a
static AST scan of the enumerated in-scope module files.

LOG-06: under default config, no emitted log line may leak a credential
(password/api_key/Authorization) or a full broker order ID -- only the
last-6 masked form is permitted. (Emitted-line enforcement tests land in a
follow-up commit within this plan.)
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# LOG-01: import-boundary test
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src" / "trading_platform"

# Exact in-scope module list (execution, reconciliation, and config/startup/
# control paths, per 10-06-PLAN.md `<key_facts>`). Any future edit that
# reintroduces a raw `logging.getLogger(...)` call in one of these files
# must fail this test -- that is the whole point of LOG-01 enforcement.
IN_SCOPE_MODULES: list[Path] = [
    _SRC / "services" / "paper_execution.py",
    _SRC / "services" / "alpaca.py",
    _SRC / "services" / "order_state_machine.py",
    _SRC / "services" / "concurrency_guard.py",
    _SRC / "services" / "operator_status.py",
    _SRC / "services" / "operator_controls.py",
    _SRC / "services" / "reconciliation.py",
    _SRC / "worker" / "__main__.py",
    _SRC / "services" / "bootstrap.py",
    _SRC / "api" / "app.py",
    _SRC / "core" / "startup.py",
    _SRC / "core" / "config_validation.py",
]


def _find_direct_getlogger_calls(path: Path) -> list[int]:
    """Return the line numbers of any `logging.getLogger(...)` call in `path`.

    Uses an AST walk (not a substring `grep`) so a `logging.getLogger`
    mention inside a comment, docstring, or string literal does not produce
    a false positive; only an actual call expression counts. Plain
    `import logging` (for `logging.WARNING`/`logging.INFO` level constants
    or `logging.Logger` type hints) is explicitly allowed.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "getLogger"
            and isinstance(func.value, ast.Name)
            and func.value.id == "logging"
        ):
            hits.append(node.lineno)
    return hits


@pytest.mark.parametrize(
    "module_path",
    IN_SCOPE_MODULES,
    ids=[p.relative_to(_SRC).as_posix() for p in IN_SCOPE_MODULES],
)
def test_import_boundary_no_direct_get_logger(module_path: Path) -> None:
    assert module_path.exists(), f"in-scope module not found: {module_path}"
    hits = _find_direct_getlogger_calls(module_path)
    assert not hits, (
        f"{module_path.relative_to(_ROOT)} calls logging.getLogger(...) "
        f"directly at line(s) {hits}; use "
        "trading_platform.core.logging.get_logger(...) instead (LOG-01)."
    )


def test_import_boundary_module_list_is_not_empty() -> None:
    # Guards against a typo'd/emptied IN_SCOPE_MODULES silently making the
    # parametrized test above a no-op.
    assert len(IN_SCOPE_MODULES) == 12
