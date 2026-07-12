from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading_platform.services.concurrency_guard import (
    CONCURRENT_RUN_LOCK_EXIT_CODE,
    ConcurrentRunLockedError,
    advisory_lock_key,
)


# ---------------------------------------------------------------------------
# Pure unit tests: key derivation + typed error (no DB)
# ---------------------------------------------------------------------------


class TestAdvisoryLockKey:
    def test_deterministic_for_same_inputs(self) -> None:
        first = advisory_lock_key("trend_following_daily", date(2024, 1, 5))
        second = advisory_lock_key("trend_following_daily", date(2024, 1, 5))

        assert first == second

    def test_varies_by_session_date(self) -> None:
        key_a = advisory_lock_key("trend_following_daily", date(2024, 1, 5))
        key_b = advisory_lock_key("trend_following_daily", date(2024, 1, 6))

        assert key_a != key_b

    def test_fits_signed_bigint_range(self) -> None:
        key = advisory_lock_key("trend_following_daily", date(2024, 1, 5))

        assert -(2**63) <= key <= 2**63 - 1


class TestConcurrentRunLockedError:
    def test_str_names_both_fields(self) -> None:
        err = ConcurrentRunLockedError("trend_following_daily", date(2024, 1, 5))

        message = str(err)

        assert "trend_following_daily" in message
        assert "2024-01-05" in message

    def test_is_exception_subclass_assertable_by_class(self) -> None:
        assert issubclass(ConcurrentRunLockedError, RuntimeError)


def test_concurrent_run_lock_exit_code_is_distinct_nonzero_constant() -> None:
    assert CONCURRENT_RUN_LOCK_EXIT_CODE == 3
    assert CONCURRENT_RUN_LOCK_EXIT_CODE != 0
    assert CONCURRENT_RUN_LOCK_EXIT_CODE != 2  # argparse's usage exit code
