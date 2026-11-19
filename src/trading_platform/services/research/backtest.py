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
  byte-identity a technical rerun must reproduce. Risk runs also cover per-signal expiry;
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
    BacktestSignal,
    BacktestTrade,
    StrategyRun,
    StrategyRunStatus,
    StrategyRunType,
)
from trading_platform.db.models.research import ResearchRunLink, StrategyVersion, TestWindowExposure
from trading_platform.db.session import session_scope
from trading_platform.services.backtesting import EngineOptions, _execute_backtest_run
from trading_platform.services.bootstrap import ensure_strategy_record
from trading_platform.services.market_data_access import persisted_session_dates
from trading_platform.services.read_recording import canonical_json, recording, sha256_hex
from trading_platform.services.research.benchmark import BuyAndHoldSingleAssetStrategy
from trading_platform.services.research.code_identity import code_sha, study_code_identity
from trading_platform.services.research.evidence import compute_evidence
from trading_platform.services.research.metrics import EquityPoint, TradeRecord, compute_metrics
from trading_platform.services.research.risk_metrics import RiskTrade, compute_risk_metrics
from trading_platform.strategies.base import BaseStrategy
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
    risk_metrics: dict[str, Any] | None = None  # runs of a specification with a risk block
    results_digest_version: int = 1


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


def risk_trades(asset: str, trades: list[BacktestTrade]) -> list[RiskTrade]:
    """The trades of a risk-block run in the shape ``compute_risk_metrics`` reads. A trade
    without levels (a v1 run, the benchmark) is left out."""

    return [
        RiskTrade(
            asset=asset,
            signal_session=t.entry_signal_session,
            exit_session=t.exit_fill_session,
            quantity=t.quantity,
            rule_entry_price=t.rule_entry_price,
            rule_exit_price=t.rule_exit_price,
            stop_price=t.stop_price,
            target_price=t.target_price,
            net_pnl=t.net_pnl,
            exit_reason=t.exit_reason,
            intrabar_order_unknown=bool(t.intrabar_order_unknown),
        )
        for t in trades
        if t.rule_entry_price is not None and t.stop_price is not None and t.target_price is not None
    ]


def _dec(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


def _trade_digest_row(trade: BacktestTrade) -> list[str | None]:
    return [
        trade.entry_fill_session.isoformat(),
        trade.exit_fill_session.isoformat() if trade.exit_fill_session else None,
        _dec(trade.quantity),
        _dec(trade.entry_price),
        _dec(trade.exit_price),
        _dec(trade.net_pnl),
        _dec(trade.rounding_slack),
        trade.status,
    ]


def results_digest_for(
    trades: list[BacktestTrade],
    equity: list[BacktestEquitySnapshot],
    *,
    expired_by_reason: Mapping[str, int] | None = None,
    expired_signals: tuple[BacktestSignal, ...] = (),
) -> str:
    """Canonical digest of what a run produced (identity of a technical rerun)."""

    equity_rows = [
        [e.session_date.isoformat(), _dec(e.total_equity), _dec(e.gross_exposure)] for e in equity
    ]
    if expired_by_reason is None:
        trade_rows = sorted(
            (_trade_digest_row(trade) for trade in trades), key=lambda row: (row[0] or "", row[1] or "")
        )
        return sha256_hex({"trades": trade_rows, "equity": equity_rows})

    risk_trade_rows = sorted(
        (
            [
                *_trade_digest_row(t),
                t.entry_signal_session.isoformat(),
                t.exit_signal_session.isoformat() if t.exit_signal_session else None,
                _dec(t.reference_price),
                _dec(t.stop_price),
                _dec(t.target_price),
                _dec(t.rule_entry_price),
                _dec(t.rule_exit_price),
                t.exit_reason,
                t.intrabar_order_unknown,
            ]
            for t in trades
        ),
        key=canonical_json,
    )
    expiry_rows = sorted(
        (
            [
                signal.session_date.isoformat(),
                signal.signal_metadata["fill_session"],
                signal.signal_metadata["entry_outcome"],
                signal.signal_metadata["levels"],
            ]
            for signal in expired_signals
        ),
        key=canonical_json,
    )
    return sha256_hex(
        {
            "version": 2,
            "trades": risk_trade_rows,
            "equity": sorted(equity_rows, key=canonical_json),
            "expired_by_reason": dict(expired_by_reason),
            "expired_signals": expiry_rows,
        }
    )


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
    settings: Settings, request: ResearchRunRequest, *, job_id: uuid.UUID | None = None,
    exposure_id: uuid.UUID | None = None,
) -> ResearchRunResult:
    if request.window_role not in WINDOW_ROLES:
        raise ValueError(f"unknown window role {request.window_role!r}")
    options = request.engine_options()

    with session_scope(settings) as session:
        spec_sha256: str | None = None
        bar_source = (request.bar_provider, request.bar_adjusted)
        strategy: BaseStrategy
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
        executed_code = (
            study_code_identity() if getattr(strategy, "risk_plan", None) is not None else code_sha()
        )
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
        if exposure_id is not None:
            reservation = session.get(TestWindowExposure, exposure_id, with_for_update=True)
            if (
                reservation is None
                or reservation.run_id is not None
                or request.window_role != "final_test"
                or reservation.study_revision_id != request.study_revision_id
                or reservation.strategy_version_id != request.strategy_version_id
                or reservation.asset != request.asset
                or (reservation.range_start, reservation.range_end) != (request.window_start, request.window_end)
            ):
                raise ValueError("Holdout run does not match an unused reservation")
            reservation.run_id = run_id
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
        risk_block: dict[str, Any] = {}
        digest_version = 1
        if "risk" in summary:
            expired_by_reason = summary["risk"]["expired_by_reason"]
            expired_signals = tuple(
                session.scalars(
                    select(BacktestSignal).where(
                        BacktestSignal.strategy_run_id == run_id,
                        BacktestSignal.signal_metadata["entry_outcome"]
                        .as_string()
                        .in_(tuple(expired_by_reason)),
                    )
                )
            )
            digest = results_digest_for(
                trades, equity, expired_by_reason=expired_by_reason, expired_signals=expired_signals
            )
            digest_version = 2
            risk_block["results_digest_version"] = digest_version
            risk_block["risk_metrics"] = compute_risk_metrics(
                trades=risk_trades(request.asset, trades),
                expired_by_reason=expired_by_reason,
                window_start=request.window_start,
                window_end=request.window_end,
                slippage_bps=request.slippage_bps,
            )
        else:
            digest = results_digest_for(trades, equity)
        completed_run = session.get(StrategyRun, run_id)
        assert completed_run is not None
        completed_run.status = StrategyRunStatus.SUCCEEDED
        completed_run.completed_at = datetime.now(UTC)
        completed_run.result_summary = {
            **summary,
            "research": {
                "metrics": metrics,
                "evidence": evidence,
                **risk_block,
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
        if exposure_id is not None:
            exposure = session.get(TestWindowExposure, exposure_id, with_for_update=True)
            if exposure is None or exposure.run_id != run_id:
                raise ValueError("Holdout reservation no longer belongs to this run")
            exposure.input_digest = input_digest
        session.flush()

    return ResearchRunResult(
        run_id=run_id,
        status=StrategyRunStatus.SUCCEEDED.value,
        metrics=metrics,
        risk_metrics=risk_block.get("risk_metrics"),
        evidence=evidence,
        results_digest=digest,
        results_digest_version=digest_version,
        input_digest=input_digest,
        spec_sha256=spec_sha256,
        code_sha=executed_code,
    )
