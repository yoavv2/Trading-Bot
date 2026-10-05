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
from trading_platform.services.execution.operations import (
    OperationConflictError,
    OperationExecutorActiveError,
    OperationOpenError,
    RiskRunAlreadyOperatedError,
)
from trading_platform.services.external_activity import ExternalActivityRejectedError

# Each translated exception carries an explicit ``outcome_uncertain`` claim
# (OPS-08/D-19). ``ConcurrentRunLockedError`` is raised when
# ``run_paper_order_submission``'s advisory lock is denied, which precedes any
# broker order submission (LOCK-01): earlier steps of a paper session
# (reconciliation run, local sync-failure corrections, broker reads) may have
# run, but none submits to the broker, so the outcome is certain. Adding a
# member here forces that decision to be made consciously.
#
# ``ExternalActivityRejectedError`` (EXT-01) is raised by ``record_external_orders``
# BEFORE any write: its closed refusal reasons are checked against broker READS only, so
# nothing was stored and nothing was sent; the outcome is certain.
#
# 20.1-15 (REC-02): ``OperationConflictError`` (a lost compare-and-set, a refused send
# authorization), ``OperationOpenError`` (the open-operation race lost at the database
# constraint) and ``RiskRunAlreadyOperatedError`` (the pinned risk run already has an
# operation) are raised BEFORE the request they refuse (the refused send authorization sends
# nothing; the other two are raised at operation creation, before any intent is registered),
# so the outcome is certain. 20.1-16: ``OperationExecutorActiveError`` (``operation_executor_active``:
# the Continue run could not take the session advisory lock, or the operation is running under
# another live Job) is raised before any write and any broker call; the outcome is certain. An executor that loses authority AFTER a POST raises a different
# error (``ExecutionAuthorityLostAfterSendError``) that is deliberately NOT translated.
DOMAIN_CONFLICT_OUTCOME_UNCERTAIN: dict[type[Exception], bool] = {
    ConcurrentRunLockedError: False,
    ExternalActivityRejectedError: False,
    OperationConflictError: False,
    OperationExecutorActiveError: False,
    OperationOpenError: False,
    RiskRunAlreadyOperatedError: False,
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
        # EXT-01: the closed refusal reason is the FIRST token of the failure message.
        message = (
            exc.failure_message()
            if isinstance(exc, ExternalActivityRejectedError)
            else f"Domain conflict: {exc}"
        )
        raise JobDomainConflictError(message, outcome_uncertain=outcome_uncertain) from exc
