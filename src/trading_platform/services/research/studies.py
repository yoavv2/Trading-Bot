"""Studies, immutable revisions, readiness, the Job graphs, evaluation, freeze, final test,
exposures and exports (plan S3; proposal Parts H, I, J).

Rules enforced here:
* a revision is an immutable settings snapshot; a change is a new revision;
* readiness lists every failing item with a closed code; a revision runs only when
  ``ready``; nothing is dropped, shortened or substituted on the way to a run;
* the initial graph runs the development and validation windows only; the final test
  is a separate, freeze-gated action that runs exactly the frozen candidate and its
  benchmark, and never re-ranks;
* one initial graph and one non-rerun final-test graph per revision, enforced by the
  database (``uq_study_revision_jobs_one_graph_per_scope``) under concurrent requests;
* every final-test run or rerun is recorded as a test-window exposure, visible from
  any study for the same asset and overlapping dates; reading results or exporting
  flips it from ``run_recorded`` to ``results_inspected``;
* a technical rerun must reproduce the original ``results_digest``; a difference is
  recorded as ``reproducibility_failure``.
"""

from __future__ import annotations

import csv
import hashlib
import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from trading_platform.core.settings import Settings
from trading_platform.db.models import Job, StrategyRun
from trading_platform.db.models.research import (
    AssetCatalogEntry,
    AssetList,
    AssetListItem,
    DataFreeze,
    ResearchFreeze,
    ResearchRunLink,
    ResearchStudy,
    StrategyVersion,
    StudyEvaluation,
    StudyRevision,
    StudyRevisionJob,
    TestWindowExposure,
)
from trading_platform.db.session import session_scope
from trading_platform.jobs.dependencies import submit_job
from trading_platform.services.calendar import get_calendar, pinned_calendar_start
from trading_platform.services.read_recording import canonical_json, sha256_hex
from trading_platform.services.research.backtest import (
    ResearchRunRequest,
    load_run_rows,
    run_research_backtest,
)
from trading_platform.services.research.code_identity import code_sha
from trading_platform.services.research.freeze import (
    IntegrityBlocksFreezeError,
    create_data_freeze,
    inputs_changed_after_freeze,
    require_inputs_unchanged,
)
from trading_platform.services.research.integrity import check_research_inputs
from trading_platform.services.research.ranking import (
    BENCHMARK_NOTE,
    COMPARISON_SCHEMA_VERSION,
    MODE_LABEL,
    STATUS_ELIGIBLE,
    STUDY_LIMITATIONS,
    final_test_outcome,
    rank_candidates,
)
from trading_platform.services.tiingo import PROVIDER as TIINGO_PROVIDER

MODE_SINGLE = "single_asset_independent"
MODE_PORTFOLIO = "portfolio_combined"
WINDOW_ROLES = ("development", "validation", "final_test")
INITIAL_WINDOWS = ("development", "validation")
SCOPE_INITIAL = "initial"
SCOPE_FINAL = "final_test"

ROLE_INGEST = "ingest"
ROLE_FREEZE = "freeze"
ROLE_BACKTEST = "backtest"
ROLE_BENCHMARK = "benchmark"
ROLE_EVALUATE = "evaluate"

STATE_RUN_RECORDED = "run_recorded"
STATE_RESULTS_INSPECTED = "results_inspected"
EXPOSURE_REASON_FINAL_TEST = "final_test"
EXPOSURE_REASON_RERUN = "final_test_rerun"
EXPOSURE_REASON_REPRO_FAILURE = "reproducibility_failure"

OUTSIDE_VISIBILITY_LIMITATION = (
    "The application records only final tests run inside it. Research done in other tools, exports "
    "read elsewhere, or knowledge from outside the application cannot be detected."
)


# ---------------------------------------------------------------------------
# Errors (closed codes)
# ---------------------------------------------------------------------------


class StudyError(Exception):
    code = "study_error"
    status = 409

    def __init__(self, message: str = "", **detail: Any) -> None:
        self.detail = detail
        super().__init__(message or self.code)


class StudyNotFoundError(StudyError):
    code = "study_not_found"
    status = 404


class RevisionNotFoundError(StudyError):
    code = "revision_not_found"
    status = 404


class InvalidStudySettingsError(StudyError):
    code = "invalid_study_settings"
    status = 422


class RevisionNotReadyError(StudyError):
    code = "revision_not_ready"
    status = 409


class RunAlreadyStartedError(StudyError):
    code = "run_already_started"


class FreezeNotAllowedError(StudyError):
    code = "freeze_not_allowed"


class AlreadyFrozenError(StudyError):
    code = "already_frozen"


class FinalTestNotFrozenError(StudyError):
    code = "final_test_not_frozen"


class FinalTestAlreadyRunError(StudyError):
    code = "final_test_already_run"


class FinalTestNotRunError(StudyError):
    code = "final_test_not_run"


class InputsNotFrozenError(StudyError):
    code = "inputs_not_frozen"


# ---------------------------------------------------------------------------
# Settings snapshot
# ---------------------------------------------------------------------------


class Window(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: date
    end: date


class Windows(BaseModel):
    model_config = ConfigDict(extra="forbid")
    development: Window
    validation: Window
    final_test: Window


class Costs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slippage_bps: Decimal = Field(ge=0)
    commission_per_order: Decimal = Field(ge=0)


class StudySettings(BaseModel):
    """The immutable per-revision settings (proposal H.1). Costs, objective and
    constraint are entered by the user; when absent the revision is ``not_ready`` with
    ``costs_missing`` / ``objective_missing`` / ``constraint_missing``, never defaulted."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["single_asset_independent", "portfolio_combined"] = MODE_SINGLE
    strategy_version_ids: list[uuid.UUID] = Field(min_length=1)
    assets: list[str] = Field(default_factory=list)
    asset_list_id: uuid.UUID | None = None
    range: Window
    windows: Windows
    initial_capital: Decimal = Field(gt=0)
    quantity_policy: Literal["fractional", "whole_shares"]
    costs: Costs | None = None
    objective: Literal["return_first", "risk_first"] | None = None
    constraint_value: Decimal | None = None
    provider: str = TIINGO_PROVIDER
    adjusted: bool = True
    note: str = Field(default="", max_length=2000)

    @field_validator("assets", mode="before")
    @classmethod
    def _normalize_assets(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            raise ValueError("assets must be a list")
        out: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("assets must be nonblank strings")
            ticker = item.strip().upper().replace(".", "-")
            if ticker not in out:
                out.append(ticker)
        return out


def _dec(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


def settings_to_json(settings: StudySettings) -> dict[str, Any]:
    return json.loads(settings.model_dump_json())


def settings_from_json(payload: dict[str, Any]) -> StudySettings:
    return StudySettings.model_validate(payload)


def ranking_criteria_hash(settings: StudySettings) -> str | None:
    if settings.objective is None or settings.constraint_value is None:
        return None
    return sha256_hex({"objective": settings.objective, "constraint_value": _dec(settings.constraint_value)})


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def _iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value is not None else None


def study_to_dict(study: ResearchStudy, revisions: list[StudyRevision] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "study_id": str(study.id),
        "name": study.name,
        "kind": study.kind,
        "created_at": _iso(study.created_at),
    }
    if revisions is not None:
        payload["revisions"] = [revision_to_dict(r) for r in revisions]
    return payload


def revision_to_dict(revision: StudyRevision) -> dict[str, Any]:
    return {
        "revision_id": str(revision.id),
        "study_id": str(revision.study_id),
        "revision_no": revision.revision_no,
        "settings": revision.settings_json,
        "ranking_criteria_hash": revision.ranking_criteria_hash,
        "data_freeze_id": str(revision.data_freeze_id) if revision.data_freeze_id else None,
        "created_at": _iso(revision.created_at),
    }


def freeze_to_dict(freeze: ResearchFreeze) -> dict[str, Any]:
    return {
        "freeze_id": str(freeze.id),
        "revision_id": str(freeze.study_revision_id),
        "candidate": {"strategy_version_id": str(freeze.strategy_version_id), "asset": freeze.asset},
        "acceptance": freeze.acceptance_json,
        "ranking_criteria_hash": freeze.ranking_criteria_hash,
        "code_sha": freeze.code_sha,
        "input_digest": freeze.input_digest,
        "calendar_start": _iso(freeze.calendar_start),
        "co_leading_choice_reason": freeze.co_leading_choice_reason,
        "frozen_at": _iso(freeze.frozen_at),
    }


def exposure_to_dict(exposure: TestWindowExposure, context: dict[str, Any]) -> dict[str, Any]:
    return {
        "exposure_id": str(exposure.id),
        "asset": exposure.asset,
        "range": {"start": exposure.range_start.isoformat(), "end": exposure.range_end.isoformat()},
        "input_digest": exposure.input_digest,
        "study_id": str(exposure.study_id) if exposure.study_id else None,
        "revision_id": str(exposure.study_revision_id) if exposure.study_revision_id else None,
        "strategy_id": str(exposure.strategy_id) if exposure.strategy_id else None,
        "strategy_version_id": str(exposure.strategy_version_id) if exposure.strategy_version_id else None,
        "run_id": str(exposure.run_id) if exposure.run_id else None,
        "reason": exposure.reason,
        "is_rerun": exposure.is_rerun,
        "state": STATE_RESULTS_INSPECTED if exposure.results_inspected_at else STATE_RUN_RECORDED,
        "run_recorded_at": _iso(exposure.run_recorded_at),
        "results_inspected_at": _iso(exposure.results_inspected_at),
        "context": context,
        "link": f"/api/v1/research/revisions/{exposure.study_revision_id}/results" if exposure.study_revision_id else None,
    }


def _lock(session: Session, name: str, key: uuid.UUID) -> None:
    digest = hashlib.sha256(f"research.{name}:".encode() + key.bytes).digest()
    session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": int.from_bytes(digest[:8], "big", signed=True)})


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class StudyService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    # -- studies and revisions ---------------------------------------------

    def list_studies(self) -> list[dict[str, Any]]:
        with session_scope(self._settings) as session:
            studies = session.execute(select(ResearchStudy).order_by(ResearchStudy.created_at.desc(), ResearchStudy.id)).scalars()
            return [study_to_dict(s) for s in studies]

    def get_study(self, study_id: uuid.UUID) -> dict[str, Any]:
        with session_scope(self._settings) as session:
            study = session.get(ResearchStudy, study_id)
            if study is None:
                raise StudyNotFoundError(study_id=str(study_id))
            revisions = list(
                session.execute(select(StudyRevision).where(StudyRevision.study_id == study_id).order_by(StudyRevision.revision_no)).scalars()
            )
            return study_to_dict(study, revisions)

    def create_study(self, *, name: str, kind: str, settings: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise InvalidStudySettingsError("name", field="name", reason="must be 1..120 characters")
        if kind not in ("smoke", "substantive"):
            raise InvalidStudySettingsError("kind", field="kind", reason="must be smoke or substantive")
        parsed = self._parse_settings(settings)
        with session_scope(self._settings) as session:
            study = ResearchStudy(id=uuid.uuid4(), name=name.strip(), kind=kind)
            session.add(study)
            session.flush()
            revision = self._insert_revision(session, study.id, parsed)
            session.flush()
            session.refresh(study)
            return {"study": study_to_dict(study), "revision": revision_to_dict(revision)}

    def create_revision(self, study_id: uuid.UUID, *, settings: dict[str, Any]) -> dict[str, Any]:
        parsed = self._parse_settings(settings)
        with session_scope(self._settings) as session:
            if session.get(ResearchStudy, study_id) is None:
                raise StudyNotFoundError(study_id=str(study_id))
            _lock(session, "study", study_id)
            revision = self._insert_revision(session, study_id, parsed)
            session.flush()
            return revision_to_dict(revision)

    def _parse_settings(self, settings: dict[str, Any]) -> StudySettings:
        try:
            return StudySettings.model_validate(settings)
        except ValidationError as exc:
            first = exc.errors()[0]
            raise InvalidStudySettingsError(
                "settings", field=".".join(str(p) for p in first["loc"]), reason=first["msg"]
            ) from exc

    def _insert_revision(self, session: Session, study_id: uuid.UUID, parsed: StudySettings) -> StudyRevision:
        if parsed.asset_list_id is not None:
            if session.get(AssetList, parsed.asset_list_id) is None:
                raise InvalidStudySettingsError("asset_list", field="asset_list_id", reason="unknown asset list")
            items = session.execute(
                select(AssetListItem.ticker).where(AssetListItem.list_id == parsed.asset_list_id).order_by(AssetListItem.position)
            ).scalars()
            snapshot = list(dict.fromkeys([*parsed.assets, *[t.upper() for t in items]]))
            parsed = parsed.model_copy(update={"assets": snapshot})
        if not parsed.assets:
            raise InvalidStudySettingsError("assets", field="assets", reason="at least one asset is required")
        latest = session.execute(
            select(func.max(StudyRevision.revision_no)).where(StudyRevision.study_id == study_id)
        ).scalar_one()
        revision = StudyRevision(
            id=uuid.uuid4(),
            study_id=study_id,
            revision_no=(latest or 0) + 1,
            settings_json=settings_to_json(parsed),
            ranking_criteria_hash=ranking_criteria_hash(parsed),
            created_at=datetime.now(UTC),
        )
        session.add(revision)
        session.flush()
        return revision

    def get_revision(self, revision_id: uuid.UUID) -> dict[str, Any]:
        with session_scope(self._settings) as session:
            revision = self._load_revision(session, revision_id)
            payload = revision_to_dict(revision)
            freeze = self._load_freeze(session, revision_id)
            payload["freeze"] = freeze_to_dict(freeze) if freeze else None
            payload["evaluations"] = {
                e.scope: {"evaluation_id": str(e.id), "created_at": _iso(e.created_at), "is_rerun": e.is_rerun}
                for e in self._evaluations(session, revision_id)
            }
            return payload

    @staticmethod
    def _load_revision(session: Session, revision_id: uuid.UUID, *, lock: bool = False) -> StudyRevision:
        stmt = select(StudyRevision).where(StudyRevision.id == revision_id)
        if lock:
            stmt = stmt.with_for_update()
        revision = session.execute(stmt).scalar_one_or_none()
        if revision is None:
            raise RevisionNotFoundError(revision_id=str(revision_id))
        return revision

    @staticmethod
    def _load_freeze(session: Session, revision_id: uuid.UUID) -> ResearchFreeze | None:
        return session.execute(select(ResearchFreeze).where(ResearchFreeze.study_revision_id == revision_id)).scalar_one_or_none()

    @staticmethod
    def _evaluations(session: Session, revision_id: uuid.UUID) -> list[StudyEvaluation]:
        return list(
            session.execute(
                select(StudyEvaluation).where(StudyEvaluation.study_revision_id == revision_id).order_by(StudyEvaluation.created_at)
            ).scalars()
        )

    # -- readiness ---------------------------------------------------------------

    def _required_start(self, window_start: date, history_required: int) -> date | None:
        """The session ``history_required`` sessions before the development window, or
        ``None`` when the pinned calendar does not reach that far (warm-up not satisfiable)."""

        calendar = get_calendar(self._settings.market_data.calendar.exchange)
        try:
            first = calendar.date_to_session(window_start, direction="next")
            required = calendar.session_offset(first, -history_required)
        except Exception:  # noqa: BLE001 - the library raises its own error types for out-of-bounds offsets
            return None
        return required.date()

    def readiness(self, revision_id: uuid.UUID) -> dict[str, Any]:
        with session_scope(self._settings) as session:
            revision = self._load_revision(session, revision_id)
            return self._readiness(session, revision)

    def _readiness(self, session: Session, revision: StudyRevision) -> dict[str, Any]:
        st = settings_from_json(revision.settings_json)
        errors: list[dict[str, Any]] = []

        def err(code: str, item: str, **detail: Any) -> None:
            errors.append({"code": code, "item": item, **detail})

        if st.mode != MODE_SINGLE:
            err("mode_not_supported_yet", st.mode)
        limit = self._settings.research.max_assets_per_study
        if len(st.assets) > limit:
            err("asset_limit_exceeded", "assets", count=len(st.assets), limit=limit)
        if st.costs is None:
            err("costs_missing", "costs")
        if st.objective is None:
            err("objective_missing", "objective")
        if st.constraint_value is None:
            err("constraint_missing", "constraint_value")
        w = st.windows
        for role in WINDOW_ROLES:
            window = getattr(w, role)
            if window.start > window.end:
                err("windows_overlap_or_unordered", role, reason="start after end")
            if window.start < st.range.start or window.end > st.range.end:
                err("window_outside_range", role)
        if not (w.development.end < w.validation.start and w.validation.end < w.final_test.start):
            err("windows_overlap_or_unordered", "windows", reason="development < validation < final_test required")
        pinned = pinned_calendar_start()
        if pinned is None:
            err("calendar_start_not_pinned", "calendar")

        versions = {
            v.id: v
            for v in session.execute(select(StrategyVersion).where(StrategyVersion.id.in_(st.strategy_version_ids))).scalars()
        }
        for version_id in st.strategy_version_ids:
            if version_id not in versions:
                err("strategy_version_not_approved", str(version_id))
        catalog = {
            row.ticker: row
            for row in session.execute(
                select(AssetCatalogEntry).where(AssetCatalogEntry.provider == st.provider, AssetCatalogEntry.ticker.in_(st.assets))
            ).scalars()
        }
        required_starts: dict[str, str | None] = {}
        for asset in st.assets:
            entry = catalog.get(asset)
            if entry is None:
                err("asset_not_in_catalog", asset)
                continue
            for version_id, version in versions.items():
                pair = f"{version_id}:{asset}"
                required = self._required_start(w.development.start, version.history_required)
                required_starts[pair] = required.isoformat() if required else None
                if required is None or (pinned is not None and required < pinned):
                    err("warmup_not_satisfiable", pair, history_required=version.history_required)
                    continue
                if entry.catalog_start is None or entry.catalog_start > required:
                    err("coverage_start_too_late", pair, required_start=required.isoformat(), catalog_start=_iso(entry.catalog_start))
                if entry.catalog_end is None or entry.catalog_end < st.range.end:
                    err("coverage_end_too_early", pair, requested_end=st.range.end.isoformat(), catalog_end=_iso(entry.catalog_end))

        freeze_job = session.execute(
            select(StudyRevisionJob)
            .where(StudyRevisionJob.study_revision_id == revision.id, StudyRevisionJob.role == ROLE_FREEZE)
            .order_by(StudyRevisionJob.created_at.desc())
        ).scalars().first()
        if freeze_job is not None and freeze_job.detail.get("integrity_errors"):
            for finding in freeze_job.detail["integrity_errors"]:
                err("integrity_error", finding.get("asset", "?"), **{k: v for k, v in finding.items() if k != "asset"})
        if revision.data_freeze_id is not None:
            freeze = session.get(DataFreeze, revision.data_freeze_id)
            if freeze is not None:
                for reason in inputs_changed_after_freeze(session, freeze):
                    err("inputs_changed_after_freeze", str(freeze.id), reason=reason)

        return {
            "revision_id": str(revision.id),
            "ready": not errors,
            "status": "ready" if not errors else "not_ready",
            "errors": errors,
            "checked": {
                "strategy_versions": len(st.strategy_version_ids),
                "assets": len(st.assets),
                "pairs": len(st.strategy_version_ids) * len(st.assets),
                "required_start_by_pair": required_starts,
                "pinned_calendar_start": _iso(pinned),
            },
        }

    # -- initial run graph ------------------------------------------------------

    def run_initial(self, revision_id: uuid.UUID) -> dict[str, Any]:
        with session_scope(self._settings) as session:
            revision = self._load_revision(session, revision_id, lock=True)
            _lock(session, "revision_run", revision_id)
            readiness = self._readiness(session, revision)
            if not readiness["ready"]:
                raise RevisionNotReadyError("not ready", errors=readiness["errors"])
            st = settings_from_json(revision.settings_json)
            versions = {v.id: v for v in session.execute(select(StrategyVersion).where(StrategyVersion.id.in_(st.strategy_version_ids))).scalars()}
            required = min(
                date.fromisoformat(v) for v in readiness["checked"]["required_start_by_pair"].values() if v is not None
            )
            jobs: dict[str, Any] = {}
            ingest_id = submit_job(
                job_type="ingest-tiingo-bars",
                payload={"from_date": required.isoformat(), "to_date": st.range.end.isoformat(), "assets": list(st.assets)},
                session=session,
            )
            self._link_job(session, ingest_id, revision_id, ROLE_INGEST, SCOPE_INITIAL, {"from_date": required.isoformat(), "to_date": st.range.end.isoformat()})
            freeze_id = submit_job(job_type="research-freeze", payload={"study_revision_id": str(revision_id)}, depends_on=[ingest_id], session=session)
            self._link_job(session, freeze_id, revision_id, ROLE_FREEZE, SCOPE_INITIAL, {})
            backtest_ids: list[uuid.UUID] = []
            for asset in st.assets:
                for role in INITIAL_WINDOWS:
                    for version_id in versions:
                        bid = submit_job(
                            job_type="research-backtest",
                            payload={"study_revision_id": str(revision_id), "strategy_version_id": str(version_id), "asset": asset, "window_role": role, "rerun_of": None},
                            depends_on=[freeze_id],
                            session=session,
                        )
                        self._link_job(session, bid, revision_id, ROLE_BACKTEST, SCOPE_INITIAL, {"strategy_version_id": str(version_id), "asset": asset, "window_role": role})
                        backtest_ids.append(bid)
                    bench = submit_job(
                        job_type="research-backtest",
                        payload={"study_revision_id": str(revision_id), "strategy_version_id": None, "asset": asset, "window_role": role, "rerun_of": None},
                        depends_on=[freeze_id],
                        session=session,
                    )
                    self._link_job(session, bench, revision_id, ROLE_BENCHMARK, SCOPE_INITIAL, {"asset": asset, "window_role": role})
                    backtest_ids.append(bench)
            evaluate_id = submit_job(
                job_type="research-evaluate",
                payload={"study_revision_id": str(revision_id), "scope": SCOPE_INITIAL, "is_rerun": False},
                depends_on=backtest_ids,
                session=session,
            )
            try:
                self._link_job(session, evaluate_id, revision_id, ROLE_EVALUATE, SCOPE_INITIAL, {})
            except IntegrityError as exc:
                raise RunAlreadyStartedError(revision_id=str(revision_id)) from exc
            jobs = {
                "ingest": str(ingest_id),
                "freeze": str(freeze_id),
                "backtests": [str(b) for b in backtest_ids],
                "evaluate": str(evaluate_id),
            }
            return {"revision_id": str(revision_id), "scope": SCOPE_INITIAL, "windows": list(INITIAL_WINDOWS), "jobs": jobs, "final_test_runs_created": 0}

    @staticmethod
    def _link_job(session: Session, job_id: uuid.UUID, revision_id: uuid.UUID, role: str, scope: str, detail: dict[str, Any], *, is_rerun: bool = False) -> None:
        session.add(StudyRevisionJob(job_id=job_id, study_revision_id=revision_id, role=role, scope=scope, is_rerun=is_rerun, detail=detail, created_at=datetime.now(UTC)))
        session.flush()

    def progress(self, revision_id: uuid.UUID) -> dict[str, Any]:
        with session_scope(self._settings) as session:
            self._load_revision(session, revision_id)
            rows = session.execute(
                select(StudyRevisionJob, Job).join(Job, Job.id == StudyRevisionJob.job_id).where(StudyRevisionJob.study_revision_id == revision_id).order_by(StudyRevisionJob.created_at)
            ).all()
            items = [
                {
                    "job_id": str(link.job_id),
                    "role": link.role,
                    "scope": link.scope,
                    "is_rerun": link.is_rerun,
                    "detail": link.detail,
                    "job_type": job.job_type,
                    "status": job.status.value,
                    "progress_step": job.progress_step,
                    "failure_reason": job.failure_reason.value if job.failure_reason else None,
                    "failure_message": job.failure_message,
                    "link": f"/api/v1/jobs/{link.job_id}",
                }
                for link, job in rows
            ]
            by_status: dict[str, int] = {}
            for item in items:
                by_status[item["status"]] = by_status.get(item["status"], 0) + 1
            return {"revision_id": str(revision_id), "count": len(items), "by_status": by_status, "jobs": items}

    # -- Job bodies -------------------------------------------------------------

    def perform_freeze(self, revision_id: uuid.UUID, *, job_id: uuid.UUID | None) -> dict[str, Any]:
        with session_scope(self._settings) as session:
            revision = self._load_revision(session, revision_id, lock=True)
            st = settings_from_json(revision.settings_json)
            ingest = session.execute(
                select(StudyRevisionJob).where(StudyRevisionJob.study_revision_id == revision_id, StudyRevisionJob.role == ROLE_INGEST).order_by(StudyRevisionJob.created_at.desc())
            ).scalars().first()
            range_start = date.fromisoformat(ingest.detail["from_date"]) if ingest else st.range.start
            report = check_research_inputs(
                session, assets=list(st.assets), range_start=range_start, range_end=st.range.end, provider=st.provider, exchange=self._settings.market_data.calendar.exchange
            )
            freeze_link = session.get(StudyRevisionJob, job_id) if job_id else None
            if not report.ok:
                if freeze_link is not None:
                    freeze_link.detail = {**freeze_link.detail, "integrity_errors": [f.to_dict() for f in report.errors][:200]}
                    session.flush()
                    session.commit()
                raise IntegrityBlocksFreezeError(report)
            freeze = create_data_freeze(
                session, self._settings, assets=list(st.assets), range_start=range_start, range_end=st.range.end, provider=st.provider, integrity=report
            )
            revision.data_freeze_id = freeze.id
            if freeze_link is not None:
                freeze_link.detail = {**freeze_link.detail, "data_freeze_id": str(freeze.id), "integrity_errors": []}
            session.flush()
            return {
                "revision_id": str(revision_id),
                "data_freeze_id": str(freeze.id),
                "input_digest": freeze.input_digest,
                "inputs_path": freeze.inputs_path,
                "integrity": report.summary(),
            }

    def _request_for(self, session: Session, revision: StudyRevision, payload: dict[str, Any]) -> tuple[ResearchRunRequest, DataFreeze]:
        st = settings_from_json(revision.settings_json)
        if revision.data_freeze_id is None:
            raise InputsNotFrozenError(revision_id=str(revision.id))
        freeze = session.get(DataFreeze, revision.data_freeze_id)
        if freeze is None:
            raise InputsNotFrozenError(revision_id=str(revision.id))
        require_inputs_unchanged(session, freeze)
        if st.costs is None:
            raise RevisionNotReadyError("costs missing", errors=[{"code": "costs_missing", "item": "costs"}])
        window = getattr(st.windows, payload["window_role"])
        version_id = payload.get("strategy_version_id")
        return (
            ResearchRunRequest(
                study_revision_id=revision.id,
                strategy_version_id=uuid.UUID(version_id) if version_id else None,
                asset=payload["asset"],
                window_role=payload["window_role"],
                window_start=window.start,
                window_end=window.end,
                initial_capital=st.initial_capital,
                commission_per_order=st.costs.commission_per_order,
                slippage_bps=st.costs.slippage_bps,
                quantity_policy=st.quantity_policy,
                bar_provider=st.provider,
                bar_adjusted=st.adjusted,
                rerun_of=uuid.UUID(payload["rerun_of"]) if payload.get("rerun_of") else None,
            ),
            freeze,
        )

    def run_backtest_job(self, payload: dict[str, Any], *, job_id: uuid.UUID | None) -> dict[str, Any]:
        revision_id = uuid.UUID(payload["study_revision_id"])
        with session_scope(self._settings) as session:
            revision = self._load_revision(session, revision_id)
            request, _freeze = self._request_for(session, revision, payload)
        result = run_research_backtest(self._settings, request, job_id=job_id)
        if request.window_role == "final_test":
            with session_scope(self._settings) as session:
                exposure = session.execute(
                    select(TestWindowExposure)
                    .where(TestWindowExposure.study_revision_id == revision_id, TestWindowExposure.asset == request.asset)
                    .where(TestWindowExposure.is_rerun.is_(request.rerun_of is not None), TestWindowExposure.run_id.is_(None))
                    .order_by(TestWindowExposure.run_recorded_at.desc())
                ).scalars().first()
                if exposure is not None and not request.is_benchmark:
                    exposure.run_id = result.run_id
        return {
            "run_id": str(result.run_id),
            "produced_run_ids": [str(result.run_id)],
            "run_status": result.status,
            "asset": request.asset,
            "window_role": request.window_role,
            "benchmark": request.is_benchmark,
            "strategy_version_id": str(request.strategy_version_id) if request.strategy_version_id else None,
            "results_digest": result.results_digest,
            "input_digest": result.input_digest,
            "code_sha": result.code_sha,
            "net_total_return": result.metrics["net_total_return"],
            "max_drawdown": result.metrics["max_drawdown"],
            "closed_trades": result.metrics["closed_trades"],
        }

    # -- evaluation ---------------------------------------------------------------

    def _runs_for(self, session: Session, revision_id: uuid.UUID, *, roles: tuple[str, ...], rerun: bool) -> list[tuple[ResearchRunLink, StrategyRun]]:
        stmt = (
            select(ResearchRunLink, StrategyRun)
            .join(StrategyRun, StrategyRun.id == ResearchRunLink.run_id)
            .where(ResearchRunLink.study_revision_id == revision_id, ResearchRunLink.window_role.in_(roles))
            .where(ResearchRunLink.rerun_of.is_not(None) if rerun else ResearchRunLink.rerun_of.is_(None))
            .order_by(ResearchRunLink.created_at)
        )
        return list(session.execute(stmt).all())

    @staticmethod
    def _research_block(run: StrategyRun) -> dict[str, Any] | None:
        block = run.result_summary.get("research") if run.result_summary else None
        return block if run.status.value == "succeeded" and block else None

    def evaluate(self, revision_id: uuid.UUID, *, scope: str, job_id: uuid.UUID | None, is_rerun: bool = False) -> dict[str, Any]:
        if scope == SCOPE_INITIAL:
            return self._evaluate_initial(revision_id, job_id=job_id)
        if scope == SCOPE_FINAL:
            return self._evaluate_final(revision_id, job_id=job_id, is_rerun=is_rerun)
        raise ValueError(f"unknown scope {scope!r}")

    def _evaluate_initial(self, revision_id: uuid.UUID, *, job_id: uuid.UUID | None) -> dict[str, Any]:
        with session_scope(self._settings) as session:
            revision = self._load_revision(session, revision_id)
            study = session.get(ResearchStudy, revision.study_id)
            st = settings_from_json(revision.settings_json)
            if st.objective is None or st.constraint_value is None or st.costs is None:
                raise RevisionNotReadyError("objective/constraint missing")
            freeze = session.get(DataFreeze, revision.data_freeze_id) if revision.data_freeze_id else None
            rows = self._runs_for(session, revision_id, roles=INITIAL_WINDOWS, rerun=False)
            benchmarks: dict[tuple[str, str], dict[str, Any]] = {}
            runs_by_key: dict[tuple[str, str, str], tuple[ResearchRunLink, StrategyRun]] = {}
            for link, run in rows:
                if link.strategy_version_id is None:
                    block = self._research_block(run)
                    if block is not None:
                        benchmarks[(link.asset, link.window_role)] = {**block["metrics"], "run_id": str(run.id)}
                else:
                    runs_by_key[(str(link.strategy_version_id), link.asset, link.window_role)] = (link, run)
            versions = {str(v.id): v for v in session.execute(select(StrategyVersion).where(StrategyVersion.id.in_(st.strategy_version_ids))).scalars()}
            candidates: list[dict[str, Any]] = []
            for version_id in [str(v) for v in st.strategy_version_ids]:
                for asset in st.assets:
                    windows: dict[str, Any] = {}
                    for role in INITIAL_WINDOWS:
                        pair = runs_by_key.get((version_id, asset, role))
                        block = self._research_block(pair[1]) if pair else None
                        windows[role] = (
                            {"run_id": str(pair[1].id), "metrics": block["metrics"], "evidence": block["evidence"], "results_digest": block["results_digest"], "input_digest": block["input_digest"]}
                            if pair and block
                            else None
                        )
                    version = versions.get(version_id)
                    candidates.append(
                        {
                            "strategy_version_id": version_id,
                            "strategy_id": str(version.strategy_id) if version else None,
                            "strategy_name": version.name if version else None,
                            "version_no": version.version_no if version else None,
                            "spec_sha256": version.spec_sha256 if version else None,
                            "asset": asset,
                            "windows": windows,
                            "benchmark": {role: benchmarks.get((asset, role)) for role in INITIAL_WINDOWS},
                        }
                    )
            ranking = rank_candidates(candidates, objective=st.objective, constraint_value=float(st.constraint_value))
            comparison = {
                "schema_version": COMPARISON_SCHEMA_VERSION,
                "scope": SCOPE_INITIAL,
                "generated_at": datetime.now(UTC).isoformat(),
                "study": {"study_id": str(study.id), "name": study.name, "kind": study.kind} if study else None,
                "revision": {"revision_id": str(revision.id), "revision_no": revision.revision_no, "ranking_criteria_hash": revision.ranking_criteria_hash},
                "settings": revision.settings_json,
                "mode_label": MODE_LABEL,
                "windows": {role: {"start": getattr(st.windows, role).start.isoformat(), "end": getattr(st.windows, role).end.isoformat()} for role in WINDOW_ROLES},
                "windows_evaluated": list(INITIAL_WINDOWS),
                "data": {
                    "provider": st.provider,
                    "adjusted": st.adjusted,
                    "data_freeze_id": str(freeze.id) if freeze else None,
                    "input_digest": freeze.input_digest if freeze else None,
                    "inputs_path": freeze.inputs_path if freeze else None,
                    "integrity": freeze.integrity_summary if freeze else None,
                    "calendar_start": _iso(freeze.calendar_start) if freeze else None,
                },
                "code_sha": code_sha(),
                "candidates": candidates,
                "ranking": ranking,
                "verdict": ranking["verdict"],
                "verdict_label": ranking["verdict_label"],
                "benchmark_note": BENCHMARK_NOTE,
                "final_test": {"state": "not_frozen", "note": "No final-test run exists for this revision; it is a separate, freeze-gated action."},
                "limitations": list(STUDY_LIMITATIONS),
                "evidence_limitations": candidates[0]["windows"]["validation"]["evidence"]["limitations"] if candidates and candidates[0]["windows"]["validation"] else [],
            }
            session.add(StudyEvaluation(id=uuid.uuid4(), study_revision_id=revision_id, scope=SCOPE_INITIAL, job_id=job_id, comparison_json=comparison, is_rerun=False, created_at=datetime.now(UTC)))
            session.flush()
            return {"revision_id": str(revision_id), "scope": SCOPE_INITIAL, "verdict": ranking["verdict"], "candidates": len(candidates), "eligible": len(ranking["ranking"]), "co_leaders": len(ranking["co_leaders"])}

    def _evaluate_final(self, revision_id: uuid.UUID, *, job_id: uuid.UUID | None, is_rerun: bool) -> dict[str, Any]:
        with session_scope(self._settings) as session:
            revision = self._load_revision(session, revision_id)
            st = settings_from_json(revision.settings_json)
            freeze = self._load_freeze(session, revision_id)
            if freeze is None:
                raise FinalTestNotFrozenError(revision_id=str(revision_id))
            rows = self._runs_for(session, revision_id, roles=("final_test",), rerun=is_rerun)
            candidate_block = benchmark_block = None
            candidate_run_id = benchmark_run_id = None
            for link, run in rows:
                block = self._research_block(run)
                if link.strategy_version_id == freeze.strategy_version_id and link.asset == freeze.asset:
                    candidate_block, candidate_run_id = block, run.id
                elif link.strategy_version_id is None and link.asset == freeze.asset:
                    benchmark_block, benchmark_run_id = block, run.id
            outcome = final_test_outcome(
                objective=st.objective or "return_first",
                acceptance=freeze.acceptance_json,
                candidate_metrics=candidate_block["metrics"] if candidate_block else None,
                candidate_evidence=candidate_block["evidence"] if candidate_block else None,
                benchmark_metrics=benchmark_block["metrics"] if benchmark_block else None,
            )
            reproducibility: dict[str, Any] | None = None
            if is_rerun:
                originals = {
                    (str(link.strategy_version_id), link.asset): self._research_block(run)
                    for link, run in self._runs_for(session, revision_id, roles=("final_test",), rerun=False)
                }
                original_candidate = originals.get((str(freeze.strategy_version_id), freeze.asset))
                original_benchmark = originals.get(("None", freeze.asset))
                checks = {
                    "candidate": (original_candidate or {}).get("results_digest") == (candidate_block or {}).get("results_digest") if original_candidate and candidate_block else None,
                    "benchmark": (original_benchmark or {}).get("results_digest") == (benchmark_block or {}).get("results_digest") if original_benchmark and benchmark_block else None,
                }
                failed = any(v is False for v in checks.values()) or any(v is None for v in checks.values())
                reproducibility = {"byte_identical": not failed, "checks": checks, "status": EXPOSURE_REASON_REPRO_FAILURE if failed else "reproduced"}
                if failed:
                    for exposure in session.execute(
                        select(TestWindowExposure).where(TestWindowExposure.study_revision_id == revision_id, TestWindowExposure.is_rerun.is_(True))
                    ).scalars():
                        exposure.reason = EXPOSURE_REASON_REPRO_FAILURE
            block = {
                "state": "evaluated",
                "scope": SCOPE_FINAL,
                "is_rerun": is_rerun,
                "freeze": freeze_to_dict(freeze),
                "candidate": {"strategy_version_id": str(freeze.strategy_version_id), "asset": freeze.asset, "run_id": str(candidate_run_id) if candidate_run_id else None, "metrics": candidate_block["metrics"] if candidate_block else None, "evidence": candidate_block["evidence"] if candidate_block else None},
                "benchmark": {"run_id": str(benchmark_run_id) if benchmark_run_id else None, "metrics": benchmark_block["metrics"] if benchmark_block else None},
                **outcome,
                "reproducibility": reproducibility,
                "re_ranking": "none; the initial ranking is never changed by the final test",
                "exposures": self._exposures(session, revision_id, st),
                "outside_visibility_limitation": OUTSIDE_VISIBILITY_LIMITATION,
                "generated_at": datetime.now(UTC).isoformat(),
            }
            session.add(StudyEvaluation(id=uuid.uuid4(), study_revision_id=revision_id, scope=SCOPE_FINAL, job_id=job_id, comparison_json=block, is_rerun=is_rerun, created_at=datetime.now(UTC)))
            session.flush()
            return {"revision_id": str(revision_id), "scope": SCOPE_FINAL, "outcome": outcome["outcome"], "is_rerun": is_rerun, "reproducibility": reproducibility}

    # -- freeze and final test ---------------------------------------------------

    def freeze_candidate(
        self,
        revision_id: uuid.UUID,
        *,
        strategy_version_id: uuid.UUID,
        asset: str,
        acceptance: dict[str, Any],
        co_leading_choice_reason: str | None = None,
    ) -> dict[str, Any]:
        with session_scope(self._settings) as session:
            revision = self._load_revision(session, revision_id, lock=True)
            _lock(session, "revision_freeze", revision_id)
            if self._load_freeze(session, revision_id) is not None:
                raise AlreadyFrozenError(revision_id=str(revision_id))
            st = settings_from_json(revision.settings_json)
            initial = next((e for e in reversed(self._evaluations(session, revision_id)) if e.scope == SCOPE_INITIAL), None)
            if initial is None:
                raise FreezeNotAllowedError("no initial evaluation", reason="initial_evaluation_missing")
            ranking = initial.comparison_json["ranking"]
            candidate = next(
                (c for c in initial.comparison_json["candidates"] if c["strategy_version_id"] == str(strategy_version_id) and c["asset"] == asset.upper()),
                None,
            )
            if candidate is None:
                raise FreezeNotAllowedError("unknown candidate", reason="candidate_not_in_study")
            if candidate["status"] != STATUS_ELIGIBLE:
                raise FreezeNotAllowedError("candidate not eligible", reason="candidate_not_eligible", status=candidate["status"])
            if len(ranking["co_leaders"]) > 1 and candidate.get("co_leading") and not (co_leading_choice_reason or "").strip():
                raise FreezeNotAllowedError("co-leading tie unresolved", reason="co_leading_choice_required", co_leaders=ranking["co_leaders"])
            missing = [k for k in ("constraint_value", "objective_minimum") if acceptance.get(k) is None]
            if missing:
                raise FreezeNotAllowedError("acceptance incomplete", reason="acceptance_incomplete", missing=missing)
            try:
                acceptance_json = {"constraint_value": float(acceptance["constraint_value"]), "objective_minimum": float(acceptance["objective_minimum"])}
            except (TypeError, ValueError) as exc:
                raise FreezeNotAllowedError("acceptance not numeric", reason="acceptance_not_numeric") from exc
            data_freeze = session.get(DataFreeze, revision.data_freeze_id) if revision.data_freeze_id else None
            freeze = ResearchFreeze(
                id=uuid.uuid4(),
                study_revision_id=revision_id,
                strategy_version_id=strategy_version_id,
                asset=asset.upper(),
                acceptance_json={**acceptance_json, "objective": st.objective, "window": "final_test"},
                ranking_criteria_hash=revision.ranking_criteria_hash or "",
                code_sha=code_sha(),
                input_digest=data_freeze.input_digest if data_freeze else "",
                calendar_start=pinned_calendar_start(),
                co_leading_choice_reason=(co_leading_choice_reason or "").strip() or None,
                frozen_at=datetime.now(UTC),
            )
            session.add(freeze)
            try:
                session.flush()
            except IntegrityError as exc:
                raise AlreadyFrozenError(revision_id=str(revision_id)) from exc
            return {"freeze": freeze_to_dict(freeze), "exposures": self._exposures(session, revision_id, st), "outside_visibility_limitation": OUTSIDE_VISIBILITY_LIMITATION}

    def run_final_test(self, revision_id: uuid.UUID, *, rerun: bool = False) -> dict[str, Any]:
        with session_scope(self._settings) as session:
            revision = self._load_revision(session, revision_id, lock=True)
            _lock(session, "revision_final_test", revision_id)
            freeze = self._load_freeze(session, revision_id)
            if freeze is None:
                raise FinalTestNotFrozenError(revision_id=str(revision_id))
            st = settings_from_json(revision.settings_json)
            data_freeze = session.get(DataFreeze, revision.data_freeze_id) if revision.data_freeze_id else None
            originals = {
                (link.strategy_version_id, link.asset): link.run_id
                for link, _run in self._runs_for(session, revision_id, roles=("final_test",), rerun=False)
            }
            final_evaluation = next((e for e in self._evaluations(session, revision_id) if e.scope == SCOPE_FINAL and not e.is_rerun), None)
            if rerun and final_evaluation is None:
                raise FinalTestNotRunError(revision_id=str(revision_id))
            version = session.get(StrategyVersion, freeze.strategy_version_id)
            window = st.windows.final_test
            candidate_payload = {
                "study_revision_id": str(revision_id),
                "strategy_version_id": str(freeze.strategy_version_id),
                "asset": freeze.asset,
                "window_role": "final_test",
                "rerun_of": str(originals[(freeze.strategy_version_id, freeze.asset)]) if rerun else None,
            }
            benchmark_payload = {
                "study_revision_id": str(revision_id),
                "strategy_version_id": None,
                "asset": freeze.asset,
                "window_role": "final_test",
                "rerun_of": str(originals[(None, freeze.asset)]) if rerun else None,
            }
            candidate_job = submit_job(job_type="research-backtest", payload=candidate_payload, session=session)
            self._link_job(session, candidate_job, revision_id, ROLE_BACKTEST, SCOPE_FINAL, {"asset": freeze.asset, "window_role": "final_test"}, is_rerun=rerun)
            benchmark_job = submit_job(job_type="research-backtest", payload=benchmark_payload, session=session)
            self._link_job(session, benchmark_job, revision_id, ROLE_BENCHMARK, SCOPE_FINAL, {"asset": freeze.asset, "window_role": "final_test"}, is_rerun=rerun)
            evaluate_job = submit_job(
                job_type="research-evaluate",
                payload={"study_revision_id": str(revision_id), "scope": SCOPE_FINAL, "is_rerun": rerun},
                depends_on=[candidate_job, benchmark_job],
                session=session,
            )
            try:
                self._link_job(session, evaluate_job, revision_id, ROLE_EVALUATE, SCOPE_FINAL, {}, is_rerun=rerun)
            except IntegrityError as exc:
                raise FinalTestAlreadyRunError(revision_id=str(revision_id)) from exc
            study = session.get(ResearchStudy, revision.study_id)
            session.add(
                TestWindowExposure(
                    id=uuid.uuid4(),
                    asset=freeze.asset,
                    range_start=window.start,
                    range_end=window.end,
                    input_digest=data_freeze.input_digest if data_freeze else None,
                    study_id=study.id if study else None,
                    study_revision_id=revision_id,
                    strategy_id=version.strategy_id if version else None,
                    strategy_version_id=freeze.strategy_version_id,
                    run_id=None,
                    reason=EXPOSURE_REASON_RERUN if rerun else EXPOSURE_REASON_FINAL_TEST,
                    is_rerun=rerun,
                    run_recorded_at=datetime.now(UTC),
                )
            )
            session.flush()
            return {
                "revision_id": str(revision_id),
                "scope": SCOPE_FINAL,
                "is_rerun": rerun,
                "candidate": {"strategy_version_id": str(freeze.strategy_version_id), "asset": freeze.asset},
                "jobs": {"candidate": str(candidate_job), "benchmark": str(benchmark_job), "evaluate": str(evaluate_job)},
                "runs_created": 2,
            }

    # -- exposures, results, export -----------------------------------------------

    def _exposures(self, session: Session, revision_id: uuid.UUID, st: StudySettings) -> dict[str, Any]:
        window = st.windows.final_test
        rows = list(
            session.execute(
                select(TestWindowExposure)
                .where(TestWindowExposure.asset.in_(st.assets))
                .where(TestWindowExposure.range_start <= window.end, TestWindowExposure.range_end >= window.start)
                .order_by(TestWindowExposure.run_recorded_at)
            ).scalars()
        )
        items = []
        for exposure in rows:
            context: dict[str, Any] = {"same_revision": exposure.study_revision_id == revision_id}
            if exposure.study_id:
                study = session.get(ResearchStudy, exposure.study_id)
                context["study_name"] = study.name if study else None
            if exposure.study_revision_id:
                rev = session.get(StudyRevision, exposure.study_revision_id)
                context["revision_no"] = rev.revision_no if rev else None
                final = next((e for e in reversed(self._evaluations(session, exposure.study_revision_id)) if e.scope == SCOPE_FINAL), None)
                context["outcome"] = final.comparison_json.get("outcome") if final else None
            if exposure.strategy_version_id:
                version = session.get(StrategyVersion, exposure.strategy_version_id)
                context["strategy_name"] = version.name if version else None
                context["version_no"] = version.version_no if version else None
            items.append(exposure_to_dict(exposure, context))
        by_state = {STATE_RUN_RECORDED: sum(1 for i in items if i["state"] == STATE_RUN_RECORDED), STATE_RESULTS_INSPECTED: sum(1 for i in items if i["state"] == STATE_RESULTS_INSPECTED)}
        return {"proposed_window": {"start": window.start.isoformat(), "end": window.end.isoformat()}, "assets": list(st.assets), "count": len(items), "by_state": by_state, "items": items, "limitation": OUTSIDE_VISIBILITY_LIMITATION}

    def exposures(self, revision_id: uuid.UUID) -> dict[str, Any]:
        with session_scope(self._settings) as session:
            revision = self._load_revision(session, revision_id)
            return {"revision_id": str(revision_id), **self._exposures(session, revision_id, settings_from_json(revision.settings_json))}

    def _mark_inspected(self, session: Session, revision_id: uuid.UUID) -> int:
        now = datetime.now(UTC)
        count = 0
        for exposure in session.execute(
            select(TestWindowExposure).where(TestWindowExposure.study_revision_id == revision_id, TestWindowExposure.results_inspected_at.is_(None))
        ).scalars():
            exposure.results_inspected_at = now
            count += 1
        return count

    def results(self, revision_id: uuid.UUID) -> dict[str, Any]:
        with session_scope(self._settings) as session:
            revision = self._load_revision(session, revision_id)
            evaluations = self._evaluations(session, revision_id)
            initial = next((e for e in reversed(evaluations) if e.scope == SCOPE_INITIAL), None)
            finals = [e for e in evaluations if e.scope == SCOPE_FINAL]
            runs = []
            for link, run in self._runs_for(session, revision_id, roles=WINDOW_ROLES, rerun=False) + self._runs_for(session, revision_id, roles=WINDOW_ROLES, rerun=True):
                block = self._research_block(run)
                runs.append(
                    {
                        "run_id": str(run.id),
                        "status": run.status.value,
                        "strategy_version_id": str(link.strategy_version_id) if link.strategy_version_id else None,
                        "benchmark": link.strategy_version_id is None,
                        "asset": link.asset,
                        "window_role": link.window_role,
                        "rerun_of": str(link.rerun_of) if link.rerun_of else None,
                        "spec_sha256": link.spec_sha256,
                        "code_sha": link.code_sha,
                        "input_digest": link.input_digest,
                        "results_digest": block["results_digest"] if block else None,
                        "metrics": block["metrics"] if block else None,
                        "evidence": block["evidence"] if block else None,
                        "link": f"/api/v1/jobs/{run.job_id}" if run.job_id else None,
                    }
                )
            inspected = self._mark_inspected(session, revision_id) if finals else 0
            return {
                "revision": revision_to_dict(revision),
                "runs": runs,
                "initial": initial.comparison_json if initial else None,
                "final_test": finals[-1].comparison_json if finals else {"state": "not_run"},
                "final_test_history": [{"evaluation_id": str(e.id), "is_rerun": e.is_rerun, "outcome": e.comparison_json.get("outcome"), "created_at": _iso(e.created_at)} for e in finals],
                "exposures_marked_inspected": inspected,
            }

    def comparison(self, revision_id: uuid.UUID) -> dict[str, Any]:
        with session_scope(self._settings) as session:
            self._load_revision(session, revision_id)
            evaluations = self._evaluations(session, revision_id)
            initial = next((e for e in reversed(evaluations) if e.scope == SCOPE_INITIAL), None)
            finals = [e for e in evaluations if e.scope == SCOPE_FINAL]
            if initial is None:
                return {"revision_id": str(revision_id), "state": "not_evaluated", "comparison": None}
            comparison = dict(initial.comparison_json)
            if finals:
                comparison["final_test"] = finals[-1].comparison_json
                self._mark_inspected(session, revision_id)
            return {"revision_id": str(revision_id), "state": "evaluated", "comparison": comparison}

    def export(self, revision_id: uuid.UUID, *, export_root: Path | None = None) -> dict[str, Any]:
        """Write ``comparison.json`` and per-run ``summary.json``/``trades.csv``/``equity_curve.csv``
        under ``.data/research/studies/<revision>/`` (git-ignored, Tiingo licence)."""

        root = export_root or (self._settings.paths.data_dir / "research" / "studies")
        directory = root / str(revision_id)
        directory.mkdir(parents=True, exist_ok=True)
        payload = self.comparison(revision_id)
        (directory / "comparison.json").write_text(json.dumps(payload["comparison"], indent=2, sort_keys=True, default=str))
        written = ["comparison.json"]
        with session_scope(self._settings) as session:
            for link, run in self._runs_for(session, revision_id, roles=WINDOW_ROLES, rerun=False) + self._runs_for(session, revision_id, roles=WINDOW_ROLES, rerun=True):
                run_dir = directory / "runs" / str(run.id)
                run_dir.mkdir(parents=True, exist_ok=True)
                trades, equity = load_run_rows(session, run.id)
                (run_dir / "summary.json").write_text(json.dumps({"run_id": str(run.id), "asset": link.asset, "window_role": link.window_role, "strategy_version_id": str(link.strategy_version_id) if link.strategy_version_id else None, "spec_sha256": link.spec_sha256, "code_sha": link.code_sha, "input_digest": link.input_digest, "result_summary": run.result_summary}, indent=2, sort_keys=True, default=str))
                with (run_dir / "trades.csv").open("w", newline="") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(["entry_fill_session", "exit_fill_session", "quantity", "entry_price", "exit_price", "net_pnl", "rounding_slack", "status"])
                    for t in trades:
                        writer.writerow([t.entry_fill_session, t.exit_fill_session or "", t.quantity, t.entry_price, t.exit_price or "", t.net_pnl or "", t.rounding_slack or "", t.status])
                with (run_dir / "equity_curve.csv").open("w", newline="") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(["session_date", "total_equity", "gross_exposure", "cash"])
                    for e in equity:
                        writer.writerow([e.session_date, e.total_equity, e.gross_exposure, e.cash])
                written.append(f"runs/{run.id}/summary.json")
            inspected = self._mark_inspected(session, revision_id)
        manifest = {"revision_id": str(revision_id), "files": written, "license_note": "Provider data for personal research use only; never commit or share this directory.", "exported_at": datetime.now(UTC).isoformat()}
        (directory / "MANIFEST.json").write_text(canonical_json(manifest))
        return {"revision_id": str(revision_id), "path": str(directory), "files": written, "exposures_marked_inspected": inspected}
