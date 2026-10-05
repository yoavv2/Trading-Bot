"""Paper-execution eligibility (COR-04, D-23): a paper session is accepted only for
the fresh evaluation session inside its execution window; historical execution is
rejected with a typed 409 while research and backtests stay ungated.

Real DB + injected clock + real XNYS sessions. Service-level precedence uses a small
``FakeStrategy``; spec/HTTP-level tests use the real ``trend_following_daily``
(universe of ten symbols, 200-bar warm-up) so the production readiness rule is exercised.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.support.basis_fixtures import seed_fresh_broker_snapshot
from tests.support.calendar_facts import (
    FakeStrategy,
    clock_at,
    et,
    seed_bars,
    seed_calendar,
    sessions_between,
)
from tests.support.migrated_db import migrated_database
from tests.support.paper_ownership import seed_registered_strategy, set_active_paper_strategy

from trading_platform.api.app import create_app
from trading_platform.core.settings import Settings, clear_settings_cache, load_settings
from trading_platform.db.models import Position, Strategy, StrategyRun, Symbol
from trading_platform.db.session import session_scope
from trading_platform.jobs.handlers.backtest_submission import BacktestSubmissionSpec
from trading_platform.jobs.handlers.paper_session import PaperSessionJobHandler
from trading_platform.jobs.handlers.paper_session_submission import (
    PaperSessionSubmissionSpec,
    PaperSessionSubmitConflict,
)
from trading_platform.jobs.handlers.risk_evaluation_submission import RiskEvaluationSubmissionSpec
from trading_platform.jobs.registry import JobRegistry, JobSubmissionConflictError
from trading_platform.services import calendar_facts as facts
from trading_platform.services.data import DailyBar as IngestedBar
from trading_platform.services.ingestion import upsert_daily_bars
from trading_platform.services.risk import run_risk_evaluation
from trading_platform.strategies.registry import build_default_registry

STRATEGY_ID = "trend_following_daily"
_TICKERS = ["AAA", "BBB"]


@pytest.fixture()
def eligibility_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    with migrated_database(monkeypatch, "eligibility") as _name:
        monkeypatch.setenv("TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED", "true")
        clear_settings_cache()
        yield load_settings()


def _seed_ready_calendar(through: date = date(2025, 12, 1)) -> None:
    seed_calendar(date(2025, 1, 2), date(2026, 3, 31))
    seed_bars(_universe(), sessions_between(date(2025, 1, 2), through))


def _universe() -> tuple[str, ...]:
    settings = load_settings()
    return build_default_registry(settings).resolve(STRATEGY_ID).metadata.universe


def _spec(now: datetime) -> PaperSessionSubmissionSpec:
    return PaperSessionSubmissionSpec(load_settings(), clock=clock_at(now))


def _own(settings: Settings) -> None:
    seed_registered_strategy(settings, STRATEGY_ID)
    set_active_paper_strategy(settings, STRATEGY_ID)


def _payload(as_of: date) -> dict[str, object]:
    return {"strategy_id": STRATEGY_ID, "as_of_session": as_of.isoformat(), "risk_run_id": None}


def _evaluate(as_of: date) -> uuid.UUID:
    """A REAL risk evaluation: it persists the input manifest the submit-time
    provenance check (PROV-01, D-25) verifies."""

    # SAF-09 (20.1-24): the evaluation sizes on a fresh broker-observed account snapshot.
    with session_scope(load_settings()) as session:
        seed_fresh_broker_snapshot(session)
    report = run_risk_evaluation(
        STRATEGY_ID, as_of_session=as_of, trigger_source="test_suite", settings=load_settings()
    )
    assert report.status == "succeeded"
    return uuid.UUID(report.run_id)


def _reingest(ticker: str, session_date: date, *, close: str) -> None:
    with session_scope(load_settings()) as session:
        symbol_id = session.execute(select(Symbol.id).where(Symbol.ticker == ticker)).scalar_one()
        upsert_daily_bars(
            session,
            [
                IngestedBar(
                    symbol=ticker,
                    session_date=session_date,
                    open=Decimal("100"),
                    high=Decimal("101"),
                    low=Decimal("99"),
                    close=Decimal(close),
                    volume=1000,
                    adjusted=True,
                    provider="polygon",
                )
            ],
            symbol_id,
        )


def _conflict(spec: PaperSessionSubmissionSpec, as_of: date, risk_run_id: uuid.UUID | None = None) -> JobSubmissionConflictError:
    payload = _payload(as_of)
    if risk_run_id is not None:
        payload["risk_run_id"] = str(risk_run_id)
    with pytest.raises(JobSubmissionConflictError) as exc_info:
        spec.validate_payload(payload)
    return exc_info.value


def _eligibility(settings: Settings, now: datetime, as_of: date, strategy: FakeStrategy) -> facts.EligibilityResult:
    with session_scope(settings) as session:
        return facts.paper_execution_eligibility(
            session, now=now, settings=settings, strategy=strategy, as_of_session=as_of
        )


# ---------------------------------------------------------------------------
# Closed sets
# ---------------------------------------------------------------------------


def test_conflict_set_is_exactly_the_seventeen_values() -> None:
    """Renamed from ``..._fifteen_values`` (D-19 / 20.1-16: the two Continue-mode gates) and
    before that ``..._eleven_values`` (REC-02 / 20.1-15: the four start-mode operation gates
    extend the set; the eleven earlier values are unchanged; before that ``..._eight_values``,
    D-15 / 20.1-10)."""

    assert {member.value for member in PaperSessionSubmitConflict} == {
        "no_active_paper_strategy",
        "strategy_not_active_paper_strategy",
        "historical_execution_rejected",
        "outside_execution_window",
        "evaluation_data_not_ready",
        "calendar_data_unavailable",
        "evaluation_data_changed",
        "strategy_settings_changed",
        "outcome_unresolved",
        "reconciliation_required",
        "reconciliation_not_clean",
        "operation_open",
        "working_order_commitments_unaccounted",
        "risk_run_already_operated",
        "evaluation_basis_unverified",
        # D-19 / 20.1-16: the Continue-mode gates.
        "operation_not_paused",
        "awaiting_reconciliation",
    }


def test_eligibility_rejection_set_is_exactly_four_values() -> None:
    assert [member.value for member in facts.EligibilityRejection] == [
        "calendar_data_unavailable",
        "historical_execution_rejected",
        "evaluation_data_not_ready",
        "outside_execution_window",
    ]


def test_every_eligibility_rejection_is_a_member_of_the_spec_conflict_enum() -> None:
    for rejection in facts.EligibilityRejection:
        assert PaperSessionSubmitConflict(rejection.value).value == rejection.value


# ---------------------------------------------------------------------------
# Spec-level: one test per rejection value + the eligible acceptance
# ---------------------------------------------------------------------------


def test_ten_am_on_session_d_with_ready_previous_session_is_eligible(eligibility_db: Settings) -> None:
    _seed_ready_calendar()
    _own(eligibility_db)
    _evaluate(date(2025, 12, 1))
    spec = _spec(et(2025, 12, 2, 10, 0))

    normalized = spec.validate_payload(_payload(date(2025, 12, 1)))

    assert normalized == {
        "strategy_id": STRATEGY_ID,
        "as_of_session": "2025-12-01",
        "risk_run_id": None,
    }


def test_paper_session_for_2026_03_13_is_historical_execution_rejected(
    eligibility_db: Settings,
) -> None:
    """The calendar is current (trading day 2026-09-29) but the requested session is
    the stale 2026-03-13: its previous session differs -> historical."""

    seed_calendar(date(2026, 1, 2), date(2026, 12, 31))
    _own(eligibility_db)
    spec = _spec(et(2026, 9, 29, 10, 0))

    error = _conflict(spec, date(2026, 3, 13))

    assert error.code == "historical_execution_rejected"
    assert error.job_type == "paper-session"
    assert error.detail["strategy_id"] == STRATEGY_ID
    assert error.detail["as_of_session"] == "2026-03-13"


def test_calendar_ending_2026_03_13_at_2026_09_29_is_calendar_data_unavailable(
    eligibility_db: Settings,
) -> None:
    seed_calendar(date(2026, 1, 2), date(2026, 3, 13))
    _own(eligibility_db)
    spec = _spec(et(2026, 9, 29, 10, 0))

    assert _conflict(spec, date(2026, 3, 13)).code == "calendar_data_unavailable"
    # even a session that WOULD be previous_session(trading day) cannot be judged
    assert _conflict(spec, date(2026, 9, 28)).code == "calendar_data_unavailable"


@pytest.mark.parametrize(
    ("now", "label"),
    [
        (et(2025, 12, 2, 8, 0), "before_open"),
        (et(2025, 12, 2, 9, 29, 59), "one_second_before_open"),
        (et(2025, 12, 2, 15, 45, 0), "at_cutoff"),
        (et(2025, 12, 2, 16, 30), "after_close"),
    ],
)
def test_outside_execution_window(eligibility_db: Settings, now: datetime, label: str) -> None:
    _seed_ready_calendar()
    _own(eligibility_db)

    # after_close: the trading day is still 2025-12-02, previous session 2025-12-01
    # becomes stale (the new candidate is 2025-12-02) but the rule is judged on D.
    error = _conflict(_spec(now), date(2025, 12, 1))

    assert error.code == "outside_execution_window", label


def test_early_close_day_cutoff_is_respected(eligibility_db: Settings) -> None:
    _seed_ready_calendar()
    _own(eligibility_db)
    as_of = date(2025, 11, 26)  # previous session of the 2025-11-28 early-close day
    _evaluate(as_of)

    _spec(et(2025, 11, 28, 12, 44, 59)).validate_payload(_payload(as_of))
    assert _conflict(_spec(et(2025, 11, 28, 12, 45, 0)), as_of).code == "outside_execution_window"
    assert _conflict(_spec(et(2025, 11, 28, 12, 46, 0)), as_of).code == "outside_execution_window"


def test_missing_bar_for_one_universe_symbol_is_evaluation_data_not_ready(
    eligibility_db: Settings,
) -> None:
    seed_calendar(date(2025, 1, 2), date(2026, 3, 31))
    universe = list(_universe())
    seed_bars(universe, sessions_between(date(2025, 1, 2), date(2025, 12, 1)))
    # remove the 2025-12-01 bar of one symbol
    from sqlalchemy import delete, select

    from trading_platform.db.models.daily_bar import DailyBar
    from trading_platform.db.models.symbol import Symbol

    with session_scope(eligibility_db) as session:
        symbol_id = session.execute(select(Symbol.id).where(Symbol.ticker == universe[0])).scalar_one()
        session.execute(
            delete(DailyBar).where(DailyBar.symbol_id == symbol_id, DailyBar.session_date == date(2025, 12, 1))
        )
    _own(eligibility_db)

    error = _conflict(_spec(et(2025, 12, 2, 10, 0)), date(2025, 12, 1))

    assert error.code == "evaluation_data_not_ready"
    assert error.detail["reason"] == "missing_bars"
    assert universe[0] in error.detail["symbols"].split(",")


def test_insufficient_history_is_evaluation_data_not_ready(eligibility_db: Settings) -> None:
    seed_calendar(date(2025, 1, 2), date(2026, 3, 31))
    seed_bars(_universe(), sessions_between(date(2025, 11, 1), date(2025, 12, 1)))  # < 200 bars
    _own(eligibility_db)

    error = _conflict(_spec(et(2025, 12, 2, 10, 0)), date(2025, 12, 1))

    assert error.code == "evaluation_data_not_ready"
    assert error.detail["reason"] == "insufficient_history"


# ---------------------------------------------------------------------------
# Service-level precedence: calendar > historical > not_ready > window
# ---------------------------------------------------------------------------


@pytest.fixture()
def small(eligibility_db: Settings) -> Settings:
    seed_calendar(date(2025, 11, 1), date(2026, 3, 31))
    return eligibility_db


def _small_strategy() -> FakeStrategy:
    return FakeStrategy(_TICKERS, warmup_periods=2)


def test_each_rejection_is_reachable_alone(small: Settings) -> None:
    ready_days = sessions_between(date(2025, 11, 24), date(2025, 12, 1))
    seed_bars(_TICKERS, ready_days)
    strategy = _small_strategy()
    ten_am = et(2025, 12, 2, 10, 0)

    assert _eligibility(small, ten_am, date(2025, 12, 1), strategy).rejection is None
    alone = {
        facts.EligibilityRejection.HISTORICAL_EXECUTION_REJECTED: (ten_am, date(2025, 11, 26)),
        facts.EligibilityRejection.OUTSIDE_EXECUTION_WINDOW: (et(2025, 12, 2, 8, 0), date(2025, 12, 1)),
        facts.EligibilityRejection.CALENDAR_DATA_UNAVAILABLE: (et(2026, 9, 29, 10, 0), date(2025, 12, 1)),
    }
    for expected, (now, as_of) in alone.items():
        assert _eligibility(small, now, as_of, strategy).rejection is expected

    seed_calendar(date(2025, 11, 1), date(2026, 3, 31))
    not_ready = _eligibility(small, ten_am, date(2025, 12, 1), FakeStrategy(["AAA", "ZZZ"], 2))
    assert not_ready.rejection is facts.EligibilityRejection.EVALUATION_DATA_NOT_READY
    assert not_ready.evaluation_session.symbols == ("ZZZ",)


def test_precedence_calendar_beats_historical(small: Settings) -> None:
    result = _eligibility(small, et(2026, 9, 29, 10, 0), date(2026, 3, 13), _small_strategy())
    assert result.rejection is facts.EligibilityRejection.CALENDAR_DATA_UNAVAILABLE


def test_precedence_historical_beats_not_ready_and_window(small: Settings) -> None:
    # no bars anywhere (not ready), before the open (outside window), stale session
    result = _eligibility(small, et(2025, 12, 2, 8, 0), date(2025, 11, 24), _small_strategy())
    assert result.rejection is facts.EligibilityRejection.HISTORICAL_EXECUTION_REJECTED


def test_precedence_not_ready_beats_outside_window(small: Settings) -> None:
    result = _eligibility(small, et(2025, 12, 2, 8, 0), date(2025, 12, 1), _small_strategy())
    assert result.rejection is facts.EligibilityRejection.EVALUATION_DATA_NOT_READY
    assert result.window.status is facts.WindowStatus.CLOSED


def test_precedence_non_trading_day_is_outside_window_only_when_data_ready(small: Settings) -> None:
    seed_bars(_TICKERS, sessions_between(date(2025, 11, 24), date(2025, 11, 28)))
    # Saturday: trading day = Monday 12-01, previous session = Friday 11-28
    result = _eligibility(small, et(2025, 11, 29, 12, 0), date(2025, 11, 28), _small_strategy())
    assert result.rejection is facts.EligibilityRejection.OUTSIDE_EXECUTION_WINDOW
    assert result.trading_day.phase is facts.TradingDayPhase.NON_TRADING_DAY


def test_eligibility_is_read_only(small: Settings) -> None:
    seed_bars(_TICKERS, sessions_between(date(2025, 11, 24), date(2025, 12, 1)))
    with session_scope(small) as session:
        facts.paper_execution_eligibility(
            session,
            now=et(2025, 12, 2, 10, 0),
            settings=small,
            strategy=_small_strategy(),
            as_of_session=date(2025, 12, 1),
        )
        assert not session.new and not session.dirty and not session.deleted


# ---------------------------------------------------------------------------
# Research and backtests are NOT gated by market hours or these rejections
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hour", [3, 10, 16, 23])
def test_research_accepts_a_past_session_at_any_hour(eligibility_db: Settings, hour: int) -> None:
    seed_calendar(date(2025, 1, 2), date(2026, 3, 31))
    now = et(2025, 12, 2, hour, 0) if hour < 23 else datetime(2025, 12, 3, 4, 30, tzinfo=UTC)
    risk = RiskEvaluationSubmissionSpec(eligibility_db, clock=clock_at(now))
    backtest = BacktestSubmissionSpec(eligibility_db, clock=clock_at(now))

    assert risk.validate_payload({"strategy_id": STRATEGY_ID, "as_of_session": "2025-03-12"})[
        "as_of_session"
    ] == "2025-03-12"
    backtest.validate_payload(
        {"strategy_id": STRATEGY_ID, "from_date": "2025-03-03", "to_date": "2025-03-12"}
    )


def test_ownership_is_checked_before_eligibility(eligibility_db: Settings) -> None:
    seed_calendar(date(2026, 1, 2), date(2026, 3, 13))
    seed_registered_strategy(eligibility_db, STRATEGY_ID)  # no owner

    error = _conflict(_spec(et(2026, 9, 29, 10, 0)), date(2026, 3, 13))

    assert error.code == "no_active_paper_strategy"


# ---------------------------------------------------------------------------
# HTTP: typed 409 body
# ---------------------------------------------------------------------------


def test_http_409_body_for_historical_execution_rejected(eligibility_db: Settings) -> None:
    seed_calendar(date(2026, 1, 2), date(2026, 12, 31))
    _own(eligibility_db)
    registry = JobRegistry()
    registry.register(
        PaperSessionJobHandler(settings=eligibility_db),
        submission_spec=_spec(et(2026, 9, 29, 10, 0)),
    )

    with TestClient(create_app(job_registry=registry)) as client:
        response = client.post(
            "/api/v1/jobs",
            headers={"Idempotency-Key": "eligibility-http-1"},
            json={"job_type": "paper-session", "payload": _payload(date(2026, 3, 13))},
        )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == {
        "code": "historical_execution_rejected",
        "job_type": "paper-session",
        "strategy_id": STRATEGY_ID,
        "as_of_session": "2026-03-13",
    }


# ---------------------------------------------------------------------------
# Provenance: the evaluation input manifest is verified last (PROV-01, D-25)
# ---------------------------------------------------------------------------

TEN_AM = et(2025, 12, 2, 10, 0)
AS_OF = date(2025, 12, 1)


def _provenance_ready(settings: Settings) -> uuid.UUID:
    _seed_ready_calendar()
    _own(settings)
    return _evaluate(AS_OF)


def _post_paper_session(settings: Settings, payload: dict[str, object], key: str):
    registry = JobRegistry()
    registry.register(PaperSessionJobHandler(settings=settings), submission_spec=_spec(TEN_AM))
    with TestClient(create_app(job_registry=registry)) as client:
        return client.post(
            "/api/v1/jobs",
            headers={"Idempotency-Key": key},
            json={"job_type": "paper-session", "payload": payload},
        )


def test_corrected_bar_after_evaluation_rejects_with_evaluation_data_changed(
    eligibility_db: Settings,
) -> None:
    _provenance_ready(eligibility_db)
    ticker = _universe()[0]

    _reingest(ticker, date(2025, 11, 20), close="123.45")

    response = _post_paper_session(eligibility_db, _payload(AS_OF), "provenance-http-data")
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "evaluation_data_changed"
    assert detail["job_type"] == "paper-session"
    assert detail["strategy_id"] == STRATEGY_ID
    assert detail["as_of_session"] == "2025-12-01"
    assert detail["request_kind"] == "bars_for_sessions"  # closed ManifestRequestKind value only


def test_changed_strategy_setting_rejects_with_strategy_settings_changed(
    eligibility_db: Settings,
) -> None:
    _provenance_ready(eligibility_db)
    changed = Settings.model_validate(load_settings().model_dump(mode="python"))
    # a signal-parameter change that leaves universe and warm-up (readiness) untouched
    changed.strategies.trend_following_daily.indicators.short_window -= 1

    registry = JobRegistry()
    registry.register(
        PaperSessionJobHandler(settings=changed),
        submission_spec=PaperSessionSubmissionSpec(changed, clock=clock_at(TEN_AM)),
    )
    with TestClient(create_app(job_registry=registry)) as client:
        response = client.post(
            "/api/v1/jobs",
            headers={"Idempotency-Key": "provenance-http-settings"},
            json={"job_type": "paper-session", "payload": _payload(AS_OF)},
        )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "strategy_settings_changed"


def test_identical_reingest_does_not_reject(eligibility_db: Settings) -> None:
    _provenance_ready(eligibility_db)
    ticker = _universe()[0]

    _reingest(ticker, date(2025, 11, 20), close="100.5")  # same values, refreshed updated_at

    normalized = _spec(TEN_AM).validate_payload(_payload(AS_OF))
    assert normalized["risk_run_id"] is None


def test_risk_run_without_manifest_rejects_evaluation_data_not_ready(
    eligibility_db: Settings,
) -> None:
    run_id = _provenance_ready(eligibility_db)
    with session_scope(eligibility_db) as session:
        run = session.get(StrategyRun, run_id)
        summary = dict(run.result_summary)
        summary.pop("evaluation_manifest")
        run.result_summary = summary

    error = _conflict(_spec(TEN_AM), AS_OF)

    assert error.code == "evaluation_data_not_ready"
    assert error.detail["reason"] == "manifest_missing"
    assert error.detail["risk_run_id"] == str(run_id)


def test_no_eligible_risk_run_rejects_evaluation_data_not_ready(eligibility_db: Settings) -> None:
    _seed_ready_calendar()
    _own(eligibility_db)

    error = _conflict(_spec(TEN_AM), AS_OF)

    assert error.code == "evaluation_data_not_ready"
    assert error.detail["reason"] == "no_eligible_risk_run"
    assert "risk_run_id" not in error.detail


def test_pinned_and_unpinned_risk_run_use_the_same_verification(eligibility_db: Settings) -> None:
    run_id = _provenance_ready(eligibility_db)
    spec = _spec(TEN_AM)

    unpinned = spec.validate_payload(_payload(AS_OF))
    pinned = spec.validate_payload({**_payload(AS_OF), "risk_run_id": str(run_id)})
    # the resolved id is never written into the payload; the pinned id stays as given
    assert unpinned["risk_run_id"] is None
    assert pinned["risk_run_id"] == str(run_id)

    _reingest(_universe()[0], date(2025, 11, 20), close="123.45")

    assert _conflict(spec, AS_OF).code == "evaluation_data_changed"
    assert _conflict(spec, AS_OF, risk_run_id=run_id).code == "evaluation_data_changed"


def test_a_new_position_after_evaluation_does_not_reject(eligibility_db: Settings) -> None:
    _provenance_ready(eligibility_db)
    ticker = _universe()[0]
    with session_scope(eligibility_db) as session:
        strategy_pk = session.execute(select(Strategy.id).where(Strategy.strategy_id == STRATEGY_ID)).scalar_one()
        symbol_pk = session.execute(select(Symbol.id).where(Symbol.ticker == ticker)).scalar_one()
        session.add(
            Position(
                strategy_id=strategy_pk,
                symbol_id=symbol_pk,
                status="open",
                quantity=Decimal("10"),
                average_entry_price=Decimal("100"),
                cost_basis=Decimal("1000"),
                opened_session_date=AS_OF,
            )
        )

    assert _spec(TEN_AM).validate_payload(_payload(AS_OF))["as_of_session"] == "2025-12-01"
