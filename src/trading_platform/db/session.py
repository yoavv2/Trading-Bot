"""Synchronous engine and session helpers for the persistence layer.

Lifecycle model: EXPLICIT RELOADABLE MANAGER.

This module is the single canonical source of engine and session-factory
construction for the whole codebase (DB-01). The engine and session
factory are NOT process-immutable singletons — they are cached in the
module-level dicts ``_ENGINE_CACHE`` / ``_SESSION_FACTORY_CACHE``, keyed by
``(database.url, database.echo)``, and can be reset at any time via
``clear_engine_cache()``. This is a deliberate design choice, not an
oversight: the test suite (and any future multi-database entrypoint)
needs to rebind the engine/session factory to a different database (e.g.
the test database vs. the local development database) within the same
process, which a true singleton would not allow.

This keyed dict-cache is the SINGLE authorized caching mechanism for the
engine/session lifecycle (DB-02). Memoizing an engine or session factory
via ``functools``'s single-value memoizing decorator (or any other
competing cache) is forbidden anywhere in this codebase — only the
reloadable dict-cache defined here may hold a live
``Engine``/``sessionmaker`` instance. (The codebase's only use of that
decorator is on ``load_settings`` in ``core/settings.py``, which caches
parsed configuration, not database engine/session objects — that is a
distinct, intentionally-singleton concern and does not compete with this
lifecycle model.)

All engine/session access must go through this module
(``trading_platform.db.session``) — it is the one canonical import path
(DB-03). ``trading_platform.db`` (the package ``__init__``) does not
re-export engine/session symbols.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from trading_platform.core.settings import DatabaseSettings, Settings, load_settings

_ENGINE_CACHE: dict[tuple[str, bool], Engine] = {}
_SESSION_FACTORY_CACHE: dict[tuple[str, bool], sessionmaker[Session]] = {}


def _resolve_database_settings(settings: Settings | DatabaseSettings | None) -> DatabaseSettings:
    if settings is None:
        return load_settings().database
    if isinstance(settings, Settings):
        return settings.database
    return settings


def build_engine(database: DatabaseSettings, *, echo: bool | None = None) -> Engine:
    resolved_echo = database.echo if echo is None else echo
    return create_engine(
        database.url,
        echo=resolved_echo,
        future=True,
        pool_pre_ping=True,
    )


def _cache_key(database: DatabaseSettings) -> tuple[str, bool]:
    return database.url, database.echo


def get_engine(settings: Settings | DatabaseSettings | None = None) -> Engine:
    database = _resolve_database_settings(settings)
    key = _cache_key(database)
    engine = _ENGINE_CACHE.get(key)
    if engine is None:
        engine = build_engine(database)
        _ENGINE_CACHE[key] = engine
    return engine


def get_session_factory(settings: Settings | DatabaseSettings | None = None) -> sessionmaker[Session]:
    database = _resolve_database_settings(settings)
    key = _cache_key(database)
    session_factory = _SESSION_FACTORY_CACHE.get(key)
    if session_factory is None:
        session_factory = sessionmaker(
            bind=get_engine(database),
            class_=Session,
            autoflush=False,
            expire_on_commit=False,
        )
        _SESSION_FACTORY_CACHE[key] = session_factory
    return session_factory


@contextmanager
def session_scope(settings: Settings | DatabaseSettings | None = None) -> Iterator[Session]:
    session = get_session_factory(settings)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_database_connection(settings: Settings | DatabaseSettings | None = None) -> tuple[bool, str]:
    engine = get_engine(settings)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True, "Database connection verified."
    except Exception as exc:  # pragma: no cover - exercised through readiness integration tests
        return False, str(exc)


def clear_engine_cache() -> None:
    for engine in _ENGINE_CACHE.values():
        engine.dispose()
    _ENGINE_CACHE.clear()
    _SESSION_FACTORY_CACHE.clear()
