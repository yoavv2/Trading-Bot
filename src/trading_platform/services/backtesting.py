"""Deterministic daily-bar backtest runner."""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any, Literal

from sqlalchemy import select

from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.models import (
    BacktestEquitySnapshot,
    BacktestSignal,
    BacktestTrade,
    StrategyRun,
    StrategyRunStatus,
    StrategyRunType,
    Symbol,
)
from trading_platform.db.session import session_scope
from trading_platform.services.backtest_reporting import persist_backtest_metrics
from trading_platform.services.bootstrap import ensure_strategy_record
from trading_platform.services.market_data_access import (
    bars_for_session_date,
    latest_completed_session,
    persisted_session_dates,
)
from trading_platform.strategies.registry import StrategyRegistry, build_default_registry
from trading_platform.strategies.signals import Signal, SignalDirection

MONEY_SCALE = Decimal("0.000001")
FRACTIONAL_QUANTITY_SCALE = Decimal("0.000001")

QUANTITY_POLICY_WHOLE_SHARES = "whole_shares"
QUANTITY_POLICY_FRACTIONAL = "fractional"


@dataclass(frozen=True)
class EngineOptions:
    """Explicit engine inputs. ``from_settings`` reproduces the trading path exactly
    (whole shares, ``settings.backtest`` capital/costs/slots, the research bar source
    defaults ``polygon``/adjusted); research runs pass every value explicitly
    (proposal Part H.2/J.3: one asset, one slot, study capital and costs, the study's
    quantity policy, ``rounding_slack`` recorded per fill, provider/adjusted pinned).
    """

    initial_capital: Decimal
    commission_per_order: Decimal
    slippage_bps: Decimal
    max_concurrent_positions: int
    quantity_policy: str
    bar_provider: str
    bar_adjusted: bool
    research: bool = False

    @classmethod
    def from_settings(cls, settings: Settings) -> "EngineOptions":
        backtest_settings = settings.backtest
        return cls(
            initial_capital=_money(backtest_settings.initial_capital),
            commission_per_order=_money(backtest_settings.fees.commission_per_order),
            slippage_bps=Decimal(str(backtest_settings.slippage.bps)),
            max_concurrent_positions=backtest_settings.max_concurrent_positions,
            quantity_policy=QUANTITY_POLICY_WHOLE_SHARES,
            bar_provider=settings.research.bar_provider,
            bar_adjusted=settings.research.bar_adjusted,
            research=False,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "initial_capital": str(self.initial_capital),
            "commission_per_order": str(self.commission_per_order),
            "slippage_bps": str(self.slippage_bps),
            "max_concurrent_positions": self.max_concurrent_positions,
            "quantity_policy": self.quantity_policy,
            "bar_provider": self.bar_provider,
            "bar_adjusted": self.bar_adjusted,
        }


@dataclass(frozen=True)
class BacktestRunReport:
    run_id: str
    strategy_id: str
    status: str
    trigger_source: str
    started_at: str
    completed_at: str | None
    result_summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "strategy_id": self.strategy_id,
            "status": self.status,
            "trigger_source": self.trigger_source,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "result_summary": self.result_summary,
        }


@dataclass(frozen=True)
class PendingAction:
    action: Literal["entry", "exit"]
    signal: Signal
    fill_session: date


@dataclass
class OpenPosition:
    symbol_id: uuid.UUID
    ticker: str
    trade: BacktestTrade


def resolve_backtest_window(
    *,
    settings: Settings,
    from_date_arg: str | None,
    to_date_arg: str | None,
) -> tuple[date, date]:
    """Resolve the backtest date range from CLI arguments and persisted data."""
    if to_date_arg is not None:
        resolved_to = date.fromisoformat(to_date_arg)
    else:
        with session_scope(settings) as db_session:
            resolved_to = latest_completed_session(
                db_session,
                exchange=settings.market_data.calendar.exchange,
            )
        if resolved_to is None:
            resolved_to = date.today() - timedelta(days=1)

    if from_date_arg is not None:
        resolved_from = date.fromisoformat(from_date_arg)
    else:
        resolved_from = resolved_to - timedelta(days=settings.market_data.ingest.default_lookback_days)

    if resolved_from > resolved_to:
        raise ValueError(
            f"from_date must be on or before to_date (got {resolved_from} > {resolved_to})."
        )

    return resolved_from, resolved_to


def run_backtest(
    strategy_id: str,
    *,
    from_date: date,
    to_date: date,
    trigger_source: str = "backtest_script",
    settings: Settings | None = None,
    registry: StrategyRegistry | None = None,
    job_id: uuid.UUID | None = None,
) -> BacktestRunReport:
    """Execute a deterministic daily-bar backtest and persist all artifacts.

    ``job_id`` is an opaque originating-Job identifier (D-02): when provided,
    it is written on ``StrategyRun.job_id`` in the same transaction that
    creates the run, so the link exists from the moment the run exists and
    survives every later state. This module stays free of any Job-framework
    import -- the caller (a Job handler) owns that dependency, not this
    service.
    """
    resolved_settings = settings or load_settings()
    resolved_registry = registry or build_default_registry(resolved_settings)
    strategy = resolved_registry.resolve(strategy_id)
    metadata = strategy.metadata
    run_id = _create_backtest_run(
        resolved_settings,
        metadata,
        trigger_source=trigger_source,
        from_date=from_date,
        to_date=to_date,
        job_id=job_id,
    )

    _update_backtest_run(
        resolved_settings,
        run_id,
        status=StrategyRunStatus.RUNNING,
        result_summary={
            "stage": "running",
            "strategy_id": metadata.strategy_id,
            "date_range": {
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat(),
            },
        },
    )

    try:
        summary = _execute_backtest_run(
            resolved_settings,
            run_id=run_id,
            strategy=strategy,
            from_date=from_date,
            to_date=to_date,
        )
        # The SUCCEEDED update is inside this same try/except (not a bare
        # trailing call) because it now also runs persist_backtest_metrics
        # in the same transaction (D-31): if metric computation or the
        # flush raises, that transaction rolls back entirely (RUNNING is
        # never overwritten with a partial SUCCEEDED), and this except
        # block still lands the run on a durable FAILED state with zero
        # backtest_metrics rows, exactly like an _execute_backtest_run
        # failure.
        return _update_backtest_run(
            resolved_settings,
            run_id,
            status=StrategyRunStatus.SUCCEEDED,
            completed_at=datetime.now(UTC),
            result_summary=summary,
        )
    except Exception as exc:
        _update_backtest_run(
            resolved_settings,
            run_id,
            status=StrategyRunStatus.FAILED,
            completed_at=datetime.now(UTC),
            error_message=str(exc),
            result_summary={
                "stage": "failed",
                "strategy_id": metadata.strategy_id,
                "date_range": {
                    "from_date": from_date.isoformat(),
                    "to_date": to_date.isoformat(),
                },
            },
        )
        raise


def _create_backtest_run(
    settings: Settings,
    metadata,
    *,
    trigger_source: str,
    from_date: date,
    to_date: date,
    job_id: uuid.UUID | None = None,
) -> uuid.UUID:
    with session_scope(settings) as session:
        strategy_record = ensure_strategy_record(session, metadata)
        strategy_run = StrategyRun(
            strategy_id=strategy_record.id,
            job_id=job_id,
            run_type=StrategyRunType.BACKTEST,
            status=StrategyRunStatus.PENDING,
            trigger_source=trigger_source,
            parameters_snapshot=_build_parameters_snapshot(
                settings=settings,
                metadata=metadata,
                from_date=from_date,
                to_date=to_date,
            ),
            result_summary={
                "stage": "pending",
                "strategy_id": metadata.strategy_id,
                "date_range": {
                    "from_date": from_date.isoformat(),
                    "to_date": to_date.isoformat(),
                },
            },
        )
        session.add(strategy_run)
        session.flush()
        return strategy_run.id


def _update_backtest_run(
    settings: Settings,
    run_id: uuid.UUID,
    *,
    status: StrategyRunStatus,
    result_summary: dict[str, Any] | None = None,
    error_message: str | None = None,
    completed_at: datetime | None = None,
) -> BacktestRunReport:
    with session_scope(settings) as session:
        strategy_run = session.get(StrategyRun, run_id)
        if strategy_run is None:
            raise LookupError(f"Missing strategy_run '{run_id}'.")

        strategy_run.status = status
        if result_summary is not None:
            strategy_run.result_summary = result_summary
        if error_message is not None:
            strategy_run.error_message = error_message
        if completed_at is not None:
            strategy_run.completed_at = completed_at

        session.flush()
        if status is StrategyRunStatus.SUCCEEDED:
            persist_backtest_metrics(session, strategy_run)
        session.refresh(strategy_run)
        strategy = strategy_run.strategy

        return BacktestRunReport(
            run_id=str(strategy_run.id),
            strategy_id=strategy.strategy_id if strategy is not None else "unknown",
            status=strategy_run.status.value,
            trigger_source=strategy_run.trigger_source,
            started_at=strategy_run.started_at.isoformat(),
            completed_at=strategy_run.completed_at.isoformat() if strategy_run.completed_at else None,
            result_summary=strategy_run.result_summary,
        )


def _execute_backtest_run(
    settings: Settings,
    *,
    run_id: uuid.UUID,
    strategy,
    from_date: date,
    to_date: date,
    options: EngineOptions | None = None,
) -> dict[str, Any]:
    exchange = settings.market_data.calendar.exchange
    # Trading path: every value below is what ``settings.backtest`` always supplied, and
    # the bar source is the access layer's own default; research passes its own options.
    opts = options or EngineOptions.from_settings(settings)
    bar_provider = opts.bar_provider
    bar_adjusted = opts.bar_adjusted
    initial_capital = opts.initial_capital
    commission_per_order = opts.commission_per_order
    slippage_bps = opts.slippage_bps
    max_concurrent_positions = opts.max_concurrent_positions
    slot_notional = initial_capital / Decimal(max_concurrent_positions)
    zero_quantity_fill_count = 0
    rounding_slack_total = Decimal("0")

    with session_scope(settings) as session:
        strategy_run = session.get(StrategyRun, run_id)
        if strategy_run is None:
            raise LookupError(f"Missing strategy_run '{run_id}'.")

        symbol_map = _ensure_symbol_rows(session, strategy.metadata.universe)
        session_dates = persisted_session_dates(
            session,
            start=from_date,
            end=to_date,
            exchange=exchange,
        )
        session_index = {session_date: index for index, session_date in enumerate(session_dates)}
        next_session_by_date = {
            session_date: session_dates[index + 1]
            for index, session_date in enumerate(session_dates[:-1])
        }

        pending_actions: dict[date, list[PendingAction]] = defaultdict(list)
        open_positions: dict[str, OpenPosition] = {}
        cash = initial_capital
        cumulative_realized_net = Decimal("0")
        signal_count = 0
        trade_count = 0
        equity_snapshot_count = 0
        filled_entry_count = 0
        filled_exit_count = 0
        duplicate_entry_count = 0
        ignored_exit_count = 0
        max_positions_rejections = 0
        no_next_session_count = 0
        skipped_entry_fill_count = 0
        skipped_exit_fill_count = 0
        ending_equity = initial_capital

        for session_date in session_dates:
            bars_today = bars_for_session_date(
                session,
                session_date,
                symbols=list(strategy.metadata.universe),
                adjusted=bar_adjusted,
                provider=bar_provider,
            )
            fills_due = pending_actions.pop(session_date, [])

            for action in [item for item in fills_due if item.action == "exit"]:
                position = open_positions.get(action.signal.symbol)
                bar = bars_today.get(action.signal.symbol)
                if position is None or bar is None:
                    skipped_exit_fill_count += 1
                    continue

                fill_price = _apply_exit_fill_price(bar.open, slippage_bps)
                exit_slippage = _slippage_cost(bar.open, fill_price, position.trade.quantity)
                exit_commission = commission_per_order
                realized_pnl = _money(
                    (fill_price - position.trade.entry_price) * position.trade.quantity
                )
                net_pnl = _money(
                    realized_pnl - position.trade.entry_commission - exit_commission
                )
                cash = _money(
                    cash + (position.trade.quantity * fill_price) - exit_commission
                )
                cumulative_realized_net = _money(cumulative_realized_net + net_pnl)

                position.trade.status = "closed"
                position.trade.exit_signal_session = action.signal.session_date
                position.trade.exit_fill_session = session_date
                position.trade.exit_price = fill_price
                position.trade.exit_commission = exit_commission
                position.trade.exit_slippage = exit_slippage
                position.trade.realized_pnl = realized_pnl
                position.trade.net_pnl = net_pnl
                position.trade.holding_period_sessions = (
                    session_index[session_date] - session_index[position.trade.entry_fill_session]
                )
                filled_exit_count += 1
                del open_positions[action.signal.symbol]

            for action in [item for item in fills_due if item.action == "entry"]:
                if action.signal.symbol in open_positions:
                    skipped_entry_fill_count += 1
                    continue
                if len(open_positions) >= max_concurrent_positions:
                    skipped_entry_fill_count += 1
                    continue

                bar = bars_today.get(action.signal.symbol)
                if bar is None:
                    skipped_entry_fill_count += 1
                    continue

                fill_price = _apply_entry_fill_price(bar.open, slippage_bps)
                quantity, rounding_slack = _entry_quantity(
                    cash=cash,
                    slot_notional=slot_notional,
                    fill_price=fill_price,
                    commission=commission_per_order,
                    quantity_policy=opts.quantity_policy,
                )
                if quantity <= 0:
                    # Proposal J.3: a quantity that rounds to zero under the selected policy
                    # is a recorded finding, not a silent skip.
                    skipped_entry_fill_count += 1
                    zero_quantity_fill_count += 1
                    continue

                entry_slippage = _slippage_cost(bar.open, fill_price, quantity)
                trade = BacktestTrade(
                    strategy_run_id=run_id,
                    symbol_id=symbol_map[action.signal.symbol].id,
                    status="open",
                    quantity=quantity,
                    entry_signal_session=action.signal.session_date,
                    entry_fill_session=session_date,
                    entry_price=fill_price,
                    entry_commission=commission_per_order,
                    entry_slippage=entry_slippage,
                    exit_commission=Decimal("0"),
                    exit_slippage=Decimal("0"),
                    # ``None`` on trading-path rows (unchanged); the actual residual on research rows.
                    rounding_slack=rounding_slack if opts.research else None,
                )
                rounding_slack_total += rounding_slack
                session.add(trade)
                session.flush()

                cash = _money(cash - (quantity * fill_price) - commission_per_order)
                open_positions[action.signal.symbol] = OpenPosition(
                    symbol_id=symbol_map[action.signal.symbol].id,
                    ticker=action.signal.symbol,
                    trade=trade,
                )
                trade_count += 1
                filled_entry_count += 1

            batch = strategy.generate_signals(session, session_date)
            next_session = next_session_by_date.get(session_date)
            scheduled_exit_symbols: set[str] = set()
            signal_actions: dict[str, tuple[str, date | None]] = {}

            for signal in batch.signals:
                if signal.direction != SignalDirection.EXIT:
                    continue
                if next_session is None:
                    signal_actions[signal.symbol] = ("no_next_session", None)
                    no_next_session_count += 1
                    continue
                if signal.symbol not in open_positions:
                    signal_actions[signal.symbol] = ("ignored_no_open_position", None)
                    ignored_exit_count += 1
                    continue

                pending_actions[next_session].append(
                    PendingAction(action="exit", signal=signal, fill_session=next_session)
                )
                scheduled_exit_symbols.add(signal.symbol)
                signal_actions[signal.symbol] = ("scheduled_exit", next_session)

            scheduled_entry_count = 0
            for signal in batch.signals:
                if signal.direction != SignalDirection.LONG:
                    continue
                if next_session is None:
                    signal_actions[signal.symbol] = ("no_next_session", None)
                    no_next_session_count += 1
                    continue
                if signal.symbol in open_positions:
                    signal_actions[signal.symbol] = ("ignored_duplicate_entry", None)
                    duplicate_entry_count += 1
                    continue

                projected_open_positions = (
                    len(open_positions) - len(scheduled_exit_symbols) + scheduled_entry_count
                )
                if projected_open_positions >= max_concurrent_positions:
                    signal_actions[signal.symbol] = ("ignored_max_positions", None)
                    max_positions_rejections += 1
                    continue

                pending_actions[next_session].append(
                    PendingAction(action="entry", signal=signal, fill_session=next_session)
                )
                signal_actions[signal.symbol] = ("scheduled_entry", next_session)
                scheduled_entry_count += 1

            for signal in batch.signals:
                action_name, fill_session = signal_actions.get(signal.symbol, ("flat", None))
                signal_metadata = dict(signal.metadata)
                signal_metadata.update(
                    {
                        "action": action_name,
                        "fill_session": fill_session.isoformat() if fill_session else None,
                        "indicators": signal.indicators.to_dict()["values"],
                    }
                )
                session.add(
                    BacktestSignal(
                        strategy_run_id=run_id,
                        symbol_id=symbol_map[signal.symbol].id,
                        session_date=signal.session_date,
                        direction=signal.direction.value,
                        reason=signal.reason.value,
                        close=signal.indicators.close,
                        sma_short=signal.indicators.sma_short,
                        sma_long=signal.indicators.sma_long,
                        bars_available=signal.indicators.bars_available,
                        signal_metadata=signal_metadata,
                    )
                )
                signal_count += 1

            gross_exposure = Decimal("0")
            unrealized_pnl = Decimal("0")
            for position in open_positions.values():
                close_bar = bars_today.get(position.ticker)
                mark_price = close_bar.close if close_bar is not None else position.trade.entry_price
                gross_exposure += position.trade.quantity * mark_price
                unrealized_pnl += (
                    mark_price - position.trade.entry_price
                ) * position.trade.quantity

            gross_exposure = _money(gross_exposure)
            unrealized_pnl = _money(unrealized_pnl)
            ending_equity = _money(cash + gross_exposure)

            session.add(
                BacktestEquitySnapshot(
                    strategy_run_id=run_id,
                    session_date=session_date,
                    cash=cash,
                    gross_exposure=gross_exposure,
                    total_equity=ending_equity,
                    realized_pnl=cumulative_realized_net,
                    unrealized_pnl=unrealized_pnl,
                    open_positions=len(open_positions),
                )
            )
            equity_snapshot_count += 1

        summary = {
            "stage": "completed",
            "strategy_id": strategy.strategy_id,
            "date_range": {
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat(),
            },
            "assumptions": settings.backtest.model_dump(mode="json"),
            "sessions_evaluated": len(session_dates),
            "signals_persisted": signal_count,
            "trades_persisted": trade_count,
            "equity_snapshots_persisted": equity_snapshot_count,
            "filled_entries": filled_entry_count,
            "filled_exits": filled_exit_count,
            "ignored_duplicate_entries": duplicate_entry_count,
            "ignored_exit_without_position": ignored_exit_count,
            "ignored_max_positions": max_positions_rejections,
            "signals_without_future_session": no_next_session_count,
            "skipped_entry_fills": skipped_entry_fill_count,
            "skipped_exit_fills": skipped_exit_fill_count,
            "open_trades": len(open_positions),
            "starting_capital": float(initial_capital),
            "ending_equity": float(ending_equity),
        }
        if opts.research:
            # Research-only, additive keys (the trading summary stays byte-identical).
            summary["engine_options"] = opts.to_dict()
            summary["zero_quantity_fills"] = zero_quantity_fill_count
            summary["rounding_slack_total"] = str(_money(rounding_slack_total))
        strategy_run.result_summary = summary
        return summary


def _build_parameters_snapshot(
    *,
    settings: Settings,
    metadata,
    from_date: date,
    to_date: date,
) -> dict[str, Any]:
    return {
        "strategy": metadata.to_public_dict(),
        "backtest": settings.backtest.model_dump(mode="json"),
        "date_range": {
            "from_date": from_date.isoformat(),
            "to_date": to_date.isoformat(),
        },
        "share_quantity_mode": "whole_shares",
    }


def _ensure_symbol_rows(session, tickers: tuple[str, ...]) -> dict[str, Symbol]:
    rows = session.execute(
        select(Symbol).where(Symbol.ticker.in_(tickers))
    ).scalars().all()
    symbol_map = {row.ticker: row for row in rows}

    for ticker in tickers:
        if ticker in symbol_map:
            continue
        symbol = Symbol(ticker=ticker, active=True)
        session.add(symbol)
        session.flush()
        symbol_map[ticker] = symbol

    return symbol_map


def _apply_entry_fill_price(open_price: Decimal, slippage_bps: Decimal) -> Decimal:
    multiplier = Decimal("1") + (slippage_bps / Decimal("10000"))
    return _money(open_price * multiplier)


def _apply_exit_fill_price(open_price: Decimal, slippage_bps: Decimal) -> Decimal:
    multiplier = Decimal("1") - (slippage_bps / Decimal("10000"))
    return _money(open_price * multiplier)


def _slippage_cost(raw_price: Decimal, fill_price: Decimal, quantity: Decimal) -> Decimal:
    return _money(abs(fill_price - raw_price) * quantity)


def _entry_quantity(
    *,
    cash: Decimal,
    slot_notional: Decimal,
    fill_price: Decimal,
    commission: Decimal,
    quantity_policy: str = QUANTITY_POLICY_WHOLE_SHARES,
) -> tuple[Decimal, Decimal]:
    """``(quantity, rounding_slack)`` under the policy (proposal J.3).

    ``affordable_notional = min(slot_notional, cash - commission)``; the quantity is
    rounded DOWN (to a whole share, or to six decimals under ``fractional``), so
    ``quantity * fill_price <= affordable_notional`` always holds and a fill can never
    overspend. The slack is the actual residual ``affordable_notional - quantity * fill_price``.
    """

    affordable_notional = min(slot_notional, cash - commission)
    if affordable_notional <= 0:
        return Decimal("0"), Decimal("0")
    if quantity_policy == QUANTITY_POLICY_FRACTIONAL:
        quantity = (affordable_notional / fill_price).quantize(FRACTIONAL_QUANTITY_SCALE, rounding=ROUND_DOWN)
    elif quantity_policy == QUANTITY_POLICY_WHOLE_SHARES:
        quantity = (affordable_notional / fill_price).quantize(Decimal("1"), rounding=ROUND_DOWN)
    else:
        raise ValueError(f"unknown quantity policy {quantity_policy!r}")
    slack = _money(affordable_notional - quantity * fill_price) if quantity > 0 else Decimal("0")
    return quantity, slack


def _money(value: Decimal | float | int) -> Decimal:
    return Decimal(str(value)).quantize(MONEY_SCALE)
