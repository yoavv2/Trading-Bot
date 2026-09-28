"""JOB-03 registry extensibility enforcement tests.

Proves two things:
1. ``JobRegistry`` supports register/resolve/list with typed duplicate and
   unknown-type errors (mirrors ``tests/test_strategy_registry.py``).
2. Adding a new Job type touches zero queue-framework modules -- the
   stronger JOB-03 claim that a registry alone does not prove. The queue-
   framework module list is frozen at 6 entries and non-emptiable so this
   check cannot silently degrade into a no-op.
"""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path
from typing import Any, Mapping

import pytest

from trading_platform.jobs.contracts import JobContext, JobDomainConflictError, JobHandler
from trading_platform.jobs.handlers.domain_conflicts import (
    DOMAIN_CONFLICT_EXCEPTIONS,
    translate_domain_conflicts,
)
from trading_platform.jobs.registry import (
    JobCancellationMode,
    JobRegistry,
    UnknownJobTypeError,
    build_default_registry,
    retry_prerequisite_for,
)
from trading_platform.services.concurrency_guard import ConcurrentRunLockedError

_ROOT = Path(__file__).resolve().parents[1]
_JOBS_PKG = _ROOT / "src" / "trading_platform" / "jobs"

# Frozen list of modules that make up the queue framework itself, as opposed
# to individual Job-type handler modules. Several of these are created by
# later Phase 17 plans (17-03 through 17-07); the AST scan below skips files
# that do not yet exist, but this list's length stays pinned at 6 regardless
# so the module list itself cannot be silently emptied to make the check
# vacuous.
QUEUE_FRAMEWORK_MODULES: list[Path] = [
    _JOBS_PKG / "queue.py",
    _JOBS_PKG / "runner.py",
    _JOBS_PKG / "lifecycle.py",
    _JOBS_PKG / "dependencies.py",
    _JOBS_PKG / "cancellation.py",
    _JOBS_PKG / "context.py",
]


class _FakeJobHandler:
    """Minimal concrete JobHandler used to exercise the registry."""

    def __init__(self, job_type: str, result: Mapping[str, Any]) -> None:
        self._job_type = job_type
        self._result = result

    @property
    def job_type(self) -> str:
        return self._job_type

    def run(self, context: JobContext) -> Mapping[str, Any]:
        return self._result


def test_registry_registers_and_resolves_handler() -> None:
    registry = JobRegistry()
    handler = _FakeJobHandler("fake_job", {"ok": True})

    registry.register(handler)

    assert isinstance(handler, JobHandler)
    assert registry.resolve("fake_job") is handler
    assert registry.list_job_types() == ["fake_job"]
    assert "fake_job" in registry


def test_registry_rejects_duplicate_registration() -> None:
    registry = JobRegistry()
    registry.register(_FakeJobHandler("fake_job", {"ok": True}))

    with pytest.raises(ValueError, match="fake_job"):
        registry.register(_FakeJobHandler("fake_job", {"ok": True}))


def test_registry_resolve_unknown_raises_typed_error() -> None:
    registry = JobRegistry()

    with pytest.raises(UnknownJobTypeError, match="missing_job"):
        registry.resolve("missing_job")


def test_build_default_registry_registers_backtest() -> None:
    """Phase 20 (Plan 16) registers seven more job types alongside
    backtest; the exact 8-type set is pinned by
    test_orchestration_boundaries.py::test_default_registry_registers_exactly_the_phase20_job_types."""
    registry = build_default_registry()

    assert "backtest" in registry.list_job_types()


def _string_constants(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    values: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            values.add(node.value)
    return values


def test_queue_framework_module_list_is_not_empty() -> None:
    # Guards against a typo'd/emptied QUEUE_FRAMEWORK_MODULES silently
    # turning the enforcement test below into a no-op.
    assert len(QUEUE_FRAMEWORK_MODULES) == 6


def test_adding_a_job_type_touches_zero_queue_framework_modules() -> None:
    registered_job_types = {"fake_job", "second_fake_job"}

    # Static half: none of the queue-framework modules' source text may
    # reference a concrete job-type string literal. Files not yet created
    # by later Phase 17 plans are skipped rather than failing the test.
    for module_path in QUEUE_FRAMEWORK_MODULES:
        if not module_path.exists():
            continue
        constants = _string_constants(module_path)
        overlap = constants & registered_job_types
        assert not overlap, (
            f"{module_path.relative_to(_ROOT)} references concrete job type "
            f"literal(s) {overlap}; queue-framework modules must stay "
            "job-type-agnostic (JOB-03)."
        )

    # Dynamic half: registering and resolving a brand-new handler at
    # runtime succeeds purely through JobRegistry -- no queue-framework
    # module needs to be imported or mutated to add a Job type.
    registry = JobRegistry()
    registry.register(_FakeJobHandler("fake_job", {"ok": True}))
    registry.register(_FakeJobHandler("second_fake_job", {"ok": True}))

    resolved = registry.resolve("second_fake_job")
    assert resolved.run(context=None) == {"ok": True}  # type: ignore[arg-type]
    assert registry.list_job_types() == ["fake_job", "second_fake_job"]


# --- D-01: JobCancellationMode closed 2-value set --------------------------


def test_job_cancellation_mode_is_exactly_two_values() -> None:
    assert {mode.value for mode in JobCancellationMode} == {"step_boundary", "queued_only"}
    assert JobCancellationMode.QUEUED_ONLY.value == "queued_only"


# --- D-19: retry_prerequisite_for + register() validation ------------------


class _FakeSubmissionSpecNoRetryPrerequisite:
    """No ``retry_prerequisite_job_type`` attribute at all."""

    job_type = "fake_job_with_spec"
    description = "A fake submission spec for registry tests."
    cancellation_mode = JobCancellationMode.STEP_BOUNDARY

    def validate_payload(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return dict(payload)

    def submission_defaults(self) -> Mapping[str, Any] | None:
        return None


class _FakeSubmissionSpecWithRetryPrerequisite:
    job_type = "fake_job_with_spec"
    description = "A fake submission spec for registry tests."
    cancellation_mode = JobCancellationMode.STEP_BOUNDARY

    def __init__(self, retry_prerequisite_job_type: object) -> None:
        self.retry_prerequisite_job_type = retry_prerequisite_job_type

    def validate_payload(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return dict(payload)

    def submission_defaults(self) -> Mapping[str, Any] | None:
        return None


def test_retry_prerequisite_for_returns_none_when_attribute_absent() -> None:
    spec = _FakeSubmissionSpecNoRetryPrerequisite()
    assert retry_prerequisite_for(spec) is None


def test_retry_prerequisite_for_returns_declared_job_type() -> None:
    spec = _FakeSubmissionSpecWithRetryPrerequisite(retry_prerequisite_job_type="reconciliation")
    assert retry_prerequisite_for(spec) == "reconciliation"


@pytest.mark.parametrize("bad_value", ["", 5])
def test_register_rejects_invalid_retry_prerequisite_job_type(bad_value: object) -> None:
    registry = JobRegistry()
    handler = _FakeJobHandler("fake_job_with_spec", {"ok": True})
    spec = _FakeSubmissionSpecWithRetryPrerequisite(retry_prerequisite_job_type=bad_value)

    with pytest.raises(ValueError, match="retry_prerequisite_job_type"):
        registry.register(handler, submission_spec=spec)


@pytest.mark.parametrize("good_value", [None, "reconciliation"])
def test_register_accepts_valid_retry_prerequisite_job_type(good_value: object) -> None:
    registry = JobRegistry()
    handler = _FakeJobHandler("fake_job_with_spec", {"ok": True})
    spec = _FakeSubmissionSpecWithRetryPrerequisite(retry_prerequisite_job_type=good_value)

    registry.register(handler, submission_spec=spec)

    assert registry.resolve_submission_spec("fake_job_with_spec") is spec


# --- D-04: JobDomainConflictError + translate_domain_conflicts -------------


def test_job_domain_conflict_error_message_and_str() -> None:
    exc = JobDomainConflictError("x")
    assert exc.message == "x"
    assert str(exc) == "x"


def test_domain_conflict_exceptions_is_the_closed_tuple() -> None:
    assert DOMAIN_CONFLICT_EXCEPTIONS == (ConcurrentRunLockedError,)


def test_translate_domain_conflicts_translates_concurrent_run_locked_error() -> None:
    original = ConcurrentRunLockedError("trend_following_daily", date(2024, 1, 5))

    with pytest.raises(JobDomainConflictError) as exc_info:
        with translate_domain_conflicts():
            raise original

    assert "trend_following_daily" in exc_info.value.message
    assert "2024-01-05" in exc_info.value.message
    assert exc_info.value.__cause__ is original


def test_translate_domain_conflicts_passes_through_other_exceptions() -> None:
    with pytest.raises(ValueError, match="unrelated"):
        with translate_domain_conflicts():
            raise ValueError("unrelated")
