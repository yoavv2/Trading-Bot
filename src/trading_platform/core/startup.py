"""Process-entrypoint startup gate: refuse to boot on invalid config.

`enforce_startup_config(*, mode, require_database=True)` is the single
chokepoint every process entrypoint (FastAPI lifespan, worker subcommands,
the dry-bootstrap flow) calls BEFORE constructing any domain service. It:

1. Builds the raw pre-pydantic settings payload (`build_settings_payload()`)
   and runs it through `validate_config` (10-01) for the caller-supplied
   `mode`. A `ConfigValidationError` (missing secret, out-of-range tolerance,
   conflicting mode/endpoint) prints the single actionable, field-naming
   message to stderr and exits with a non-zero, distinct-from-concurrency-
   lock code (CFG-05, CFG-07 surfaced at startup).
2. If `require_database`, runs `check_database_connection` (already-existing
   DB primitive in `db/session.py` — not reinvented here) against the
   validated settings. An unreachable DB prints an actionable message naming
   the DB target (host/port/name) and reason, then exits non-zero (CFG-04).
3. Returns the validated `Settings` on success — callers use this returned
   instance instead of a second, potentially-divergent `load_settings()`
   call.

This module performs I/O (stderr, DB connectivity) and raises `SystemExit`
by design — that is its entire purpose (CFG-06: all validation runs before
any service init; a single failure prevents service init entirely). It is
NOT hooked into `load_settings()` (which is `@lru_cache`'d and used
throughout the test suite) — entrypoints call this gate explicitly instead.
"""

from __future__ import annotations

import sys

from trading_platform.core.settings import Settings, build_settings_payload
from trading_platform.db.session import check_database_connection
from trading_platform.services.config.validation import (
    ConfigValidationError,
    ExecutionMode,
    validate_config,
)

# Distinct from CONCURRENT_RUN_LOCK_EXIT_CODE (services/concurrency_guard.py,
# value 3) so operators/schedulers can tell a config/DB startup refusal apart
# from a concurrency-lock denial. 78 is EX_CONFIG in BSD sysexits.h.
CONFIG_VALIDATION_EXIT_CODE = 78


def enforce_startup_config(
    *,
    mode: ExecutionMode = ExecutionMode.BACKTEST,
    require_database: bool = True,
    payload: dict | None = None,
) -> Settings:
    """Validate config (and, by default, DB reachability) or exit non-zero.

    Must run BEFORE any domain service/state construction at the calling
    entrypoint. Returns the validated `Settings` on success; never returns
    on failure (raises `SystemExit`).
    """
    resolved_payload = payload if payload is not None else build_settings_payload()

    try:
        settings = validate_config(resolved_payload, mode=mode)
    except ConfigValidationError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(CONFIG_VALIDATION_EXIT_CODE) from exc

    if require_database:
        reachable, reason = check_database_connection(settings)
        if not reachable:
            database = settings.database
            print(
                "Database unreachable at startup: "
                f"{database.host}:{database.port}/{database.name} — {reason}",
                file=sys.stderr,
            )
            raise SystemExit(CONFIG_VALIDATION_EXIT_CODE)

    return settings
