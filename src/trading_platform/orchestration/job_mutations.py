"""Transport-independent idempotent Job submission, cancellation, and retry.

Operator retry (D-16..D-20) is exposed exclusively through
``JobOrchestrationService.retry()``: no other function in this codebase
submits a Job with ``retry_of_job_id`` set, so no automatic retry path
exists anywhere else (D-17).
"""

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
    JobCancellationMode,
    JobRegistry,
    UnknownJobTypeError,
    admission_check_for,
    admission_lock_for,
    retry_prerequisite_for,
)

SUBMIT_ENDPOINT_ID = "POST:/api/v1/jobs"
CANCEL_ENDPOINT_ID = "POST:/api/v1/jobs/{job_id}/cancel"
RETRY_ENDPOINT_ID = "POST:/api/v1/jobs/{job_id}/retry"
LOCAL_OPERATOR = "local_operator"
MAX_IDEMPOTENCY_KEY_LENGTH = 255
MAX_CANCELLATION_REASON_LENGTH = 500
IDEMPOTENCY_CONFLICT_CODE = "idempotency_key_conflict"
INVALID_JOB_PAYLOAD_CODE = "invalid_job_payload"
RETRY_BLOCKED_CODE = "reconciliation_required"
JOB_MUTATION_ENDPOINT_KEY_CONSTRAINT = "uq_job_mutations_endpoint_key"
JOB_RETRY_LINEAGE_CONSTRAINT = "uq_jobs_retry_of_job_id"


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


class JobNotCancellableRunningError(ValueError):
    """Raised when a RUNNING Job's type is cancellable only while queued (D-02)."""

    def __init__(self, *, job_id: UUID) -> None:
        self.job_id = job_id
        super().__init__("Job is running and its type is cancellable only while queued.")


class JobNotRetryableError(ValueError):
    """Raised when retry targets a Job that is not FAILED or CANCELLED (D-17)."""

    def __init__(self, *, job_id: UUID, status: str) -> None:
        self.job_id = job_id
        self.status = status
        super().__init__(f"Job '{job_id}' is not retryable from status '{status}'.")


class RetryAlreadyExistsError(ValueError):
    """Raised when a Job already has a retry linked via ``retry_of_job_id`` (D-17)."""

    def __init__(self, *, job_id: UUID, existing_retry_job_id: UUID) -> None:
        self.job_id = job_id
        self.existing_retry_job_id = existing_retry_job_id
        super().__init__(f"Job '{job_id}' already has a retry: '{existing_retry_job_id}'.")


@dataclass(frozen=True)
class RetryBlock:
    """D-19: the reconcile-first block preventing retry of an uncertain FAILED Job."""

    code: str
    required_job_type: str
    strategy_id: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "required_job_type": self.required_job_type,
            "strategy_id": self.strategy_id,
        }


class RetryBlockedError(ValueError):
    """Raised when D-19's reconcile-first predicate blocks a retry."""

    def __init__(self, *, job_id: UUID, block: RetryBlock) -> None:
        self.job_id = job_id
        self.block = block
        super().__init__(
            f"Job '{job_id}' retry is blocked pending a successful '{block.required_job_type}' "
            f"Job for strategy '{block.strategy_id}'."
        )


class InvalidRetryPayloadError(ValueError):
    """Raised when the stored payload fails D-18 revalidation against the current spec."""

    def __init__(self, *, job_id: UUID, reason: str) -> None:
        self.job_id = job_id
        self.reason = reason
        super().__init__(f"Job '{job_id}' cannot be retried: {reason}")


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


def _is_named_uniqueness_error(exc: IntegrityError, *, constraint_name: str) -> bool:
    diagnostic = getattr(exc.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None) == constraint_name


def _run_admission_check(spec: Any, payload: Mapping[str, Any], *, session: Any) -> None:
    """Run the spec's optional SER admission hook inside the Job-insert transaction."""

    hook = admission_check_for(spec)
    if hook is not None:
        hook(payload, session=session)


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
            # SER: admission of a broker-touching type takes the ownership
            # singleton FOR SHARE and re-checks ownership BEFORE the Job row
            # is inserted, in this same transaction (validate_payload above ran
            # in its own session, so it cannot guarantee this).
            _run_admission_check(spec, normalized_payload, session=session)
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
                if not _is_named_uniqueness_error(exc, constraint_name=JOB_MUTATION_ENDPOINT_KEY_CONSTRAINT):
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

            if job.status is JobStatus.RUNNING:
                # D-02: a RUNNING Job whose type is cancellable only while
                # queued is rejected here, before session.begin_nested(), so a
                # rejected cancel writes zero rows (no JobMutation, no
                # cancellation_requested_at, no JobEvent). This check runs
                # against the same row-locked `job` object acquired above, so
                # the claim/cancel race (claim_next_job vs. this cancel) is
                # serialized by the row lock rather than racing independently.
                # An unregistered job_type is NOT treated as queued-only --
                # the cooperative request path below still applies to it.
                try:
                    spec = self._registry.resolve_submission_spec(job.job_type)
                except UnknownJobTypeError:
                    spec = None
                if spec is not None and spec.cancellation_mode is JobCancellationMode.QUEUED_ONLY:
                    raise JobNotCancellableRunningError(job_id=job_id)

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
                if not _is_named_uniqueness_error(exc, constraint_name=JOB_MUTATION_ENDPOINT_KEY_CONSTRAINT):
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

    def _retry_block_for(self, session: Any, job: Job) -> RetryBlock | None:
        """D-19: reconcile-first predicate, reading only the ``jobs`` table.

        Returns ``None`` unless ``job`` is FAILED with ``outcome_uncertain``
        True, its type declares a ``retry_prerequisite_job_type``, and no Job
        of that prerequisite type has SUCCEEDED for the same ``strategy_id``
        with a later ``completed_at``.
        """

        if job.status is not JobStatus.FAILED or not job.outcome_uncertain:
            return None

        try:
            spec = self._registry.resolve_submission_spec(job.job_type)
        except UnknownJobTypeError:
            return None

        prerequisite = retry_prerequisite_for(spec)
        if prerequisite is None:
            return None

        strategy_id = job.payload.get("strategy_id") if isinstance(job.payload, Mapping) else None

        satisfied = session.execute(
            select(Job.id)
            .where(
                Job.job_type == prerequisite,
                Job.status == JobStatus.SUCCEEDED,
                Job.completed_at > job.completed_at,
                Job.payload["strategy_id"].as_string() == strategy_id,
            )
            .limit(1)
        ).scalar_one_or_none()
        if satisfied is not None:
            return None

        return RetryBlock(code=RETRY_BLOCKED_CODE, required_job_type=prerequisite, strategy_id=strategy_id)

    def _lock_ownership_singleton_before_job_row(self, session: Any, job_id: UUID) -> None:
        job_type = session.execute(select(Job.job_type).where(Job.id == job_id)).scalar_one_or_none()
        if job_type is None:
            return
        try:
            spec = self._registry.resolve_submission_spec(job_type)
        except UnknownJobTypeError:
            return
        lock = admission_lock_for(spec)
        if lock is not None:
            lock(session=session)

    def retry_block(self, *, job_id: UUID) -> RetryBlock | None:
        """Read-only D-19/D-20 query: the current reconcile-first block for ``job_id``, or None."""

        with session_scope(self._settings) as session:
            job = self._require_job(session, job_id)
            return self._retry_block_for(session, job)

    def retry(self, *, job_id: UUID, idempotency_key: str | None) -> MutationResult:
        """Retry a FAILED or CANCELLED Job exactly once per endpoint/key identity (D-16..D-19).

        No other function in this codebase submits a Job with
        ``retry_of_job_id`` set -- this method is the sole retry path (D-17).
        """

        key = self._validate_idempotency_key(idempotency_key)
        fingerprint = _request_fingerprint({"job_id": str(job_id)}, job_type="retry")

        with session_scope(self._settings) as session:
            existing = self._existing_outcome(
                session,
                endpoint_id=RETRY_ENDPOINT_ID,
                key=key,
                fingerprint=fingerprint,
            )
            if existing is not None:
                return existing

            # SER lock order is "singleton first, then other rows": take the
            # ownership singleton FOR SHARE BEFORE the original Job row is
            # locked. The job type is read with a column-level select (no row
            # lock, nothing enters the identity map); a missing Job or an
            # unregistered type falls through to the existing errors below.
            self._lock_ownership_singleton_before_job_row(session, job_id)

            original = self._require_job(session, job_id, lock=True)

            # A concurrent same-key retry may have committed while this
            # request waited on the row lock above; the pre-lock replay
            # check cannot see it. Re-check under the lock so the loser
            # replays (D-16) instead of hitting retry_exists (409).
            existing = self._existing_outcome(
                session,
                endpoint_id=RETRY_ENDPOINT_ID,
                key=key,
                fingerprint=fingerprint,
            )
            if existing is not None:
                return existing

            if original.status not in (JobStatus.FAILED, JobStatus.CANCELLED):
                raise JobNotRetryableError(job_id=job_id, status=original.status.value)

            existing_retry_id = session.execute(
                select(Job.id).where(Job.retry_of_job_id == job_id)
            ).scalar_one_or_none()
            if existing_retry_id is not None:
                raise RetryAlreadyExistsError(job_id=job_id, existing_retry_job_id=existing_retry_id)

            try:
                spec = self._registry.resolve_submission_spec(original.job_type)
            except UnknownJobTypeError as exc:
                raise UnknownJobTypeForSubmissionError(job_type=original.job_type) from exc

            block = self._retry_block_for(session, original)
            if block is not None:
                raise RetryBlockedError(job_id=job_id, block=block)

            try:
                spec.validate_payload(original.payload)
            except InvalidJobPayloadError as exc:
                raise InvalidRetryPayloadError(job_id=job_id, reason=exc.reason) from exc

            # D-18: the stored payload is only ever revalidated, never
            # modified -- the new Job's payload is a verbatim copy.
            retry_payload = dict(original.payload)

            # SER: same admission lock + ownership re-check as submit.
            _run_admission_check(spec, retry_payload, session=session)

            try:
                with session.begin_nested():
                    new_job_id = submit_job(
                        job_type=original.job_type,
                        payload=retry_payload,
                        retry_of_job_id=job_id,
                        session=session,
                    )
                    session.add(
                        JobMutation(
                            endpoint_id=RETRY_ENDPOINT_ID,
                            idempotency_key=key,
                            request_fingerprint=fingerprint,
                            job_id=new_job_id,
                        )
                    )
                    session.flush()
            except IntegrityError as exc:
                if _is_named_uniqueness_error(exc, constraint_name=JOB_MUTATION_ENDPOINT_KEY_CONSTRAINT):
                    existing = self._existing_outcome(
                        session,
                        endpoint_id=RETRY_ENDPOINT_ID,
                        key=key,
                        fingerprint=fingerprint,
                    )
                    if existing is None:  # pragma: no cover - protects against a malformed constraint error
                        raise
                    return existing
                if _is_named_uniqueness_error(exc, constraint_name=JOB_RETRY_LINEAGE_CONSTRAINT):
                    winning_retry_id = session.execute(
                        select(Job.id).where(Job.retry_of_job_id == job_id)
                    ).scalar_one_or_none()
                    if winning_retry_id is None:  # pragma: no cover - protects against a malformed constraint error
                        raise
                    raise RetryAlreadyExistsError(
                        job_id=job_id, existing_retry_job_id=winning_retry_id
                    ) from exc
                raise

            return MutationResult(
                reference=self._reference(self._require_job(session, new_job_id)),
                replayed=False,
                created=True,
            )
