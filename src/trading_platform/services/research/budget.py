"""Shared provider request budget (plan S3 prerequisite).

Every authenticated Tiingo request this application makes, in any process or worker
and including each retry, is admitted here first. Admission is atomic: one short
transaction takes ``pg_advisory_xact_lock(provider)``, counts the ledger for the hour
and day windows and the month's distinct symbols, refuses with a closed code when a
limit would be exceeded, else inserts the attempt row and commits. Two processes can
never both admit the 51st request of an hour, and a restarted process sees the
attempts of the one it replaced.

Counting is by **attempt**: a request is charged when it is sent, whatever the provider
answers, and a transport retry is a new attempt. The provider also counts attempts, so
this is the conservative accounting.

Limits come from ``settings.research.tiingo`` (``requests_per_hour``,
``requests_per_day``, ``unique_symbols_per_month``), to be set to the account's verified
entitlement. **Requests made outside this application** (another tool, the website, a
second key) are not observable and are not counted; the limits should be configured
with that margin in mind, and a provider 429 is still handled as a visible failure.
"""

from __future__ import annotations

import hashlib
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import func, select, text

from trading_platform.core.settings import Settings
from trading_platform.db.models.research import ProviderRequestLedgerEntry
from trading_platform.db.session import session_scope

HOUR = timedelta(hours=1)
DAY = timedelta(days=1)

OUTCOME_OK = "ok"
OUTCOME_RATE_LIMITED = "rate_limited"
OUTCOME_AUTH_FAILED = "auth_failed"
OUTCOME_TRANSPORT_ERROR = "transport_error"
OUTCOME_HTTP_ERROR = "http_error"

PURPOSE_METADATA = "metadata"
PURPOSE_PRICES = "prices"


class BudgetExhaustedError(Exception):
    """Admission refused: a configured limit would be exceeded (``provider_budget_exhausted``)."""

    code = "provider_budget_exhausted"

    def __init__(self, provider: str, limit_name: str, limit: int, used: int) -> None:
        self.provider = provider
        self.limit_name = limit_name
        self.limit = limit
        self.used = used
        super().__init__(f"{provider}: {limit_name} limit {limit} reached ({used} used).")


@dataclass(frozen=True)
class Admission:
    entry_id: uuid.UUID | None
    provider: str
    symbol: str | None
    purpose: str


class RequestBudget(Protocol):
    """What a provider client needs: admit before sending, complete after."""

    def admit(self, *, symbol: str | None, purpose: str) -> Admission: ...

    def complete(self, admission: Admission, *, outcome: str, status_code: int | None) -> None: ...


@dataclass(frozen=True)
class BudgetLimits:
    requests_per_hour: int
    requests_per_day: int
    unique_symbols_per_month: int


def default_process_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def provider_lock_key(provider: str) -> int:
    digest = hashlib.sha256(b"research.provider_request_ledger:" + provider.encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def _month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


class DatabaseRequestBudget:
    """The shared budget: one ledger row per attempt, admitted under an advisory lock."""

    def __init__(
        self,
        settings: Settings,
        *,
        provider: str,
        limits: BudgetLimits | None = None,
        process_id: str | None = None,
        job_id: uuid.UUID | None = None,
        clock=None,
    ) -> None:
        self._settings = settings
        self._provider = provider
        tiingo = settings.research.tiingo
        self._limits = limits or BudgetLimits(
            requests_per_hour=tiingo.requests_per_hour,
            requests_per_day=tiingo.requests_per_day,
            unique_symbols_per_month=tiingo.unique_symbols_per_month,
        )
        self._process_id = process_id or default_process_id()
        self._job_id = job_id
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def limits(self) -> BudgetLimits:
        return self._limits

    def usage(self) -> dict[str, int]:
        """Current counts (informational; admission re-counts under the lock)."""

        now = self._clock()
        with session_scope(self._settings) as session:
            return self._counts(session, now)

    def _counts(self, session, now: datetime) -> dict[str, int]:
        base = select(func.count()).select_from(ProviderRequestLedgerEntry).where(
            ProviderRequestLedgerEntry.provider == self._provider
        )
        hour = session.execute(base.where(ProviderRequestLedgerEntry.attempted_at > now - HOUR)).scalar_one()
        day = session.execute(base.where(ProviderRequestLedgerEntry.attempted_at > now - DAY)).scalar_one()
        symbols = session.execute(
            select(func.count(func.distinct(ProviderRequestLedgerEntry.symbol)))
            .where(ProviderRequestLedgerEntry.provider == self._provider)
            .where(ProviderRequestLedgerEntry.symbol.is_not(None))
            .where(ProviderRequestLedgerEntry.attempted_at >= _month_start(now))
        ).scalar_one()
        return {"hour": int(hour), "day": int(day), "month_symbols": int(symbols)}

    def admit(self, *, symbol: str | None, purpose: str) -> Admission:
        now = self._clock()
        normalized = symbol.strip().upper() if symbol else None
        with session_scope(self._settings) as session:
            session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": provider_lock_key(self._provider)}
            )
            counts = self._counts(session, now)
            if counts["hour"] >= self._limits.requests_per_hour:
                raise BudgetExhaustedError(self._provider, "requests_per_hour", self._limits.requests_per_hour, counts["hour"])
            if counts["day"] >= self._limits.requests_per_day:
                raise BudgetExhaustedError(self._provider, "requests_per_day", self._limits.requests_per_day, counts["day"])
            if normalized is not None and counts["month_symbols"] >= self._limits.unique_symbols_per_month:
                seen = session.execute(
                    select(func.count())
                    .select_from(ProviderRequestLedgerEntry)
                    .where(ProviderRequestLedgerEntry.provider == self._provider)
                    .where(ProviderRequestLedgerEntry.symbol == normalized)
                    .where(ProviderRequestLedgerEntry.attempted_at >= _month_start(now))
                ).scalar_one()
                if not seen:
                    raise BudgetExhaustedError(
                        self._provider,
                        "unique_symbols_per_month",
                        self._limits.unique_symbols_per_month,
                        counts["month_symbols"],
                    )
            entry = ProviderRequestLedgerEntry(
                id=uuid.uuid4(),
                provider=self._provider,
                symbol=normalized,
                purpose=purpose,
                attempted_at=now,
                process_id=self._process_id,
                job_id=self._job_id,
            )
            session.add(entry)
            session.flush()
            return Admission(entry_id=entry.id, provider=self._provider, symbol=normalized, purpose=purpose)

    def complete(self, admission: Admission, *, outcome: str, status_code: int | None) -> None:
        if admission.entry_id is None:
            return
        with session_scope(self._settings) as session:
            entry = session.get(ProviderRequestLedgerEntry, admission.entry_id)
            if entry is None:
                return
            entry.outcome = outcome
            entry.status_code = status_code
            entry.completed_at = self._clock()


class UnlimitedRequestBudget:
    """No accounting: for unit tests of the adapter's parsing and error mapping only."""

    def __init__(self) -> None:
        self.admitted: list[Admission] = []
        self.completed: list[tuple[Admission, str, int | None]] = []

    def admit(self, *, symbol: str | None, purpose: str) -> Admission:
        admission = Admission(entry_id=None, provider="tiingo", symbol=symbol, purpose=purpose)
        self.admitted.append(admission)
        return admission

    def complete(self, admission: Admission, *, outcome: str, status_code: int | None) -> None:
        self.completed.append((admission, outcome, status_code))
