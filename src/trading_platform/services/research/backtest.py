"""One research backtest: a strategy version (or the buy-and-hold benchmark) on one asset
over one window, through the shared engine with explicit research options (plan S3 (b)).

What every run records (proposal Parts F, H.2, J.2):
* a ``strategy_runs`` row (``run_type = backtest``, ``trigger_source = research_job``)
  whose ``parameters_snapshot`` carries the explicit engine options, provider and
  adjustment basis, window and ``code_sha``;
* a 1:1 ``research_run_links`` row with the study revision, version, asset, window
  role, ``spec_sha256``, ``code_sha``, ``input_digest`` and ``rerun_of``;
* ``input_digest``: SHA-256 over every bar and session read the engine made (the
  read-recording seam of PROV-01), so a rerun on changed inputs is detectable;
* ``results_digest``: SHA-256 over the persisted trades and equity curve, the
  byte-identity a technical rerun must reproduce;
* the pinned metrics and evidence descriptors in ``result_summary["research"]``.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from trading_platform.core.settings import Settings
from trading_platform.db.models import (
    BacktestEquitySnapshot,
    BacktestTrade,
    StrategyRun,
    StrategyRunStatus,
    StrategyRunType,
)
from trading_platform.db.models.research import ResearchRunLink, StrategyVersion
from trading_platform.db.session import session_scope
from trading_platform.services.backtesting import EngineOptions, _execute_backtest_run
from trading_platform.services.bootstrap import ensure_strategy_record
from trading_platform.services.market_data_access import persisted_session_dates
from trading_platform.services.read_recording import recording, sha256_hex
from trading_platform.services.research.benchmark import BuyAndHoldSingleAssetStrategy
from trading_platform.services.research.code_identity import code_sha
from trading_platform.services.research.evidence import compute_evidence
from trading_platform.services.research.metrics import EquityPoint, TradeRecord, compute_metrics
from trading_platform.strategies.research_registry import strategy_from_version

TRIGGER_SOURCE = "research_job"
WINDOW_ROLES = ("development", "validation", "final_test")


class _DigestRecorder:
    """Collects every recorded accessor read; the input digest is the hash of the list."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def record(self, kind: str, params: Mapping[str, Any], digest: str, count: int) -> None:
        self.requests.append({"kind": kind, "params": dict(params), "digest": digest, "count": count})

    @property
    def digest(self) -> str:
        return sha256_hex(self.requests)


@dataclass(frozen=True)
class ResearchRunRequest:
    study_revision_id: uuid.UUID
    strategy_version_id: uuid.UUID | None  # None: the buy-and-hold benchmark
    asset: str
    window_role: str
    window_start: date
    window_end: date
    initial_capital: Decimal
    commission_per_order: Decimal
    slippage_bps: Decimal
    quantity_policy: str
    bar_provider: str
    bar_adjusted: bool
    rerun_of: uuid.UUID | None = None

    @property
    def is_benchmark(self) -> bool:
        return self.strategy_version_id is None

    def engine_options(self) -> EngineOptions:
        return EngineOptions(
            initial_capital=self.initial_capital,
            commission_per_order=self.commission_per_order,
            slippage_bps=self.slippage_bps,
            max_concurrent_positions=1,
            quantity_policy=self.quantity_policy,
            bar_provider=self.bar_provider,
            bar_adjusted=self.bar_adjusted,
            research=True,
        )


@dataclass(frozen=True)
class ResearchRunResult:
    run_id: uuid.UUID
    status: str
    metrics: dict[str, Any]
    evidence: dict[str, Any]
    results_digest: str
    input_digest: str
    spec_sha256: str | None
    code_sha: str


def _trade_record(trade: BacktestTrade) -> TradeRecord:
    return TradeRecord(
        status=trade.status,
        quantity=trade.quantity,
        entry_fill_session=trade.entry_fill_session,
        entry_price=trade.entry_price,
        entry_commission=trade.entry_commission,
        entry_slippage=trade.entry_slippage,
        exit_fill_session=trade.exit_fill_session,
        exit_price=trade.exit_price,
        exit_commission=trade.exit_commission,
        exit_slippage=trade.exit_slippage,
        net_pnl=trade.net_pnl,
        holding_period_sessions=trade.holding_period_sessions,
        rounding_slack=trade.rounding_slack,
    )


def _dec(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


def results_digest_for(trades: list[BacktestTrade], equity: list[BacktestEquitySnapshot]) -> str:
    """Canonical digest of what a run produced (identity of a technical rerun)."""

    trade_rows = sorted(
        (
            [
                t.entry_fill_session.isoformat(),
                t.exit_fill_session.isoformat() if t.exit_fill_session else None,
                _dec(t.quantity),
                _dec(t.entry_price),
                _dec(t.exit_price),
                _dec(t.net_pnl),
                _dec(t.rounding_slack),
                t.status,
            ]
            for t in trades
        ),
        key=lambda row: (row[0], row[1] or ""),
    )
    equity_rows = [[e.session_date.isoformat(), _dec(e.total_equity), _dec(e.gross_exposure)] for e in equity]
    return sha256_hex({"trades": trade_rows, "equity": equity_rows})


def load_run_rows(session, run_id: uuid.UUID) -> tuple[list[BacktestTrade], list[BacktestEquitySnapshot]]:
    trades = list(
        session.execute(
            select(BacktestTrade).where(BacktestTrade.strategy_run_id == run_id).order_by(BacktestTrade.entry_fill_session, BacktestTrade.id)
        ).scalars()
    )
    equity = list(
        session.execute(
            select(BacktestEquitySnapshot).where(BacktestEquitySnapshot.strategy_run_id == run_id).order_by(BacktestEquitySnapshot.session_date)
        ).scalars()
    )
    return trades, equity


def run_research_backtest(
    settings: Settings, request: ResearchRunRequest, *, job_id: uuid.UUID | None = None
) -> ResearchRunResult:
    if request.window_role not in WINDOW_ROLES:
        raise ValueError(f"unknown window role {request.window_role!r}")
    executed_code = code_sha()
    options = request.engine_options()

    with session_scope(settings) as session:
        spec_sha256: str | None = None
        bar_source = (request.bar_provider, request.bar_adjusted)
        if request.is_benchmark:
            strategy = BuyAndHoldSingleAssetStrategy(
                settings, asset=request.asset, window_start=request.window_start, bar_source=bar_source
            )
        else:
            version = session.get(StrategyVersion, request.strategy_version_id)
            if version is None:
                raise LookupError(f"strategy version {request.strategy_version_id} not found")
            strategy = strategy_from_version(settings, version, universe=(request.asset,), bar_source=bar_source)
            spec_sha256 = version.spec_sha256
        metadata = strategy.metadata
        strategy_record = ensure_strategy_record(session, metadata)
        run = StrategyRun(
            strategy_id=strategy_record.id,
            job_id=job_id,
            run_type=StrategyRunType.BACKTEST,
            status=StrategyRunStatus.RUNNING,
            trigger_source=TRIGGER_SOURCE,
            parameters_snapshot={
                "research": True,
                "study_revision_id": str(request.study_revision_id),
                "strategy_version_id": str(request.strategy_version_id) if request.strategy_version_id else None,
                "benchmark": request.is_benchmark,
                "asset": request.asset,
                "window_role": request.window_role,
                "window": {"start": request.window_start.isoformat(), "end": request.window_end.isoformat()},
                "engine_options": options.to_dict(),
                "spec_sha256": spec_sha256,
                "code_sha": executed_code,
                "rerun_of": str(request.rerun_of) if request.rerun_of else None,
                "strategy": metadata.to_public_dict(),
            },
            result_summary={"stage": "running"},
        )
        session.add(run)
        session.flush()
        run_id = run.id
        session.add(
            ResearchRunLink(
                run_id=run_id,
                study_revision_id=request.study_revision_id,
                strategy_version_id=request.strategy_version_id,
                asset=request.asset,
                window_role=request.window_role,
                spec_sha256=spec_sha256,
                code_sha=executed_code,
                rerun_of=request.rerun_of,
                created_at=datetime.now(UTC),
            )
        )
        session.flush()

    recorder = _DigestRecorder()
    try:
        with recording(recorder):
            summary = _execute_backtest_run(
                settings,
                run_id=run_id,
                strategy=strategy,
                from_date=request.window_start,
                to_date=request.window_end,
                options=options,
            )
    except Exception as exc:
        with session_scope(settings) as session:
            failed = session.get(StrategyRun, run_id)
            if failed is not None:
                failed.status = StrategyRunStatus.FAILED
                failed.completed_at = datetime.now(UTC)
                failed.error_message = str(exc)
                failed.result_summary = {"stage": "failed", "error_type": type(exc).__name__}
        raise

    input_digest = recorder.digest
    with session_scope(settings) as session:
        trades, equity = load_run_rows(session, run_id)
        session_dates = persisted_session_dates(
            session, start=request.window_start, end=request.window_end, exchange=settings.market_data.calendar.exchange
        )
        session_index = {d: i for i, d in enumerate(session_dates)}
        metrics = compute_metrics(
            initial_capital=request.initial_capital,
            equity=[EquityPoint(e.session_date, e.total_equity, e.gross_exposure, e.unrealized_pnl) for e in equity],
            trades=[_trade_record(t) for t in trades],
            skipped_fills=int(summary.get("skipped_entry_fills", 0)) + int(summary.get("skipped_exit_fills", 0)),
            zero_quantity_fills=int(summary.get("zero_quantity_fills", 0)),
        )
        evidence = compute_evidence(trades=[_trade_record(t) for t in trades], metrics=metrics, session_index=session_index)
        digest = results_digest_for(trades, equity)
        run = session.get(StrategyRun, run_id)
        assert run is not None
        run.status = StrategyRunStatus.SUCCEEDED
        run.completed_at = datetime.now(UTC)
        run.result_summary = {
            **summary,
            "research": {
                "metrics": metrics,
                "evidence": evidence,
                "results_digest": digest,
                "input_digest": input_digest,
                "input_reads": len(recorder.requests),
                "window_role": request.window_role,
                "asset": request.asset,
                "benchmark": request.is_benchmark,
            },
        }
        link = session.get(ResearchRunLink, run_id)
        assert link is not None
        link.input_digest = input_digest
        session.flush()

    return ResearchRunResult(
        run_id=run_id,
        status=StrategyRunStatus.SUCCEEDED.value,
        metrics=metrics,
        evidence=evidence,
        results_digest=digest,
        input_digest=input_digest,
        spec_sha256=spec_sha256,
        code_sha=executed_code,
    )
