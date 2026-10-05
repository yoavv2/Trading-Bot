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
from trading_platform.api.routes import market_data as market_data_route
from trading_platform.core import clock
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.base import Base
from trading_platform.db.models import Job, PaperOrder, StrategyRun
from trading_platform.db.models.daily_bar import DailyBar as DailyBarModel
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import session_scope
from trading_platform.services import alpaca as alpaca_module
from trading_platform.services import ingestion as ingestion_module
from trading_platform.services import symbol_metadata_sync as symbol_metadata_sync_module
from trading_platform.services.alpaca import AlpacaClient, PriceFailure, PriceLookupError
from trading_platform.services.data import DailyBar, DailyBarRequest
from trading_platform.services.execution import submit_orders as submit_orders_module
from trading_platform.services.polygon import PolygonAuthError
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


JOB_BOOKKEEPING_TABLES = frozenset(
    {"jobs", "job_events", "job_logs", "job_mutations", "job_dependencies"}
)


def business_counts() -> dict[str, int]:
    """Row counts of every application table EXCEPT the Job bookkeeping of the request itself."""

    return {k: v for k, v in row_counts().items() if k not in JOB_BOOKKEEPING_TABLES}


@dataclass
class Phase201Env:
    client: TestClient
    broker: ScriptedAlpaca
    price: HarnessPrice
    market: HarnessMarketData
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

    def run_until_done(self, job_id: str) -> dict[str, Any]:
        """Worker passes (``--once`` takes the oldest queued Job) until this Job is terminal."""

        for _ in range(6):
            detail = self.job(job_id)
            if detail["status"] not in ("queued", "running"):
                return detail
            run_worker_once()
        return self.job(job_id)

    def run_job(self, job_type: str, payload: dict[str, Any], key: str | None = None) -> dict[str, Any]:
        """Submit (must be 202), run one worker pass, return the Job read."""

        submitted = self.submit_job(job_type, payload, key)
        assert submitted.status_code == 202, submitted.text
        return self.run_until_done(submitted.json()["job_id"])

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
        return self.run_until_done(submitted.json()["job_id"])

    def continue_payload(self, operation_id: str) -> dict[str, Any]:
        return {"mode": "continue", "operation_id": operation_id}

    def continue_submit(self, operation_id: str) -> httpx.Response:
        return self.submit_job("paper-session", self.continue_payload(operation_id))

    def run_continue(self, operation_id: str) -> dict[str, Any]:
        submitted = self.continue_submit(operation_id)
        assert submitted.status_code == 202, submitted.text
        return self.run_until_done(submitted.json()["job_id"])

    def operation(self, operation_id: str) -> dict[str, Any]:
        response = self.get(f"/api/v1/execution-operations/{operation_id}")
        assert response.status_code == 200, response.text
        return response.json()

    def next_trading_day(self, evaluation_session: date = EXEC_SESSION) -> date:
        """Move the clock to 10:00 ET of the session after ``evaluation_session`` (whose bars are
        seeded now), so a NEW evaluation of it is the fresh one. Returns ``evaluation_session``."""

        seed_rising_bars(UNIVERSE, [evaluation_session])
        following = sessions_between(evaluation_session + timedelta(days=1), evaluation_session + timedelta(days=7))[0]
        self.set_clock(et(following.year, following.month, following.day, 10, 0))
        return evaluation_session

    def own(self, strategy_id: str = STRATEGY, *, enabled: bool = True) -> None:
        """Explicit direct seeding of an enabled owner (E3-E12; E1 and E13 use the real PUT)."""

        seed_registered_strategy(load_settings(), strategy_id, enabled=enabled, owner=True)

    def set_strategy_status(self, strategy_id: str, status: str, reason: str = "e2e") -> httpx.Response:
        return self.put(
            f"/api/v1/controls/strategies/{strategy_id}", {"status": status, "reason": reason}
        )

    def put_owner(self, strategy_id: str | None, reason: str = "e2e") -> httpx.Response:
        return self.put(
            "/api/v1/controls/active-paper-strategy", {"strategy_id": strategy_id, "reason": reason}
        )

    def conflict_code(self, response: httpx.Response) -> str:
        assert response.status_code == 409, response.text
        return str(response.json()["detail"]["code"])


def detail_code(response: httpx.Response) -> str:
    return str(response.json()["detail"]["code"])


def operation_of(job: dict[str, Any]) -> dict[str, Any]:
    operation = job["result_summary"].get("operation")
    assert operation is not None, job["result_summary"]
    return dict(operation)


FIRST_BAR_DATE = date(2025, 11, 3)


def rising_close(ticker: str, session_date: date) -> Decimal:
    """The seeded close of ``ticker`` on ``session_date`` (strictly rising per session)."""

    offset = UNIVERSE.index(ticker) if ticker in UNIVERSE else 0
    index = len(sessions_between(FIRST_BAR_DATE, session_date)) - 1
    return Decimal(100 + offset * 50 + index)


class HarnessMarketData:
    """The two Polygon seams of the market-data Jobs (as in tests/test_market_data_job_types_e2e.py)
    scripted per test: ``corrections`` change a close, ``failing_bars`` raise an auth error for a
    ticker, ``unknown_tickers`` have no overview (404), ``metadata_auth_failure`` fails the whole
    metadata operation."""

    def __init__(self) -> None:
        self.corrections: dict[tuple[str, date], Decimal] = {}
        self.failing_bars: set[str] = set()
        self.unknown_tickers: set[str] = set()
        self.metadata_auth_failure = False

    def polygon_client_class(self) -> type:
        harness = self

        class _Client:
            def __init__(self, _settings: Any) -> None:
                pass

            def __enter__(self) -> _Client:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def fetch_daily_bars(self, request: DailyBarRequest) -> list[DailyBar]:
                if request.symbol in harness.failing_bars:
                    raise PolygonAuthError("simulated 401")
                bars = []
                for session_date in sessions_between(request.from_date, request.to_date):
                    close = harness.corrections.get(
                        (request.symbol, session_date), rising_close(request.symbol, session_date)
                    )
                    bars.append(
                        DailyBar(
                            symbol=request.symbol,
                            session_date=session_date,
                            open=close,
                            high=close + 1,
                            low=close - 1,
                            close=close,
                            volume=1000,
                            adjusted=request.adjusted,
                            provider=request.provider,
                        )
                    )
                return bars

        return _Client

    def overview(self, ticker: str, _settings: Any) -> dict[str, Any] | None:
        if self.metadata_auth_failure:
            raise PolygonAuthError("simulated 401")
        if ticker in self.unknown_tickers:
            return None
        return {
            "name": f"{ticker} Fake Inc.",
            "market": "stocks",
            "locale": "us",
            "primary_exchange": "XNAS",
            "type": "CS",
            "active": True,
            "list_date": "2000-01-03",
        }


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
            existing = set(
                session.execute(
                    select(DailyBarModel.session_date).where(DailyBarModel.symbol_id == symbol.id)
                ).scalars()
            )
            for session_date in session_dates:
                if session_date in existing:
                    continue
                close = rising_close(ticker, session_date)
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
    # The other registered strategies keep their shipped configuration (E1, E13 use a second one).
    for shipped in load_settings().paths.strategy_config_dir.glob("*.yaml"):
        (directory / shipped.name).write_text(shipped.read_text())
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
        monkeypatch.setenv("TRADING_PLATFORM_MARKET_DATA__POLYGON__API_KEY", "fake-polygon-key")  # pragma: allowlist secret
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

        # The calendar-state read (R4) has its own server-time seam.
        monkeypatch.setattr(market_data_route, "_now", harness_now)

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
        market = HarnessMarketData()
        monkeypatch.setattr(ingestion_module, "PolygonClient", market.polygon_client_class())
        monkeypatch.setattr(symbol_metadata_sync_module, "fetch_ticker_overview", market.overview)

        with TestClient(create_app()) as client:
            env = Phase201Env(
                client=client, broker=broker, price=price, market=market, clock_cell=cell
            )
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
    assert persisted["persisted_state"] == "paused"  # nothing wrote
    assert persisted["persisted_reason"] == "working_order_commitments_unaccounted"
    assert persisted["state"] == "terminated" and persisted["reason"] == "execution_window_elapsed"
    assert persisted["will_end"] is True and persisted["window"] == "elapsed"

    # (b) run time: a Continue accepted after the cutoff ends the operation in the WORKER, which
    # reads the same patched time (the run-time window check) and persists the termination.
    touched = env.run_continue(operation_id)
    assert touched["status"] == "succeeded", touched.get("failure_message")
    after = env.operation(operation_id)
    assert after["persisted_state"] == "terminated"
    assert after["persisted_reason"] == "execution_window_elapsed"
    assert env.broker.post_count == 1  # nothing was sent past the window


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



# ---------------------------------------------------------------------------
# Scenarios E1 - E8
# ---------------------------------------------------------------------------


def account_run_row(run_id: str) -> dict[str, Any]:
    """The persisted account-scope reconciliation run (divergence, findings, classification)."""

    from trading_platform.db.models import AccountReconciliationRun

    with session_scope(load_settings()) as session:
        run = session.get(AccountReconciliationRun, uuid.UUID(run_id))
        assert run is not None
        return {
            "status": str(run.status),
            "blocks_execution": run.blocks_execution,
            "finding_count": run.finding_count,
            "findings": list(run.findings or []),
            "account_divergence": dict(run.account_divergence or {}),
            "unexplained_exposure": dict(run.unexplained_exposure or {}),
            "classification_summary": dict(run.classification_summary or {}),
        }


def check_ids(body: dict[str, Any], *, passed: bool) -> list[str]:
    return [c["id"] for c in body["checks"] if c["passed"] is passed]


def test_e1_first_start_from_no_owner(phase201_env: Phase201Env) -> None:
    """E1 | Requirements: [PAPER-01, PAPER-02, ACCT-01, COR-01]

    Decisions: D-03 (typed ownership refusals), D-04 (seeding control, new owner disabled),
    D-06 (account-scope Jobs with no owner), D-27 (COR-01 replay of 29 Sep).

    SPEC CONFLICT (reported): 05 E1 names ``strategy_not_active_paper_strategy`` for the
    no-owner start; CONTEXT D-03 and 04 P20.1-01 name ``no_active_paper_strategy``, which is
    asserted here; the second code is asserted once another strategy owns the account.
    """

    env = phase201_env
    env.expect_posts(1)  # only the final owner session sends

    # M10 with no owner -> no_active_paper_strategy (also no owner row to enable).
    refused = env.start_session()
    assert env.conflict_code(refused) == "no_active_paper_strategy"
    # the read-only owner view: null owner and the blocker.
    view = env.get("/api/v1/controls/active-paper-strategy").json()
    assert view["strategy_id"] is None
    assert "no_active_paper_strategy" in view["trading_blocked_reasons"]

    # A strategy enabled BEFORE it owns anything stays enabled until seeded (then disabled).
    assert env.set_strategy_status(STRATEGY, "enabled").status_code == 200

    # M6 before M4/M5: the account is not proven -> check_failed:A6 (no fresh reconciliation).
    early = env.put_owner(STRATEGY)
    assert early.status_code == 409
    assert early.json()["detail"]["code"] == "check_failed:A6"
    assert "A6" in early.json()["detail"]["failed_checks"]
    assert env.get("/api/v1/controls/active-paper-strategy").json()["strategy_id"] is None

    # M4 + M5 with NO owner (account scope): the broker account has buying power 4x cash (COR-01).
    env.broker.set_account(cash=100000, buying_power=400000, equity=100000)
    synced, reconciled = env.settle("account")
    assert synced["status"] == "succeeded" and reconciled["status"] == "succeeded"
    assert reconciled["result_summary"]["blocks_execution"] is False

    # M6 now seeds; the new owner is DISABLED even though it was enabled before.
    seeded = env.put_owner(STRATEGY)
    assert seeded.status_code == 200, seeded.text
    body = seeded.json()
    assert body["kind"] == "seeding" and body["changed"] is True
    assert body["active_paper_strategy"]["strategy_id"] == STRATEGY
    assert env.get(f"/api/v1/controls/strategies/{STRATEGY}").json()["status"] == "disabled"

    # M7 enables it.
    enabled = env.set_strategy_status(STRATEGY, "enabled")
    assert enabled.status_code == 200 and enabled.json()["status"] == "enabled"

    # COR-01 replay of 29 Sep: evaluate (persists its own derived snapshot), then a FRESH account
    # reconciliation shows no account_divergence.
    env.evaluate()
    fresh = env.reconcile()
    assert fresh["result_summary"]["blocks_execution"] is False
    run = account_run_row(fresh["result_summary"]["run_id"])
    assert run["account_divergence"] == {} and run["blocks_execution"] is False

    # A non-owner is refused with the second D-03 code.
    other = env.submit_job(
        "paper-session",
        {"strategy_id": OTHER_STRATEGY, "as_of_session": EVAL_SESSION.isoformat(), "risk_run_id": None},
    )
    assert env.conflict_code(other) == "strategy_not_active_paper_strategy"

    # The owner's own session is accepted and sends exactly one order (the first candidate).
    session_job = env.run_session()
    assert session_job["status"] == "succeeded"
    assert operation_of(session_job)["state"] == "paused"
    assert env.broker.post_count == 1


def test_e2_29_sep_carry_over(phase201_env: Phase201Env) -> None:
    """E2 | Requirements: [REC-01, ACCT-01, COR-05]

    Decisions: D-12/D-14 (recovery predicate), E-5 (nothing_submitted needs execution-path
    evidence), D-27 (a fresh clean account reconciliation resolves).
    """

    env = phase201_env
    env.expect_posts(0)
    ids = seed_29_sep_jobs()

    view = env.get("/api/v1/controls/active-paper-strategy").json()
    assert view["strategy_id"] is None
    a5 = next(c for c in view["checks"] if c["id"] == "A5")
    assert a5["passed"] is False and a5["reason_code"] == "unresolved_outcome"
    job_refs = {ref["id"] for ref in a5["evidence_refs"] if ref["kind"] == "job"}
    assert job_refs == {str(job_id) for job_id in ids}
    assert view["trading_blocked_reasons"] == ["no_active_paper_strategy"]

    for job_id in ids:
        recovery = env.get(f"/api/v1/jobs/{job_id}/recovery")
        assert recovery.status_code == 200, recovery.text
        body = recovery.json()
        assert body["resolved"] is False and body["gate_code"] == "reconciliation_required"
        assert body["intents"], body
        assert {i["classification"] for i in body["intents"]} == {"nothing_submitted"}
        assert all(i["resubmission_permitted"] is False for i in body["intents"])
    with session_scope(load_settings()) as session:
        linked = session.execute(
            select(func.count()).select_from(StrategyRun).where(StrategyRun.job_id.in_(ids))
        ).scalar_one()
    assert linked == 0  # no paper_execution run was ever linked

    # A fresh clean account-scope reconciliation resolves the carry-over.
    fresh = env.reconcile()
    assert fresh["result_summary"]["blocks_execution"] is False
    after = env.get("/api/v1/controls/active-paper-strategy").json()
    assert next(c for c in after["checks"] if c["id"] == "A5")["passed"] is True
    for job_id in ids:
        body = env.get(f"/api/v1/jobs/{job_id}/recovery").json()
        assert body["resolved"] is True and body["gate_code"] is None



def test_e3_session_with_one_working_order(phase201_env: Phase201Env) -> None:
    """E3 | Requirements: [REC-02]

    Decisions: D-17 (one accepted order pauses the operation, TL-1/TL-2), D-19 (Continue gates),
    D-26 (original client_order_id, never a resend), S2-R3 (fresh price per send).

    SPEC CONFLICT (reported): 05 E3 expects ``awaiting_reconciliation`` for a fill WITHOUT a sync;
    the platform cannot know about the fill without a sync, so the order is still locally
    non-terminal and the refusal is ``working_order_commitments_unaccounted``;
    ``awaiting_reconciliation`` is asserted after the account sync and BEFORE the reconciliation.
    OBSERVATION (reported): an ACCOUNT-scope sync never creates a Position, so after a filled owned
    order the sync that lets the reconciliation come out clean is the OWNER-scope one (05 M4 allows
    "account or owner").
    """

    env = phase201_env
    env.own()
    env.expect_posts(2)  # intent 1 on start, intent 2 on Continue
    env.evaluate()

    started = env.run_session()
    assert started["status"] == "succeeded" and started["outcome"] == "paused"
    operation = operation_of(started)
    assert (operation["state"], operation["reason"]) == (
        "paused",
        "working_order_commitments_unaccounted",
    )
    assert operation["next_action"] == "wait_for_order_then_sync_and_continue"
    operation_id = operation["id"]
    intents = intents_of(env, operation_id)
    first_cid = intents["AAPL"]["client_order_id"]
    second_cid = intents["MSFT"]["client_order_id"]
    assert intents["AAPL"]["state"] == "submitted"
    assert {intents["MSFT"]["state"], intents["NVDA"]["state"]} <= {"planned", "registered_unsent"}
    assert env.broker.post_count == 1 and env.broker.posts_for(first_cid) == 1

    # Continue before the order is terminal -> refused.
    early = env.continue_submit(operation_id)
    assert env.conflict_code(early) == "working_order_commitments_unaccounted"

    # The order fills at the broker; without a sync the platform still sees it working.
    env.broker.fill_order(first_cid)
    unsynced = env.continue_submit(operation_id)
    assert env.conflict_code(unsynced) == "working_order_commitments_unaccounted"

    # Account sync only: the order is terminal locally, but no reconciliation follows it yet.
    assert env.sync()["status"] == "succeeded"
    awaiting = env.continue_submit(operation_id)
    assert env.conflict_code(awaiting) == "awaiting_reconciliation"
    assert awaiting.json()["detail"]["operation_id"] == operation_id

    # Owner sync (positions) + standalone reconciliation: Continue is accepted and sends intent 2.
    synced, reconciled = env.settle()
    assert reconciled["result_summary"]["blocks_execution"] is False
    continued = env.run_continue(operation_id)
    assert continued["status"] == "succeeded", continued.get("failure_message")
    assert env.broker.post_count == 2
    assert env.broker.post_client_order_ids == [first_cid, second_cid]  # ORIGINAL id of intent 2
    assert env.broker.posts_for(first_cid) == 1  # intent 1 is never POSTed again
    states = intent_states_of(env, operation_id)
    assert states["AAPL"] == "submitted" and states["MSFT"] == "submitted"
    assert states["NVDA"] in {"planned", "registered_unsent"}
    assert operation_of(continued)["reason"] == "working_order_commitments_unaccounted"


def test_e3_no_price_observation_pauses_price_unavailable_with_zero_posts(
    phase201_env: Phase201Env,
) -> None:
    """E3 | Requirements: [REC-02]

    Companion case of S2-R3 (round 3): without a fresh latest-trade observation the operation
    pauses ``price_unavailable`` and sends NOTHING (the price seam returns no observation).
    """

    env = phase201_env
    env.own()
    env.expect_posts(0)
    env.evaluate()
    env.price.available = False
    job = env.run_session()
    operation = operation_of(job)
    assert (operation["state"], operation["reason"]) == ("paused", "price_unavailable")
    assert set(intent_states_of(env, operation["id"]).values()) <= {"planned", "registered_unsent"}
    assert env.broker.post_count == 0


def test_e4_immediately_filled_order(phase201_env: Phase201Env) -> None:
    """E4 | Requirements: [REC-02]

    Decisions: D-17/TL-1 (an order already filled in the POST response still pauses: its effects
    are not synced), D-19 (Continue after sync + clean reconciliation).

    LIMITATION PINNED (a decision is pending, reported in the SUMMARY): the account-scope sync
    alone leaves the broker position untracked locally (it never creates a Position, D-09), so the
    account reconciliation is blocking (MISSING_LOCAL) and a Continue run pauses
    ``reconciliation_blocking``, although 05 M4 / procedure 2 say "account or owner" sync. The
    stall assertion below documents CURRENT behaviour, not a desired invariant; if the product
    decision changes it, update this test with that decision. After the owner-scope sync the
    reconciliation is clean and Continue sends the next intent.
    """

    env = phase201_env
    env.own()
    env.expect_posts(2)
    env.broker.default_status = "filled"
    env.evaluate()

    started = env.run_session()
    operation = operation_of(started)
    assert operation["state"] == "paused", operation
    operation_id = operation["id"]
    assert env.broker.post_count == 1
    assert intent_states_of(env, operation_id)["AAPL"] == "submitted"

    # Account-scope sync + reconciliation: the position is not tracked locally -> blocking.
    assert env.sync()["status"] == "succeeded"
    blocked = env.reconcile()
    assert blocked["result_summary"]["blocks_execution"] is True
    assert account_run_row(blocked["result_summary"]["run_id"])["findings"][0]["event_type"] == (
        "MISSING_LOCAL"
    )
    stalled = env.run_continue(operation_id)
    assert operation_of(stalled)["state"] == "paused"
    assert operation_of(stalled)["reason"] in {"reconciliation_blocking", "unrecognized_broker_activity"}
    assert env.broker.post_count == 1

    # Owner-scope sync + clean reconciliation -> Continue proceeds with intent 2.
    synced, reconciled = env.settle()
    assert reconciled["result_summary"]["blocks_execution"] is False
    continued = env.run_continue(operation_id)
    assert continued["status"] == "succeeded", continued.get("failure_message")
    assert env.broker.post_count == 2
    assert intent_states_of(env, operation_id)["MSFT"] == "submitted"


def test_e4_no_price_observation_pauses_price_unavailable_with_zero_posts(
    phase201_env: Phase201Env,
) -> None:
    """E4 | Requirements: [REC-02]

    Companion case of S2-R3: a filled-order scenario with no fresh observation pauses
    ``price_unavailable`` with zero POSTs; once a fresh price is back the same pinned intent is
    sent exactly once (PD-1).
    """

    env = phase201_env
    env.own()
    env.expect_posts(1)
    env.broker.default_status = "filled"
    env.evaluate()
    env.price.available = False
    job = env.run_session()
    operation = operation_of(job)
    assert (operation["state"], operation["reason"]) == ("paused", "price_unavailable")
    assert env.broker.post_count == 0
    first_cid = intents_of(env, operation["id"])["AAPL"]["client_order_id"]

    # Continue is an M11: it needs a standalone reconciliation first, and every other check.
    env.price.available = True
    assert env.conflict_code(env.continue_submit(operation["id"])) == "awaiting_reconciliation"
    assert env.reconcile()["result_summary"]["blocks_execution"] is False
    again = env.run_continue(operation["id"])
    assert again["status"] == "succeeded", again.get("failure_message")
    assert env.broker.post_client_order_ids == [first_cid]


@pytest.mark.parametrize("variant", ["pass", "cash_shortfall"])
def test_e5_earlier_fill_changes_the_portfolio(phase201_env: Phase201Env, variant: str) -> None:
    """E5 | Requirements: [REC-02, PROV-01]

    Decisions: D-25 (the pinned run's manifest is verified again at every Continue; an earlier
    fill is NOT a changed evaluation input), D-17/D-26 (risk is re-checked on the unchanged intent,
    never re-planned), S2-R3 (fresh price).
    """

    env = phase201_env
    env.own()
    env.expect_posts(2 if variant == "pass" else 1)
    env.evaluate()
    env.broker.default_status = "filled"
    started = env.run_session()
    operation_id = operation_of(started)["id"]
    before = intents_of(env, operation_id)
    risk_run_before = env.operation(operation_id)["risk_run_id"]
    first = before["AAPL"]["client_order_id"]
    assert env.broker.post_client_order_ids == [first]

    if variant == "cash_shortfall":
        env.broker.set_account(cash=10, buying_power=10)
    synced, reconciled = env.settle()
    assert reconciled["result_summary"]["blocks_execution"] is False

    continued = env.run_continue(operation_id)
    assert continued["status"] == "succeeded", continued.get("failure_message")
    operation = operation_of(continued)
    after = intents_of(env, operation_id)

    if variant == "pass":
        # intent 2 is sent UNCHANGED; no re-evaluation was requested because of the fill.
        assert operation["state"] != "requires_reevaluation"
        assert after["MSFT"]["state"] == "submitted"
        for key in ("symbol", "side", "quantity", "client_order_id", "reference_price"):
            assert after["MSFT"][key] == before["MSFT"][key]
        assert env.broker.post_client_order_ids == [first, before["MSFT"]["client_order_id"]]
        # the SAME pinned evaluation (manifest still matching): no re-evaluation happened.
        assert env.operation(operation_id)["risk_run_id"] == risk_run_before
    else:
        assert operation["state"] == "requires_reevaluation"
        assert operation["reason"] == "risk_limit_failed:insufficient_cash"
        assert env.broker.post_count == 1  # nothing further was sent
        # identities unchanged, no re-plan: the unsent intents are exactly as planned.
        for symbol in ("MSFT", "NVDA"):
            assert after[symbol]["client_order_id"] == before[symbol]["client_order_id"]
            assert after[symbol]["quantity"] == before[symbol]["quantity"]
            assert after[symbol]["state"] in {"planned", "registered_unsent"}
        assert set(after) == set(before)


def end_operation(env: Phase201Env, operation_id: str, reason: str = "e2e end") -> dict[str, Any]:
    response = env.post(f"/api/v1/execution-operations/{operation_id}/end", {"reason": reason})
    assert response.status_code == 200, response.text
    return response.json()


def ingest_bars(env: Phase201Env, ticker: str, session_date: date) -> dict[str, Any]:
    return env.run_job(
        "ingest-bars",
        {
            "from_date": session_date.isoformat(),
            "to_date": session_date.isoformat(),
            "symbols": [ticker],
        },
    )


def paused_after_one_fill(env: Phase201Env) -> tuple[str, dict[str, dict[str, Any]]]:
    """Own the strategy, evaluate, start a session whose first order fills immediately, then
    synchronize (owner sync + clean standalone reconciliation). Returns the paused operation id
    and its intents."""

    env.own()
    env.broker.default_status = "filled"
    env.evaluate()
    started = env.run_session()
    operation_id = operation_of(started)["id"]
    before = intents_of(env, operation_id)
    _, reconciled = env.settle()
    assert reconciled["result_summary"]["blocks_execution"] is False
    return operation_id, before


@pytest.mark.parametrize("variant", ["corrected_close", "identical_reingest"])
def test_e6_data_correction(phase201_env: Phase201Env, variant: str) -> None:
    """E6 | Requirements: [PROV-01, REC-02]

    Decisions: D-25 (evaluation manifest verified at every Continue), D-19 (a data change makes
    ``requires_reevaluation`` with nothing sent), D-12 (M12 End leaves unsent intents
    ``cancelled_unsent``), S3-R4 (a new start after earlier orders needs sync, a clean standalone
    reconciliation and a NEW evaluation).
    """

    env = phase201_env
    env.market.corrections[("AAPL", EVAL_SESSION)] = Decimal("125")
    if variant == "identical_reingest":
        env.market.corrections.clear()
    # both variants end with exactly two orders: the first filled one and one more (intent 2 on
    # Continue, or the justified MSFT action of the new session).
    env.expect_posts(2)
    operation_id, before = paused_after_one_fill(env)
    first_cid = before["AAPL"]["client_order_id"]

    ingest = ingest_bars(env, "AAPL", EVAL_SESSION)  # applies the (possibly identical) provider data
    assert ingest["status"] == "succeeded", ingest.get("failure_message")

    continued = env.run_continue(operation_id)
    assert continued["status"] == "succeeded", continued.get("failure_message")
    operation = operation_of(continued)

    if variant == "identical_reingest":
        assert operation["state"] != "requires_reevaluation"
        assert env.broker.post_count == 2
        assert intents_of(env, operation_id)["MSFT"]["state"] == "submitted"
        return

    assert (operation["state"], operation["reason"]) == (
        "requires_reevaluation",
        "evaluation_data_changed",
    )
    assert env.broker.post_count == 1  # nothing was sent after the correction
    # M12 End: the unsent intents are cancelled_unsent; the filled order is untouched.
    ended = end_operation(env, operation_id)
    assert ended["state"] == "terminated" or ended.get("operation", {}).get("state") == "terminated"
    states = intent_states_of(env, operation_id)
    assert states["AAPL"] == "submitted"
    assert states["MSFT"] == "cancelled_unsent" and states["NVDA"] == "cancelled_unsent"

    # M8 -> M10 on the corrected data. S3-R4: an evaluation made after the sync but WITHOUT a
    # standalone reconciliation in between is not a verified basis.
    env.sync("strategy", strategy_id=STRATEGY, as_of_session=EVAL_SESSION.isoformat())
    env.evaluate()
    unverified = env.start_session()
    assert env.conflict_code(unverified) == "evaluation_basis_unverified"
    assert unverified.json()["detail"]["reason"] == "reconciliation_missing"
    # the full post-execution synchronization, THEN the new evaluation, is accepted.
    env.settle()
    env.evaluate()
    fresh = env.run_session()
    assert fresh["status"] == "succeeded", fresh.get("failure_message")
    # TL-10: the action that already reached the broker (AAPL buy) is not sent again; the
    # never-sent MSFT action is justified on the verified basis and goes out.
    assert [env.broker.order_for(cid)["symbol"] for cid in env.broker.post_client_order_ids] == [
        "AAPL",
        "MSFT",
    ]
    assert env.broker.post_count == 2
    assert env.broker.posts_for(first_cid) == 1



def recovery_of(env: Phase201Env, job_id: str) -> dict[str, Any]:
    response = env.get(f"/api/v1/jobs/{job_id}/recovery")
    assert response.status_code == 200, response.text
    return response.json()


def ambiguous_session(env: Phase201Env, behavior: str = "read_timeout_after_create") -> tuple[
    dict[str, Any], str, str, str
]:
    """Own, evaluate and start a session whose first POST times out. Returns the failed Job, the
    operation id, the in-doubt intent's client_order_id and its paper-order (recovery intent) id."""

    env.own()
    env.evaluate()
    env.broker.script_post(behavior)
    job = env.run_session()
    assert job["status"] == "failed" and job["outcome_uncertain"] is True
    assert "AmbiguousOrderSubmissionError" in job["failure_message"]
    recovery = recovery_of(env, job["id"])
    assert len(recovery["intents"]) == 1
    intent = recovery["intents"][0]
    operation = env.get(f"/api/v1/execution-operations?strategy_id={STRATEGY}").json()["items"][0]
    return job, operation["operation_id"], intent["client_order_id"], intent["intent_id"]


def test_e7_ambiguous_submission_order_found(phase201_env: Phase201Env) -> None:
    """E7 | Requirements: [COR-06, REC-01]

    Decisions: D-12 (one attempt, an ambiguous POST is never retried), D-15 (every fresh
    submission and every OPS-07 retry is refused while an outcome is unresolved), D-14 (a found
    order resolves by sync + a clean standalone reconciliation, with NO broker statement), D-26
    (the intent is never POSTed again).
    """

    env = phase201_env
    env.expect_posts(2)  # the timed-out POST, then intent 2 after the resolution
    job, operation_id, first_cid, intent_id = ambiguous_session(env)
    assert env.broker.post_count == 1 and attempt_classes(first_cid) == ["ambiguous"]
    assert intents_of(env, operation_id)["AAPL"]["state"] == "ambiguous"
    recovery = recovery_of(env, job["id"])
    assert recovery["resolved"] is False and recovery["gate_code"] == "outcome_unresolved"
    assert recovery["intents"][0]["resubmission_permitted"] is False
    assert recovery["intents"][0]["classification"] == "not_found"  # nothing synced yet

    # A fresh paper-session submission and an OPS-07 retry are refused.
    fresh = env.start_session()
    assert env.conflict_code(fresh) == "outcome_unresolved"
    retried = env.post(f"/api/v1/jobs/{job['id']}/retry", key="retry-e7")
    assert retried.status_code == 409 and detail_code(retried) == "outcome_unresolved"
    assert env.broker.post_count == 1

    # The broker did create the order, and it fills; the account sync FINDS it.
    env.broker.fill_order(first_cid)
    assert env.sync()["status"] == "succeeded"
    found = recovery_of(env, job["id"])["intents"][0]
    assert found["classification"] == "found_verified" and found["broker_state"] == "filled"
    assert found["statement"] is None  # no broker statement was ever involved
    assert recovery_of(env, job["id"])["gate_code"] == "reconciliation_required"

    # Owner sync (positions) + a clean standalone reconciliation resolves it.
    _, reconciled = env.settle()
    assert reconciled["result_summary"]["blocks_execution"] is False
    resolved = recovery_of(env, job["id"])
    assert resolved["resolved"] is True and resolved["gate_code"] is None
    aps = env.get("/api/v1/controls/active-paper-strategy").json()
    assert next(c for c in aps["checks"] if c["id"] == "A5")["passed"] is True

    # The order is never re-POSTed; the operation resumes with the NEXT intent only.
    continued = env.run_continue(operation_id)
    assert continued["status"] == "succeeded", continued.get("failure_message")
    assert env.broker.posts_for(first_cid) == 1 and env.broker.post_count == 2
    assert intent_states_of(env, operation_id)["AAPL"] == "submitted"
    assert intent_states_of(env, operation_id)["MSFT"] == "submitted"


def absence_evidence_over_grace(env: Phase201Env, job_id: str) -> dict[str, Any]:
    """Two syncs a grace period apart while the order is never visible (validity evidence)."""

    grace = load_settings().execution.recovery_absence_grace_seconds
    assert env.sync()["status"] == "succeeded"
    env.advance(seconds=grace + 1)
    assert env.sync()["status"] == "succeeded"
    return recovery_of(env, job_id)["intents"][0]


def test_e8_ambiguous_submission_order_never_found(phase201_env: Phase201Env) -> None:
    """E8 | Requirements: [COR-06, REC-01, REC-02]

    Decisions: D-14/TL-4 (absence evidence, elapsed time, session close and End never resolve an
    in-doubt intent; there is no product-level release; W-1 declined), D-12 (End cancels unsent
    intents only and never a broker order), J-2 (zero cancel calls).
    """

    env = phase201_env
    env.expect_posts(1)
    job, operation_id, first_cid, intent_id = ambiguous_session(env, "read_timeout_without_create")
    assert env.broker.post_count == 1 and env.broker.find(first_cid) is None  # never created

    # Absence evidence during the validity window (clock inside it, grace period elapsed).
    intent = absence_evidence_over_grace(env, job["id"])
    assert intent["classification"] == "not_found" and intent["blocking"] is True
    assert intent["absence_evidence"], intent  # items a-d with timestamps
    package = recovery_of(env, job["id"])["evidence_package"][0]
    assert package["attempts"][0]["outcome_class"] == "ambiguous"
    assert package["lookup_results"] or package["scan_results"]
    assert env.conflict_code(env.start_session()) == "outcome_unresolved"

    # M12 End (inside the window): terminated, the intent stays ambiguous and listed everywhere.
    ended = end_operation(env, operation_id)
    assert ended["state"] == "terminated" and ended["reason"] == "cancelled_by_operator"
    assert intent_states_of(env, operation_id)["AAPL"] == "ambiguous"
    listed = env.operation(operation_id)
    assert [i["intent_id"] for i in listed["unresolved_intents"]] == [
        intents_of(env, operation_id)["AAPL"]["intent_id"]
    ]
    assert recovery_of(env, job["id"])["intents"][0]["classification"] == "not_found"
    assert env.conflict_code(env.start_session()) == "outcome_unresolved"

    # After the session close (the clock moved to the next trading day, a NEW evaluation exists):
    # absence evidence keeps accumulating and nothing resolves; every fresh submission is 409.
    evaluation_session = env.next_trading_day()
    assert env.sync()["status"] == "succeeded"
    after_close = recovery_of(env, job["id"])
    assert after_close["resolved"] is False and after_close["gate_code"] == "outcome_unresolved"
    env.evaluate(evaluation_session)
    assert env.conflict_code(env.start_session(evaluation_session)) == "outcome_unresolved"
    assert intent_states_of(env, operation_id)["AAPL"] == "ambiguous"
    assert env.operation(operation_id)["unresolved_intents"]
    # Nothing in the API resolves it: there is no cancel/withdraw route, and no cancel call happened.
    assert env.broker.cancel_calls == 0 and env.broker.post_count == 1


def record_statement(env: Phase201Env, intent_id: str, statement: str = "not_received") -> httpx.Response:
    return env.post(
        f"/api/v1/recovery/intents/{intent_id}/broker-statement",
        {
            "statement": statement,
            "reference": "ticket-e2e-1",
            "reason": "broker support answered in writing",
        },
    )


def test_e8_ambiguous_submission_order_never_found_statement_while_open_is_evidence_only(
    phase201_env: Phase201Env,
) -> None:
    """E8 | Requirements: [COR-06, REC-01, REC-02]

    Variant A (round 5, 2026-10-04): a ``not_received`` broker statement recorded while the
    operation is open, with complete absence evidence and the executing worker terminated, is
    audited EVIDENCE only: the intent stays unresolved, Continue and a new evaluation with changed
    data are 409 outcome_unresolved, a handover is refused with A5 among the failed checks (A1 is
    named first while the operation is open), and nothing is resent. ``previous_executor_terminated``
    no longer exists (an unknown key is rejected).
    """

    env = phase201_env
    env.expect_posts(1)
    job, operation_id, first_cid, intent_id = ambiguous_session(env, "read_timeout_without_create")
    intent = absence_evidence_over_grace(env, job["id"])
    assert intent["absence_evidence_complete"] is True

    recorded = record_statement(env, intent_id)
    assert recorded.status_code == 200, recorded.text
    after = recovery_of(env, job["id"])
    assert after["resolved"] is False and after["gate_code"] == "outcome_unresolved"
    assert after["intents"][0]["statement"] is not None  # attached as evidence
    assert after["intents"][0]["blocking"] is True
    assert after["intents"][0]["resubmission_permitted"] is False
    assert after["evidence_package"][0]["statement_recorded"] is True
    assert intent_states_of(env, operation_id)["AAPL"] == "ambiguous"

    # the removed round-3 key is an unknown key now.
    stale = env.post(
        f"/api/v1/recovery/intents/{intent_id}/broker-statement",
        {
            "statement": "not_received",
            "reference": "t",
            "reason": "r",
            "previous_executor_terminated": True,
        },
    )
    assert stale.status_code in (400, 422), stale.text

    # Continue -> 409 outcome_unresolved.
    assert env.conflict_code(env.continue_submit(operation_id)) == "outcome_unresolved"
    # A new evaluation with changed data does not release it either.
    env.market.corrections[("MSFT", EVAL_SESSION)] = Decimal("200")
    assert ingest_bars(env, "MSFT", EVAL_SESSION)["status"] == "succeeded"
    env.evaluate()
    assert env.conflict_code(env.start_session()) == "outcome_unresolved"
    # A handover is refused: A1 named first (operation open), A5 among the failures.
    handover = env.put_owner(OTHER_STRATEGY)
    assert handover.status_code == 409
    detail = handover.json()["detail"]
    assert detail["code"] == "check_failed:A1" and "A5" in detail["failed_checks"]
    assert env.broker.post_count == 1 and env.broker.posts_for(first_cid) == 1


def test_e8_ambiguous_submission_order_never_found_statement_after_end_does_not_resend(
    phase201_env: Phase201Env,
) -> None:
    """E8 | Requirements: [COR-06, REC-01, REC-02]

    Variant B: End first, then the ``not_received`` statement (allowed after the operation ends):
    the intent is still unresolved, nothing is resent, and a fresh session after a NEW evaluation
    is still 409 outcome_unresolved.
    """

    env = phase201_env
    env.expect_posts(1)
    job, operation_id, first_cid, intent_id = ambiguous_session(env, "read_timeout_without_create")
    absence_evidence_over_grace(env, job["id"])
    ended = end_operation(env, operation_id)
    assert ended["state"] == "terminated"

    recorded = record_statement(env, intent_id)
    assert recorded.status_code == 200, recorded.text
    after = recovery_of(env, job["id"])
    assert after["resolved"] is False and after["intents"][0]["statement"] is not None
    assert intent_states_of(env, operation_id)["AAPL"] == "ambiguous"
    assert env.operation(operation_id)["unresolved_intents"]

    env.evaluate()
    assert env.conflict_code(env.start_session()) == "outcome_unresolved"
    assert env.broker.post_count == 1 and env.broker.posts_for(first_cid) == 1


def test_e8_ambiguous_submission_order_never_found_late_order_found_resolves(
    phase201_env: Phase201Env,
) -> None:
    """E8 | Requirements: [COR-06, REC-01, REC-02]

    Variant C: the order existed all along but was invisible; when the fake broker later shows it
    by client_order_id the sync classifies it found_verified, a clean standalone reconciliation
    resolves the outcome, and only then is a fresh session allowed (after a new evaluation, other
    checks passing). No second order is ever sent for the intent.
    """

    env = phase201_env
    env.expect_posts(2)  # the timed-out POST, then the justified MSFT action of the new session
    job, operation_id, first_cid, intent_id = ambiguous_session(env, "read_timeout_after_create")
    env.broker.hide_order(first_cid)
    absence_evidence_over_grace(env, job["id"])
    assert recovery_of(env, job["id"])["intents"][0]["classification"] == "not_found"
    end_operation(env, operation_id)
    assert env.conflict_code(env.start_session()) == "outcome_unresolved"

    # The broker now shows the order (and it has filled).
    env.broker.clear_hidden()
    env.broker.fill_order(first_cid)
    synced = env.sync("strategy", strategy_id=STRATEGY, as_of_session=EVAL_SESSION.isoformat())
    assert synced["status"] == "succeeded"
    found = recovery_of(env, job["id"])
    assert found["intents"][0]["classification"] == "found_verified"
    assert found["resolved"] is False and found["gate_code"] == "reconciliation_required"
    reconciled = env.reconcile()
    assert reconciled["result_summary"]["blocks_execution"] is False
    resolved = recovery_of(env, job["id"])
    assert resolved["resolved"] is True and resolved["gate_code"] is None

    # A fresh session needs a NEW evaluation on the verified basis; then it is allowed.
    env.evaluate()
    fresh = env.run_session()
    assert fresh["status"] == "succeeded", fresh.get("failure_message")
    assert env.broker.posts_for(first_cid) == 1 and env.broker.post_count == 2
    assert [env.broker.order_for(cid)["symbol"] for cid in env.broker.post_client_order_ids] == [
        "AAPL",
        "MSFT",
    ]


def test_e8_no_withdrawal_route(phase201_env: Phase201Env) -> None:
    """E8 | Requirements: [COR-06, REC-01, REC-02]

    W-1 declined: no route of the application has 'withdraw' in its path (and no cancel path of a
    broker order exists), so no product-level release of an in-doubt intent can be reached.
    """

    env = phase201_env
    env.expect_posts(0)
    paths = [getattr(route, "path", "") for route in env.client.app.routes]
    assert paths and not [path for path in paths if "withdraw" in path.lower()]
    assert not [path for path in paths if "cancel" in path and "orders" in path]



# ---------------------------------------------------------------------------
# Scenarios E9 - E15
# ---------------------------------------------------------------------------

PAPER_BOOK_TABLES = ("paper_orders", "paper_fills", "positions", "active_paper_strategy")


def test_e9_external_activity(phase201_env: Phase201Env) -> None:
    """E9 | Requirements: [EXT-01, COR-05, ACCT-01]

    Decisions: D-10 (external orders must be terminal and net flat to be recorded), D-05
    (unrecognized activity blocks until recorded), D-27 (recording writes no order, fill, position
    or ownership and ends with a FRESH account check in the same Job).

    SPEC CONFLICT (reported): the plan text names a submit-time 409 ``reconciliation_not_clean``
    for the start; that code is a RECOVERY gate code and applies only while an uncertain outcome
    exists (D-15). With unrecognized activity and no uncertain outcome the start is accepted
    (202) and REFUSED AT RUN TIME: the Job's in-session reconciliation blocks it
    (``blocked_reconciliation``), no operation is created and nothing is sent. Asserted as it is.
    """

    env = phase201_env
    env.expect_posts(1)  # only the final allowed session sends
    env.own()
    env.evaluate()

    bought = env.broker.inject_external_order("AAPL", "buy", 5, "filled", price=100)
    sold = env.broker.inject_external_order("AAPL", "sell", 5, "filled", price=101)
    still_open = env.broker.inject_external_order("MSFT", "buy", 3, "new")

    # M5: the unrecognized items are reported, with origin tags, and the reconciliation blocks.
    reconciled = env.reconcile()
    assert reconciled["result_summary"]["blocks_execution"] is True
    run = account_run_row(reconciled["result_summary"]["run_id"])
    assert run["blocks_execution"] is True
    assert run["classification_summary"]["orders"]["unrecognized"] == 3
    assert "origin_tags" in run["classification_summary"]
    aps = env.get("/api/v1/controls/active-paper-strategy").json()
    assert next(c for c in aps["checks"] if c["id"] == "A4")["reason_code"] == "unrecognized_items"

    # M10 is refused at run time: blocked_reconciliation, zero POSTs, no operation.
    refused = env.run_session()
    assert refused["status"] == "succeeded"
    assert refused["result_summary"]["action"] == "blocked_reconciliation"
    # the Job read says so truthfully: outcome blocked, never a plain success (COMPAT-01).
    assert (refused["outcome"], refused["outcome_reason"]) == ("blocked", "reconciliation")
    assert refused["operation"] is None
    assert "operation" not in refused["result_summary"] or refused["result_summary"]["operation"] is None
    assert env.get(f"/api/v1/execution-operations?strategy_id={STRATEGY}").json()["count"] == 0
    assert env.broker.post_count == 0

    # M9 with an OPEN external order: FAILED domain_conflict external_order_not_terminal, no writes.
    before = business_counts()
    rejected = env.run_job(
        "record-external-activity",
        {
            "order_ids": [bought["id"], sold["id"], still_open["id"]],
            "reason": "manual round trip plus an open order",
        },
    )
    assert rejected["status"] == "failed" and rejected["failure_reason"] == "domain_conflict"
    assert rejected["failure_message"].startswith("external_order_not_terminal")
    assert business_counts() == before  # nothing stored by the refused recording

    # Neutralize at the broker (itself external activity), then record everything: recorded AND
    # the fresh account check in the same Job is clean.
    env.broker.terminal(still_open["id"], "canceled")
    before = business_counts()
    recorded = env.run_job(
        "record-external-activity",
        {
            "order_ids": [bought["id"], sold["id"], still_open["id"]],
            "reason": "manual round trip, all terminal and net flat",
        },
    )
    assert recorded["status"] == "succeeded", recorded.get("failure_message")
    assert recorded["result_summary"]["outcome"] == "clean"
    assert recorded["result_summary"]["blocks_execution"] is False
    assert len(recorded["result_summary"]["recorded_activity_ids"]) == 3
    after = business_counts()
    assert after["external_broker_activity"] == before["external_broker_activity"] + 3
    for table in PAPER_BOOK_TABLES:  # recording writes no orders, fills, positions or ownership
        assert after[table] == before[table], table

    # The session is allowed now.
    allowed = env.run_session()
    assert allowed["status"] == "succeeded", allowed.get("failure_message")
    assert operation_of(allowed)["state"] == "paused"
    assert env.broker.post_count == 1


def test_e10_historical_execution(phase201_env: Phase201Env) -> None:
    """E10 | Requirements: [COR-04]

    Decisions: D-23 (paper execution only for the fresh evaluation session inside its window;
    research is ungated). A paper-session for an old evaluation session is the typed 409
    ``historical_execution_rejected`` (20.1-05 ``JobSubmissionConflictError``); a risk-evaluation
    (research) for the same date still succeeds.
    """

    env = phase201_env
    env.expect_posts(0)
    env.own()
    env.evaluate()

    env.set_clock(et(2025, 12, 3, 10, 0))  # the evaluation session is no longer the previous one
    rejected = env.start_session()
    assert env.conflict_code(rejected) == "historical_execution_rejected"
    detail = rejected.json()["detail"]
    assert detail["job_type"] == "paper-session" and detail["as_of_session"] == "2025-12-01"

    research = env.run_job(
        "risk-evaluation", {"strategy_id": STRATEGY, "as_of_session": EVAL_SESSION.isoformat()}
    )
    assert research["status"] == "succeeded", research.get("failure_message")
    assert env.broker.post_count == 0


def test_e11_window_expiry(phase201_env: Phase201Env) -> None:
    """E11 | Requirements: [COR-04, REC-02]

    Decisions: D-18 (lazy window expiry on the next touch: unsent intents ``expired_unsent``,
    sent orders untouched), D-17/TL-2 (a working order from the expired operation still blocks
    every new session until it is terminal and synced), S3-R4 (new start needs sync + clean
    reconciliation + a NEW evaluation), J-2 (zero cancel calls).
    """

    env = phase201_env
    env.expect_posts(2)  # the working AAPL order, then the first action of the next day
    env.own()
    env.evaluate()
    started = env.run_session()
    operation_id = operation_of(started)["id"]
    first_cid = intents_of(env, operation_id)["AAPL"]["client_order_id"]
    assert operation_of(started)["reason"] == "working_order_commitments_unaccounted"

    # The clock moves past the cutoff; the next touch (Continue) terminates the operation.
    env.set_clock(et(2025, 12, 2, 15, 50))
    touched = env.run_continue(operation_id)  # accepted past the window so the run can end it
    assert touched["status"] == "succeeded", touched.get("failure_message")
    assert operation_of(touched)["state"] == "terminated"
    read = env.operation(operation_id)
    assert read["persisted_state"] == "terminated"  # persisted by the touch, not only read lazily
    assert read["reason"] == "execution_window_elapsed"
    states = intent_states_of(env, operation_id)
    assert states["AAPL"] == "submitted"
    assert states["MSFT"] == "expired_unsent" and states["NVDA"] == "expired_unsent"
    assert env.broker.post_count == 1 and env.broker.cancel_calls == 0

    # A working order from it still blocks a new session (next trading day, new evaluation).
    evaluation_session = env.next_trading_day()
    env.evaluate(evaluation_session)
    blocked = env.start_session(evaluation_session)
    assert env.conflict_code(blocked) == "working_order_commitments_unaccounted"

    # The order becomes terminal and is synced: still not enough without the clean reconciliation.
    env.broker.fill_order(first_cid)
    env.sync("strategy", strategy_id=STRATEGY, as_of_session=EVAL_SESSION.isoformat())
    env.evaluate(evaluation_session)
    skipped = env.start_session(evaluation_session)
    assert env.conflict_code(skipped) == "evaluation_basis_unverified"
    assert skipped.json()["detail"]["reason"] == "reconciliation_missing"

    # Sync, clean standalone reconciliation, THEN the new evaluation: a new session is allowed.
    env.settle()
    env.evaluate(evaluation_session)
    fresh = env.run_session(evaluation_session)
    assert fresh["status"] == "succeeded", fresh.get("failure_message")
    assert env.broker.post_count == 2
    assert env.broker.posts_for(first_cid) == 1



def symbol_readiness(env: Phase201Env, symbol: str) -> tuple[str, str | None]:
    state = env.get(f"/api/v1/market-data/calendar-state?strategy_id={STRATEGY}").json()
    rows = state["evaluation_sessions"][STRATEGY]["symbols"]
    row = next(item for item in rows if item["symbol"] == symbol)
    return row["metadata"], row["metadata_reason"]


def test_e12_symbol_metadata(phase201_env: Phase201Env) -> None:
    """E12 | Requirements: [COR-03, COR-04]

    Decisions: D-28/COR-03 (batch outcomes complete/partial/failed, kept distinct from the Job
    lifecycle; the ``jobs`` table gains no column), D-29 (a symbol without metadata is
    ``not_ready(missing_metadata)`` and its candidate is rejected ``symbol_not_ready`` while the
    other symbols trade), R4 (calendar-state per-symbol readiness).
    """

    env = phase201_env
    env.expect_posts(2)  # AAPL on start, MSFT on Continue; never NVDA
    env.own()
    env.broker.default_status = "filled"
    with session_scope(load_settings()) as session:
        columns_before = {
            row[0]
            for row in session.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'jobs'")
            )
        }
        nvda = session.execute(select(Symbol).where(Symbol.ticker == "NVDA")).scalar_one()
        nvda.metadata_provider = None
        nvda.market = None
        nvda.primary_exchange = None
        nvda.symbol_type = None
    assert symbol_readiness(env, "NVDA") == ("not_ready", "missing_metadata")
    assert symbol_readiness(env, "AAPL") == ("ready", None)

    # M3 with one unknown ticker: the Job succeeded, the OUTCOME is partial with a per-symbol reason.
    env.market.unknown_tickers.add("NVDA")
    partial = env.run_job("sync-symbol-metadata", {"symbols": list(UNIVERSE)})
    assert partial["status"] == "succeeded" and partial["outcome"] == "partial"
    assert partial["result_summary"]["failures"] == [{"symbol": "NVDA", "reason": "not_found"}]
    assert sorted(partial["result_summary"]["synced"]) == ["AAPL", "MSFT"]
    assert symbol_readiness(env, "NVDA") == ("not_ready", "missing_metadata")  # R4

    # A provider authentication failure: the WHOLE operation failed, nothing was marked synced.
    env.market.unknown_tickers.clear()
    env.market.metadata_auth_failure = True
    failed = env.run_job("sync-symbol-metadata", {"symbols": ["NVDA"]})
    assert failed["status"] == "failed" and failed["outcome"] == "failed"
    with session_scope(load_settings()) as session:
        nvda_provider = session.execute(
            select(Symbol.metadata_provider).where(Symbol.ticker == "NVDA")
        ).scalar_one()
    assert nvda_provider is None
    env.market.metadata_auth_failure = False

    # COR-03 batch outcome of ingest-bars: one failed symbol -> partial, all failed -> failed.
    env.market.failing_bars = {"MSFT"}
    one_failed = env.run_job(
        "ingest-bars",
        {"from_date": "2025-12-01", "to_date": "2025-12-01", "symbols": ["AAPL", "MSFT"]},
    )
    assert one_failed["status"] == "succeeded" and one_failed["outcome"] == "partial"
    env.market.failing_bars = {"AAPL", "MSFT"}
    all_failed = env.run_job(
        "ingest-bars",
        {"from_date": "2025-12-01", "to_date": "2025-12-01", "symbols": ["AAPL", "MSFT"]},
    )
    assert all_failed["status"] == "failed" and all_failed["outcome"] == "failed"
    env.market.failing_bars = set()

    # The evaluation rejects the not-ready symbol; the others trade.
    evaluation = env.evaluate()
    from trading_platform.db.models import RiskEvent

    with session_scope(load_settings()) as session:
        decisions = {
            ticker: (outcome, code)
            for ticker, outcome, code in session.execute(
                select(Symbol.ticker, RiskEvent.outcome, RiskEvent.decision_code)
                .join(Symbol, Symbol.id == RiskEvent.symbol_id)
                .where(RiskEvent.strategy_run_id == uuid.UUID(evaluation["result_summary"]["run_id"]))
            )
        }
    assert decisions["NVDA"] == ("rejected", "symbol_not_ready")
    assert decisions["AAPL"] == ("approved", "approved") and decisions["MSFT"] == ("approved", "approved")

    started = env.run_session()
    operation_id = operation_of(started)["id"]
    assert set(intents_of(env, operation_id)) == {"AAPL", "MSFT"}  # NVDA has no intent
    env.settle()
    env.run_continue(operation_id)
    sent = [env.broker.order_for(cid)["symbol"] for cid in env.broker.post_client_order_ids]
    assert sent == ["AAPL", "MSFT"]

    with session_scope(load_settings()) as session:
        columns_after = {
            row[0]
            for row in session.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'jobs'")
            )
        }
    assert columns_after == columns_before and "outcome" not in columns_after


def seed_falling_bars(session_date: date, close: int = 90) -> None:
    """One bar per universe symbol at a low close, so the trend strategy's exit condition holds for
    a held symbol and no entry signal exists for the others."""

    with session_scope(load_settings()) as session:
        for ticker in UNIVERSE:
            symbol_id = session.execute(select(Symbol.id).where(Symbol.ticker == ticker)).scalar_one()
            session.add(
                DailyBarModel(
                    symbol_id=symbol_id,
                    session_date=session_date,
                    open=Decimal(close),
                    high=Decimal(close + 1),
                    low=Decimal(close - 1),
                    close=Decimal(close),
                    volume=1000,
                    adjusted=True,
                    provider="polygon",
                )
            )


def test_e13_handover(phase201_env: Phase201Env) -> None:
    """E13 | Requirements: [PAPER-02]

    Decisions: D-04 (handover only through the guarded control), D-04/TL-6 (flat required: A3),
    A7 (the outgoing owner must be disabled), new owner starts disabled, the old owner no longer
    passes the submit-time ownership gate.

    The position is OWNED and consistent: the real entry fill, and later the real exit fill of the
    strategy's own next-day exit signal, both synchronized and reconciled clean.
    """

    env = phase201_env
    env.expect_posts(2)  # the entry, and the exit that flattens the book
    env.own()
    env.broker.default_status = "filled"
    env.evaluate()
    started = env.run_session()
    operation_id = operation_of(started)["id"]
    end_operation(env, operation_id)  # A1: no open operation
    _, reconciled = env.settle()
    assert reconciled["result_summary"]["blocks_execution"] is False
    assert [p["symbol"] for p in env.broker.positions()] == ["AAPL"]

    # M6 with a position: the first failing check is A3; A7 (owner enabled) fails as well.
    refused = env.put_owner(OTHER_STRATEGY)
    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert detail["code"] == "check_failed:A3", detail
    assert {"A3", "A7"} <= set(detail["failed_checks"])
    a3 = next(c for c in detail["checks"] if c["id"] == "A3")
    assert a3["reason_code"] == "open_positions"
    assert env.get("/api/v1/controls/active-paper-strategy").json()["strategy_id"] == STRATEGY

    # The strategy's own exit signal on the next trading day flattens the book (owned exit fill).
    seed_falling_bars(EXEC_SESSION)
    evaluation_session = env.next_trading_day(EXEC_SESSION)
    # S3-R4: a new start after earlier orders needs sync + a clean standalone reconciliation + a NEW
    # evaluation; with the reconciliation skipped the start is 409 evaluation_basis_unverified.
    env.sync("strategy", strategy_id=STRATEGY, as_of_session=EVAL_SESSION.isoformat())
    env.evaluate(evaluation_session)
    skipped = env.start_session(evaluation_session)
    assert env.conflict_code(skipped) == "evaluation_basis_unverified"
    assert skipped.json()["detail"]["reason"] == "reconciliation_missing"
    assert env.reconcile()["result_summary"]["blocks_execution"] is False
    env.evaluate(evaluation_session)
    exited = env.run_session(evaluation_session)
    assert exited["status"] == "succeeded", exited.get("failure_message")
    exit_operation_id = operation_of(exited)["id"]
    assert [i["side"] for i in env.operation(exit_operation_id)["intents"]] == ["sell"]
    end_operation(env, exit_operation_id)

    # Outgoing owner disabled, flat, account sync (owner scope derives the position) + reconciliation.
    assert env.set_strategy_status(STRATEGY, "disabled").status_code == 200
    _, reconciled = env.settle()
    assert reconciled["result_summary"]["blocks_execution"] is False
    assert env.broker.positions() == []

    handed_over = env.put_owner(OTHER_STRATEGY, "handover to the second strategy")
    assert handed_over.status_code == 200, handed_over.text
    body = handed_over.json()
    assert body["kind"] == "handover" and body["previous_strategy_id"] == STRATEGY
    assert body["active_paper_strategy"]["strategy_id"] == OTHER_STRATEGY
    assert env.get(f"/api/v1/controls/strategies/{OTHER_STRATEGY}").json()["status"] == "disabled"

    # The old owner no longer passes the submit-time ownership gate.
    old = env.start_session(evaluation_session)
    assert env.conflict_code(old) == "strategy_not_active_paper_strategy"


def test_e14_mutations_disabled(phase201_env: Phase201Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """E14 | Requirements: [COMPAT-01]

    Decisions: ORCH-07/D-19 (every mutating route is guarded by ``orchestration.mutations_enabled``
    ahead of body and path validation). With the flag false EVERY new mutating route returns 403
    ``mutations_disabled`` and a full-table row-count snapshot is identical before and after.
    """

    env = phase201_env
    env.expect_posts(1)  # only the ambiguous POST of the arrangement (made while enabled)
    job, operation_id, _, intent_id = ambiguous_session(env, "read_timeout_without_create")

    monkeypatch.setenv("TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED", "false")
    clear_settings_cache()
    assert load_settings().orchestration.mutations_enabled is False
    before = row_counts()
    with TestClient(create_app()) as disabled:
        assert disabled.app.state.settings.orchestration.mutations_enabled is False
        responses = {
            "PUT /controls/active-paper-strategy": disabled.put(
                "/api/v1/controls/active-paper-strategy",
                json={"strategy_id": OTHER_STRATEGY, "reason": "x"},
            ),
            "POST /execution-operations/{id}/end": disabled.post(
                f"/api/v1/execution-operations/{operation_id}/end", json={"reason": "x"}
            ),
            "POST /recovery/intents/{id}/broker-statement": disabled.post(
                f"/api/v1/recovery/intents/{intent_id}/broker-statement",
                json={"statement": "not_received", "reference": "t", "reason": "r"},
            ),
            "POST /jobs record-external-activity": disabled.post(
                "/api/v1/jobs",
                headers={"Idempotency-Key": "e14-record"},
                json={
                    "job_type": "record-external-activity",
                    "payload": {"order_ids": ["abc"], "reason": "x"},
                },
            ),
            "POST /jobs paper-session continue": disabled.post(
                "/api/v1/jobs",
                headers={"Idempotency-Key": "e14-continue"},
                json={
                    "job_type": "paper-session",
                    "payload": {"mode": "continue", "operation_id": operation_id},
                },
            ),
        }
    for name, response in responses.items():
        assert response.status_code == 403, (name, response.text)
        assert response.json()["detail"]["code"] == "mutations_disabled", name
    assert row_counts() == before  # zero writes anywhere
    assert env.broker.post_count == 1


def test_e15_legacy_console_truthfulness_api_half(phase201_env: Phase201Env) -> None:
    """E15 | Requirements: [COMPAT-01]

    API half of the legacy-console truthfulness scenario. SPEC CONFLICT (reported): 05 E15 is a
    console-rendering scenario while this gate is HTTP-only and edits nothing under ``console/``;
    this test asserts over HTTP the FACTS the console renders from, and the console half is the
    vitest suite of plan 20.1-14 (run as a named verify step of this plan).

    Facts: the catalog marks ``paper-session`` and ``record-external-activity`` ``api_only``; a
    paper-session Job whose operation paused is lifecycle ``succeeded`` with outcome ``paused``
    (never plain success); the payloads the account forms post (``{scope: 'account'}``) are
    accepted with NO owner and run to SUCCEEDED; the active-paper-strategy read returns a null
    owner with ``no_active_paper_strategy`` blocking trading; analytics ``latest_reconciliation``
    is the account-scope run.
    """

    env = phase201_env
    env.expect_posts(1)

    catalog = env.get("/api/v1/job-types").json()
    items = catalog["items"] if isinstance(catalog, dict) else catalog
    by_type = {item["job_type"]: item for item in items}
    assert by_type["paper-session"]["console_submission"] == "api_only"
    assert by_type["record-external-activity"]["console_submission"] == "api_only"
    assert by_type["reconciliation"]["console_submission"] != "api_only"
    assert by_type["broker-order-sync"]["console_submission"] != "api_only"

    # The two forms post {scope: 'account'}: accepted with NO owner and run to SUCCEEDED.
    synced = env.run_job("broker-order-sync", {"scope": "account"})
    reconciled = env.run_job("reconciliation", {"scope": "account"})
    assert synced["status"] == "succeeded" and reconciled["status"] == "succeeded"
    view = env.get("/api/v1/controls/active-paper-strategy").json()
    assert view["strategy_id"] is None
    assert "no_active_paper_strategy" in view["trading_blocked_reasons"]

    # A paper-session Job whose operation paused is never plain success.
    env.own()
    # analytics latest_reconciliation is the account-scope run (not a stale strategy run).
    analytics = env.get(f"/api/v1/analytics/strategies/{STRATEGY}").json()
    latest = analytics["paper"]["latest_reconciliation"]
    assert latest["scope"] == "account"
    assert latest["run_id"] == reconciled["result_summary"]["run_id"]

    env.evaluate()
    paused = env.run_session()
    assert paused["status"] == "succeeded"
    assert paused["outcome"] == "paused"
    assert paused["outcome_reason"] == "working_order_commitments_unaccounted"
    assert paused["operation"]["state"] == "paused"
    assert paused["operation"]["id"] == operation_of(paused)["id"]
