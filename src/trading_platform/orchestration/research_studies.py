"""Research study orchestration: the one place that hands the Job submission seam to
``StudyService`` (JOB-04: domain services never import the Job framework themselves).
The research API routes and tests build their service through ``build_study_service``.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy.orm import Session

from trading_platform.core.settings import Settings
from trading_platform.jobs.dependencies import submit_job
from trading_platform.services.research.studies import StudyService


def submit_in_session(
    session: Session, job_type: str, payload: dict[str, Any], depends_on: Sequence[uuid.UUID]
) -> uuid.UUID:
    return submit_job(job_type=job_type, payload=payload, depends_on=list(depends_on), session=session)


def build_study_service(settings: Settings) -> StudyService:
    """A ``StudyService`` able to submit the study Job graphs."""

    return StudyService(settings, job_submitter=submit_in_session)
