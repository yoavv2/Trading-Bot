"""Import-boundary + reloadability tests for the DB lifecycle model (DB-01/02/03).

These tests pin two invariants established in plan 10-03:

1. Every engine/session lifecycle symbol is imported from exactly one
   canonical path — ``trading_platform.db.session`` — never from the
   package surface ``trading_platform.db``.
2. The reloadable-manager model is preserved: calling ``clear_engine_cache()``
   forces a fresh engine to be constructed on next access, so the test suite
   (and any future multi-database entrypoint) can still rebind to a
   different database within the same process.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from trading_platform.core.settings import load_settings
from trading_platform.db.session import clear_engine_cache, get_engine

# The set of engine/session lifecycle symbols that must only ever be
# imported from `trading_platform.db.session`.
_LIFECYCLE_SYMBOLS = {
    "get_engine",
    "get_session_factory",
    "session_scope",
    "build_engine",
    "clear_engine_cache",
    "check_database_connection",
}

_SRC_ROOT = _ROOT / "src" / "trading_platform"


def _iter_source_files() -> list[Path]:
    return sorted(_SRC_ROOT.rglob("*.py"))


def test_lifecycle_symbols_imported_only_from_canonical_session_module() -> None:
    """Every engine/session symbol import must come from trading_platform.db.session.

    Scans all of `src/trading_platform/**/*.py` for `from trading_platform.db
    import <symbol>` (or `from trading_platform.db import (...)`) statements
    that import any lifecycle symbol from the package surface instead of the
    canonical `trading_platform.db.session` module, and fails if any are
    found (DB-03).
    """
    violations: list[str] = []

    for path in _iter_source_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = node.module or ""
            imported_names = {alias.name for alias in node.names}
            offending = imported_names & _LIFECYCLE_SYMBOLS

            if not offending:
                continue

            if module == "trading_platform.db.session":
                # Canonical path — allowed.
                continue

            if module in ("trading_platform.db", "db", ".db", ".") or module.endswith(
                ".db"
            ):
                violations.append(
                    f"{path.relative_to(_ROOT)}: imports {sorted(offending)} "
                    f"from '{module}' instead of 'trading_platform.db.session'"
                )

    assert not violations, "Non-canonical lifecycle-symbol imports found:\n" + "\n".join(
        violations
    )


def test_db_package_does_not_reexport_lifecycle_symbols() -> None:
    """The `trading_platform.db` package surface must not expose lifecycle symbols."""
    import trading_platform.db as db_package

    exported = set(getattr(db_package, "__all__", []))
    leaked = exported & _LIFECYCLE_SYMBOLS
    assert not leaked, f"trading_platform.db re-exports lifecycle symbols: {leaked}"

    for symbol in _LIFECYCLE_SYMBOLS:
        assert not hasattr(
            db_package, symbol
        ), f"trading_platform.db still exposes '{symbol}' as an attribute"


def test_clear_engine_cache_forces_a_fresh_engine() -> None:
    """clear_engine_cache() must force get_engine() to rebuild (test-DB-swap capability)."""
    settings = load_settings()

    engine_before = get_engine(settings.database)
    engine_before_same = get_engine(settings.database)
    assert engine_before is engine_before_same, "cache should return the same engine on repeat calls"

    clear_engine_cache()

    engine_after = get_engine(settings.database)
    assert engine_after is not engine_before, "clear_engine_cache() must force a fresh engine"

    # Leave the cache clean for subsequent tests/fixtures.
    clear_engine_cache()
