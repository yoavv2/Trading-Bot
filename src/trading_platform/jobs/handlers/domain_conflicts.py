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

# Each translated exception carries an explicit ``outcome_uncertain`` claim
# (OPS-08/D-19). ``ConcurrentRunLockedError`` is raised when
# ``run_paper_order_submission``'s advisory lock is denied, which precedes any
# broker order submission (LOCK-01): earlier steps of a paper session
# (reconciliation run, local sync-failure corrections, broker reads) may have
# run, but none submits to the broker, so the outcome is certain. Adding a
# member here forces that decision to be made consciously.
DOMAIN_CONFLICT_OUTCOME_UNCERTAIN: dict[type[Exception], bool] = {
    ConcurrentRunLockedError: False,
}

DOMAIN_CONFLICT_EXCEPTIONS: tuple[type[Exception], ...] = tuple(DOMAIN_CONFLICT_OUTCOME_UNCERTAIN)


@contextmanager
def translate_domain_conflicts() -> Iterator[None]:
    """Catch any of ``DOMAIN_CONFLICT_EXCEPTIONS`` and re-raise as
    ``JobDomainConflictError``, preserving the original as ``__cause__``.

    Any other exception passes through untouched.
    """

    try:
        yield
    except DOMAIN_CONFLICT_EXCEPTIONS as exc:
        outcome_uncertain = next(
            uncertain
            for exception_type, uncertain in DOMAIN_CONFLICT_OUTCOME_UNCERTAIN.items()
            if isinstance(exc, exception_type)
        )
        raise JobDomainConflictError(
            f"Domain conflict: {exc}", outcome_uncertain=outcome_uncertain
        ) from exc
