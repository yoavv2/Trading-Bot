"""Evaluation input manifest (PROV-01, D-25/D-26): recording, digests, verification.

Pure digest tests need no database; recording and verification tests use a real
migrated PostgreSQL database. The manifest records every read request made
through the shared market-data accessors (resolved as-of bound + SHA-256 result
digest, empty results included) and verification re-executes them AS RECORDED.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session as SASession
from tests.support.calendar_facts import seed_bars, seed_calendar, sessions_between
from tests.support.migrated_db import migrated_database
from tests.support.query_counter import count_queries
from tests.support.symbol_metadata import ready_symbol_fields

from trading_platform.core.settings import Settings, clear_settings_cache, load_settings
from trading_platform.db.models import RiskEvent, StrategyRun, Symbol
from trading_platform.db.models.daily_bar import DailyBar as DailyBarModel
from trading_platform.db.models.market_session import MarketSession
from trading_platform.db.session import session_scope
from trading_platform.services import read_recording
from trading_platform.services.calendar import upsert_market_sessions
from trading_platform.services.data import DailyBar
from trading_platform.services.evaluation_manifest import (
    MANIFEST_VERSION,
    EvaluationManifest,
    ManifestFormatError,
    ManifestRecorder,
    ManifestRequestKind,
    ManifestVerificationStatus,
    compute_settings_digest,
    verify_manifest,
)
from trading_platform.services.ingestion import upsert_daily_bars
from trading_platform.services.market_data_access import (
    SessionBar,
    bars_for_session_date,
    bars_for_sessions,
    bars_for_sessions_many,
    latest_completed_session,
    missing_bars_for_session,
    persisted_session_dates,
)
from trading_platform.services.read_recording import (
    digest_bar_map,
    digest_bars,
    digest_dates,
    digest_symbols,
    recording,
)
from trading_platform.strategies.base import StrategyMetadata

AS_OF = date(2024, 3, 15)
FIRST = date(2024, 1, 2)
N_SESSIONS = 5


@pytest.fixture()
def manifest_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    with migrated_database(monkeypatch, "manifest") as _name:
        clear_settings_cache()
        yield load_settings()


def _bar(
    *,
    close: str = "100.5",
    session_date: date = AS_OF,
    provider_timestamp: datetime | None = None,
    vwap: str | None = None,
) -> SessionBar:
    return SessionBar(
        symbol="AAA",
        session_date=session_date,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal(close),
        volume=1000,
        adjusted=True,
        provider="polygon",
        vwap=Decimal(vwap) if vwap is not None else None,
        trade_count=None,
        provider_timestamp=provider_timestamp,
    )


# ---------------------------------------------------------------------------
# Closed sets
# ---------------------------------------------------------------------------


def test_manifest_request_kind_set_is_exactly_the_five_accessors() -> None:
    assert {member.value for member in ManifestRequestKind} == {
        "bars_for_sessions",
        "missing_bars_for_session",
        "bars_for_session_date",
        "latest_completed_session",
        "persisted_session_dates",
    }


def test_manifest_verification_status_set_is_exactly_four_values() -> None:
    assert {member.value for member in ManifestVerificationStatus} == {
        "matches",
        "evaluation_data_changed",
        "strategy_settings_changed",
        "manifest_missing",
    }


def test_manifest_version_is_one() -> None:
    assert MANIFEST_VERSION == 1


# ---------------------------------------------------------------------------
# Pure digest tests
# ---------------------------------------------------------------------------


def test_decimal_normalization_makes_1_50_and_1_5_digest_equal() -> None:
    assert digest_bars([_bar(close="1.50")]) == digest_bars([_bar(close="1.5")])
    assert digest_bars([_bar(close="100")]) == digest_bars([_bar(close="1E+2")])
    assert digest_bars([_bar(close="1.5")]) != digest_bars([_bar(close="1.51")])


def test_provider_timestamp_is_excluded_from_the_bar_digest() -> None:
    stamped = _bar(provider_timestamp=datetime(2024, 3, 16, tzinfo=UTC))
    assert digest_bars([stamped]) == digest_bars([_bar()])


def test_every_digested_field_matters() -> None:
    base = digest_bars([_bar()])
    assert digest_bars([_bar(vwap="100.1")]) != base
    assert digest_bars([_bar(session_date=date(2024, 3, 14))]) != base


def test_empty_digest_is_a_constant() -> None:
    assert digest_bars([]) == read_recording.EMPTY_DIGEST
    assert digest_dates([]) == read_recording.EMPTY_DIGEST
    assert digest_symbols([]) == read_recording.EMPTY_DIGEST
    assert digest_bar_map({}) == read_recording.EMPTY_DIGEST
    assert len(read_recording.EMPTY_DIGEST) == 64


def test_bar_digest_sorts_ascending_by_session_so_accessor_order_never_matters() -> None:
    first, second = _bar(session_date=date(2024, 3, 14)), _bar(session_date=date(2024, 3, 15))
    assert digest_bars([second, first]) == digest_bars([first, second])


def test_bar_map_digest_is_keyed_by_symbol() -> None:
    one = {"AAA": _bar(close="100"), "BBB": _bar(close="101")}
    swapped = {"AAA": _bar(close="101"), "BBB": _bar(close="100")}
    assert digest_bar_map(one) != digest_bar_map(swapped)
    assert digest_bar_map(one) == digest_bar_map(dict(reversed(list(one.items()))))


def test_no_recorder_means_active_recorder_is_none() -> None:
    assert read_recording.active_recorder() is None


def test_recording_context_restores_and_suspension_hides_the_recorder() -> None:
    recorder = ManifestRecorder()
    with recording(recorder):
        assert read_recording.active_recorder() is recorder
        with read_recording.suspended():
            assert read_recording.active_recorder() is None
        assert read_recording.active_recorder() is recorder
    assert read_recording.active_recorder() is None


def test_read_recording_does_not_import_the_data_access_layer() -> None:
    import inspect

    source = inspect.getsource(read_recording)
    assert "trading_platform.services.market_data_access" not in source
    assert "trading_platform.db" not in source


# ---------------------------------------------------------------------------
# Seeding helpers (real DB)
# ---------------------------------------------------------------------------

_TICKERS = [f"S{i:02d}" for i in range(10)]


def _seed(symbols: list[str], *, through: date = AS_OF, start: date = FIRST) -> None:
    seed_calendar(date(2024, 1, 2), date(2024, 6, 28))
    seed_bars(symbols, sessions_between(start, through))


def _universe_metadata(**overrides: Any) -> StrategyMetadata:
    values: dict[str, Any] = {
        "strategy_id": "fake",
        "display_name": "Fake",
        "version": "1.0.0",
        "enabled": True,
        "description": "d",
        "config_reference": "c",
        "universe": ("B", "A"),
        "indicators": {"short_window": 2, "long_window": 3, "warmup_periods": 3},
        "risk": {"max_positions": 10, "risk_per_trade": 0.01},
        "exits": {"close_below": "sma_2", "exit_window": 2},
    }
    values.update(overrides)
    return StrategyMetadata(**values)


def _evaluate(
    session: SASession,
    symbols: list[str],
    *,
    held: list[str] | None = None,
    as_of: date = AS_OF,
) -> EvaluationManifest:
    """Make the reads a risk evaluation makes, under a recorder; return the manifest."""

    recorder = ManifestRecorder()
    with recording(recorder):
        for ticker in symbols:
            bars_for_sessions(session, symbol=ticker, n_sessions=N_SESSIONS, as_of=as_of)
        latest = latest_completed_session(session, exchange="XNYS")
        assert latest is not None
        persisted_session_dates(session, start=as_of, end=latest, exchange="XNYS")
        missing_bars_for_session(session, as_of, symbols=symbols)
        if held:
            bars_for_session_date(session, as_of, symbols=held)
    manifest = recorder.build(compute_settings_digest(_universe_metadata()))
    # round trip through the persisted JSON shape
    return EvaluationManifest.from_dict(manifest.to_dict())


def _settings_digest() -> str:
    return compute_settings_digest(_universe_metadata())


def _verify(manifest: EvaluationManifest | None, settings_digest: str | None = None):
    with session_scope(load_settings()) as session:
        return verify_manifest(
            session,
            manifest,
            current_settings_digest=settings_digest or _settings_digest(),
        )


def _evaluate_in_scope(symbols: list[str], *, held: list[str] | None = None) -> EvaluationManifest:
    with session_scope(load_settings()) as session:
        return _evaluate(session, symbols, held=held)


def _reingest(ticker: str, session_date: date, *, close: str) -> None:
    with session_scope(load_settings()) as session:
        symbol = session.execute(select(Symbol).where(Symbol.ticker == ticker)).scalar_one_or_none()
        if symbol is None:
            symbol = Symbol(ticker=ticker, **ready_symbol_fields())
            session.add(symbol)
            session.flush()
        upsert_daily_bars(
            session,
            [
                DailyBar(
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
            symbol.id,
        )


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def test_each_accessor_records_one_request(manifest_db: Settings) -> None:
    _seed(["AAA", "BBB"])
    recorder = ManifestRecorder()
    with session_scope(manifest_db) as session, recording(recorder):
        bars_for_sessions(session, symbol="AAA", n_sessions=N_SESSIONS, as_of=AS_OF)
        latest_completed_session(session, exchange="XNYS", as_of=AS_OF)
        persisted_session_dates(session, start=AS_OF, end=AS_OF, exchange="XNYS")
        bars_for_session_date(session, AS_OF, symbols=["BBB", "AAA"])
        # nested: missing_bars_for_session -> bars_for_session_date records ONCE
        missing_bars_for_session(session, AS_OF, symbols=["AAA", "BBB"])
    manifest = recorder.build(_settings_digest())

    assert [request.kind for request in manifest.requests] == [
        ManifestRequestKind.BARS_FOR_SESSIONS,
        ManifestRequestKind.LATEST_COMPLETED_SESSION,
        ManifestRequestKind.PERSISTED_SESSION_DATES,
        ManifestRequestKind.BARS_FOR_SESSION_DATE,
        ManifestRequestKind.MISSING_BARS_FOR_SESSION,
    ]
    assert manifest.version == MANIFEST_VERSION
    assert manifest.settings_digest == _settings_digest()
    valuation = manifest.requests[3]
    assert valuation.params["symbols"] == ["AAA", "BBB"]  # sorted
    assert valuation.count == 2
    missing = manifest.requests[4]
    assert missing.params["symbols"] == ["AAA", "BBB"]
    assert missing.count == 0


def test_empty_results_are_recorded(manifest_db: Settings) -> None:
    _seed(["AAA"])
    recorder = ManifestRecorder()
    with session_scope(manifest_db) as session, recording(recorder):
        assert bars_for_sessions(session, symbol="ZZZ", n_sessions=N_SESSIONS, as_of=AS_OF) == []
        assert missing_bars_for_session(session, AS_OF, symbols=["ZZZ"]) == ["ZZZ"]
        assert bars_for_session_date(session, AS_OF, symbols=["ZZZ"]) == {}
    manifest = recorder.build(_settings_digest())

    assert len(manifest.requests) == 3
    assert manifest.requests[0].digest == read_recording.EMPTY_DIGEST
    assert manifest.requests[0].count == 0
    assert manifest.requests[2].digest == read_recording.EMPTY_DIGEST


def test_resolved_bounds_are_recorded(manifest_db: Settings) -> None:
    _seed(["AAA"])
    recorder = ManifestRecorder()
    with session_scope(manifest_db) as session, recording(recorder):
        bars_for_sessions(session, symbol="AAA", n_sessions=N_SESSIONS)  # as_of=None
        latest = latest_completed_session(session, exchange="XNYS")  # unbounded
        bounded = latest_completed_session(session, exchange="XNYS", as_of=date(2024, 3, 1))
    manifest = recorder.build(_settings_digest())

    assert manifest.requests[0].params["as_of"] == date.today().isoformat()
    assert manifest.requests[1].params == {"exchange": "XNYS", "as_of_bound": latest.isoformat()}
    assert manifest.requests[2].params == {"exchange": "XNYS", "as_of_bound": "2024-03-01"}
    assert bounded == date(2024, 3, 1)


def test_unrecorded_calls_do_not_require_a_recorder(manifest_db: Settings) -> None:
    _seed(["AAA"])
    with session_scope(manifest_db) as session:
        assert len(bars_for_sessions(session, symbol="AAA", n_sessions=N_SESSIONS, as_of=AS_OF)) == N_SESSIONS


def test_duplicate_identical_requests_are_recorded_once(manifest_db: Settings) -> None:
    _seed(["AAA"])
    recorder = ManifestRecorder()
    with session_scope(manifest_db) as session, recording(recorder):
        bars_for_sessions(session, symbol="AAA", n_sessions=N_SESSIONS, as_of=AS_OF)
        bars_for_sessions(session, symbol="AAA", n_sessions=N_SESSIONS, as_of=AS_OF)
    assert len(recorder.build(_settings_digest()).requests) == 1


# ---------------------------------------------------------------------------
# bars_for_sessions_many
# ---------------------------------------------------------------------------


def test_bars_for_sessions_many_equals_per_symbol_reads_and_is_one_statement(
    manifest_db: Settings,
) -> None:
    _seed(_TICKERS)
    with session_scope(manifest_db) as session:
        expected = {
            ticker: bars_for_sessions(session, symbol=ticker, n_sessions=N_SESSIONS, as_of=AS_OF)
            for ticker in _TICKERS
        }
        with count_queries(session) as counter:
            many = bars_for_sessions_many(session, _TICKERS, N_SESSIONS, as_of=AS_OF)
    assert counter.count == 1
    assert many == expected


def test_bars_for_sessions_many_returns_empty_for_both_absent_shapes(manifest_db: Settings) -> None:
    _seed(["AAA"])
    with session_scope(manifest_db) as session:
        session.add(Symbol(ticker="NOBARS", **ready_symbol_fields()))
        session.flush()
        many = bars_for_sessions_many(session, ["AAA", "NOBARS", "NOROW"], N_SESSIONS, as_of=AS_OF)
    assert len(many["AAA"]) == N_SESSIONS
    assert many["NOBARS"] == []  # symbol row, zero bars
    assert many["NOROW"] == []  # no symbol row at all


# ---------------------------------------------------------------------------
# Verification acceptance cases
# ---------------------------------------------------------------------------


def test_fresh_manifest_matches_immediately(manifest_db: Settings) -> None:
    _seed(_TICKERS)
    manifest = _evaluate_in_scope(_TICKERS, held=["S00"])

    result = _verify(manifest)

    assert result.status is ManifestVerificationStatus.MATCHES
    assert result.mismatched_request is None


def test_manifest_missing_for_none() -> None:
    result = verify_manifest(None, None, current_settings_digest="x")  # type: ignore[arg-type]
    assert result.status is ManifestVerificationStatus.MANIFEST_MISSING


def test_identical_reingest_keeps_manifest_matching(manifest_db: Settings) -> None:
    _seed(_TICKERS)
    manifest = _evaluate_in_scope(_TICKERS, held=["S00"])
    with session_scope(manifest_db) as session:
        before = session.execute(
            select(DailyBarModel.updated_at).where(DailyBarModel.session_date == AS_OF)
        ).scalars().all()

    # the upsert refreshes updated_at but not a single value
    _reingest("S00", AS_OF, close="100.5")

    with session_scope(manifest_db) as session:
        after = session.execute(
            select(DailyBarModel.updated_at).where(DailyBarModel.session_date == AS_OF)
        ).scalars().all()
    assert max(after) > max(before)
    assert _verify(manifest).status is ManifestVerificationStatus.MATCHES


def test_corrected_close_makes_evaluation_stale(manifest_db: Settings) -> None:
    _seed(_TICKERS)
    manifest = _evaluate_in_scope(_TICKERS)

    _reingest("S03", date(2024, 3, 14), close="250.00")

    result = _verify(manifest)
    assert result.status is ManifestVerificationStatus.EVALUATION_DATA_CHANGED
    assert result.mismatched_request is not None
    assert result.mismatched_request.kind is ManifestRequestKind.BARS_FOR_SESSIONS


def test_backfilled_missing_bar_makes_evaluation_stale(manifest_db: Settings) -> None:
    seed_calendar(date(2024, 1, 2), date(2024, 6, 28))
    seed_bars(_TICKERS, sessions_between(FIRST, AS_OF))
    with session_scope(manifest_db) as session:
        # the as-of bar of one symbol is missing at evaluation time
        symbol_id = session.execute(select(Symbol.id).where(Symbol.ticker == "S05")).scalar_one()
        session.query(DailyBarModel).filter(
            DailyBarModel.symbol_id == symbol_id, DailyBarModel.session_date == AS_OF
        ).delete()
    manifest = _evaluate_in_scope(_TICKERS)

    _reingest("S05", AS_OF, close="100.5")

    assert _verify(manifest).status is ManifestVerificationStatus.EVALUATION_DATA_CHANGED


def test_symbol_entirely_absent_then_ingested_is_stale(manifest_db: Settings) -> None:
    """The 29 Sep shape: an empty recorded result whose digest later changes."""

    _seed(_TICKERS)
    manifest = _evaluate_in_scope([*_TICKERS, "NEWCO"])
    assert any(
        request.kind is ManifestRequestKind.BARS_FOR_SESSIONS
        and request.params["symbol"] == "NEWCO"
        and request.digest == read_recording.EMPTY_DIGEST
        for request in manifest.requests
    )
    assert _verify(manifest).status is ManifestVerificationStatus.MATCHES

    _reingest("NEWCO", AS_OF, close="100.5")

    assert _verify(manifest).status is ManifestVerificationStatus.EVALUATION_DATA_CHANGED


def test_strategy_parameter_change_is_strategy_settings_changed(manifest_db: Settings) -> None:
    _seed(_TICKERS)
    manifest = _evaluate_in_scope(_TICKERS)
    changed = compute_settings_digest(
        _universe_metadata(indicators={"short_window": 5, "long_window": 3, "warmup_periods": 3})
    )

    assert _verify(manifest, changed).status is ManifestVerificationStatus.STRATEGY_SETTINGS_CHANGED
    for override in (
        {"universe": ("A", "B", "C")},
        {"exits": {"close_below": "sma_3", "exit_window": 2}},
        {"version": "1.0.1"},
    ):
        assert compute_settings_digest(_universe_metadata(**override)) != _settings_digest()


def test_settings_digest_is_checked_before_any_data_read(manifest_db: Settings) -> None:
    _seed(_TICKERS)
    manifest = _evaluate_in_scope(_TICKERS)
    with session_scope(manifest_db) as session, count_queries(session) as counter:
        result = verify_manifest(session, manifest, current_settings_digest="different")
    assert result.status is ManifestVerificationStatus.STRATEGY_SETTINGS_CHANGED
    assert counter.count == 0


def test_risk_limit_configuration_change_still_matches(manifest_db: Settings) -> None:
    _seed(_TICKERS)
    manifest = _evaluate_in_scope(_TICKERS)
    base = _settings_digest()

    looser = _universe_metadata(risk={"max_positions": 99, "risk_per_trade": 0.5})
    assert compute_settings_digest(looser) == base
    assert compute_settings_digest(_universe_metadata(universe=("A", "B"))) == base  # order-insensitive
    assert compute_settings_digest(_universe_metadata(display_name="Renamed")) == base
    assert _verify(manifest, compute_settings_digest(looser)).status is ManifestVerificationStatus.MATCHES


def test_new_position_after_evaluation_still_matches(manifest_db: Settings) -> None:
    _seed(_TICKERS)
    # valuation read recorded with the ORIGINAL symbols; a later position adds a symbol
    with_valuation = _evaluate_in_scope(_TICKERS, held=["S00"])
    without_valuation = _evaluate_in_scope(_TICKERS)
    assert any(r.kind is ManifestRequestKind.BARS_FOR_SESSION_DATE for r in with_valuation.requests)
    assert not any(r.kind is ManifestRequestKind.BARS_FOR_SESSION_DATE for r in without_valuation.requests)

    # "a fill opens a new position": nothing the manifest re-reads is derived from positions
    assert _verify(with_valuation).status is ManifestVerificationStatus.MATCHES
    assert _verify(without_valuation).status is ManifestVerificationStatus.MATCHES


def test_calendar_synced_ahead_by_60_sessions_still_matches(manifest_db: Settings) -> None:
    seed_calendar(date(2024, 1, 2), date(2024, 3, 29))
    seed_bars(_TICKERS, sessions_between(FIRST, AS_OF))
    manifest = _evaluate_in_scope(_TICKERS, held=["S00"])

    with session_scope(manifest_db) as session:
        sessions_before = session.execute(select(func.count()).select_from(MarketSession)).scalar_one()
        upsert_market_sessions(session, date(2024, 3, 30), date(2024, 6, 28))
        sessions_after = session.execute(select(func.count()).select_from(MarketSession)).scalar_one()
    assert sessions_after - sessions_before >= 60

    assert _verify(manifest).status is ManifestVerificationStatus.MATCHES


def test_passage_of_a_day_with_new_later_bars_still_matches(manifest_db: Settings) -> None:
    _seed(_TICKERS, through=AS_OF)
    manifest = _evaluate_in_scope(_TICKERS)

    seed_bars(_TICKERS, sessions_between(date(2024, 3, 18), date(2024, 3, 22)))

    assert _verify(manifest).status is ManifestVerificationStatus.MATCHES


def test_manifest_dict_round_trip_and_format_errors() -> None:
    recorder = ManifestRecorder()
    manifest = recorder.build("digest")
    assert EvaluationManifest.from_dict(manifest.to_dict()) == manifest
    assert manifest.to_dict() == {"version": MANIFEST_VERSION, "settings_digest": "digest", "requests": []}
    for bad in (
        {},
        {"version": 99, "settings_digest": "d", "requests": []},
        {"version": 1, "settings_digest": "d", "requests": [{"kind": "select_everything"}]},
        {"version": 1, "settings_digest": "d", "requests": "nope"},
    ):
        with pytest.raises(ManifestFormatError):
            EvaluationManifest.from_dict(bad)


def test_verification_is_read_only(manifest_db: Settings) -> None:
    _seed(_TICKERS)
    manifest = _evaluate_in_scope(_TICKERS, held=["S00"])
    tables = (DailyBarModel, MarketSession, Symbol, StrategyRun, RiskEvent)

    def counts(session: SASession) -> list[int]:
        return [session.execute(select(func.count()).select_from(t)).scalar_one() for t in tables]

    flushes: list[object] = []

    def _before_flush(*_args: object) -> None:
        flushes.append(_args)

    with session_scope(manifest_db) as session:
        before = counts(session)
        event.listen(SASession, "before_flush", _before_flush)
        try:
            result = verify_manifest(session, manifest, current_settings_digest=_settings_digest())
            assert not session.new and not session.dirty and not session.deleted
        finally:
            event.remove(SASession, "before_flush", _before_flush)
        after = counts(session)
    assert result.status is ManifestVerificationStatus.MATCHES
    assert flushes == []
    assert before == after


# ---------------------------------------------------------------------------
# Query counts: bounded, independent of symbol count
# ---------------------------------------------------------------------------


def _wide_symbols(count: int) -> list[str]:
    return [f"W{i:02d}" for i in range(count)]


def _bar_window_manifest(symbols: list[str]) -> EvaluationManifest:
    recorder = ManifestRecorder()
    with session_scope(load_settings()) as session, recording(recorder):
        for ticker in symbols:
            bars_for_sessions(session, symbol=ticker, n_sessions=260, as_of=date(2025, 3, 31))
    return recorder.build(_settings_digest())


def _seed_wide(symbols: list[str]) -> None:
    seed_calendar(date(2024, 1, 2), date(2025, 6, 30))
    seed_bars(symbols, sessions_between(date(2024, 1, 2), date(2025, 3, 31)))


def test_verification_query_count_10_symbols_x_260_sessions_is_at_most_3(manifest_db: Settings) -> None:
    ten = _wide_symbols(10)
    _seed_wide(ten)
    manifest = _bar_window_manifest(ten)
    assert len(manifest.requests) == 10
    assert all(request.count == 260 for request in manifest.requests)

    with session_scope(manifest_db) as session, count_queries(session) as counter:
        result = verify_manifest(session, manifest, current_settings_digest=_settings_digest())

    assert result.status is ManifestVerificationStatus.MATCHES
    assert counter.count <= 3


def test_verification_query_count_is_the_same_for_20_symbols(manifest_db: Settings) -> None:
    ten, twenty = _wide_symbols(10), _wide_symbols(20)
    _seed_wide(twenty)
    counts: dict[int, int] = {}
    for symbols in (ten, twenty):
        manifest = _bar_window_manifest(symbols)
        with session_scope(manifest_db) as session, count_queries(session) as counter:
            result = verify_manifest(session, manifest, current_settings_digest=_settings_digest())
        assert result.status is ManifestVerificationStatus.MATCHES
        counts[len(symbols)] = counter.count
    assert counts[10] == counts[20]
    assert counts[20] <= 3


def test_whole_evaluation_shaped_manifest_verifies_in_at_most_6_statements(manifest_db: Settings) -> None:
    _seed(_TICKERS)
    manifest = _evaluate_in_scope(_TICKERS, held=["S00", "S01"])

    with session_scope(manifest_db) as session, count_queries(session) as counter:
        result = verify_manifest(session, manifest, current_settings_digest=_settings_digest())

    assert result.status is ManifestVerificationStatus.MATCHES
    assert counter.count <= 6
