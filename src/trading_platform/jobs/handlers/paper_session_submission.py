"""PaperSessionSubmissionSpec: the public input contract for the
``paper-session`` Job type (D-01, D-03, D-15, D-23, D-28).

Validation is strict and typed: unknown payload keys, a missing required
field, a wrong-typed/blank ``strategy_id``, a malformed ``as_of_session``,
an unregistered ``strategy_id``, an ``as_of_session`` in the future (judged
against the exchange-local date, never the host's local date), an
``as_of_session`` that is not an actual exchange trading session, a
``risk_run_id`` that is not a well-formed UUID, and a ``risk_run_id`` that
does not reference a SUCCEEDED risk-evaluation run for the same strategy
and session each raise a typed ``InvalidJobPayloadError`` with a stable,
closed rejection reason. Nothing is ever defaulted inside
``validate_payload`` -- the Job payload records exactly what will run.
``submission_defaults`` is a separate, read-only method computed at
catalog-read time only; it performs no validation and never raises for a
bad payload (there is none to validate). ``risk_run_id`` is deliberately
NOT defaulted here (D-23) -- the console form always sends an explicit
``null`` when the operator leaves the field blank.

The ``paper-session`` Job type is cancellable only while queued (D-01):
it performs broker submission inside one opaque service call, so a
RUNNING Job of this type is never cancellable (``JobOrchestrationService``
rejects the request per D-02).

Recovery gate (D-15, supersedes Phase 20 D-19; REC-01): after the eligibility check,
EVERY submission -- fresh or OPS-07 retry, since ``submit()`` and ``retry()`` both call
``validate_payload`` -- is refused with a typed ``JobSubmissionConflictError`` (HTTP 409)
while an uncertain outcome of the strategy is unresolved: ``outcome_unresolved`` (some
registered intent is not established), ``reconciliation_required`` (all established but no
clean standalone reconciliation completed after the latest broker-touching effect) or
``reconciliation_not_clean`` (the newest qualifying one is blocking, failed or has
unresolved reasons). The body carries ``required_job_type='reconciliation'``. The spec
declares ``recovery_gated = True`` so the Job detail ``retry_blocked`` field and ``retry()``
recompute the block by re-running this validation. Validation precedes the idempotent
replay lookup, so a replayed key re-evaluates the gate.

Eligibility (D-23, COR-04): after the ownership check a paper session is accepted
only for the fresh evaluation session inside its execution window. The
closed 409 codes ``calendar_data_unavailable`` > ``historical_execution_rejected``
> ``evaluation_data_not_ready`` > ``outside_execution_window`` come from the
services-layer ``paper_execution_eligibility`` read, judged at the injected clock.
Research, backtests and evaluation of past sessions are never gated by it.

Start-mode operation gates (20.1-15, REC-02/D-16/D-17): after the recovery gate and before the
provenance check, a new or retried request is refused, in this fixed order and from the EFFECTIVE
state (an operation past its window counts as ended; the gate is read-only and writes nothing):
``operation_open`` (the strategy has an open operation; details: operation_id, next_action
'continue'), ``working_order_commitments_unaccounted`` (a working or unsynced order of the
strategy), ``risk_run_already_operated`` (the pinned risk run's operation is terminated; a
COMPLETED one is accepted and becomes the ``noop_existing_orders`` run) and
``evaluation_basis_unverified`` (S3-R4: the pinned risk run's portfolio basis is not verified
against the strategy's executions). ``risk_run_id: null`` is resolved read-only through
``latest_eligible_risk_run_id``, the same function the manifest check and the run-time pin use.

Continue mode (20.1-16, D-19): the optional payload ``mode`` is ``start`` (absent means start; the
normalized start payload never contains it) or ``continue`` with exactly ``{mode, operation_id}``.
A Continue is its own paper-session mode with its own Idempotency-Key, not an OPS-07 retry (a retry
of a continue-mode Job re-runs this gate). It is refused, read-only and in this order: ownership,
the recovery gate, ``operation_not_paused``, ``working_order_commitments_unaccounted`` and
``awaiting_reconciliation`` (``services/execution/continuation.py``, the same predicate the run
re-verifies inside the advisory lock). Eligibility, the start gates and the manifest check do not
apply: an elapsed window is accepted so the run can terminate the operation, and provenance is
re-verified per intent at run time.

Provenance (D-25, PROV-01): as the LAST validation step the risk run that will be
used (the pinned ``risk_run_id``, else the latest eligible one) has its stored
evaluation input manifest verified against the current source data and signal
settings. A mismatch is a typed 409 ``evaluation_data_changed`` (data) or
``strategy_settings_changed`` (signal settings); no eligible run, or a run with
no manifest (evaluated before 20.1-06), is ``evaluation_data_not_ready``. Cash,
positions, open orders and risk limits are NOT provenance (D-26): they are
re-checked fresh at execution. The resolved run id is never written into the
normalized payload.

Ownership (D-03, PAPER-01): after every payload check, a strategy that is not
the active paper strategy is refused with a typed ``JobSubmissionConflictError``
(HTTP 409 ``strategy_not_active_paper_strategy``; ``no_active_paper_strategy``
when no strategy owns the account). The check is repeated inside the Job-insert
transaction under the ownership singleton lock (``check_admission``) and again
at run time before every broker action.

Field-level parsing and the shared semantic checks are delegated to
``jobs/handlers/payload_fields.py`` (P19 D-08/D-09 precedent, generalized
in Phase 20).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, field_validator
from sqlalchemy import select

from trading_platform.core.settings import Settings
from trading_platform.db.models import (
    OPEN_OPERATION_STATES,
    ExecutionOperation,
    OperationState,
    Strategy,
    StrategyRun,
)
from trading_platform.db.session import session_scope
from trading_platform.jobs.handlers.payload_fields import (
    evaluation_session_default,
    map_validation_error,
    parse_iso_date,
    require_active_paper_strategy,
    require_registered_strategy,
    require_trading_session_not_future,
)
from trading_platform.jobs.registry import (
    InvalidJobPayloadError,
    JobCancellationMode,
    JobSubmissionConflictError,
)
from trading_platform.services.active_paper_strategy import lock_active_paper_strategy_shared
from trading_platform.services.calendar_facts import (
    EligibilityRejection,
    paper_execution_eligibility,
)
from trading_platform.services.evaluation_manifest import ManifestVerificationStatus
from trading_platform.services.execution.continuation import continue_precheck
from trading_platform.services.execution.intent_identity import (
    load_basis_verification_rows,
    verify_evaluation_basis,
)
from trading_platform.services.execution.operations import effective_state
from trading_platform.services.execution.permission import strategy_working_orders
from trading_platform.services.recovery import REQUIRED_JOB_TYPE, strategy_recovery_status
from trading_platform.services.risk import (
    is_eligible_risk_run,
    latest_eligible_risk_run_id,
    verify_risk_run_manifest,
)

PAPER_SESSION_JOB_TYPE = "paper-session"

#: The two values of the optional payload ``mode`` (absent means ``start``).
MODE_START = "start"
MODE_CONTINUE = "continue"
_START_FIELDS = frozenset({"strategy_id", "as_of_session", "risk_run_id"})


class PaperSessionPayloadRejection(StrEnum):
    """Closed, stable set of machine-readable ``paper-session`` payload
    rejection reasons. One parametrized test case exists per value. The six
    shared values equal the corresponding shared ``PayloadFieldRejection``
    value; ``invalid_risk_run_id``/``risk_run_not_eligible`` are specific to
    this type's optional pinned risk run (D-23)."""

    UNKNOWN_PAYLOAD_KEYS = "unknown_payload_keys"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    INVALID_FIELD_TYPE = "invalid_field_type"
    INVALID_DATE = "invalid_date"
    UNKNOWN_STRATEGY_ID = "unknown_strategy_id"
    AS_OF_SESSION_IN_FUTURE = "as_of_session_in_future"
    AS_OF_SESSION_NOT_TRADING_SESSION = "as_of_session_not_trading_session"
    AS_OF_SESSION_OUT_OF_CALENDAR_RANGE = "as_of_session_out_of_calendar_range"
    INVALID_RISK_RUN_ID = "invalid_risk_run_id"
    RISK_RUN_NOT_ELIGIBLE = "risk_run_not_eligible"
    # 20.1-16 (D-19): the Continue mode payload (``{mode: 'continue', operation_id}``).
    INVALID_MODE = "invalid_mode"
    OPERATION_ID_REQUIRED = "operation_id_required"
    INVALID_OPERATION_ID = "invalid_operation_id"
    OPERATION_ID_FORBIDDEN_IN_START_MODE = "operation_id_forbidden_in_start_mode"
    CONTINUE_FORBIDS_START_FIELDS = "continue_forbids_start_fields"
    OPERATION_NOT_FOUND = "operation_not_found"


class PaperSessionSubmitConflict(StrEnum):
    """Closed set of state-conflict codes (HTTP 409) ``paper-session`` submission
    can raise via ``JobSubmissionConflictError`` (D-03, D-15, D-23, D-25): the two
    ownership refusals, the four execution-eligibility refusals, the two evaluation
    provenance refusals, the three recovery gate codes and the four start-mode operation gates
    (20.1-15); later plans extend it. One parametrized test case exists per value."""

    NO_ACTIVE_PAPER_STRATEGY = "no_active_paper_strategy"
    STRATEGY_NOT_ACTIVE_PAPER_STRATEGY = "strategy_not_active_paper_strategy"
    HISTORICAL_EXECUTION_REJECTED = "historical_execution_rejected"
    OUTSIDE_EXECUTION_WINDOW = "outside_execution_window"
    EVALUATION_DATA_NOT_READY = "evaluation_data_not_ready"
    CALENDAR_DATA_UNAVAILABLE = "calendar_data_unavailable"
    EVALUATION_DATA_CHANGED = "evaluation_data_changed"
    STRATEGY_SETTINGS_CHANGED = "strategy_settings_changed"
    # D-15 (20.1-10): the three uncertain-outcome recovery gate codes.
    OUTCOME_UNRESOLVED = "outcome_unresolved"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    RECONCILIATION_NOT_CLEAN = "reconciliation_not_clean"
    # REC-02 (20.1-15): the start-mode operation gates, in this precedence order, then S3-R4.
    OPERATION_OPEN = "operation_open"
    WORKING_ORDER_COMMITMENTS_UNACCOUNTED = "working_order_commitments_unaccounted"
    RISK_RUN_ALREADY_OPERATED = "risk_run_already_operated"
    EVALUATION_BASIS_UNVERIFIED = "evaluation_basis_unverified"
    # D-19 (20.1-16): the Continue-mode gates (after the recovery gate, in this order).
    OPERATION_NOT_PAUSED = "operation_not_paused"
    AWAITING_RECONCILIATION = "awaiting_reconciliation"


def _default_clock() -> datetime:
    return datetime.now(UTC)


class _PaperSessionPayload(BaseModel):
    """Shape validation only -- semantic checks (registry lookup, future-date
    rejection, trading-session membership, risk-run eligibility) happen
    after this model validates, inside
    ``PaperSessionSubmissionSpec.validate_payload``.

    ``risk_run_id`` has NO default (D-23): the key must always be present in
    the payload, even when its value is ``None`` -- a payload that omits the
    key entirely is rejected as ``missing_required_field``, exactly like
    ``strategy_id``/``as_of_session``.
    """

    model_config = ConfigDict(extra="forbid")

    strategy_id: StrictStr = Field(min_length=1, max_length=64)
    as_of_session: date
    risk_run_id: StrictStr | None

    @field_validator("strategy_id", mode="before")
    @classmethod
    def _strip_strategy_id(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("as_of_session", mode="before")
    @classmethod
    def _parse_as_of_session(cls, value: Any) -> date:
        return parse_iso_date(value)

    @field_validator("risk_run_id", mode="before")
    @classmethod
    def _strip_risk_run_id(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value


class PaperSessionSubmissionSpec:
    """Transport-neutral validation and normalization for the
    ``paper-session`` public Job type (``JobSubmissionSpec`` protocol)."""

    job_type = PAPER_SESSION_JOB_TYPE
    description = (
        "Run the daily paper-trading session (reconcile, correct, submit orders) for one strategy and session. "
        "Cancellable only while queued; once running, the session runs to completion."
    )
    cancellation_mode = JobCancellationMode.QUEUED_ONLY
    # D-15: submission and retry are gated by the uncertain-outcome recovery predicate
    # inside ``validate_payload``; read by ``recovery_gated_for(spec)``.
    recovery_gated = True

    def __init__(self, settings: Settings, *, clock: Callable[[], datetime] | None = None) -> None:
        self._settings = settings
        self._clock = clock or _default_clock

    def validate_payload(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        raw = dict(payload)
        if "mode" in raw:
            mode = raw.pop("mode")
            if mode == MODE_CONTINUE:
                return self._validate_continue(raw)
            if mode != MODE_START:
                raise InvalidJobPayloadError(
                    job_type=PAPER_SESSION_JOB_TYPE,
                    reason=PaperSessionPayloadRejection.INVALID_MODE.value,
                )
        if "operation_id" in raw:
            raise InvalidJobPayloadError(
                job_type=PAPER_SESSION_JOB_TYPE,
                reason=PaperSessionPayloadRejection.OPERATION_ID_FORBIDDEN_IN_START_MODE.value,
            )
        try:
            parsed = _PaperSessionPayload.model_validate(raw)
        except ValidationError as exc:
            reason = PaperSessionPayloadRejection(map_validation_error(exc).value)
            raise InvalidJobPayloadError(
                job_type=PAPER_SESSION_JOB_TYPE, reason=reason.value
            ) from exc

        strategy_id = parsed.strategy_id
        as_of_session = parsed.as_of_session
        risk_run_id = parsed.risk_run_id

        strategy = require_registered_strategy(
            self._settings, strategy_id, job_type=PAPER_SESSION_JOB_TYPE
        )
        require_trading_session_not_future(
            self._settings, self._clock, as_of_session, job_type=PAPER_SESSION_JOB_TYPE
        )

        canonical_risk_run_id: str | None = None
        pinned_risk_run_id: uuid.UUID | None = None
        if risk_run_id is not None:
            try:
                parsed_risk_run_id = uuid.UUID(risk_run_id)
            except ValueError as exc:
                raise InvalidJobPayloadError(
                    job_type=PAPER_SESSION_JOB_TYPE,
                    reason=PaperSessionPayloadRejection.INVALID_RISK_RUN_ID.value,
                ) from exc
            if not is_eligible_risk_run(
                risk_run_id=parsed_risk_run_id,
                strategy_id=strategy_id,
                as_of_session=as_of_session,
                settings=self._settings,
            ):
                raise InvalidJobPayloadError(
                    job_type=PAPER_SESSION_JOB_TYPE,
                    reason=PaperSessionPayloadRejection.RISK_RUN_NOT_ELIGIBLE.value,
                )
            canonical_risk_run_id = str(parsed_risk_run_id)
            pinned_risk_run_id = parsed_risk_run_id

        # D-03: state check LAST, after every shape/semantic payload check.
        require_active_paper_strategy(
            self._settings,
            strategy_id,
            job_type=PAPER_SESSION_JOB_TYPE,
            conflict_enum=PaperSessionSubmitConflict,
        )

        # D-23: execution eligibility at the injected clock (read-only).
        with session_scope(self._settings) as db_session:
            eligibility = paper_execution_eligibility(
                db_session,
                now=self._clock(),
                settings=self._settings,
                strategy=strategy,
                as_of_session=as_of_session,
            )
        if eligibility.rejection is not None:
            detail = {"strategy_id": strategy_id, "as_of_session": as_of_session.isoformat()}
            if eligibility.rejection is EligibilityRejection.EVALUATION_DATA_NOT_READY:
                evaluation = eligibility.evaluation_session
                detail["reason"] = evaluation.reason.value if evaluation.reason else ""
                detail["symbols"] = ",".join(evaluation.symbols)
            raise JobSubmissionConflictError(
                job_type=PAPER_SESSION_JOB_TYPE,
                code=PaperSessionSubmitConflict(eligibility.rejection.value).value,
                detail=detail,
            )

        # D-15: an unresolved uncertain outcome of this strategy refuses EVERY submission
        # (fresh or retry). Placed before the (expensive) manifest verification so the
        # safety refusal wins over provenance.
        self._require_recovery_resolved(strategy_id=strategy_id, as_of_session=as_of_session)

        # REC-02 (20.1-15): the start-mode operation gates (read-only, effective state).
        resolved_risk_run_id = pinned_risk_run_id or latest_eligible_risk_run_id(
            strategy_id=strategy_id, as_of_session=as_of_session, settings=self._settings
        )
        self._require_start_gates(
            strategy_id=strategy_id,
            as_of_session=as_of_session,
            risk_run_id=resolved_risk_run_id,
        )

        self._require_matching_manifest(
            strategy_id=strategy_id,
            as_of_session=as_of_session,
            pinned_risk_run_id=pinned_risk_run_id,
        )

        return {
            "strategy_id": strategy_id,
            "as_of_session": as_of_session.isoformat(),
            "risk_run_id": canonical_risk_run_id,
        }

    def _reject(self, reason: PaperSessionPayloadRejection) -> InvalidJobPayloadError:
        return InvalidJobPayloadError(job_type=PAPER_SESSION_JOB_TYPE, reason=reason.value)

    def _validate_continue(self, raw: dict[str, Any]) -> Mapping[str, Any]:
        """Continue mode (D-19): exactly ``{mode: 'continue', operation_id}``; the strategy,
        session and risk run are the operation's. Gates, in order: ownership -> the recovery
        gate -> ``operation_not_paused`` -> ``working_order_commitments_unaccounted`` ->
        ``awaiting_reconciliation``. Eligibility, the start gates and the manifest check do NOT
        apply: the window verdict comes from the effective state (an elapsed window is accepted
        so the run can terminate the operation, 05 E11) and provenance is re-verified per
        intent at run time (a change becomes ``requires_reevaluation``, never a 409 here).
        Read-only: nothing is written."""

        unknown = set(raw) - {"operation_id"} - _START_FIELDS
        if unknown:
            raise self._reject(PaperSessionPayloadRejection.UNKNOWN_PAYLOAD_KEYS)
        if set(raw) & _START_FIELDS:
            raise self._reject(PaperSessionPayloadRejection.CONTINUE_FORBIDS_START_FIELDS)
        value = raw.get("operation_id")
        if value is None or (isinstance(value, str) and not value.strip()):
            raise self._reject(PaperSessionPayloadRejection.OPERATION_ID_REQUIRED)
        if not isinstance(value, str):
            raise self._reject(PaperSessionPayloadRejection.INVALID_OPERATION_ID)
        try:
            operation_id = uuid.UUID(value.strip())
        except ValueError as exc:
            raise self._reject(PaperSessionPayloadRejection.INVALID_OPERATION_ID) from exc

        found: tuple[str, date] | None = None
        with session_scope(self._settings) as session:
            operation = session.get(ExecutionOperation, operation_id)
            if operation is not None:
                found = (
                    session.execute(
                        select(Strategy.strategy_id).where(Strategy.id == operation.strategy_id)
                    ).scalar_one(),
                    operation.as_of_session,
                )
        if found is None:
            raise self._reject(PaperSessionPayloadRejection.OPERATION_NOT_FOUND)
        strategy_id, as_of_session = found

        require_active_paper_strategy(
            self._settings,
            strategy_id,
            job_type=PAPER_SESSION_JOB_TYPE,
            conflict_enum=PaperSessionSubmitConflict,
        )
        self._require_recovery_resolved(strategy_id=strategy_id, as_of_session=as_of_session)
        with session_scope(self._settings) as session:
            operation = session.get(ExecutionOperation, operation_id)
            assert operation is not None
            refusal = continue_precheck(
                session, operation, strategy_id, now=self._clock(), settings=self._settings
            )
        if refusal is not None:
            raise JobSubmissionConflictError(
                job_type=PAPER_SESSION_JOB_TYPE,
                code=PaperSessionSubmitConflict(refusal.code).value,
                detail={
                    "strategy_id": strategy_id,
                    "as_of_session": as_of_session.isoformat(),
                    **refusal.detail,
                },
            )
        return {"mode": MODE_CONTINUE, "operation_id": str(operation_id)}

    def _require_recovery_resolved(self, *, strategy_id: str, as_of_session: date) -> None:
        """D-15 gate: the domain predicate (read-only, two statements) decides."""

        with session_scope(self._settings) as db_session:
            status = strategy_recovery_status(db_session, strategy_id)
        if status.gate_code is not None:
            raise JobSubmissionConflictError(
                job_type=PAPER_SESSION_JOB_TYPE,
                code=PaperSessionSubmitConflict(status.gate_code.value).value,
                detail={
                    "strategy_id": strategy_id,
                    "as_of_session": as_of_session.isoformat(),
                    "required_job_type": REQUIRED_JOB_TYPE,
                },
            )

    def _require_start_gates(
        self, *, strategy_id: str, as_of_session: date, risk_run_id: uuid.UUID | None
    ) -> None:
        """operation_open -> working_order_commitments_unaccounted -> risk_run_already_operated
        -> evaluation_basis_unverified (fixed precedence; read-only; EFFECTIVE state)."""

        now = self._clock()
        base: dict[str, str] = {
            "strategy_id": strategy_id,
            "as_of_session": as_of_session.isoformat(),
        }
        with session_scope(self._settings) as session:
            strategy_pk = session.execute(
                select(Strategy.id).where(Strategy.strategy_id == strategy_id)
            ).scalar_one_or_none()
            if strategy_pk is None:
                return  # no execution history of this strategy exists yet
            open_operation = (
                session.execute(
                    select(ExecutionOperation).where(
                        ExecutionOperation.strategy_id == strategy_pk,
                        ExecutionOperation.state.in_([s.value for s in OPEN_OPERATION_STATES]),
                    )
                )
                .scalars()
                .first()
            )
            if open_operation is not None:
                effective = effective_state(
                    session, open_operation, now=now, settings=self._settings
                )
                if effective.state in OPEN_OPERATION_STATES:
                    raise JobSubmissionConflictError(
                        job_type=PAPER_SESSION_JOB_TYPE,
                        code=PaperSessionSubmitConflict.OPERATION_OPEN.value,
                        detail={
                            **base,
                            "operation_id": str(open_operation.id),
                            "operation_state": effective.state.value,
                            "operation_reason": effective.reason or "",
                            "next_action": "continue",
                        },
                    )
            working = strategy_working_orders(session, strategy_id)
            if working:
                raise JobSubmissionConflictError(
                    job_type=PAPER_SESSION_JOB_TYPE,
                    code=PaperSessionSubmitConflict.WORKING_ORDER_COMMITMENTS_UNACCOUNTED.value,
                    detail={
                        **base,
                        "working_orders": ",".join(order.client_order_id for order in working[:20]),
                    },
                )
            if risk_run_id is None:
                return
            pinned = session.execute(
                select(ExecutionOperation).where(
                    ExecutionOperation.strategy_id == strategy_pk,
                    ExecutionOperation.risk_run_id == risk_run_id,
                )
            ).scalar_one_or_none()
            if pinned is not None:
                effective = effective_state(session, pinned, now=now, settings=self._settings)
                if effective.state is OperationState.TERMINATED:
                    raise JobSubmissionConflictError(
                        job_type=PAPER_SESSION_JOB_TYPE,
                        code=PaperSessionSubmitConflict.RISK_RUN_ALREADY_OPERATED.value,
                        detail={
                            **base,
                            "risk_run_id": str(risk_run_id),
                            "operation_id": str(pinned.id),
                            "operation_reason": effective.reason or "",
                            "next_action": "new_evaluation_required",
                        },
                    )
            risk_run = session.get(StrategyRun, risk_run_id)
            if risk_run is None:
                return  # provenance reports a missing run
            verification = verify_evaluation_basis(
                load_basis_verification_rows(
                    session, strategy_public_id=strategy_id, risk_run=risk_run
                )
            )
            if verification.failure is not None:
                raise JobSubmissionConflictError(
                    job_type=PAPER_SESSION_JOB_TYPE,
                    code=PaperSessionSubmitConflict.EVALUATION_BASIS_UNVERIFIED.value,
                    detail={
                        **base,
                        "risk_run_id": str(risk_run_id),
                        "reason": verification.failure.value,
                    },
                )

    def _require_matching_manifest(
        self,
        *,
        strategy_id: str,
        as_of_session: date,
        pinned_risk_run_id: uuid.UUID | None,
    ) -> None:
        """D-25: the evaluation that will be executed must still reflect the
        currently valid source data and signal settings (read-only)."""

        detail: dict[str, str] = {
            "strategy_id": strategy_id,
            "as_of_session": as_of_session.isoformat(),
        }
        run_id = pinned_risk_run_id or latest_eligible_risk_run_id(
            strategy_id=strategy_id, as_of_session=as_of_session, settings=self._settings
        )
        if run_id is None:
            detail["reason"] = "no_eligible_risk_run"
            raise JobSubmissionConflictError(
                job_type=PAPER_SESSION_JOB_TYPE,
                code=PaperSessionSubmitConflict.EVALUATION_DATA_NOT_READY.value,
                detail=detail,
            )
        detail["risk_run_id"] = str(run_id)
        verification = verify_risk_run_manifest(
            risk_run_id=run_id, strategy_id=strategy_id, settings=self._settings
        )
        status = verification.status
        if status is ManifestVerificationStatus.MATCHES:
            return
        if status is ManifestVerificationStatus.MANIFEST_MISSING:
            detail["reason"] = "manifest_missing"
            code = PaperSessionSubmitConflict.EVALUATION_DATA_NOT_READY
        elif status is ManifestVerificationStatus.STRATEGY_SETTINGS_CHANGED:
            code = PaperSessionSubmitConflict.STRATEGY_SETTINGS_CHANGED
        else:
            code = PaperSessionSubmitConflict.EVALUATION_DATA_CHANGED
            if verification.mismatched_request is not None:
                detail["request_kind"] = verification.mismatched_request.kind.value
        raise JobSubmissionConflictError(
            job_type=PAPER_SESSION_JOB_TYPE, code=code.value, detail=detail
        )

    def lock_admission(self, *, session: Any) -> None:
        """SER lock step alone: the ownership singleton FOR SHARE (fails closed)."""

        lock_active_paper_strategy_shared(session)

    def check_admission(self, payload: Mapping[str, Any], *, session: Any) -> None:
        """SER admission (inside the Job-insert transaction, before the insert):
        lock the ownership singleton FOR SHARE, then re-run the same ownership
        check against the transaction's own view."""

        self.lock_admission(session=session)
        strategy_id: str = payload.get("strategy_id")  # type: ignore[assignment]
        if payload.get("mode") == MODE_CONTINUE:
            # Continue payloads carry no strategy: it is the operation's (read in this
            # transaction, so the ownership check sees the same view).
            strategy_id = session.execute(
                select(Strategy.strategy_id)
                .join(ExecutionOperation, ExecutionOperation.strategy_id == Strategy.id)
                .where(ExecutionOperation.id == uuid.UUID(str(payload["operation_id"])))
            ).scalar_one()
        require_active_paper_strategy(
            self._settings,
            strategy_id,
            job_type=PAPER_SESSION_JOB_TYPE,
            conflict_enum=PaperSessionSubmitConflict,
            session=session,
        )

    def submission_defaults(self) -> dict[str, str] | None:
        """Console pre-fill, computed at read time (D-24): the EVALUATION candidate
        session (latest calendar-completed persisted session), never "latest session
        with bars". Returns ``None`` when the calendar does not cover the clock's date. Deliberately never
        includes ``risk_run_id`` (D-23) -- the form sends an explicit
        ``null`` for it."""

        latest = evaluation_session_default(self._settings, self._clock)
        if latest is None:
            return None
        return {"as_of_session": latest.isoformat()}
