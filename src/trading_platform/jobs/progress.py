"""Generic progress snapshot for the Job framework (D-11, D-12, D-14).

D-11: the progress snapshot is a generic value object -- an optional
0-100 percent, a current step/message, and optional current/total
counters -- so any Job type can report useful progress without
inventing a per-type schema.

D-12: nothing in this module resets progress on a terminal transition.
``apply_progress`` performs a partial update of only the fields the
caller supplied; ``FAILED`` and ``CANCELLED`` Jobs keep their last
reported snapshot untouched, and only a handler reaching completion
writes 100 percent via ``mark_completed`` (called by the runner on the
``SUCCEEDED`` path only -- never on ``FAILED``/``CANCELLED``).

D-14: progress remains queryable for the lifetime of the owning Job
record. Phase 17 adds no pruning, compaction, or TTL for progress data,
and no code in this package may add one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trading_platform.db.models.job import Job

_MAX_STEP_CHARS = 255


@dataclass(frozen=True)
class ProgressSnapshot:
    """A partial progress report a handler can send at any safe point.

    All four fields are optional. ``step`` longer than 255 characters
    (the ``jobs.progress_step`` column width) is truncated rather than
    rejected -- progress reporting must never raise merely because a
    handler's step description ran long.
    """

    percent: int | None = None
    step: str | None = None
    current: int | None = None
    total: int | None = None

    def __post_init__(self) -> None:
        if self.percent is not None:
            if not isinstance(self.percent, int) or isinstance(self.percent, bool):
                raise ValueError(f"percent must be an int, got {type(self.percent).__name__}")
            if not (0 <= self.percent <= 100):
                raise ValueError(f"percent must be between 0 and 100 inclusive, got {self.percent}")

        for field_name, value in (("current", self.current), ("total", self.total)):
            if value is not None:
                if not isinstance(value, int) or isinstance(value, bool):
                    raise ValueError(f"{field_name} must be an int, got {type(value).__name__}")
                if value < 0:
                    raise ValueError(f"{field_name} must be non-negative, got {value}")

        if self.current is not None and self.total is not None and self.current > self.total:
            raise ValueError(f"current ({self.current}) must be <= total ({self.total})")

        if self.step is not None and len(self.step) > _MAX_STEP_CHARS:
            # Truncate rather than raise -- object.__setattr__ is required
            # because the dataclass is frozen.
            object.__setattr__(self, "step", self.step[:_MAX_STEP_CHARS])

    def is_empty(self) -> bool:
        """True when none of the four fields were supplied."""
        return self.percent is None and self.step is None and self.current is None and self.total is None

    def to_dict(self) -> dict[str, int | str | None]:
        """Return the four fields as a plain dict, for embedding in log
        context or API responses."""
        return {
            "percent": self.percent,
            "step": self.step,
            "current": self.current,
            "total": self.total,
        }


def apply_progress(job: "Job", snapshot: ProgressSnapshot, *, now: datetime) -> bool:
    """Write only the non-None fields of ``snapshot`` onto ``job`` (D-12).

    This is a partial update: reporting only ``step`` must not blank out
    a previously reported ``percent``, and vice versa. Returns ``False``
    and performs no write at all when ``snapshot.is_empty()`` -- callers
    should treat that as a no-op, not an error.

    D-12: this function never resets progress on a terminal transition.
    It is called only while a Job is actively reporting; a ``FAILED`` or
    ``CANCELLED`` Job simply stops receiving calls and keeps whatever
    snapshot it last had. Only ``mark_completed`` below writes 100.
    """
    if snapshot.is_empty():
        return False

    if snapshot.percent is not None:
        job.progress_percent = snapshot.percent
    if snapshot.step is not None:
        job.progress_step = snapshot.step
    if snapshot.current is not None:
        job.progress_current = snapshot.current
    if snapshot.total is not None:
        job.progress_total = snapshot.total
    job.progress_updated_at = now
    return True


def mark_completed(job: "Job", *, now: datetime) -> None:
    """Set ``progress_percent = 100`` for a Job reaching completion.

    Called by the runner on the ``SUCCEEDED`` path only. Never call this
    on the ``FAILED`` or ``CANCELLED`` paths -- per D-12, those Jobs
    preserve their last reported progress, whatever it was.
    """
    job.progress_percent = 100
    job.progress_updated_at = now
