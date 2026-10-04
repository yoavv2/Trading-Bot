"""Test seam for suites whose subject is NOT the per-intent permission check (20.1-15).

Every paper order now passes the fixed-precedence permission check (execution window, evaluation
provenance, fresh price, portfolio risk) and the S1 send authorization (transaction T1, which
needs a RUNNING Job holding a lease). Tests that drive ``run_paper_session`` /
``run_paper_order_submission`` directly use fixed historical sessions, hand-seeded risk runs and
no worker, so this seam replaces, in TESTS ONLY (production has no bypass flag):

* the run-time window verdict with "open" and the manifest verification with "matches"
  (mirrors ``tests/support/paper_eligibility.py``, which does the same for submit time);
* the production price source with ``FreshPriceSource`` (a fresh trade at the intent's own
  reference price), so every sending suite keeps sending exactly as before; only the S2-R3 tests
  override it with a ``ScriptedPriceSource``;
* the executor identity: a Job-less call (``job_id=None``) gets a freshly inserted RUNNING Job
  with a lease, and a given Job that is not RUNNING with a lease is made so; a Job a real worker
  holds (the e2e suites) is left untouched, so those suites exercise the real lease check.

The permission precedence, the price step and the guard itself are proven with the real
functions in ``tests/test_operation_permission.py`` and ``tests/test_paper_session_operations.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from tests.support.price_source import FreshPriceSource

from trading_platform.core.settings import Settings
from trading_platform.db.models import Job, JobStatus
from trading_platform.db.session import session_scope
from trading_platform.services.evaluation_manifest import (
    ManifestVerification,
    ManifestVerificationStatus,
)
from trading_platform.services.execution import permission
from trading_platform.services.execution import submit_orders as submit_orders_module
from trading_platform.services.execution.operations import WindowVerdict

TEST_LEASE_OWNER = "test-worker"


def _open_window(*args: object, **kwargs: object) -> permission.WindowFacts:
    return permission.WindowFacts(verdict=WindowVerdict.OPEN, session_opens_at=None)


def _matching_manifest(**kwargs: object) -> ManifestVerification:
    return ManifestVerification(ManifestVerificationStatus.MATCHES)


def leased_job_identity(settings: Settings, job_id: uuid.UUID | None) -> tuple[uuid.UUID, str]:
    """Executor identity for tests: a RUNNING Job holding a lease far in the future."""

    far = datetime.now(UTC) + timedelta(days=1)
    with session_scope(settings) as session:
        job = session.get(Job, job_id) if job_id is not None else None
        if job is None:
            job = Job(
                id=job_id or uuid.uuid4(),
                job_type="paper-session",
                payload={},
                status=JobStatus.RUNNING,
            )
            session.add(job)
        if job.status is JobStatus.RUNNING and job.lease_owner is not None:
            # A Job a real worker holds (the e2e suites): its own lease is left untouched.
            return job.id, job.lease_owner
        job.status = JobStatus.RUNNING
        job.lease_owner = TEST_LEASE_OWNER
        job.lease_expires_at = far
        session.flush()
        return job.id, job.lease_owner


def allow_direct_paper_execution(
    monkeypatch: pytest.MonkeyPatch, *, price_source: FreshPriceSource | None = None
) -> FreshPriceSource:
    """Install the seam; returns the default ``FreshPriceSource`` so a test can inspect it."""

    source = price_source or FreshPriceSource()
    monkeypatch.setattr(permission, "evaluation_window_facts", _open_window)
    monkeypatch.setattr(permission, "verify_risk_run_manifest", _matching_manifest)
    monkeypatch.setattr(submit_orders_module, "_default_price_source", lambda settings: source)
    monkeypatch.setattr(submit_orders_module, "_executor_identity", leased_job_identity)
    return source
