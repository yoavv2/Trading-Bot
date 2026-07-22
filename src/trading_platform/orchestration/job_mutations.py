"""Transport-independent idempotent Job submission and cancellation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from trading_platform.core.settings import DatabaseSettings, Settings
from trading_platform.db.models import Job, JobMutation, JobStatus
from trading_platform.db.session import session_scope
from trading_platform.jobs.cancellation import JobNotCancellableError, request_cancellation
from trading_platform.jobs.dependencies import submit_job
from trading_platform.jobs.registry import (
    InvalidJobPayloadError,
    JobRegistry,
    UnknownJobTypeError,
)

SUBMIT_ENDPOINT_ID = "POST:/api/v1/jobs"
CANCEL_ENDPOINT_ID = "POST:/api/v1/jobs/{job_id}/cancel"
LOCAL_OPERATOR = "local_operator"
MAX_IDEMPOTENCY_KEY_LENGTH = 255
MAX_CANCELLATION_REASON_LENGTH = 500
IDEMPOTENCY_CONFLICT_CODE = "idempotency_key_conflict"
INVALID_JOB_PAYLOAD_CODE = "invalid_job_payload"


@dataclass(frozen=True)
class JobReference:
    """Compact, point-in-time transport-neutral representation of a Job."""

    job_id: str
    job_type: str
    status: str
    links: Mapping[str, str]

    def to_dict(self) -> dict[str, object]:
        """Return the intentionally small mutation response contract."""

        return {
            "job_id": self.job_id,
            "job_type": self.job_type,
            "status": self.status,
            "links": dict(self.links),
        }


@dataclass(frozen=True)
class MutationResult:
    """Outcome of a successful new or replayed mutation."""

    reference: JobReference
    replayed: bool
    created: bool


class MissingIdempotencyKeyError(ValueError):
    """Raised when a mutation has no idempotency key."""


class InvalidIdempotencyKeyError(ValueError):
    """Raised when an idempotency key is blank or exceeds its bound."""

    def __init__(self, *, idempotency_key: str) -> None:
        self.idempotency_key = idempotency_key
        super().__init__("Idempotency key must be nonblank and at most 255 characters.")


class UnknownJobTypeForSubmissionError(ValueError):
    """Raised when a handler is unavailable for public submission."""

    def __init__(self, *, job_type: str) -> None:
        self.job_type = job_type
        super().__init__(f"Job type '{job_type}' is not publicly submittable.")


class IdempotencyConflictError(ValueError):
    """Raised when a key is reused with a different canonical operation."""

    code = IDEMPOTENCY_CONFLICT_CODE

    def __init__(self, *, original_job_id: str) -> None:
        self.original_job_id = original_job_id
        super().__init__(
            f"Idempotency key is already bound to Job '{original_job_id}' for a different request."
        )


class JobMutationNotFoundError(LookupError):
    """Raised when a cancellation target Job does not exist."""

    def __init__(self, *, job_id: UUID) -> None:
        self.job_id = job_id
        super().__init__(f"Job '{job_id}' was not found.")


class JobTerminalConflictError(ValueError):
    """Raised when a fresh cancellation targets a completed Job."""

    def __init__(self, *, job_id: UUID, status: str) -> None:
        self.job_id = job_id
        self.status = status
        super().__init__(f"Job '{job_id}' is terminal with status '{status}'.")


class InvalidCancellationReasonError(ValueError):
    """Raised when a normalized cancellation reason exceeds its bound."""

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__("Cancellation reason must be at most 500 characters after trimming.")


def _relative_links(job_id: UUID) -> dict[str, str]:
    root = f"/api/v1/jobs/{job_id}"
    return {
        "self": root,
        "progress": f"{root}/progress",
        "logs": f"{root}/logs",
        "events": f"{root}/events",
    }


def _request_fingerprint(material: Mapping[str, Any], *, job_type: str) -> str:
    """Return a SHA-256 digest of canonical, JSON-safe operation material."""

    try:
        serialized = json.dumps(
            material,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise InvalidJobPayloadError(job_type=job_type, reason="Payload is not JSON-canonicalizable.") from exc
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _is_named_uniqueness_error(exc: IntegrityError) -> bool:
    diagnostic = getattr(exc.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None) == "uq_job_mutations_endpoint_key"


class JobOrchestrationService:
    """Own Job mutation validation, identity, transaction composition, and references."""

    def __init__(self, settings: Settings | DatabaseSettings, registry: JobRegistry) -> None:
        self._settings = settings
        self._registry = registry

    @staticmethod
    def _validate_idempotency_key(idempotency_key: str | None) -> str:
        if idempotency_key is None:
            raise MissingIdempotencyKeyError("Idempotency key is required.")
        if not idempotency_key.strip() or len(idempotency_key) > MAX_IDEMPOTENCY_KEY_LENGTH:
            raise InvalidIdempotencyKeyError(idempotency_key=idempotency_key)
        return idempotency_key

    @staticmethod
    def _reference(job: Job) -> JobReference:
        return JobReference(
            job_id=str(job.id),
            job_type=job.job_type,
            status=job.status.value,
            links=_relative_links(job.id),
        )

    @staticmethod
    def _require_job(session: Any, job_id: UUID, *, lock: bool = False) -> Job:
        job = session.get(Job, job_id, with_for_update=lock)
        if job is None:
            raise JobMutationNotFoundError(job_id=job_id)
        return job

    def _existing_outcome(self, session: Any, *, endpoint_id: str, key: str, fingerprint: str) -> MutationResult | None:
        mutation = session.execute(
            select(JobMutation).where(
                JobMutation.endpoint_id == endpoint_id,
                JobMutation.idempotency_key == key,
            )
        ).scalar_one_or_none()
        if mutation is None:
            return None
        job = self._require_job(session, mutation.job_id)
        if mutation.request_fingerprint != fingerprint:
            raise IdempotencyConflictError(original_job_id=str(job.id))
        return MutationResult(reference=self._reference(job), replayed=True, created=False)

    def submit(
        self,
        *,
        job_type: str,
        payload: Mapping[str, Any],
        idempotency_key: str | None,
    ) -> MutationResult:
        """Submit a registered, validated Job exactly once per endpoint/key identity."""

        key = self._validate_idempotency_key(idempotency_key)
        normalized_type = job_type.strip()
        try:
            self._registry.resolve(normalized_type)
            spec = self._registry.resolve_submission_spec(normalized_type)
        except UnknownJobTypeError as exc:
            raise UnknownJobTypeForSubmissionError(job_type=normalized_type) from exc

        validated_payload = spec.validate_payload(payload)
        if not isinstance(validated_payload, Mapping):
            raise InvalidJobPayloadError(
                job_type=normalized_type,
                reason="Submission specification returned a non-mapping payload.",
            )
        normalized_payload = dict(validated_payload)
        fingerprint = _request_fingerprint(
            {"job_type": normalized_type, "payload": normalized_payload}, job_type=normalized_type
        )

        with session_scope(self._settings) as session:
            existing = self._existing_outcome(
                session,
                endpoint_id=SUBMIT_ENDPOINT_ID,
                key=key,
                fingerprint=fingerprint,
            )
            if existing is not None:
                return existing
            try:
                with session.begin_nested():
                    job_id = submit_job(
                        job_type=normalized_type,
                        payload=normalized_payload,
                        session=session,
                    )
                    session.add(
                        JobMutation(
                            endpoint_id=SUBMIT_ENDPOINT_ID,
                            idempotency_key=key,
                            request_fingerprint=fingerprint,
                            job_id=job_id,
                        )
                    )
                    session.flush()
            except IntegrityError as exc:
                if not _is_named_uniqueness_error(exc):
                    raise
                existing = self._existing_outcome(
                    session,
                    endpoint_id=SUBMIT_ENDPOINT_ID,
                    key=key,
                    fingerprint=fingerprint,
                )
                if existing is None:  # pragma: no cover - protects against a malformed constraint error
                    raise
                return existing

            return MutationResult(
                reference=self._reference(self._require_job(session, job_id)),
                replayed=False,
                created=True,
            )

    def cancel(
        self,
        *,
        job_id: UUID,
        reason: str | None,
        idempotency_key: str | None,
    ) -> MutationResult:
        """Cancel a Job idempotently while retaining the first request audit facts."""

        key = self._validate_idempotency_key(idempotency_key)
        normalized_reason = reason.strip() if reason is not None else None
        normalized_reason = normalized_reason or None
        if normalized_reason is not None and len(normalized_reason) > MAX_CANCELLATION_REASON_LENGTH:
            raise InvalidCancellationReasonError(reason=normalized_reason)
        fingerprint = _request_fingerprint(
            {"job_id": str(job_id), "reason": normalized_reason}, job_type="cancellation"
        )

        with session_scope(self._settings) as session:
            existing = self._existing_outcome(
                session,
                endpoint_id=CANCEL_ENDPOINT_ID,
                key=key,
                fingerprint=fingerprint,
            )
            if existing is not None:
                return existing

            job = self._require_job(session, job_id, lock=True)
            if job.status in (JobStatus.SUCCEEDED, JobStatus.FAILED):
                raise JobTerminalConflictError(job_id=job_id, status=job.status.value)

            try:
                with session.begin_nested():
                    if job.status is not JobStatus.CANCELLED and not (
                        job.status is JobStatus.RUNNING and job.cancellation_requested_at is not None
                    ):
                        request_cancellation(
                            job_id=job_id,
                            requested_by=LOCAL_OPERATOR,
                            reason=normalized_reason,
                            session=session,
                        )
                    session.add(
                        JobMutation(
                            endpoint_id=CANCEL_ENDPOINT_ID,
                            idempotency_key=key,
                            request_fingerprint=fingerprint,
                            job_id=job_id,
                        )
                    )
                    session.flush()
            except IntegrityError as exc:
                if not _is_named_uniqueness_error(exc):
                    raise
                existing = self._existing_outcome(
                    session,
                    endpoint_id=CANCEL_ENDPOINT_ID,
                    key=key,
                    fingerprint=fingerprint,
                )
                if existing is None:  # pragma: no cover - protects against malformed constraint errors
                    raise
                return existing
            except LookupError as exc:
                raise JobMutationNotFoundError(job_id=job_id) from exc
            except JobNotCancellableError as exc:
                raise JobTerminalConflictError(job_id=job_id, status=exc.status.value) from exc

            return MutationResult(
                reference=self._reference(self._require_job(session, job_id)),
                replayed=False,
                created=True,
            )
