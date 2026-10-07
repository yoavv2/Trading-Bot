"""Data freezes, preserved inputs, change detection and restore (proposal Part J.2).

What is reproducible, detectable and not:
* reproducible (bit-identical): re-running on the frozen research DB, or on a DB
  restored from the ``inputs/`` export of the same freeze;
* detectable only: any bar, symbol or session row in the freeze's scope changed after
  ``frozen_at`` (``inputs_changed_after_freeze``), and provider restatements;
* not reproducible: the original provider fetch.

The digest covers exactly the research-relevant columns of every bar row in scope
(both series), the sessions in range and the symbol identities; ``updated_at`` and
``provider_timestamp`` are excluded so a no-op re-ingest never changes it.
"""

from __future__ import annotations

import csv
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_platform.core.settings import Settings
from trading_platform.db.models.daily_bar import DailyBar as DailyBarModel
from trading_platform.db.models.market_data_ingestion_run import MarketDataIngestionRun
from trading_platform.db.models.market_session import MarketSession
from trading_platform.db.models.research import DataFreeze
from trading_platform.db.models.symbol import Symbol
from trading_platform.services.calendar import pinned_calendar_start
from trading_platform.services.read_recording import canonical_json, sha256_hex
from trading_platform.services.research.integrity import IntegrityReport

MANIFEST_VERSION = 1
BARS_FILE = "daily_bars.csv"
SESSIONS_FILE = "market_sessions.csv"
SYMBOLS_FILE = "symbols.csv"
INTEGRITY_FILE = "INTEGRITY.json"
MANIFEST_FILE = "MANIFEST.json"

BAR_COLUMNS = (
    "ticker",
    "session_date",
    "adjusted",
    "provider",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "split_factor",
    "dividend_cash",
)
SESSION_COLUMNS = ("exchange", "session_date", "market_open", "market_close", "early_close")
SYMBOL_COLUMNS = ("ticker", "name", "market", "primary_exchange", "symbol_type", "list_date", "metadata_provider")


class IntegrityBlocksFreezeError(Exception):
    def __init__(self, report: IntegrityReport) -> None:
        self.report = report
        super().__init__(f"{len(report.errors)} integrity error(s) block the freeze.")


class InputsChangedAfterFreezeError(Exception):
    code = "inputs_changed_after_freeze"

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__("; ".join(reasons))


class RestoreDigestMismatchError(Exception):
    pass


def _dec(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


def _bar_record(ticker: str, bar: DailyBarModel) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "session_date": bar.session_date.isoformat(),
        "adjusted": bar.adjusted,
        "provider": bar.provider,
        "open": _dec(bar.open),
        "high": _dec(bar.high),
        "low": _dec(bar.low),
        "close": _dec(bar.close),
        "volume": int(bar.volume),
        "split_factor": _dec(bar.split_factor),
        "dividend_cash": _dec(bar.dividend_cash),
    }


def _session_record(row: MarketSession) -> dict[str, Any]:
    return {
        "exchange": row.exchange,
        "session_date": row.session_date.isoformat(),
        "market_open": row.market_open.isoformat() if row.market_open else None,
        "market_close": row.market_close.isoformat() if row.market_close else None,
        "early_close": bool(row.early_close),
    }


def _symbol_record(row: Symbol) -> dict[str, Any]:
    return {
        "ticker": row.ticker,
        "name": row.name,
        "market": row.market,
        "primary_exchange": row.primary_exchange,
        "symbol_type": row.symbol_type,
        "list_date": row.list_date.isoformat() if row.list_date else None,
        "metadata_provider": row.metadata_provider,
    }


@dataclass
class FrozenInputs:
    bars: list[dict[str, Any]]
    sessions: list[dict[str, Any]]
    symbols: list[dict[str, Any]]

    @property
    def digest(self) -> str:
        return sha256_hex({"bars": self.bars, "sessions": self.sessions, "symbols": self.symbols})


def collect_inputs(
    session: Session,
    *,
    assets: list[str],
    range_start: date,
    range_end: date,
    provider: str,
    exchange: str = "XNYS",
) -> FrozenInputs:
    tickers = sorted({a.upper() for a in assets})
    symbols = list(
        session.execute(select(Symbol).where(Symbol.ticker.in_(tickers)).order_by(Symbol.ticker)).scalars()
    )
    by_id = {s.id: s.ticker for s in symbols}
    bars = list(
        session.execute(
            select(DailyBarModel)
            .where(DailyBarModel.symbol_id.in_(list(by_id)))
            .where(DailyBarModel.provider == provider)
            .where(DailyBarModel.session_date >= range_start)
            .where(DailyBarModel.session_date <= range_end)
        ).scalars()
    )
    bar_records = sorted(
        (_bar_record(by_id[b.symbol_id], b) for b in bars),
        key=lambda r: (r["ticker"], r["session_date"], r["adjusted"]),
    )
    sessions = list(
        session.execute(
            select(MarketSession)
            .where(MarketSession.exchange == exchange)
            .where(MarketSession.session_date >= range_start)
            .where(MarketSession.session_date <= range_end)
            .order_by(MarketSession.session_date.asc())
        ).scalars()
    )
    return FrozenInputs(
        bars=bar_records,
        sessions=[_session_record(s) for s in sessions],
        symbols=[_symbol_record(s) for s in symbols],
    )


def create_data_freeze(
    session: Session,
    settings: Settings,
    *,
    assets: list[str],
    range_start: date,
    range_end: date,
    provider: str,
    integrity: IntegrityReport,
    export_root: Path | None = None,
    now: datetime | None = None,
) -> DataFreeze:
    """Freeze the inputs (refused on integrity errors) and export them to ``inputs/``."""

    if not integrity.ok:
        raise IntegrityBlocksFreezeError(integrity)
    frozen_at = now or datetime.now(UTC)
    inputs = collect_inputs(
        session, assets=assets, range_start=range_start, range_end=range_end, provider=provider
    )
    freeze = DataFreeze(
        id=uuid.uuid4(),
        provider=provider,
        assets=sorted({a.upper() for a in assets}),
        range_start=range_start,
        range_end=range_end,
        calendar_start=pinned_calendar_start(),
        input_digest=inputs.digest,
        integrity_summary=integrity.summary(),
        frozen_at=frozen_at,
    )
    root = export_root or (settings.paths.data_dir / "research" / "freezes")
    directory = root / str(freeze.id) / "inputs"
    export_inputs(inputs, integrity=integrity, freeze=freeze, directory=directory, settings=settings)
    freeze.inputs_path = str(directory)
    session.add(freeze)
    session.flush()
    return freeze


def export_inputs(
    inputs: FrozenInputs,
    *,
    integrity: IntegrityReport,
    freeze: DataFreeze,
    directory: Path,
    settings: Settings,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}

    def _write_csv(name: str, columns: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
        path = directory / name
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(columns))
            writer.writeheader()
            for row in rows:
                writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in columns})
        files[name] = sha256_hex(path.read_text())

    _write_csv(BARS_FILE, BAR_COLUMNS, inputs.bars)
    _write_csv(SESSIONS_FILE, SESSION_COLUMNS, inputs.sessions)
    _write_csv(SYMBOLS_FILE, SYMBOL_COLUMNS, inputs.symbols)
    (directory / INTEGRITY_FILE).write_text(json.dumps(integrity.to_dict(), indent=2, sort_keys=True))
    files[INTEGRITY_FILE] = sha256_hex((directory / INTEGRITY_FILE).read_text())
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "freeze_id": str(freeze.id),
        "provider": freeze.provider,
        "assets": list(freeze.assets),
        "range_start": freeze.range_start.isoformat(),
        "range_end": freeze.range_end.isoformat(),
        "calendar_start": freeze.calendar_start.isoformat() if freeze.calendar_start else None,
        "frozen_at": freeze.frozen_at.isoformat(),
        "input_digest": freeze.input_digest,
        "files": files,
        "row_counts": {"bars": len(inputs.bars), "sessions": len(inputs.sessions), "symbols": len(inputs.symbols)},
        "license_note": "Provider data for personal research use only; never commit or share this directory.",
    }
    (directory / MANIFEST_FILE).write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return directory


def inputs_changed_after_freeze(session: Session, freeze: DataFreeze) -> list[str]:
    """Reasons the frozen scope changed after ``frozen_at`` (empty when unchanged)."""

    reasons: list[str] = []
    tickers = list(freeze.assets)
    symbol_ids = list(session.execute(select(Symbol.id).where(Symbol.ticker.in_(tickers))).scalars())
    newer_bars = session.execute(
        select(func.count())
        .select_from(DailyBarModel)
        .where(DailyBarModel.symbol_id.in_(symbol_ids))
        .where(DailyBarModel.provider == freeze.provider)
        .where(DailyBarModel.session_date >= freeze.range_start)
        .where(DailyBarModel.session_date <= freeze.range_end)
        .where(DailyBarModel.updated_at > freeze.frozen_at)
    ).scalar_one()
    if newer_bars:
        reasons.append(f"{newer_bars} bar row(s) changed after the freeze")
    newer_symbols = session.execute(
        select(func.count()).select_from(Symbol).where(Symbol.ticker.in_(tickers)).where(Symbol.updated_at > freeze.frozen_at)
    ).scalar_one()
    if newer_symbols:
        reasons.append(f"{newer_symbols} symbol row(s) changed after the freeze")
    newer_sessions = session.execute(
        select(func.count())
        .select_from(MarketSession)
        .where(MarketSession.session_date >= freeze.range_start)
        .where(MarketSession.session_date <= freeze.range_end)
        .where(MarketSession.updated_at > freeze.frozen_at)
    ).scalar_one()
    if newer_sessions:
        reasons.append(f"{newer_sessions} session row(s) changed after the freeze")
    frozen_assets = {t.upper() for t in tickers}
    later_runs = [
        run
        for run in session.execute(
            select(MarketDataIngestionRun)
            .where(MarketDataIngestionRun.provider == freeze.provider)
            .where(MarketDataIngestionRun.completed_at > freeze.frozen_at)
        ).scalars()
        if frozen_assets.intersection(str(s).upper() for s in (run.symbols_requested or []))
    ]
    if later_runs:
        reasons.append(f"{len(later_runs)} ingestion run(s) for frozen assets completed after the freeze")
    return reasons


def require_inputs_unchanged(session: Session, freeze: DataFreeze) -> None:
    reasons = inputs_changed_after_freeze(session, freeze)
    if reasons:
        raise InputsChangedAfterFreezeError(reasons)


def verify_freeze_digest(session: Session, freeze: DataFreeze) -> bool:
    inputs = collect_inputs(
        session,
        assets=list(freeze.assets),
        range_start=freeze.range_start,
        range_end=freeze.range_end,
        provider=freeze.provider,
    )
    return inputs.digest == freeze.input_digest


@dataclass
class RestoreReport:
    manifest: dict[str, Any]
    bars_inserted: int
    sessions_inserted: int
    symbols_inserted: int
    digest_verified: bool


def _parse_dec(value: str) -> Decimal | None:
    return Decimal(value) if value not in ("", None) else None


def restore_inputs(session: Session, directory: Path) -> RestoreReport:
    """Rebuild symbols, sessions and bars from an ``inputs/`` export; verify the digest."""

    manifest = json.loads((directory / MANIFEST_FILE).read_text())
    for name, expected in manifest["files"].items():
        actual = sha256_hex((directory / name).read_text())
        if actual != expected:
            raise RestoreDigestMismatchError(f"{name} does not match the manifest digest")

    with (directory / SYMBOLS_FILE).open() as handle:
        symbol_rows = list(csv.DictReader(handle))
    with (directory / SESSIONS_FILE).open() as handle:
        session_rows = list(csv.DictReader(handle))
    with (directory / BARS_FILE).open() as handle:
        bar_rows = list(csv.DictReader(handle))

    symbols_by_ticker: dict[str, Symbol] = {}
    for row in symbol_rows:
        existing = session.execute(select(Symbol).where(Symbol.ticker == row["ticker"])).scalar_one_or_none()
        if existing is None:
            existing = Symbol(id=uuid.uuid4(), ticker=row["ticker"], active=True)
            session.add(existing)
        existing.name = row.get("name") or None
        existing.market = row.get("market") or None
        existing.primary_exchange = row.get("primary_exchange") or None
        existing.symbol_type = row.get("symbol_type") or None
        existing.list_date = date.fromisoformat(row["list_date"]) if row.get("list_date") else None
        existing.metadata_provider = row.get("metadata_provider") or None
        symbols_by_ticker[row["ticker"]] = existing
    session.flush()

    sessions_inserted = 0
    for row in session_rows:
        session_date = date.fromisoformat(row["session_date"])
        existing_session = session.execute(
            select(MarketSession).where(MarketSession.exchange == row["exchange"], MarketSession.session_date == session_date)
        ).scalar_one_or_none()
        if existing_session is None:
            session.add(
                MarketSession(
                    id=uuid.uuid4(),
                    exchange=row["exchange"],
                    session_date=session_date,
                    market_open=datetime.fromisoformat(row["market_open"]) if row.get("market_open") else None,
                    market_close=datetime.fromisoformat(row["market_close"]) if row.get("market_close") else None,
                    early_close=row.get("early_close") == "True",
                )
            )
            sessions_inserted += 1
    session.flush()

    bars_inserted = 0
    for row in bar_rows:
        symbol = symbols_by_ticker[row["ticker"]]
        session.add(
            DailyBarModel(
                id=uuid.uuid4(),
                symbol_id=symbol.id,
                session_date=date.fromisoformat(row["session_date"]),
                open=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
                volume=int(row["volume"]),
                adjusted=row["adjusted"] == "True",
                provider=row["provider"],
                split_factor=_parse_dec(row.get("split_factor", "")),
                dividend_cash=_parse_dec(row.get("dividend_cash", "")),
            )
        )
        bars_inserted += 1
    session.flush()

    inputs = collect_inputs(
        session,
        assets=list(manifest["assets"]),
        range_start=date.fromisoformat(manifest["range_start"]),
        range_end=date.fromisoformat(manifest["range_end"]),
        provider=manifest["provider"],
    )
    verified = inputs.digest == manifest["input_digest"]
    if not verified:
        raise RestoreDigestMismatchError("restored inputs do not reproduce the manifest input_digest")
    return RestoreReport(
        manifest=manifest,
        bars_inserted=bars_inserted,
        sessions_inserted=sessions_inserted,
        symbols_inserted=len(symbols_by_ticker),
        digest_verified=verified,
    )


def canonical_manifest(freeze: DataFreeze) -> str:
    return canonical_json(
        {
            "freeze_id": str(freeze.id),
            "input_digest": freeze.input_digest,
            "assets": list(freeze.assets),
            "range": [freeze.range_start.isoformat(), freeze.range_end.isoformat()],
        }
    )
