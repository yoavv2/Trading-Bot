"""Synchronous engine and session helpers for the persistence layer."""

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
