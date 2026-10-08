"""DB-backed S0 checks: migration 0031, Tiingo ingestion, catalog, integrity, freeze,
restore, the research-DB script, example seeding, research strategy registry, and the
evaluation-manifest parameter pin for the trading path."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
import sqlalchemy as sa
from alembic import command
from scripts.migrate import build_alembic_config
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import DBAPIError
from tests.support.migrated_db import _admin_params, _connect_admin, migrated_database
from tests.support.research_fixtures import make_bars, seed_research_bars, xnys_sessions

from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import StrategyVersion
from trading_platform.db.models.daily_bar import DailyBar as DailyBarModel
from trading_platform.db.models.market_data_ingestion_run import MarketDataIngestionRun
from trading_platform.db.models.market_session import MarketSession
from trading_platform.db.models.research import AssetCatalogEntry
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import session_scope
from trading_platform.services.calendar import upsert_market_sessions
from trading_platform.services.evaluation_manifest import ManifestRecorder
from trading_platform.services.read_recording import recording
from trading_platform.services.research import catalog as catalog_module
from trading_platform.services.research.examples import (
    EXAMPLE_KEYS,
    example_strategy_id,
    load_example_specs,
    seed_example_versions,
)
from trading_platform.services.research.freeze import (
    InputsChangedAfterFreezeError,
    IntegrityBlocksFreezeError,
    collect_inputs,
    create_data_freeze,
    inputs_changed_after_freeze,
    require_inputs_unchanged,
    restore_inputs,
    verify_freeze_digest,
)
from trading_platform.services.research.integrity import (
    IntegrityCode,
    check_asset_rows,
    check_research_inputs,
)
from trading_platform.services.research.tiingo_ingestion import (
    PROVIDER_LEVEL_ERRORS,
    ingest_tiingo_daily_bars,
)
from trading_platform.services.tiingo import (
    TiingoAssetMetadata,
    TiingoAuthError,
    TiingoDailyRow,
    TiingoRateLimitError,
    TiingoRequestBudgetExceededError,
)
from trading_platform.strategies.research_registry import (
    UnknownStrategyVersionError,
    build_research_strategy_registry,
    strategy_from_version,
)
from trading_platform.strategies.trend_following_daily import strategy as tf_module

pytestmark = pytest.mark.usefixtures("research_db")


@pytest.fixture()
def research_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with migrated_database(monkeypatch, "research_s0") as name:
        yield name


# ---------------------------------------------------------------------------
# Migration 0031
# ---------------------------------------------------------------------------


def test_migration_head_tables_columns_and_append_only_trigger(research_db: str) -> None:
    settings = load_settings()
    engine = sa.create_engine(settings.database.url)
    inspector = inspect(engine)
    with engine.connect() as connection:
        assert connection.execute(text("select version_num from alembic_version")).scalar_one() == "0033_research_assistant"
    tables = set(inspector.get_table_names())
    assert {
        "ai_drafts", "strategy_versions", "strategy_drafts", "asset_catalog", "asset_lists",
        "asset_list_items", "data_freezes", "research_studies", "study_revisions",
        "research_freezes", "test_window_exposures", "research_run_links",
    } <= tables
    bar_cols = {c["name"]: c for c in inspector.get_columns("daily_bars")}
    assert str(bar_cols["volume"]["type"]).upper() == "BIGINT"
    assert {"split_factor", "dividend_cash"} <= set(bar_cols)
    run_cols = {c["name"] for c in inspector.get_columns("strategy_runs")}
    assert not {"study_revision_id", "strategy_version_id", "window_role"} & run_cols  # shared table untouched
    link_cols = {c["name"] for c in inspector.get_columns("research_run_links")}
    assert {"run_id", "study_revision_id", "strategy_version_id", "asset", "window_role", "spec_sha256", "code_sha", "input_digest", "rerun_of"} <= link_cols
    assert "rounding_slack" in {c["name"] for c in inspector.get_columns("backtest_trades")}
    assert "uq_research_run_links_final_test_once" in {i["name"] for i in inspector.get_indexes("research_run_links")}
    engine.dispose()

    with session_scope(settings) as session:
        session.add(
            StrategyVersion(
                id=uuid.uuid4(), strategy_id=uuid.uuid4(), version_no=1, name="x", yaml_text="y",
                spec_json={}, spec_sha256="a" * 64, history_required=1, history_minimum=1,
                scale_class="price_scale_free", explanation="e", approved_at=datetime.now(UTC),
                created_at=datetime.now(UTC),
            )
        )
    for stmt in ("UPDATE strategy_versions SET name = 'z'", "DELETE FROM strategy_versions"):
        with pytest.raises(DBAPIError) as info, session_scope(settings) as session:
            session.execute(text(stmt))
        assert "append-only" in str(info.value)


def test_migration_0031_round_trips(research_db: str) -> None:
    config = build_alembic_config()
    command.downgrade(config, "0030_phase20_1_evidence_update_guards")
    engine = sa.create_engine(load_settings().database.url)
    assert "asset_catalog" not in inspect(engine).get_table_names()
    engine.dispose()
    command.upgrade(config, "head")
    engine = sa.create_engine(load_settings().database.url)
    assert "asset_catalog" in inspect(engine).get_table_names()
    engine.dispose()


# ---------------------------------------------------------------------------
# Trading-path pin: manifest parameters byte-identical
# ---------------------------------------------------------------------------


def test_trading_manifest_parameters_are_byte_identical(research_db: str) -> None:
    settings = load_settings()
    sessions = xnys_sessions(date(2024, 1, 2), 210)
    bars = make_bars("AAPL", sessions)
    with session_scope(settings) as session:
        seed_research_bars(session, "AAPL", bars, provider="polygon", both_series=False, split_factor=None, dividend_cash=None)
    universe = settings.strategies.trend_following_daily.universe
    object.__setattr__(settings.strategies.trend_following_daily, "universe", ("AAPL",))
    try:
        recorder = ManifestRecorder()
        with session_scope(settings) as session, recording(recorder):
            tf_module.TrendFollowingDailyStrategy(settings).generate_signals(session, sessions[-1])
    finally:
        object.__setattr__(settings.strategies.trend_following_daily, "universe", universe)
    params = [r.params for r in recorder._requests if r.kind.value == "bars_for_sessions"]
    assert params == [
        {"symbol": "AAPL", "n_sessions": 200, "as_of": sessions[-1].isoformat(), "exchange": "XNYS", "adjusted": True, "provider": "polygon"}
    ]


# ---------------------------------------------------------------------------
# Tiingo ingestion
# ---------------------------------------------------------------------------


class StubTiingoClient:
    def __init__(self, series: dict[str, list[TiingoDailyRow]], *, failing: set[str] | None = None) -> None:
        self.series = series
        self.failing = failing or set()
        self.requests_made = 0

    def fetch_metadata(self, ticker: str) -> TiingoAssetMetadata:
        self.requests_made += 1
        if ticker in self.failing:
            raise RuntimeError("provider failure")
        return TiingoAssetMetadata(ticker=ticker, name=f"{ticker} Corp", exchange_code="NASDAQ", start_date=date(1990, 1, 2), end_date=date(2026, 10, 6))

    def fetch_daily_prices(self, ticker: str, from_date: date, to_date: date) -> list[TiingoDailyRow]:
        self.requests_made += 1
        return [r for r in self.series[ticker] if from_date <= r.session_date <= to_date]

    def close(self) -> None:
        pass


def _tiingo_rows(sessions: list[date], *, scale: Decimal = Decimal("0.25"), big_volume: bool = False) -> list[TiingoDailyRow]:
    rows = []
    for index, session_date in enumerate(sessions):
        close = Decimal(100 + index)
        rows.append(
            TiingoDailyRow(
                session_date=session_date,
                open=close - 1, high=close + 1, low=close - 2, close=close,
                volume=1_000 + index,
                adj_open=(close - 1) * scale, adj_high=(close + 1) * scale, adj_low=(close - 2) * scale, adj_close=close * scale,
                adj_volume=(3_000_000_000 if big_volume else 4_000 + index),
                div_cash=Decimal(0), split_factor=Decimal(1),
            )
        )
    return rows


def test_tiingo_ingestion_writes_both_series_with_factors_and_metadata(research_db: str) -> None:
    settings = load_settings()
    sessions = xnys_sessions(date(2024, 3, 4), 5)
    with session_scope(settings) as session:
        upsert_market_sessions(session, sessions[0], sessions[-1])
        session.add(AssetCatalogEntry(id=uuid.uuid4(), provider="tiingo", ticker="SPY", exchange="NYSE", asset_type="ETF", currency="USD", synced_at=datetime.now(UTC)))
    client = StubTiingoClient({"AAPL": _tiingo_rows(sessions, big_volume=True), "SPY": _tiingo_rows(sessions)})
    result = ingest_tiingo_daily_bars(assets=["AAPL", "SPY"], from_date=sessions[0], to_date=sessions[-1], settings=settings, client=client)
    assert result.run_status == "succeeded" and result.bars_upserted == 20
    with session_scope(settings) as session:
        aapl = session.execute(select(Symbol).where(Symbol.ticker == "AAPL")).scalar_one()
        assert (aapl.name, aapl.metadata_provider, aapl.list_date) == ("AAPL Corp", "tiingo", date(1990, 1, 2))
        spy = session.execute(select(Symbol).where(Symbol.ticker == "SPY")).scalar_one()
        assert (spy.market, spy.symbol_type) == ("stocks", "ETF")
        rows = session.execute(select(DailyBarModel).where(DailyBarModel.symbol_id == aapl.id).order_by(DailyBarModel.session_date, DailyBarModel.adjusted)).scalars().all()
        assert len(rows) == 10 and all(r.provider == "tiingo" for r in rows)
        raw, adjusted = rows[0], rows[1]
        assert (raw.adjusted, adjusted.adjusted) == (False, True)
        assert adjusted.close == raw.close * Decimal("0.25")
        assert adjusted.volume == 3_000_000_000  # above int32
        assert raw.split_factor == Decimal(1) and raw.dividend_cash == Decimal(0)
        run = session.execute(select(MarketDataIngestionRun)).scalar_one()
        assert (run.provider, run.status, run.page_count) == ("tiingo", "succeeded", client.requests_made)
    # idempotent re-ingest
    again = ingest_tiingo_daily_bars(assets=["AAPL"], from_date=sessions[0], to_date=sessions[-1], settings=settings, client=client)
    with session_scope(settings) as session:
        count = session.execute(select(sa.func.count()).select_from(DailyBarModel)).scalar_one()
    assert count == 20 and again.run_status == "succeeded"


def test_tiingo_ingestion_partial_and_all_failed(research_db: str) -> None:
    settings = load_settings()
    sessions = xnys_sessions(date(2024, 3, 4), 3)
    with session_scope(settings) as session:
        upsert_market_sessions(session, sessions[0], sessions[-1])
    client = StubTiingoClient({"AAPL": _tiingo_rows(sessions), "MSFT": _tiingo_rows(sessions)}, failing={"MSFT"})
    result = ingest_tiingo_daily_bars(assets=["AAPL", "MSFT"], from_date=sessions[0], to_date=sessions[-1], settings=settings, client=client)
    assert result.run_status == "partial" and result.symbols_failed == ["MSFT"]
    all_fail = StubTiingoClient({"MSFT": []}, failing={"MSFT"})
    result = ingest_tiingo_daily_bars(assets=["MSFT"], from_date=sessions[0], to_date=sessions[-1], settings=settings, client=all_fail)
    assert result.run_status == "failed"
    with pytest.raises(Exception):
        result.raise_for_all_symbols_failed()


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

TIINGO_CSV = """ticker,exchange,assetType,priceCurrency,startDate,endDate
AAPL,NASDAQ,Stock,USD,1980-12-12,2026-10-06
SPY,NYSE,ETF,USD,1993-01-29,2026-10-06
SPY,NYSE ARCA,ETF,USD,1993-01-29,2026-10-07
BRK-B,NYSE,Stock,USD,1996-05-09,2026-10-06
BRK.B,NYSE,Stock,USD,1996-05-09,2026-10-06
NONAME,NASDAQ,Stock,USD,2010-01-04,2026-10-06
SECONLY,NASDAQ,Stock,USD,2010-01-04,2026-10-06
XOTC,PINK,Stock,USD,2000-01-03,2026-10-06
VFIAX,NMFQS,Mutual Fund,USD,2000-11-13,2026-10-06
EURSTK,NYSE,Stock,EUR,2000-01-03,2026-10-06
"""
NASDAQ_TXT = """Nasdaq Traded|Symbol|Security Name|Listing Exchange|Market Category|ETF|Round Lot Size|Test Issue|Financial Status|CQS Symbol|NASDAQ Symbol|NextShares
Y|AAPL|Apple Inc. - Common Stock|Q|Q|N|100|N|N||AAPL|N
Y|SPY|SPDR S&P 500 ETF Trust|P| |Y|100|N||SPY|SPY|N
Y|BRK.B|Berkshire Hathaway Inc. Class B|N| |N|100|N||BRK.B|BRK.B|N
Y|ZTST|Test Issue Should Be Ignored|Q|Q|N|100|Y|N||ZTST|N
File Creation Time: 1007202608:03|||||
"""
SEC_JSON = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, "1": {"cik_str": 1, "ticker": "SECONLY", "title": "Sec Only Corp"}}


def test_catalog_sync_scope_names_sources_search_and_enrichment(research_db: str) -> None:
    settings = load_settings()
    sources = catalog_module.CatalogSources(tiingo_csv_text=TIINGO_CSV, nasdaq_text=NASDAQ_TXT, sec_json=SEC_JSON)
    with session_scope(settings) as session:
        report = catalog_module.sync_asset_catalog(session, sources)
        # Two source rows repeat a ticker (SPY on a second exchange, BRK.B beside BRK-B): one
        # row per ticker survives, the latest catalog_end wins, the drop is reported.
        assert (report.rows_total, report.rows_named, report.duplicate_tickers) == (5, 4, 2)
        assert report.named_by_source == {"nasdaq_trader": 3, "sec_edgar": 1}
        assert report.to_dict()["name_search_coverage"] == "populated names only"
        rows = {r.ticker: r for r in session.execute(select(AssetCatalogEntry)).scalars()}
        assert set(rows) == {"AAPL", "SPY", "BRK-B", "NONAME", "SECONLY"}
        assert rows["SPY"].exchange == "NYSE ARCA" and rows["SPY"].catalog_end == date(2026, 10, 7)
        assert rows["BRK-B"].name == "Berkshire Hathaway Inc. Class B" and rows["BRK-B"].name_source == "nasdaq_trader"
        assert rows["SECONLY"].name_source == "sec_edgar" and rows["NONAME"].name is None
        assert catalog_module.name_coverage(session) == (4, 5)
        assert [r.ticker for r in catalog_module.search_assets(session, "apple")] == ["AAPL"]
        assert [r.ticker for r in catalog_module.search_assets(session, "br")] == ["BRK-B"]
        assert catalog_module.search_assets(session, "noname corp") == []  # unnamed rows are invisible to name search

        class StubClient:
            def fetch_metadata(self, ticker: str) -> TiingoAssetMetadata:
                return TiingoAssetMetadata(ticker=ticker, name="No Name Holdings", exchange_code="NASDAQ", start_date=None, end_date=None)

        enriched = catalog_module.enrich_asset_name(session, StubClient(), "NONAME")
        assert enriched is not None and enriched.name_source == "tiingo_metadata"
        assert catalog_module.name_coverage(session) == (5, 5)
        # a re-sync without directory names keeps the fetched name
        catalog_module.sync_asset_catalog(session, catalog_module.CatalogSources(tiingo_csv_text=TIINGO_CSV))
        assert catalog_module.get_asset(session, "noname").name == "No Name Holdings"


# ---------------------------------------------------------------------------
# Integrity (pure row checks + DB report) and freeze gating
# ---------------------------------------------------------------------------


def _row(session_date: date, adjusted: bool, *, provider: str = "tiingo", o=100, h=101, lo=99, c=100, v=1000, sf=Decimal(1), dc=Decimal(0)):
    return _R(session_date, adjusted, provider, Decimal(o), Decimal(h), Decimal(lo), Decimal(c), v, sf, dc)


class _R:
    def __init__(self, session_date, adjusted, provider, open, high, low, close, volume, split_factor, dividend_cash):
        self.session_date, self.adjusted, self.provider = session_date, adjusted, provider
        self.open, self.high, self.low, self.close, self.volume = open, high, low, close, volume
        self.split_factor, self.dividend_cash = split_factor, dividend_cash


def _pair(session_date: date, **kw):
    return [_row(session_date, False, **kw), _row(session_date, True, **kw)]


def _codes(session_dates, rows, today=date(2024, 12, 31)):
    return {f.code for f in check_asset_rows("A", session_dates, rows, provider="tiingo", today=today)}


def test_integrity_row_codes_each_have_a_fixture() -> None:
    d = xnys_sessions(date(2024, 3, 4), 3)
    clean = _pair(d[0]) + _pair(d[1]) + _pair(d[2])
    assert _codes(d, clean) == set()
    assert IntegrityCode.DUPLICATE_KEY in _codes(d, clean + [_row(d[0], False)])
    assert IntegrityCode.DATE_NOT_SESSION in _codes(d, clean + _pair(date(2024, 3, 9)))
    assert IntegrityCode.ORDERING in _codes(d, clean, today=d[1])
    assert IntegrityCode.PRICE_NONFINITE in _codes(d, _pair(d[0], c=Decimal("NaN")) + _pair(d[1]) + _pair(d[2]))
    assert IntegrityCode.PRICE_NONPOSITIVE in _codes(d, _pair(d[0], lo=0) + _pair(d[1]) + _pair(d[2]))
    assert IntegrityCode.OHLC_RELATION in _codes(d, _pair(d[0], h=99) + _pair(d[1]) + _pair(d[2]))
    assert IntegrityCode.VOLUME_INVALID in _codes(d, _pair(d[0], v=-1) + _pair(d[1]) + _pair(d[2]))
    assert IntegrityCode.MISSING_SESSION in _codes(d, _pair(d[0]) + _pair(d[2]))
    assert IntegrityCode.PAIR_MISSING in _codes(d, clean[:-1])
    inconsistent = clean[:-2] + [_row(d[2], False), _row(d[2], True, h=150)]
    assert IntegrityCode.ADJUSTMENT_RATIO_INCONSISTENT in _codes(d, inconsistent)
    # Provider-consistent rows stored at six decimals (the S6 smoke run's SPY shape): the
    # per-field ratios differ from the close ratio by ~2.6e-9 only because each stored value
    # was rounded; that is not an inconsistency. A real 1e-4 drift on one field still is.
    factor = Decimal("0.938480139")
    raw = {"o": Decimal("433.59"), "h": Decimal("441.07"), "lo": Decimal("433.19"), "c": Decimal("441.07")}
    rounded = {k: (v * factor).quantize(Decimal("0.000001")) for k, v in raw.items()}
    stored = clean[:-2] + [_row(d[2], False, **raw), _row(d[2], True, **rounded)]
    assert IntegrityCode.ADJUSTMENT_RATIO_INCONSISTENT not in _codes(d, stored)
    drifted = clean[:-2] + [_row(d[2], False, **raw), _row(d[2], True, **{**rounded, "h": rounded["h"] + Decimal("0.05")})]
    assert IntegrityCode.ADJUSTMENT_RATIO_INCONSISTENT in _codes(d, drifted)
    assert IntegrityCode.SOURCE_MIXED in _codes(d, clean + [_row(d[0], True, provider="polygon")])
    assert IntegrityCode.FACTOR_MISSING in _codes(d, _pair(d[0], sf=None) + _pair(d[1]) + _pair(d[2]))
    assert IntegrityCode.ASSET_ABSENT in _codes(d, [])
    jump = _pair(d[0]) + _pair(d[1]) + _pair(d[2], o=200, h=201, lo=199, c=200)
    assert {IntegrityCode.EXTREME_MOVE_ADJUSTED, IntegrityCode.EXTREME_MOVE_RAW_UNEXPLAINED} <= _codes(d, jump)
    explained = _pair(d[0]) + _pair(d[1]) + [_row(d[2], False, o=25, h=26, lo=24, c=25, sf=Decimal(4)), _row(d[2], True)]
    assert IntegrityCode.EXTREME_MOVE_RAW_UNEXPLAINED not in _codes(d, explained)
    assert IntegrityCode.ZERO_VOLUME_SESSION in _codes(d, _pair(d[0], v=0) + _pair(d[1]) + _pair(d[2]))


def test_integrity_report_and_freeze_gate_on_the_database(research_db: str, tmp_path: Path) -> None:
    settings = load_settings()
    sessions = xnys_sessions(date(2024, 1, 2), 30)
    with session_scope(settings) as session:
        seed_research_bars(session, "AAPL", make_bars("AAPL", sessions), provider="tiingo")
        seed_research_bars(session, "MSFT", make_bars("MSFT", sessions, seed=2), provider="tiingo")
        report = check_research_inputs(session, assets=["AAPL", "MSFT"], range_start=sessions[0], range_end=sessions[-1], provider="tiingo")
        assert report.ok and report.sessions_checked == 30
        # a warning does not block; an error does
        msft = session.execute(select(Symbol).where(Symbol.ticker == "MSFT")).scalar_one()
        session.execute(sa.update(DailyBarModel).where(DailyBarModel.symbol_id == msft.id, DailyBarModel.session_date == sessions[3]).values(volume=0))
        warned = check_research_inputs(session, assets=["AAPL", "MSFT"], range_start=sessions[0], range_end=sessions[-1], provider="tiingo")
        assert warned.ok and warned.counts() == {"zero_volume_session": 2}
        session.execute(sa.delete(DailyBarModel).where(DailyBarModel.symbol_id == msft.id, DailyBarModel.session_date == sessions[5], DailyBarModel.adjusted.is_(True)))
        broken = check_research_inputs(session, assets=["AAPL", "MSFT"], range_start=sessions[0], range_end=sessions[-1], provider="tiingo")
        assert not broken.ok and IntegrityCode.PAIR_MISSING in {f.code for f in broken.errors}
        with pytest.raises(IntegrityBlocksFreezeError):
            create_data_freeze(session, settings, assets=["AAPL", "MSFT"], range_start=sessions[0], range_end=sessions[-1], provider="tiingo", integrity=broken, export_root=tmp_path)


def test_freeze_export_change_detection_and_restore(research_db: str, tmp_path: Path) -> None:
    settings = load_settings()
    sessions = xnys_sessions(date(2024, 1, 2), 40)
    with session_scope(settings) as session:
        seed_research_bars(session, "AAPL", make_bars("AAPL", sessions), provider="tiingo")
        report = check_research_inputs(session, assets=["AAPL"], range_start=sessions[0], range_end=sessions[-1], provider="tiingo")
        freeze = create_data_freeze(session, settings, assets=["AAPL"], range_start=sessions[0], range_end=sessions[-1], provider="tiingo", integrity=report, export_root=tmp_path)
        freeze_id = freeze.id
        inputs_dir = Path(freeze.inputs_path)
        digest = freeze.input_digest
    assert {p.name for p in inputs_dir.iterdir()} == {"daily_bars.csv", "market_sessions.csv", "symbols.csv", "INTEGRITY.json", "MANIFEST.json"}
    assert str(inputs_dir).startswith(str(tmp_path))

    from trading_platform.db.models.research import DataFreeze

    with session_scope(settings) as session:
        freeze = session.get(DataFreeze, freeze_id)
        assert verify_freeze_digest(session, freeze)
        assert inputs_changed_after_freeze(session, freeze) == []
        assert collect_inputs(session, assets=["AAPL"], range_start=sessions[0], range_end=sessions[-1], provider="tiingo").digest == digest
        aapl = session.execute(select(Symbol).where(Symbol.ticker == "AAPL")).scalar_one()
        session.execute(
            sa.update(DailyBarModel)
            .where(DailyBarModel.symbol_id == aapl.id, DailyBarModel.session_date == sessions[10], DailyBarModel.adjusted.is_(True))
            .values(close=Decimal("1.5"), updated_at=datetime.now(UTC) + timedelta(seconds=1))
        )
        session.flush()
        reasons = inputs_changed_after_freeze(session, freeze)
        assert reasons and "bar row" in reasons[0]
        assert not verify_freeze_digest(session, freeze)
        with pytest.raises(InputsChangedAfterFreezeError) as info:
            require_inputs_unchanged(session, freeze)
        assert info.value.code == "inputs_changed_after_freeze"

    # Restore into an emptied database reproduces the digest exactly.
    with session_scope(settings) as session:
        session.execute(sa.delete(DailyBarModel))
        session.execute(sa.delete(Symbol))
        session.execute(sa.delete(MarketSession))
    with session_scope(settings) as session:
        restored = restore_inputs(session, inputs_dir)
        assert restored.digest_verified and restored.bars_inserted == 80 and restored.sessions_inserted == 40
        freeze = session.get(DataFreeze, freeze_id)
        assert verify_freeze_digest(session, freeze)


def test_create_research_db_script_creates_migrates_refuses_trading_name(research_db: str, tmp_path: Path) -> None:
    from scripts import create_research_db

    assert create_research_db.main(["--database", "trading_platform"]) == 2
    name = f"research_script_{uuid.uuid4().hex[:8]}"
    try:
        assert create_research_db.main(["--database", name]) == 0
        params = _admin_params()
        with psycopg.connect(host=params["host"], port=params["port"], user=params["user"], password=params["password"], dbname=name) as connection:
            assert connection.execute("select version_num from alembic_version").fetchone()[0] == "0033_research_assistant"
        assert create_research_db.main(["--database", name]) == 0  # idempotent
    finally:
        os.environ["TRADING_PLATFORM_DATABASE__NAME"] = research_db
        clear_settings_cache()
        with _connect_admin() as connection:
            with connection.cursor() as cursor:
                # Only this role's backends: an autovacuum worker on the fresh database belongs
                # to another role and terminating it raises InsufficientPrivilege (the same
                # intermittent teardown error tests/test_alpaca_execution.py shows); the
                # ``migrated_database`` helper applies the same filter.
                cursor.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND usename = current_user AND pid <> pg_backend_pid()",
                    (name,),
                )
                cursor.execute(f'DROP DATABASE IF EXISTS "{name}"')


# ---------------------------------------------------------------------------
# Example seeding and the research strategy registry
# ---------------------------------------------------------------------------


def test_examples_seed_idempotently_and_versions_are_immutable(research_db: str) -> None:
    settings = load_settings()
    examples = {e.key: e for e in load_example_specs()}
    with session_scope(settings) as session:
        first = seed_example_versions(session, now=datetime(2026, 10, 7, tzinfo=UTC))
        assert len(first) == 4 and all(v.source == "example" and v.version_no == 1 for v in first)
        assert {v.strategy_id for v in first} == {example_strategy_id(k) for k in EXAMPLE_KEYS}
        for version in first:
            key = next(k for k in EXAMPLE_KEYS if example_strategy_id(k) == version.strategy_id)
            assert version.spec_sha256 == examples[key].compiled.spec_sha256
            assert version.history_required == examples[key].compiled.history_required
        again = seed_example_versions(session)
        assert {v.id for v in again} == {v.id for v in first}
    with session_scope(settings) as session:
        version = session.execute(select(StrategyVersion)).scalars().first()
        version.name = "tampered"
        with pytest.raises(DBAPIError):
            session.flush()
        session.rollback()


def test_research_registry_resolves_versions_and_checks_hashes(research_db: str) -> None:
    settings = load_settings()
    with session_scope(settings) as session:
        versions = seed_example_versions(session)
        registry = build_research_strategy_registry(settings, session, universe=("AAPL",))
        assert len(registry.list_keys()) == 4
        rsi_version = next(v for v in versions if v.strategy_id == example_strategy_id("rsi_mean_reversion_daily"))
        strategy = registry.resolve(rsi_version.id)
        assert strategy.warmup_periods == 100 and strategy.metadata.universe == ("AAPL",)
        assert strategy.strategy_id == f"research:{rsi_version.id}"
        with pytest.raises(UnknownStrategyVersionError):
            registry.resolve(uuid.uuid4())
        tampered = StrategyVersion(
            id=uuid.uuid4(), strategy_id=uuid.uuid4(), version_no=1, name="t", yaml_text="",
            spec_json=rsi_version.spec_json, spec_sha256="0" * 64, history_required=100, history_minimum=15,
            scale_class="price_scale_free", explanation="", approved_at=datetime.now(UTC), created_at=datetime.now(UTC),
        )
        with pytest.raises(ValueError):
            strategy_from_version(settings, tampered, universe=("AAPL",))


# ---------------------------------------------------------------------------
# Provider-level failures abort the run with their closed code (review fix, 2026-10-07)
# ---------------------------------------------------------------------------


class RateLimitedAfterOneClient(StubTiingoClient):
    """Serves the first asset, then answers every request with HTTP 429."""

    def __init__(self, series):
        super().__init__(series)
        self.metadata_calls = 0

    def fetch_metadata(self, ticker: str) -> TiingoAssetMetadata:
        self.metadata_calls += 1
        if self.metadata_calls > 1:
            raise TiingoRateLimitError("429")
        return super().fetch_metadata(ticker)


def test_rate_limit_aborts_the_run_with_the_closed_code_and_propagates(research_db: str) -> None:
    settings = load_settings()
    sessions = xnys_sessions(date(2024, 3, 4), 3)
    with session_scope(settings) as session:
        upsert_market_sessions(session, sessions[0], sessions[-1])
    client = RateLimitedAfterOneClient({t: _tiingo_rows(sessions) for t in ("AAPL", "MSFT", "NVDA")})
    with pytest.raises(TiingoRateLimitError):
        ingest_tiingo_daily_bars(assets=["AAPL", "MSFT", "NVDA"], from_date=sessions[0], to_date=sessions[-1], settings=settings, client=client)
    # The loop stopped at the first 429: NVDA was never requested against the exhausted budget.
    assert client.metadata_calls == 2
    with session_scope(settings) as session:
        run = session.execute(select(MarketDataIngestionRun)).scalar_one()
        assert (run.status, run.error_message, run.symbols_failed) == ("failed", "provider_rate_limited", ["MSFT"])
        assert run.bars_upserted == 6 and run.completed_at is not None
    assert PROVIDER_LEVEL_ERRORS == (TiingoAuthError, TiingoRateLimitError, TiingoRequestBudgetExceededError)


def test_missing_key_refuses_before_any_run_row_exists(research_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__TIINGO__API_KEY", "")
    clear_settings_cache()
    settings = load_settings()
    assert settings.research.tiingo.api_key == ""
    with pytest.raises(TiingoAuthError):
        ingest_tiingo_daily_bars(assets=["AAPL"], from_date=date(2024, 3, 4), to_date=date(2024, 3, 6), settings=settings)
    with session_scope(settings) as session:
        assert session.execute(select(sa.func.count()).select_from(MarketDataIngestionRun)).scalar_one() == 0


def test_name_enrichment_asks_the_provider_at_most_once_per_asset(research_db: str) -> None:
    settings = load_settings()
    with session_scope(settings) as session:
        session.add(AssetCatalogEntry(id=uuid.uuid4(), provider="tiingo", ticker="NONAME", exchange="NASDAQ", asset_type="Stock", currency="USD", synced_at=datetime.now(UTC)))
        session.flush()

        class NamelessClient:
            calls = 0

            def fetch_metadata(self, ticker: str) -> TiingoAssetMetadata:
                self.calls += 1
                return TiingoAssetMetadata(ticker=ticker, name=None, exchange_code=None, start_date=None, end_date=None)

        client = NamelessClient()
        first = catalog_module.enrich_asset_name(session, client, "NONAME")
        assert first is not None and first.name is None and first.names_fetched_at is not None
        second = catalog_module.enrich_asset_name(session, client, "NONAME")
        assert second is first and client.calls == 1


# ---------------------------------------------------------------------------
# Contract §9 item 3 on the database path: approved versions, registry, real bar loader
# ---------------------------------------------------------------------------


def test_rsi_history_dependence_through_approved_versions_and_the_database(research_db: str) -> None:
    """A ``history: 50`` version approved through the S2 service and the seeded
    ``history: 100`` example, both resolved by the research registry and run against
    bars loaded by ``bars_for_sessions`` from the migrated database; the 100-bar version
    matches the original Python strategy read from the same rows."""

    from trading_platform.services.research.strategies import ResearchStrategyService
    from trading_platform.strategies.rsi_mean_reversion_daily import strategy as rsi_module
    from trading_platform.strategies.signals import SignalDirection

    settings = load_settings()
    sessions = xnys_sessions(date(2016, 1, 4), 900)  # same price path as the in-memory parity fixture
    bars = make_bars("AAPL", sessions, seed=3, volatility=0.025)
    with session_scope(settings) as session:
        seed_research_bars(session, "AAPL", bars, provider="polygon", both_series=False, split_factor=None, dividend_cash=None)
        seeded = {v.strategy_id: v for v in seed_example_versions(session)}
    example_100 = seeded[example_strategy_id("rsi_mean_reversion_daily")]
    yaml_50 = example_100.yaml_text.replace("history: 100", "history: 50")
    assert yaml_50 != example_100.yaml_text
    service = ResearchStrategyService(settings)
    version_50 = service.approve_draft(uuid.UUID(service.create_draft(title="rsi 50", yaml_text=yaml_50)["draft_id"]))
    assert version_50["history_required"] == 50

    universe = settings.strategies.rsi_mean_reversion_daily.universe
    object.__setattr__(settings.strategies.rsi_mean_reversion_daily, "universe", ("AAPL",))
    try:
        with session_scope(settings) as session:
            registry = build_research_strategy_registry(settings, session, universe=("AAPL",))
            s50 = registry.resolve(version_50["version_id"])
            s100 = registry.resolve(example_100.id)
            original = rsi_module.RsiMeanReversionDailyStrategy(settings)
            differing_values = 0
            differing_directions = 0
            directions_seen: set[SignalDirection] = set()
            for as_of in sessions[150:]:
                a = s50.generate_signals(session, as_of).signals[0]
                b = s100.generate_signals(session, as_of).signals[0]
                o = original.generate_signals(session, as_of).signals[0]
                directions_seen.add(b.direction)
                if a.indicators.values["rsi14"] != b.indicators.values["rsi14"]:
                    differing_values += 1
                if a.direction != b.direction:
                    differing_directions += 1
                assert (o.direction, o.indicators.values["rsi"]) == (b.direction, b.indicators.values["rsi14"]), as_of
    finally:
        object.__setattr__(settings.strategies.rsi_mean_reversion_daily, "universe", universe)
    assert differing_values > 600 and differing_directions >= 1
    assert {SignalDirection.LONG, SignalDirection.EXIT} <= directions_seen
