"""Test seam for tests whose subject is NOT paper-execution eligibility (COR-04).

``paper-session`` submission now asks ``paper_execution_eligibility`` (D-23):
only the fresh evaluation session inside its window is accepted. Tests about
ownership, retry, lifecycle or the worker use fixed historical sessions and the
real clock, so they replace that single read with an "eligible" answer here.
Eligibility itself is proven with the real read in
``tests/test_paper_session_eligibility.py``. This stub lives in tests only;
production code has no bypass flag.
"""

from __future__ import annotations

import pytest

from trading_platform.services import calendar_facts as facts

_ELIGIBLE = facts.EligibilityResult(
    rejection=None,
    trading_day=facts.TradingDay(status=facts.FactStatus.UNKNOWN),
    evaluation_session=facts.EvaluationSession(status=facts.EvaluationStatus.UNKNOWN),
    window=facts.ExecutionWindow(status=facts.WindowStatus.UNKNOWN),
)


def allow_paper_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "trading_platform.jobs.handlers.paper_session_submission.paper_execution_eligibility",
        lambda *args, **kwargs: _ELIGIBLE,
    )
