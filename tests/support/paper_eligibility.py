"""Test seam for tests whose subject is NOT paper-execution eligibility (COR-04)
or evaluation provenance (PROV-01).

``paper-session`` submission now asks ``paper_execution_eligibility`` (D-23):
only the fresh evaluation session inside its window is accepted, and (D-25) the
evaluation manifest of the risk run to be used must still match the current data
and signal settings. Tests about ownership, retry, lifecycle or the worker use
fixed historical sessions, hand-seeded risk runs and the real clock, so they
replace those reads with "eligible" / "manifest matches" answers here.
Eligibility and provenance themselves are proven with the real reads in
``tests/test_paper_session_eligibility.py`` and ``tests/test_evaluation_manifest.py``.
This stub lives in tests only; production code has no bypass flag.
"""

from __future__ import annotations

import uuid

import pytest

from trading_platform.services import calendar_facts as facts
from trading_platform.services.evaluation_manifest import (
    ManifestVerification,
    ManifestVerificationStatus,
)

_ELIGIBLE = facts.EligibilityResult(
    rejection=None,
    trading_day=facts.TradingDay(status=facts.FactStatus.UNKNOWN),
    evaluation_session=facts.EvaluationSession(status=facts.EvaluationStatus.UNKNOWN),
    window=facts.ExecutionWindow(status=facts.WindowStatus.UNKNOWN),
)


_MATCHES = ManifestVerification(ManifestVerificationStatus.MATCHES)


def allow_paper_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    module = "trading_platform.jobs.handlers.paper_session_submission"
    monkeypatch.setattr(f"{module}.paper_execution_eligibility", lambda *args, **kwargs: _ELIGIBLE)
    # Provenance (20.1-06): an eligible run exists and its manifest matches.
    monkeypatch.setattr(f"{module}.latest_eligible_risk_run_id", lambda **kwargs: uuid.UUID(int=1))
    monkeypatch.setattr(f"{module}.verify_risk_run_manifest", lambda **kwargs: _MATCHES)
