"""Reusable SQL statement-count context manager for tests.

Dependency-free (stdlib + sqlalchemy only). Attaches a `before_cursor_execute`
listener to the underlying `Engine` for the duration of a `with` block so
tests can assert a hard, literal bound on the number of SQL statements a code
path issues (e.g. "preflight issues at most 2 queries"), rather than a timing
or "roughly constant" heuristic.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from sqlalchemy import event
from sqlalchemy.engine import Connectable, Engine


@dataclass
class QueryCounter:
    """Mutable counter updated in-place while the `with count_queries(...)` block is open."""

    count: int = 0
    statements: list[str] = field(default_factory=list)


def _resolve_engine(bind: Connectable) -> Engine:
    """Resolve the underlying `Engine` from a Session, Engine, or Connection."""
    get_bind = getattr(bind, "get_bind", None)
    if callable(get_bind):
        bind = get_bind()

    engine = getattr(bind, "engine", None)
    if isinstance(engine, Engine):
        return engine

    raise TypeError(f"Cannot resolve a SQLAlchemy Engine from bind of type {type(bind)!r}.")


@contextmanager
def count_queries(bind: Connectable) -> Iterator[QueryCounter]:
    """Count every SQL statement (SELECT/INSERT/UPDATE/DELETE) executed against
    `bind`'s underlying Engine for the duration of the `with` block.

    `bind` may be a SQLAlchemy Session, Engine, or Connection. Does not filter
    by statement type or table -- callers assert on the raw count.
    """
    engine = _resolve_engine(bind)
    counter = QueryCounter()

    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany) -> None:
        counter.count += 1
        counter.statements.append(statement)

    event.listen(engine, "before_cursor_execute", _before_cursor_execute)
    try:
        yield counter
    finally:
        event.remove(engine, "before_cursor_execute", _before_cursor_execute)
