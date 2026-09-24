"""D-22: per-type mode preflight -- config_invalid before dispatch, worker survival.

Reuses the temp-DB fixture pattern established by tests/test_job_runner.py
(no shared conftest.py entry exists for it).
"""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Mapping

import psycopg
import pytest
from alembic import command

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.migrate import build_alembic_config

from trading_platform.core.settings import build_settings_payload, clear_settings_cache
from trading_platform.db.models import Job, JobFailureReason, JobLog, JobStatus
from trading_platform.db.session import clear_engine_cache, session_scope
from trading_platform.jobs.contracts import JobContext
from trading_platform.jobs.dependencies import submit_job
from trading_platform.jobs.queue import claim_next_job
from trading_platform.jobs.registry import JobRegistry
from trading_platform.jobs.runner import execute_job, run_worker_loop
from trading_platform.services.config.validation import ExecutionMode, config_failure_message
from trading_platform.worker.commands.run_jobs import required_mode_preflight, run_jobs_command
from trading_platform.worker.parser import build_parser


def _admin_connection_settings() -> dict[str, str]:
    return {
        "host": os.getenv("TRADING_PLATFORM_DATABASE__HOST", "localhost"),
        "port": os.getenv("TRADING_PLATFORM_DATABASE__PORT", "5432"),
        "user": os.getenv("TRADING_PLATFORM_DATABASE__USER", "trading_platform"),
        "password": os.getenv("TRADING_PLATFORM_DATABASE__PASSWORD", "trading_platform"),
        "dbname": os.getenv("TRADING_PLATFORM_ADMIN_DB", "postgres"),
    }


def _connect_admin(params: dict[str, str] | None = None) -> psycopg.Connection:
    params = params or _admin_connection_settings()
    return psycopg.connect(
        host=params["host"],
        port=params["port"],
        user=params["user"],
        password=params["password"],
        dbname=params["dbname"],
        autocommit=True,
    )


def _set_database_env(monkeypatch: pytest.MonkeyPatch, database_name: str) -> None:
    params = _admin_connection_settings()
    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__HOST", params["host"])
    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__PORT", params["port"])
    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__USER", params["user"])
    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__PASSWORD", params["password"])
    monkeypatch.setenv("TRADING_PLATFORM_DATABASE__NAME", database_name)


@pytest.fixture()
def migrated_job_runner_preflight_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    database_name = f"job_runner_preflight_{uuid.uuid4().hex[:8]}"
    admin_params = _admin_connection_settings()

    try:
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{database_name}"')
    except psycopg.Error as exc:  # pragma: no cover - exercised when local Postgres is unavailable
        pytest.fail(
            "PostgreSQL is required for tests/test_job_runner_preflight.py. "
            "Start the local db service first (for example `docker compose up -d db`). "
            f"Connection error: {exc}"
        )

    _set_database_env(monkeypatch, database_name)
    # D-22: base state is BACKTEST-safe -- alpaca provider, empty broker
    # keys. Individual tests override api_key/api_secret via monkeypatch and
    # must call clear_settings_cache() themselves afterward.
    monkeypatch.setenv("TRADING_PLATFORM_BROKER__PROVIDER", "alpaca")
    monkeypatch.setenv("TRADING_PLATFORM_BROKER__ALPACA__API_KEY", "")
    monkeypatch.setenv("TRADING_PLATFORM_BROKER__ALPACA__API_SECRET", "")
    clear_settings_cache()
    clear_engine_cache()
    command.upgrade(build_alembic_config(), "head")

    try:
        yield database_name
    finally:
        clear_settings_cache()
        clear_engine_cache()
        with _connect_admin(admin_params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = %s
                      AND usename = current_user
                      AND pid <> pg_backend_pid()
                    """,
                    (database_name,),
                )
                cursor.execute(f'DROP DATABASE IF EXISTS "{database_name}"')


# --- Local fake handlers ----------------------------------------------------


class _ModeHandler:
    """A handler that declares a required_execution_mode (duck-typed, D-22)."""

    def __init__(self, job_type: str, required_execution_mode: ExecutionMode, calls: list[str]) -> None:
        self.job_type = job_type
        self.required_execution_mode = required_execution_mode
        self._calls = calls

    def run(self, context: JobContext) -> Mapping[str, Any]:
        self._calls.append(self.job_type)
        return {"ok": True}


class _UndeclaredHandler:
    """A handler with no required_execution_mode attribute at all."""

    job_type = "preflight_undeclared"

    def run(self, context: JobContext) -> Mapping[str, Any]:
        raise AssertionError("handler.run must never be invoked")


def _registry(*handlers: Any) -> JobRegistry:
    registry = JobRegistry()
    for handler in handlers:
        registry.register(handler)
    return registry


def _submit(job_type: str) -> uuid.UUID:
    return submit_job(job_type=job_type, payload={})


def _claim(job_id: uuid.UUID, *, worker_id: str = "worker-1") -> None:
    with session_scope() as session:
        claimed = claim_next_job(session, worker_id=worker_id)
        assert claimed == job_id


def _get_job(job_id: uuid.UUID) -> Job:
    with session_scope() as session:
        job = session.get(Job, job_id)
        assert job is not None
        session.expunge(job)
        return job


def _job_log_count(job_id: uuid.UUID) -> int:
    with session_scope() as session:
        return session.query(JobLog).filter(JobLog.job_id == job_id).count()


# --- Tests -------------------------------------------------------------


def test_paper_mode_job_fails_config_invalid_before_dispatch(
    migrated_job_runner_preflight_db: str,
) -> None:
    calls: list[str] = []
    handler = _ModeHandler("preflight_paper", ExecutionMode.PAPER, calls)
    job_id = _submit(handler.job_type)
    _claim(job_id)

    status = execute_job(
        job_id=job_id,
        worker_id="worker-1",
        registry=_registry(handler),
        preflight=required_mode_preflight,
    )

    assert status is JobStatus.FAILED
    job = _get_job(job_id)
    assert job.failure_reason is JobFailureReason.CONFIG_INVALID
    assert job.outcome_uncertain is False
    assert job.failure_message is not None
    assert "broker.alpaca.api_key" in job.failure_message
    assert calls == []
    assert _job_log_count(job_id) == 0


def test_config_invalid_message_excludes_secret_values(
    migrated_job_runner_preflight_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = "SENTINEL-KEY-7f3a"
    monkeypatch.setenv("TRADING_PLATFORM_BROKER__ALPACA__API_KEY", sentinel)
    monkeypatch.setenv("TRADING_PLATFORM_BROKER__ALPACA__API_SECRET", "")
    clear_settings_cache()

    calls: list[str] = []
    handler = _ModeHandler("preflight_paper_secret", ExecutionMode.PAPER, calls)
    job_id = _submit(handler.job_type)
    _claim(job_id)

    status = execute_job(
        job_id=job_id,
        worker_id="worker-1",
        registry=_registry(handler),
        preflight=required_mode_preflight,
    )

    assert status is JobStatus.FAILED
    job = _get_job(job_id)
    assert job.failure_message is not None
    assert "broker.alpaca.api_secret" in job.failure_message
    assert sentinel not in job.failure_message


def test_worker_continues_after_config_invalid(migrated_job_runner_preflight_db: str) -> None:
    calls: list[str] = []
    paper_handler = _ModeHandler("preflight_paper_continue", ExecutionMode.PAPER, calls)
    backtest_handler = _ModeHandler("preflight_backtest_continue", ExecutionMode.BACKTEST, calls)
    paper_job_id = _submit(paper_handler.job_type)
    backtest_job_id = _submit(backtest_handler.job_type)

    report = run_worker_loop(
        worker_id="worker-1",
        registry=_registry(paper_handler, backtest_handler),
        max_jobs=2,
        once=False,
        poll_interval_seconds=0.01,
        preflight=required_mode_preflight,
    )

    assert report["stopped_reason"] == "max_jobs"
    assert report["jobs_executed"] == 2
    assert _get_job(paper_job_id).status is JobStatus.FAILED
    assert _get_job(paper_job_id).failure_reason is JobFailureReason.CONFIG_INVALID
    assert _get_job(backtest_job_id).status is JobStatus.SUCCEEDED
    assert calls == [backtest_handler.job_type]


def test_undeclared_mode_fails_config_invalid(migrated_job_runner_preflight_db: str) -> None:
    handler = _UndeclaredHandler()
    job_id = _submit(handler.job_type)
    _claim(job_id)

    status = execute_job(
        job_id=job_id,
        worker_id="worker-1",
        registry=_registry(handler),
        preflight=required_mode_preflight,
    )

    assert status is JobStatus.FAILED
    job = _get_job(job_id)
    assert job.failure_reason is JobFailureReason.CONFIG_INVALID
    assert job.failure_message is not None
    assert "declares no required_execution_mode" in job.failure_message


def test_preflight_exception_is_contained(migrated_job_runner_preflight_db: str) -> None:
    calls: list[str] = []
    handler = _ModeHandler("preflight_raises", ExecutionMode.BACKTEST, calls)
    job_id = _submit(handler.job_type)
    _claim(job_id)

    def _raising_preflight(_handler: Any) -> str | None:
        raise RuntimeError("leak-me")

    status = execute_job(
        job_id=job_id,
        worker_id="worker-1",
        registry=_registry(handler),
        preflight=_raising_preflight,
    )

    assert status is JobStatus.FAILED
    job = _get_job(job_id)
    assert job.failure_reason is JobFailureReason.CONFIG_INVALID
    assert job.failure_message is not None
    assert "leak-me" not in job.failure_message
    assert "RuntimeError" in job.failure_message
    assert calls == []


def test_no_preflight_keeps_phase17_behavior(migrated_job_runner_preflight_db: str) -> None:
    calls: list[str] = []
    handler = _ModeHandler("preflight_omitted", ExecutionMode.PAPER, calls)
    job_id = _submit(handler.job_type)
    _claim(job_id)

    status = execute_job(
        job_id=job_id,
        worker_id="worker-1",
        registry=_registry(handler),
    )

    assert status is JobStatus.SUCCEEDED
    assert calls == [handler.job_type]


def test_run_jobs_boots_with_backtest_level_config(
    migrated_job_runner_preflight_db: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = build_parser().parse_args(["run-jobs", "--once", "--compact"])

    run_jobs_command(args)

    captured = capsys.readouterr()
    report = captured.out
    assert '"stopped_reason"' in report


def test_config_failure_message_contract(migrated_job_runner_preflight_db: str) -> None:
    payload = build_settings_payload()

    assert config_failure_message(payload, mode=ExecutionMode.BACKTEST) is None

    paper_message = config_failure_message(payload, mode=ExecutionMode.PAPER)
    assert paper_message is not None
    assert paper_message.startswith("Configuration invalid for paper mode: ")
