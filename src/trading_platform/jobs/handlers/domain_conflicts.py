"""D-04: translates typed domain-layer conflict exceptions into the
framework-level ``JobDomainConflictError`` signal.

``DOMAIN_CONFLICT_EXCEPTIONS`` is the explicit, closed set of domain
exceptions a Job handler may translate this way -- adding a member
requires a test update (see ``tests/test_job_registry.py``). This module
lives under ``jobs/handlers/`` (not a queue-framework module), so it is
the one place in the ``jobs`` package permitted to import a concrete
domain exception (``ConcurrentRunLockedError``); ``jobs/runner.py`` and
``jobs/contracts.py`` never do (JOB-04).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from trading_platform.jobs.contracts import JobDomainConflictError
from trading_platform.services.concurrency_guard import ConcurrentRunLockedError

DOMAIN_CONFLICT_EXCEPTIONS: tuple[type[Exception], ...] = (ConcurrentRunLockedError,)


@contextmanager
def translate_domain_conflicts() -> Iterator[None]:
    """Catch any of ``DOMAIN_CONFLICT_EXCEPTIONS`` and re-raise as
    ``JobDomainConflictError``, preserving the original as ``__cause__``.

    Any other exception passes through untouched.
    """

    try:
        yield
    except DOMAIN_CONFLICT_EXCEPTIONS as exc:
        raise JobDomainConflictError(f"Domain conflict: {exc}") from exc
