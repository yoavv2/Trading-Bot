"""Derived (non-hand-listed) mutation boundary for scripts/ and worker commands.

ORCH-08 / BND-01. ``tests/test_orchestration_boundaries.py`` pins scripts/ and
worker modules against a HAND-LISTED set of mutating entry points
(``_MUTATING_ENTRY_POINTS``). Lower-level writers such as ``upsert_daily_bars``
or ``upsert_symbol`` are not on that list, so a new script or worker command
calling them directly would slip through. This module closes that hole by
DERIVING the writer set from the service layer itself.

Discovery rule (deliberately simple, AST-only, no imports of service code).
A function or method under ``src/trading_platform/services/**/*.py`` is a
"discovered mutator" when its OWN body (nested defs are attributed to
themselves) contains any of:

  1. ``<session>.add(...)``, ``.add_all(...)``, ``.merge(...)``,
     ``.delete(...)`` or ``.flush(...)`` where the receiver's terminal name
     contains ``session`` (``session``, ``db_session``, ``self._session``...).
     The receiver restriction is what keeps ``some_set.add(x)`` /
     ``dict.update(x)`` from being flagged.
  2. ``<session-or-connection>.execute(<dml>)`` where ``<dml>`` is a call chain
     rooted at ``insert(...)``, ``update(...)``, ``delete(...)`` or
     ``pg_insert(...)`` -- either inline or via a local variable assigned from
     such a chain (``stmt = pg_insert(M).values(...)``; ``stmt =
     stmt.on_conflict_do_update(...)``).
  3. any ``.bulk_*(...)`` call (``bulk_save_objects``, ``bulk_insert_mappings``...).

Dunder methods are excluded (a constructor is never called by name from a
script). The discovered names are unioned with ``_MUTATING_ENTRY_POINTS`` and
every call/import of any of those names in the scanned scripts/worker paths
must be either absent or in ``_ALLOWED_MUTATING_CALLS``.

Known limits (documented, not hidden): detection is name-based, so a
same-named read-only function elsewhere would be flagged (fix detection, do not
add broad exemptions); it is not transitive (a function that only calls a
mutator is not itself discovered -- the hand-listed entry points cover the
high-level orchestration functions); and attribute mutation of a loaded ORM
object without an explicit ``flush``/``add`` is not detected.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_orchestration_boundaries import (  # noqa: E402
    _ALLOWED_MUTATING_CALLS,
    _MUTATING_ENTRY_POINTS,
    _ROOT,
    _scanned_mutation_paths,
    _terminal_name,
)

_SERVICES_ROOT = _ROOT / "src/trading_platform/services"

_SESSION_WRITE_METHODS = {"add", "add_all", "merge", "delete", "flush"}
_DML_ROOTS = {"insert", "update", "delete", "pg_insert"}
_FunctionDef = (ast.FunctionDef, ast.AsyncFunctionDef)


def _receiver_is_session(receiver: ast.expr) -> bool:
    name = _terminal_name(receiver)
    return name is not None and "session" in name.lower()


def _dml_root(node: ast.expr) -> bool:
    """True when ``node`` is a call/attribute chain rooted at insert/update/delete/pg_insert."""
    while True:
        if isinstance(node, ast.Call):
            node = node.func
        elif isinstance(node, ast.Attribute):
            node = node.value
        elif isinstance(node, ast.Name):
            return node.id in _DML_ROOTS
        else:
            return False


def _own_nodes(function: ast.AST):
    """Walk a function body without descending into nested function definitions."""
    stack = list(ast.iter_child_nodes(function))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, _FunctionDef):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _writes_directly(function: ast.AST) -> bool:
    nodes = list(_own_nodes(function))

    dml_variables: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Assign) and _dml_root(node.value):
            dml_variables.update(t.id for t in node.targets if isinstance(t, ast.Name))
    # Second pass so ``stmt = stmt.on_conflict_do_update(...)`` chains stay tracked.
    for node in nodes:
        if isinstance(node, ast.Assign):
            root = node.value
            while isinstance(root, (ast.Call, ast.Attribute)):
                root = root.func if isinstance(root, ast.Call) else root.value
            if isinstance(root, ast.Name) and root.id in dml_variables:
                dml_variables.update(t.id for t in node.targets if isinstance(t, ast.Name))

    for node in nodes:
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        method = node.func.attr
        receiver = node.func.value
        if method in _SESSION_WRITE_METHODS and _receiver_is_session(receiver):
            return True
        if method.startswith("bulk_"):
            return True
        if method == "execute" and node.args:
            statement = node.args[0]
            if _dml_root(statement):
                return True
            if isinstance(statement, ast.Name) and statement.id in dml_variables:
                return True
    return False


def discover_mutators_in_source(source: str) -> set[str]:
    tree = ast.parse(source)
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, _FunctionDef)
        and not (node.name.startswith("__") and node.name.endswith("__"))
        and _writes_directly(node)
    }


def _discover_service_mutators() -> dict[str, set[str]]:
    """{function name: {relative service files that define a direct writer of that name}}"""
    found: dict[str, set[str]] = {}
    for path in sorted(_SERVICES_ROOT.rglob("*.py")):
        rel = path.relative_to(_ROOT).as_posix()
        for name in discover_mutators_in_source(path.read_text()):
            found.setdefault(name, set()).add(rel)
    return found


def _mutator_hits(source: str, rel: str, names: set[str]) -> list[tuple[str, str, int]]:
    """(rel path, name, lineno) for every call or from-import of a name in ``names``."""
    hits: list[tuple[str, str, int]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            name = _terminal_name(node.func)
            if name in names:
                hits.append((rel, name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            hits.extend((rel, alias.name, node.lineno) for alias in node.names if alias.name in names)
    return hits


def test_service_mutator_discovery_is_not_vacuous() -> None:
    discovered = _discover_service_mutators()

    # The low-level writers the hand-listed set is blind to.
    assert "upsert_daily_bars" in discovered
    assert "upsert_symbol" in discovered
    assert discovered["upsert_daily_bars"] == {"src/trading_platform/services/ingestion.py"}
    assert discovered["upsert_symbol"] == {"src/trading_platform/services/ingestion.py"}
    assert "upsert_daily_bars" not in _MUTATING_ENTRY_POINTS
    assert "upsert_symbol" not in _MUTATING_ENTRY_POINTS

    # Also picks up the other pg_insert-style writer and orchestration writers.
    assert "upsert_market_sessions" in discovered
    assert "ensure_strategy_record" in discovered

    # Non-trivially sized: a broken walker that finds a handful of names must fail.
    assert len(discovered) >= 30, sorted(discovered)


def test_discovery_detects_writers_and_ignores_non_session_receivers() -> None:
    source = """
from sqlalchemy import insert
from sqlalchemy.dialects.postgresql import insert as pg_insert

def writes_via_add(session, row):
    session.add(row)

def writes_via_method_session(self, row):
    self._session.flush()

def writes_via_inline_dml(db_session):
    db_session.execute(insert(Model).values(a=1))

def writes_via_variable_dml(session, rows):
    stmt = pg_insert(Model).values(rows)
    stmt = stmt.on_conflict_do_update(index_elements=["id"], set_={})
    session.execute(stmt)

def writes_via_bulk(session, rows):
    session.bulk_save_objects(rows)

def outer_reads_only(session):
    def inner_writer():
        session.add(object())
    return session.execute(select(Model)).all()

def set_add_is_not_a_write(rows):
    seen = set()
    for row in rows:
        seen.add(row)
    seen.update(rows)
    headers = {}
    headers.update({"a": 1})
    return seen

def select_execute_is_not_a_write(session):
    return session.execute(select(Model).where(Model.id == 1)).scalars().all()

class Svc:
    def __init__(self, session):
        session.add(object())

    def method_writer(self, session):
        session.delete(object())
"""
    assert discover_mutators_in_source(source) == {
        "writes_via_add",
        "writes_via_method_session",
        "writes_via_inline_dml",
        "writes_via_variable_dml",
        "writes_via_bulk",
        "inner_writer",
        "method_writer",
    }


def test_scripts_and_workers_call_no_derived_service_mutators() -> None:
    derived = set(_discover_service_mutators())
    forbidden = derived | _MUTATING_ENTRY_POINTS

    offenders = [
        f"{rel}:{lineno} uses {name}"
        for path in _scanned_mutation_paths()
        for rel, name, lineno in _mutator_hits(
            path.read_text(), path.relative_to(_ROOT).as_posix(), forbidden
        )
        if (rel, name) not in _ALLOWED_MUTATING_CALLS
    ]

    assert offenders == [], (
        "scripts/ and worker modules must not call service-layer writers "
        "(derived from services/**, unioned with _MUTATING_ENTRY_POINTS):\n"
        + "\n".join(offenders)
    )


def test_derived_scan_flags_a_bypass_the_hand_listed_scan_misses() -> None:
    """The scan is live: a bypass calling upsert_daily_bars/upsert_symbol is
    caught by the derived set but NOT by the hand-listed one."""
    bypass = (
        "from trading_platform.services.ingestion import upsert_daily_bars, upsert_symbol\n"
        "def main(session):\n"
        "    sym = upsert_symbol(session, 'AAPL')\n"
        "    upsert_daily_bars(session, [], sym.id)\n"
    )
    rel = "scripts/new_bypass.py"

    assert _mutator_hits(bypass, rel, _MUTATING_ENTRY_POINTS) == []

    derived_hits = _mutator_hits(bypass, rel, set(_discover_service_mutators()) | _MUTATING_ENTRY_POINTS)
    assert {(r, name) for r, name, _ in derived_hits} == {
        (rel, "upsert_symbol"),
        (rel, "upsert_daily_bars"),
    }
