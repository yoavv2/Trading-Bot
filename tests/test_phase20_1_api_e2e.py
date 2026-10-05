"""Phase 20.1 gate (plan 20.1-13): the fifteen API end-to-end safety scenarios E1..E15 of
05-INTERIM-API-OPERATIONS section 3.

What the gate proves: ownership, attribution, recovery, execution operations, controls and the
legacy-console facts COMPOSE. Every scenario runs over HTTP (FastAPI ``TestClient``) through the
production ``create_app()`` registry, the real ``run-jobs --once`` worker command and a real
PostgreSQL database; only the broker is faked, and only at the HTTP TRANSPORT
(``tests/support/scripted_broker.py``), so the real attempt log, submission classes, status mapping,
pagination and lookups run. Eligibility, evaluation provenance, the run-time window and the
executor identity are the REAL ones (no ``allow_paper_execution`` stub); the harness clock moves
time for submit-time validation, run-time checks and lazy operation expiry. The only other seam is
the pre-send latest-trade price source (S2-R3), scripted fresh unless a test removes it.

Every scenario declares its expected broker POST count (default 0); the harness fails the test at
teardown when the scripted broker saw a different number of order POSTs, any cancel call, or any
market-data call.

Run: ``PYTHONPATH=src .venv/bin/python -m pytest tests/test_phase20_1_api_e2e.py -q``.
"""

from __future__ import annotations

import argparse
import importlib
import pkgutil
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from tests.support.calendar_facts import et, seed_calendar, sessions_between
from tests.support.migrated_db import migrated_database
from tests.support.paper_ownership import seed_registered_strategy
from tests.support.price_source import FreshPriceSource
from tests.support.scripted_broker import ScriptedAlpaca
from tests.support.symbol_metadata import ready_symbol_fields

from trading_platform.api.app import create_app
from trading_platform.core import clock
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.base import Base
from trading_platform.db.models import Job, PaperOrder, StrategyRun
from trading_platform.db.models.daily_bar import DailyBar as DailyBarModel
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import session_scope
from trading_platform.services import alpaca as alpaca_module
from trading_platform.services.alpaca import AlpacaClient, PriceFailure, PriceLookupError
from trading_platform.services.execution import submit_orders as submit_orders_module
from trading_platform.worker.commands.run_jobs import run_jobs_command
from trading_platform.worker.parser import build_parser

STRATEGY = "trend_following_daily"
OTHER_STRATEGY = "donchian_breakout_daily"
UNIVERSE = ("AAPL", "MSFT", "NVDA")
#: The evaluation session of the default scenarios (a Monday) and the session it executes in.
EVAL_SESSION = date(2025, 12, 1)
EXEC_SESSION = date(2025, 12, 2)
EXEC_NOW = et(2025, 12, 2, 10, 0)  # inside the execution window of EVAL_SESSION
_FAKE_API_KEY = "fake-key-not-a-secret"  # pragma: allowlist secret
_FAKE_API_SECRET = "fake-secret-not-a-secret"  # pragma: allowlist secret


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class HarnessPrice:
    """The pre-send latest-trade seam (S2-R3): a fresh trade at the intent's reference price, or
    nothing at all when ``available`` is False (the operation then pauses price_unavailable)."""

    def __init__(self) -> None:
        self.available = True
        self.source = FreshPriceSource()

    def latest_trade(self, symbol: str, *, reference_price: Decimal | None = None) -> Any:
        if not self.available:
            raise PriceLookupError(PriceFailure.PRICE_LOOKUP_FAILED)
        return self.source.latest_trade(symbol, reference_price=reference_price)

    def close(self) -> None:
        return None


def run_worker_once() -> dict[str, Any]:
    """One real ``run-jobs --once`` poll pass through the CLI entrypoint."""

    args: argparse.Namespace = build_parser().parse_args(["run-jobs", "--once", "--compact"])
    try:
        run_jobs_command(args)
    except SystemExit as exc:  # pragma: no cover - failure path
        pytest.fail(f"run_jobs_command exited unexpectedly: {exc!r}")
    return {}


def _all_tables() -> list[str]:
    return [table.name for table in Base.metadata.sorted_tables]


def row_counts() -> dict[str, int]:
    """A full row-count snapshot of every application table."""

    counts: dict[str, int] = {}
    with session_scope(load_settings()) as session:
        for name in _all_tables():
            counts[name] = int(session.execute(text(f'SELECT count(*) FROM "{name}"')).scalar_one())
    return counts


@dataclass
class Phase201Env:
    client: TestClient
    broker: ScriptedAlpaca
    price: HarnessPrice
    clock_cell: dict[str, datetime]
    posts_expected: int = 0
    _keys: int = 0
    _submission_clocks: list[Any] = field(default_factory=list)

    # -- time ---------------------------------------------------------------

    def set_clock(self, when: datetime) -> None:
        self.clock_cell["now"] = when

    def advance(self, **delta: float) -> datetime:
        self.clock_cell["now"] = self.clock_cell["now"] + timedelta(**delta)
        return self.clock_cell["now"]

    @property
    def now(self) -> datetime:
        return self.clock_cell["now"]

    # -- budget -------------------------------------------------------------

    def expect_posts(self, count: int) -> None:
        self.posts_expected = count

    # -- HTTP ---------------------------------------------------------------

    def get(self, path: str) -> httpx.Response:
        return self.client.get(path)

    def put(self, path: str, body: dict[str, Any]) -> httpx.Response:
        return self.client.put(path, json=body)

    def post(
        self, path: str, body: dict[str, Any] | None = None, key: str | None = None
    ) -> httpx.Response:
        headers = {"Idempotency-Key": key} if key else {}
        return self.client.post(path, json=body or {}, headers=headers)

    def _key(self, prefix: str) -> str:
        self._keys += 1
        return f"{prefix}-{self._keys}-{uuid.uuid4().hex[:8]}"

    def submit_job(
        self, job_type: str, payload: dict[str, Any], key: str | None = None
    ) -> httpx.Response:
        return self.client.post(
            "/api/v1/jobs",
            headers={"Idempotency-Key": key or self._key(job_type)},
            json={"job_type": job_type, "payload": payload},
        )

    def job(self, job_id: str) -> dict[str, Any]:
        response = self.client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200, response.text
        return response.json()

    def run_job(self, job_type: str, payload: dict[str, Any], key: str | None = None) -> dict[str, Any]:
        """Submit (must be 202), run one worker pass, return the Job read."""

        submitted = self.submit_job(job_type, payload, key)
        assert submitted.status_code == 202, submitted.text
        run_worker_once()
        return self.job(submitted.json()["job_id"])

    # -- the operator procedures (05 section 2) ------------------------------

    def sync(self, scope: str = "account", **extra: Any) -> dict[str, Any]:
        return self.run_job("broker-order-sync", {"scope": scope, **extra})

    def reconcile(self, scope: str = "account", **extra: Any) -> dict[str, Any]:
        return self.run_job("reconciliation", {"scope": scope, **extra})

    def settle(self, scope: str = "strategy") -> tuple[dict[str, Any], dict[str, Any]]:
        """Post-execution synchronization (S3-R4): a broker-order-sync, then a standalone
        account-scope reconciliation (05 M4 -> M5). After an owned FILL the sync must be the
        owner-scope one: the account-scope sync never creates a Position row, so the account
        reconciliation would report the broker position as MISSING_LOCAL (see E4)."""

        if scope == "strategy":
            synced = self.sync("strategy", strategy_id=STRATEGY, as_of_session=EVAL_SESSION.isoformat())
        else:
            synced = self.sync()
        reconciled = self.reconcile()
        return synced, reconciled

    def evaluate(self, session_date: date = EVAL_SESSION) -> dict[str, Any]:
        detail = self.run_job(
            "risk-evaluation", {"strategy_id": STRATEGY, "as_of_session": session_date.isoformat()}
        )
        assert detail["status"] == "succeeded", detail.get("failure_message")
        return detail

    def start_payload(
        self, session_date: date = EVAL_SESSION, risk_run_id: str | None = None
    ) -> dict[str, Any]:
        return {
            "strategy_id": STRATEGY,
            "as_of_session": session_date.isoformat(),
            "risk_run_id": risk_run_id,
        }

    def start_session(self, session_date: date = EVAL_SESSION) -> httpx.Response:
        return self.submit_job("paper-session", self.start_payload(session_date))

    def run_session(self, session_date: date = EVAL_SESSION) -> dict[str, Any]:
        """Start (must be accepted) and run the worker; returns the Job read."""

        submitted = self.start_session(session_date)
        assert submitted.status_code == 202, submitted.text
        run_worker_once()
        return self.job(submitted.json()["job_id"])

    def continue_payload(self, operation_id: str) -> dict[str, Any]:
        return {"mode": "continue", "operation_id": operation_id}

    def continue_submit(self, operation_id: str) -> httpx.Response:
        return self.submit_job("paper-session", self.continue_payload(operation_id))

    def run_continue(self, operation_id: str) -> dict[str, Any]:
        submitted = self.continue_submit(operation_id)
        assert submitted.status_code == 202, submitted.text
        run_worker_once()
        return self.job(submitted.json()["job_id"])

    def operation(self, operation_id: str) -> dict[str, Any]:
        response = self.get(f"/api/v1/execution-operations/{operation_id}")
        assert response.status_code == 200, response.text
        return response.json()

    def own(self, strategy_id: str = STRATEGY, *, enabled: bool = True) -> None:
        """Explicit direct seeding of an enabled owner (E3-E12; E1 and E13 use the real PUT)."""

        seed_registered_strategy(load_settings(), strategy_id, enabled=enabled, owner=True)

    def conflict_code(self, response: httpx.Response) -> str:
        assert response.status_code == 409, response.text
        return str(response.json()["detail"]["code"])


def detail_code(response: httpx.Response) -> str:
    return str(response.json()["detail"]["code"])


def operation_of(job: dict[str, Any]) -> dict[str, Any]:
    operation = job["result_summary"].get("operation")
    assert operation is not None, job["result_summary"]
    return dict(operation)


def seed_rising_bars(
    symbols: tuple[str, ...], session_dates: list[date], *, base: int = 100
) -> None:
    """One adjusted polygon bar per (symbol, date) with strictly rising closes, so the trend
    strategy's entry condition holds for every symbol and every window."""

    settings = load_settings()
    with session_scope(settings) as session:
        for offset, ticker in enumerate(symbols):
            symbol = session.execute(select(Symbol).where(Symbol.ticker == ticker)).scalar_one_or_none()
            if symbol is None:
                symbol = Symbol(ticker=ticker, **ready_symbol_fields())
                session.add(symbol)
                session.flush()
            for index, session_date in enumerate(session_dates):
                close = Decimal(base + offset * 50 + index)
                session.add(
                    DailyBarModel(
                        symbol_id=symbol.id,
                        session_date=session_date,
                        open=close,
                        high=close + 1,
                        low=close - 1,
                        close=close,
                        volume=1000,
                        adjusted=True,
                        provider="polygon",
                    )
                )


def _write_strategy_config(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{STRATEGY}.yaml").write_text(
        yaml.safe_dump(
            {
                "strategy_id": STRATEGY,
                "display_name": "TrendFollowingDailyV1",
                "enabled": True,
                "universe": list(UNIVERSE),
                "indicators": {"short_window": 2, "long_window": 3, "warmup_periods": 3},
                "risk": {"max_positions": 10, "risk_per_trade": 0.01},
                "exits": {"close_below": "sma_2", "exit_window": 2},
            }
        )
    )


def _submission_modules() -> list[Any]:
    """Every ``jobs/handlers/*_submission.py`` module (the specs bind ``_default_clock``)."""

    import trading_platform.jobs.handlers as handlers

    modules = []
    for info in pkgutil.iter_modules(handlers.__path__):
        if info.name.endswith("_submission"):
            modules.append(importlib.import_module(f"trading_platform.jobs.handlers.{info.name}"))
    return modules


@pytest.fixture()
def phase201_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[Phase201Env]:
    """Mutations enabled, fake credentials, a migrated database, a seeded XNYS calendar and rising
    bars for the three-symbol universe through EVAL_SESSION, the scripted broker bound at the
    transport seam, the harness clock and the price seam, and the production app."""

    with migrated_database(monkeypatch, "phase201"):
        strategy_dir = tmp_path / "strategies"
        _write_strategy_config(strategy_dir)
        monkeypatch.setenv("TRADING_PLATFORM_STRATEGY_CONFIG_DIR", str(strategy_dir))
        monkeypatch.setenv("TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED", "true")
        monkeypatch.setenv("TRADING_PLATFORM_BROKER__ALPACA__API_KEY", _FAKE_API_KEY)
        monkeypatch.setenv("TRADING_PLATFORM_BROKER__ALPACA__API_SECRET", _FAKE_API_SECRET)
        monkeypatch.setenv("TRADING_PLATFORM_BROKER__ALPACA__RETRY_BACKOFF_FACTOR", "0")
        clear_settings_cache()

        seed_calendar(date(2025, 1, 2), date(2026, 3, 31))
        seed_rising_bars(UNIVERSE, sessions_between(date(2025, 11, 3), EVAL_SESSION))

        # The clock: ONE cell read by core.clock.now_utc (run-time services, lazy expiry) and by
        # every spec's module-level default clock (submit-time validation), installed before
        # create_app() so the registry the app and the worker build binds the patched functions.
        cell: dict[str, datetime] = {"now": EXEC_NOW}

        def harness_now() -> datetime:
            return cell["now"]

        monkeypatch.setattr(clock, "now_utc", harness_now)
        for module in _submission_modules():
            if hasattr(module, "_default_clock"):
                monkeypatch.setattr(module, "_default_clock", harness_now)

        broker = ScriptedAlpaca(now=lambda: max(datetime.now(UTC), cell["now"]))
        original_init = AlpacaClient.__init__

        def scripted_init(self: AlpacaClient, settings: Any, *, http_client: Any = None) -> None:
            if http_client is None:
                http_client = httpx.Client(
                    base_url=settings.base_url, transport=broker.transport()
                )
            original_init(self, settings, http_client=http_client)

        monkeypatch.setattr(AlpacaClient, "__init__", scripted_init)

        price = HarnessPrice()
        monkeypatch.setattr(submit_orders_module, "_default_price_source", lambda settings: price)

        with TestClient(create_app()) as client:
            env = Phase201Env(client=client, broker=broker, price=price, clock_cell=cell)
            yield env
            # The budget finalizer: zero unexpected POSTs, zero cancels, zero market-data calls.
            broker.assert_budget(env.posts_expected)
        clear_settings_cache()




# ---------------------------------------------------------------------------
# Shared scenario helpers
# ---------------------------------------------------------------------------


def attempt_classes(client_order_id: str | None = None) -> list[str]:
    """Outcome classes of the REAL attempt log (oldest first); an incomplete attempt reads None."""

    from trading_platform.db.models import OrderSubmissionAttempt

    with session_scope(load_settings()) as session:
        statement = select(OrderSubmissionAttempt.outcome_class).order_by(
            OrderSubmissionAttempt.started_at.asc(), OrderSubmissionAttempt.attempt_number.asc()
        )
        if client_order_id is not None:
            statement = statement.join(
                PaperOrder, PaperOrder.id == OrderSubmissionAttempt.paper_order_id
            ).where(PaperOrder.client_order_id == client_order_id)
        return list(session.execute(statement).scalars())


def intents_of(env: Phase201Env, operation_id: str) -> dict[str, dict[str, Any]]:
    """The operation read's intents keyed by symbol."""

    return {item["symbol"]: item for item in env.operation(operation_id)["intents"]}


def intent_states_of(env: Phase201Env, operation_id: str) -> dict[str, str]:
    return {symbol: item["state"] for symbol, item in intents_of(env, operation_id).items()}


def seed_29_sep_jobs(strategy_id: str = STRATEGY) -> list[uuid.UUID]:
    """The 29 Sep shape: two uncertain paper-session Jobs and one uncertain broker-order-sync Job,
    none with a linked paper_execution run and none with an order row. They complete well before
    anything the scenario runs afterwards (wall clock), so a fresh reconciliation is later."""

    from tests.support.recovery_fixtures import seed_job

    base = datetime.now(UTC) - timedelta(hours=3)
    with session_scope(load_settings()) as session:
        jobs = [
            seed_job(
                session,
                job_type="paper-session",
                strategy_id=strategy_id,
                completed_at=base,
                payload={"as_of_session": "2025-09-26"},
            ),
            seed_job(
                session,
                job_type="paper-session",
                strategy_id=strategy_id,
                completed_at=base + timedelta(minutes=1),
                payload={"as_of_session": "2025-09-26"},
            ),
            seed_job(
                session,
                job_type="broker-order-sync",
                strategy_id=strategy_id,
                completed_at=base + timedelta(minutes=2),
            ),
        ]
        return [job.id for job in jobs]


def set_status_open_orders() -> int:
    from trading_platform.db.models import OrderLifecycleState

    with session_scope(load_settings()) as session:
        return int(
            session.execute(
                select(func.count())
                .select_from(PaperOrder)
                .where(
                    PaperOrder.status.in_(
                        [OrderLifecycleState.SUBMITTED, OrderLifecycleState.PENDING_SUBMISSION]
                    )
                )
            ).scalar_one()
        )


# ---------------------------------------------------------------------------
# Harness self-tests
# ---------------------------------------------------------------------------


def test_harness_transport_is_the_only_broker_path(phase201_env: Phase201Env) -> None:
    """Harness | every AlpacaClient built without an injected client talks to the scripted
    transport (one class-level patch); the original constructor still refuses missing
    credentials. Requirements: []"""

    env = phase201_env
    assert AlpacaClient.__init__.__name__ == "scripted_init"
    client = AlpacaClient(load_settings().broker.alpaca)
    assert client.get_account().cash == Decimal("100000")
    assert env.broker.calls_total == 1 and env.broker.get_log == ["/v2/account?"]
    # AlpacaExecutionService builds its client through the same patched constructor.
    alpaca_module.AlpacaExecutionService(load_settings().broker.alpaca)
    blank = load_settings().broker.alpaca.model_copy(update={"api_key": ""})
    with pytest.raises(alpaca_module.AlpacaAuthError):
        AlpacaClient(blank)


def test_harness_post_budget_and_cancel_counter() -> None:
    """Harness | the budget assertion fails on a wrong POST count, on any cancel call and on any
    market-data call; connect errors are attempts but not POSTs that reached the broker."""

    broker = ScriptedAlpaca()
    client = broker.http_client()
    order = {
        "symbol": "AAPL",
        "qty": "1",
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "client_order_id": "c-1",
    }
    broker.assert_budget(0)
    assert client.post("/v2/orders", json=order).status_code == 200
    assert broker.post_count == 1 and broker.posts_for("c-1") == 1
    with pytest.raises(AssertionError, match="declared 0"):
        broker.assert_budget(0)
    broker.assert_budget(1)
    broker.script_post("connect_error")
    with pytest.raises(httpx.ConnectError):
        client.post("/v2/orders", json={**order, "client_order_id": "c-2"})
    assert broker.post_attempts == 2 and broker.post_count == 1
    assert client.delete("/v2/orders/abc").status_code == 405
    assert broker.cancel_calls == 1
    with pytest.raises(AssertionError, match="cancel"):
        broker.assert_budget(1)
    broker.cancel_calls = 0
    client.get("/v2/stocks/AAPL/trades/latest")
    with pytest.raises(AssertionError, match="market-data"):
        broker.assert_budget(1)
    broker.data_calls = 0
    broker.assert_budget(1)


@pytest.mark.parametrize(
    ("behavior", "expected_classes", "expected_posts"),
    [
        ("read_timeout_after_create", ["ambiguous"], 1),
        ("read_timeout_without_create", ["ambiguous"], 1),
        ("http_500", ["ambiguous"], 1),
        ("connect_error", ["pre_connection"] * 4, 0),
        ("duplicate_422", ["duplicate_reported"], 1),
        ("accept", ["accepted"], 1),
    ],
)
def test_harness_scripts_produce_real_attempt_log_classes(
    phase201_env: Phase201Env, behavior: str, expected_classes: list[str], expected_posts: int
) -> None:
    """Harness | each script behavior yields the intended client-visible failure and the REAL
    attempt log records the matching class (ambiguous for a timeout or a 5xx, pre_connection for
    a connect error after the configured retries, duplicate_reported for the 422).
    Requirements: []"""

    env = phase201_env
    env.own()
    env.expect_posts(expected_posts)
    env.evaluate()
    env.broker.script_post(behavior, times=10 if behavior == "connect_error" else 1)
    job = env.run_session()
    classes = attempt_classes()
    if behavior == "duplicate_422":
        # the 422 reply is resolved by exactly one lookup; the attempt is recorded as such
        assert classes == expected_classes
        assert any(line.startswith("/v2/orders:by_client_order_id") for line in env.broker.get_log)
    else:
        assert classes == expected_classes
    if behavior in ("read_timeout_after_create", "read_timeout_without_create", "http_500"):
        assert job["status"] == "failed" and job["outcome_uncertain"] is True
    if behavior == "connect_error":
        assert operation_of(job)["state"] == "paused"
        assert operation_of(job)["reason"] == "broker_unavailable"
    if behavior == "accept":
        assert operation_of(job)["reason"] == "working_order_commitments_unaccounted"


def test_harness_clock_reaches_submit_run_time_and_lazy_expiry(phase201_env: Phase201Env) -> None:
    """Harness | ONE set_clock call reaches (a) submit-time spec validation, (b) the run-time
    execution-window check inside a Continue and (c) lazy operation expiry on a read; nothing
    sleeps. Requirements: []"""

    env = phase201_env
    env.own()
    env.expect_posts(1)
    env.evaluate()
    job = env.run_session()
    operation_id = operation_of(job)["id"]

    # (a) submit time: one calendar day later the evaluation session is historical.
    env.set_clock(et(2025, 12, 3, 10, 0))
    rejected = env.start_session()
    assert env.conflict_code(rejected) == "historical_execution_rejected"

    # (c) lazy expiry: past the cutoff of the SAME day nothing wrote, the read already says so.
    env.set_clock(et(2025, 12, 2, 15, 50))
    persisted = env.operation(operation_id)
    assert persisted["persisted_state"] == "paused"
    assert persisted["state"] == "terminated" or persisted["will_end"] is True, persisted

    # (b) run time: a Continue accepted after the cutoff ends the operation in the worker.
    env.set_clock(et(2025, 12, 2, 10, 5))
    assert env.operation(operation_id)["state"] == "paused"  # inside the window again


def test_harness_29_sep_fixture_shape(phase201_env: Phase201Env) -> None:
    """Harness | the shared 29 Sep fixture: exactly three uncertain Jobs (two paper-session, one
    broker-order-sync), no linked paper_execution run. Requirements: []"""

    env = phase201_env
    env.own()
    ids = seed_29_sep_jobs()
    assert len(ids) == 3
    with session_scope(load_settings()) as session:
        jobs = list(session.execute(select(Job).where(Job.id.in_(ids))).scalars())
        assert sorted(job.job_type for job in jobs) == [
            "broker-order-sync",
            "paper-session",
            "paper-session",
        ]
        assert all(job.outcome_uncertain for job in jobs)
        linked = session.execute(
            select(func.count()).select_from(StrategyRun).where(StrategyRun.job_id.in_(ids))
        ).scalar_one()
        assert linked == 0


def test_harness_owned_position_reconciles_clean(phase201_env: Phase201Env) -> None:
    """Harness | an order that fills at the scripted broker, once synced, reconciles clean: the
    scripted positions, fills and account are consistent with the platform's own records.
    Requirements: []"""

    env = phase201_env
    env.own()
    env.expect_posts(1)
    env.broker.default_status = "filled"
    env.evaluate()
    job = env.run_session()
    assert operation_of(job)["state"] == "paused"
    synced, reconciled = env.settle()
    assert synced["status"] == "succeeded" and reconciled["status"] == "succeeded", synced
    assert reconciled["result_summary"]["blocks_execution"] is False
    assert [p["symbol"] for p in env.broker.positions()] == ["AAPL"]
