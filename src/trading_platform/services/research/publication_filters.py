"""Which of a version's signals are published (signals plan, Step 1 status).

The pilot candidate produces several times more signals than the product publishes. A
publication rule decides, with what is known when a signal is issued or at the open of
its fill session, whether the signal is published. The engine's record is not changed: a
signal a rule holds back still runs its course internally and is never published later.

This module measures such rules on the stored runs of a finished signals study. It reads
the runs' entry signals and trades and runs no backtest. The rules and the windows come
from a protocol written before any filtered result is computed
(``config/research/signals_v2_publication_filters.yaml``).

Rules (``RuleKind``), each applied on its own:

    none              every signal is published.
    min_initial_risk  a published signal is withdrawn at the open of its fill session
                      when (open - stop) / open is below the rule's value.
    open_cap          after the close of session D a signal is published only while the
                      published trades still open after D, plus the signals already
                      published for D, number fewer than the cap. Signals of one session
                      are taken in the order of sha256("<session>:<asset>").

Run it (research database, read only):

    TRADING_PLATFORM_RESEARCH__MODE=true TRADING_PLATFORM_DATABASE__NAME=trading_research \\
        PYTHONPATH=src python -m trading_platform.services.research.publication_filters \\
        --protocol config/research/signals_v2_publication_filters.yaml --out docs/studies/<name>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import uuid
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from trading_platform.core.settings import PROJECT_ROOT, Settings, load_settings
from trading_platform.db.models.backtest_signal import BacktestSignal
from trading_platform.db.models.backtest_trade import BacktestTrade
from trading_platform.db.session import session_scope
from trading_platform.services.research.backtest import risk_trades
from trading_platform.services.research.code_identity import study_code_identity
from trading_platform.services.research.risk_metrics import RiskTrade, compute_risk_metrics, net_r
from trading_platform.strategies.trade_rules import EntryOutcome

TRADING_DATABASE = "trading_platform"
SCHEDULED_ENTRY = "scheduled_entry"
SELECTION_WINDOWS = ("development", "validation")


class RuleKind(StrEnum):
    NONE = "none"
    MIN_INITIAL_RISK = "min_initial_risk"
    OPEN_CAP = "open_cap"


class PublicationOutcome(StrEnum):
    PUBLISHED_TRADE = "published_trade"
    PUBLISHED_EXPIRED = "published_expired"  # published, then expired at the open (entry rule 3)
    WITHDRAWN_LOW_INITIAL_RISK = "withdrawn_low_initial_risk"
    HELD_BACK_OPEN_CAP = "held_back_open_cap"


class SourceDataError(ValueError):
    """The stored runs do not have the shape this study reads."""


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoRule(_Strict):
    id: str = Field(min_length=1, max_length=64)
    kind: Literal[RuleKind.NONE]


class MinInitialRiskRule(_Strict):
    id: str = Field(min_length=1, max_length=64)
    kind: Literal[RuleKind.MIN_INITIAL_RISK]
    min_risk_fraction: Decimal = Field(gt=0, lt=1)


class OpenCapRule(_Strict):
    id: str = Field(min_length=1, max_length=64)
    kind: Literal[RuleKind.OPEN_CAP]
    max_open: int = Field(ge=1)


Rule = Annotated[NoRule | MinInitialRiskRule | OpenCapRule, Field(discriminator="kind")]


class TargetRate(_Strict):
    min: Decimal = Field(gt=0)
    max: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def _ordered(self) -> TargetRate:
        if self.min > self.max:
            raise ValueError("target rate min must be at or below its max")
        return self


class PublicationProtocol(_Strict):
    name: str = Field(min_length=1, max_length=120)
    source_results: str = Field(min_length=1)
    version: str = Field(min_length=1)
    windows: list[Literal["development", "validation"]] = Field(min_length=1)
    target_trades_per_month: TargetRate
    rules: list[Rule] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> PublicationProtocol:
        ids = [rule.id for rule in self.rules]
        if len(set(ids)) != len(ids):
            raise ValueError("rule ids must be unique")
        if len(set(self.windows)) != len(self.windows):
            raise ValueError("windows must be unique")
        return self


def load_protocol(path: Path) -> tuple[PublicationProtocol, str]:
    """The protocol and the SHA-256 of the file it was read from."""

    raw = path.read_bytes()
    return PublicationProtocol.model_validate(yaml.safe_load(raw)), hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------------------
# The rules, pure
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntrySignal:
    """One entry signal of the engine's record: issued after the close of
    ``signal_session``; ``trade`` is ``None`` when it expired at the next open."""

    asset: str
    signal_session: date
    trade: RiskTrade | None
    expiry_reason: str | None = None


def initial_risk_fraction(trade: RiskTrade) -> Decimal:
    """(open - stop) / open at the fill session's open."""

    return (trade.rule_entry_price - trade.stop_price) / trade.rule_entry_price


def session_order_key(signal: EntrySignal) -> str:
    """The order of signals issued on one session: fixed in advance, outcome-blind."""

    return hashlib.sha256(
        f"{signal.signal_session.isoformat()}:{signal.asset}".encode()
    ).hexdigest()


def _unfiltered(signal: EntrySignal) -> PublicationOutcome:
    return (
        PublicationOutcome.PUBLISHED_TRADE
        if signal.trade is not None
        else PublicationOutcome.PUBLISHED_EXPIRED
    )


def _apply_open_cap(
    signals: list[EntrySignal], max_open: int
) -> list[tuple[EntrySignal, PublicationOutcome]]:
    decided: list[tuple[EntrySignal, PublicationOutcome]] = []
    open_trades: list[RiskTrade] = []  # published trades, pruned as their exits pass
    by_session: dict[date, list[EntrySignal]] = {}
    for signal in signals:
        by_session.setdefault(signal.signal_session, []).append(signal)
    for session_date in sorted(by_session):
        # A trade that exited on or before this session has freed its place by the close.
        open_trades = [
            t for t in open_trades if t.exit_session is None or t.exit_session > session_date
        ]
        published_today = 0
        for signal in sorted(by_session[session_date], key=session_order_key):
            if len(open_trades) + published_today >= max_open:
                decided.append((signal, PublicationOutcome.HELD_BACK_OPEN_CAP))
                continue
            published_today += 1
            decided.append((signal, _unfiltered(signal)))
        # Trades published today open at the next session; they count from then on.
        open_trades.extend(
            signal.trade
            for signal, outcome in decided[-len(by_session[session_date]) :]
            if outcome is PublicationOutcome.PUBLISHED_TRADE and signal.trade is not None
        )
    return decided


def apply_rule(
    signals: list[EntrySignal], rule: Rule
) -> list[tuple[EntrySignal, PublicationOutcome]]:
    """Every signal with what the rule does to it. The input order does not matter."""

    keys = [(s.asset, s.signal_session) for s in signals]
    if len(set(keys)) != len(keys):
        raise SourceDataError("more than one entry signal for an asset on one session")
    if isinstance(rule, OpenCapRule):
        return _apply_open_cap(signals, rule.max_open)
    ordered = sorted(signals, key=lambda s: (s.signal_session, session_order_key(s)))
    if isinstance(rule, MinInitialRiskRule):
        return [
            (
                signal,
                PublicationOutcome.WITHDRAWN_LOW_INITIAL_RISK
                if signal.trade is not None
                and initial_risk_fraction(signal.trade) < rule.min_risk_fraction
                else _unfiltered(signal),
            )
            for signal in ordered
        ]
    return [(signal, _unfiltered(signal)) for signal in ordered]


def max_open_published(decided: list[tuple[EntrySignal, PublicationOutcome]]) -> int:
    """The most published trades open at once, counted after each session's close with
    that session's newly published trades included."""

    trades = [
        s.trade
        for s, o in decided
        if o is PublicationOutcome.PUBLISHED_TRADE and s.trade is not None
    ]
    worst = 0
    for session_date in sorted({t.signal_session for t in trades}):
        open_now = sum(
            1
            for t in trades
            if t.signal_session <= session_date
            and (t.exit_session is None or t.exit_session > session_date)
        )
        worst = max(worst, open_now)
    return worst


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


def measure(
    decided: list[tuple[EntrySignal, PublicationOutcome]],
    *,
    window_start: date,
    window_end: date,
    slippage_bps: Decimal,
) -> dict[str, Any]:
    """The published record of one rule in one window."""

    published = [
        s.trade
        for s, o in decided
        if o is PublicationOutcome.PUBLISHED_TRADE and s.trade is not None
    ]
    expired = Counter(
        s.expiry_reason
        for s, o in decided
        if o is PublicationOutcome.PUBLISHED_EXPIRED and s.expiry_reason is not None
    )
    metrics = compute_risk_metrics(
        trades=published,
        expired_by_reason=dict(expired),
        window_start=window_start,
        window_end=window_end,
        slippage_bps=slippage_bps,
    )
    closed = [t for t in published if t.closed]
    results = [net_r(t) for t in closed]
    by_asset: dict[str, Decimal] = {}
    for trade, value in zip(closed, results, strict=True):
        by_asset[trade.asset] = by_asset.get(trade.asset, Decimal(0)) + value
    return {
        "outcomes": {
            outcome.value: sum(1 for _, o in decided if o is outcome)
            for outcome in PublicationOutcome
        },
        "risk_metrics": metrics,
        "net_r_median": float(statistics.median(results)) if results else None,
        "assets_with_closed_trades": len(by_asset),
        "profitable_assets": sum(1 for value in by_asset.values() if value > 0),
        "max_open_published": max_open_published(decided),
    }


def read_verdict(by_window: Mapping[str, dict[str, Any]], target: TargetRate) -> dict[str, Any]:
    """The protocol's two readings of a rule, and its ranking value."""

    means = [by_window[role]["risk_metrics"]["net_r"]["mean"] for role in by_window]
    keeps_edge = all(mean is not None and mean > 0 for mean in means)
    if "development" in by_window:
        development = by_window["development"]["risk_metrics"]
        total = development["net_r"]["total"] or 0.0
        keeps_edge = keeps_edge and total > 0 and total >= development["max_r_drawdown"]
    rates = [by_window[role]["risk_metrics"]["trades_per_month"] for role in by_window]
    return {
        "keeps_edge": keeps_edge,
        "meets_rate": all(float(target.min) <= rate <= float(target.max) for rate in rates),
        "ranking_value": min(means) if all(mean is not None for mean in means) else None,
    }


# ---------------------------------------------------------------------------
# Reading the stored runs
# ---------------------------------------------------------------------------


def load_run_signals(
    session: Any, *, run_id: uuid.UUID, asset: str
) -> tuple[list[EntrySignal], int]:
    """The entry signals of one stored run, and how many had no recorded outcome (issued
    on the window's last session, never resolved)."""

    trades = risk_trades(
        asset,
        list(
            session.execute(
                select(BacktestTrade).where(BacktestTrade.strategy_run_id == run_id)
            ).scalars()
        ),
    )
    by_session = {t.signal_session: t for t in trades}
    if len(by_session) != len(trades):
        raise SourceDataError(f"run {run_id}: two trades share an entry signal session")
    rows = session.execute(
        select(BacktestSignal.session_date, BacktestSignal.signal_metadata).where(
            BacktestSignal.strategy_run_id == run_id,
            BacktestSignal.signal_metadata["action"].as_string() == SCHEDULED_ENTRY,
        )
    ).all()
    signals: list[EntrySignal] = []
    unresolved = 0
    expiries = {o.value for o in EntryOutcome if o is not EntryOutcome.OPENED}
    for session_date, metadata in rows:
        outcome = metadata.get("entry_outcome")
        if outcome is None:
            unresolved += 1
        elif outcome == EntryOutcome.OPENED.value:
            trade = by_session.pop(session_date, None)
            if trade is None:
                raise SourceDataError(
                    f"run {run_id}: signal of {session_date} opened but has no trade"
                )
            signals.append(EntrySignal(asset, session_date, trade))
        elif outcome in expiries:
            signals.append(EntrySignal(asset, session_date, None, outcome))
        else:
            raise SourceDataError(f"run {run_id}: unknown entry outcome {outcome!r}")
    if by_session:
        raise SourceDataError(f"run {run_id}: {len(by_session)} trades have no entry signal")
    return signals, unresolved


def run_publication_study(
    settings: Settings, protocol: PublicationProtocol, *, protocol_sha256: str
) -> dict[str, Any]:
    source_path = PROJECT_ROOT / protocol.source_results
    raw = source_path.read_bytes()
    source = json.loads(raw)
    if source.get("status") != "completed":
        raise SourceDataError(f"{protocol.source_results} is not a completed study")
    if protocol.version not in source["versions"]:
        raise SourceDataError(f"{protocol.source_results} has no version {protocol.version!r}")
    slippage_bps = Decimal(str(source["protocol"]["costs"]["slippage_bps"]))

    windows: dict[str, dict[str, Any]] = {}
    signals: dict[str, list[EntrySignal]] = {}
    with session_scope(settings) as session:
        for role in protocol.windows:
            runs = [
                r
                for r in source["runs"]
                if r["version"] == protocol.version and r["window_role"] == role
            ]
            if sorted(r["asset"] for r in runs) != sorted(source["protocol"]["assets"]):
                raise SourceDataError(
                    f"{role}: the stored runs do not cover the source study's assets exactly once"
                )
            collected: list[EntrySignal] = []
            unresolved = 0
            for run in runs:
                found, pending = load_run_signals(
                    session, run_id=uuid.UUID(run["run_id"]), asset=run["asset"]
                )
                collected.extend(found)
                unresolved += pending
            window = source["protocol"]["windows"][role]
            windows[role] = {
                "start": window["start"],
                "end": window["end"],
                "runs": len(runs),
                "entry_signals": len(collected),
                "signals_without_outcome": unresolved,
            }
            signals[role] = collected

    rules: list[dict[str, Any]] = []
    for rule in protocol.rules:
        by_window: dict[str, dict[str, Any]] = {
            role: measure(
                apply_rule(signals[role], rule),
                window_start=date.fromisoformat(windows[role]["start"]),
                window_end=date.fromisoformat(windows[role]["end"]),
                slippage_bps=slippage_bps,
            )
            for role in protocol.windows
        }
        rules.append(
            {
                "rule": rule.model_dump(mode="json"),
                "windows": by_window,
                **read_verdict(by_window, protocol.target_trades_per_month),
            }
        )
    rules.sort(
        key=lambda r: (
            -(r["ranking_value"] if r["ranking_value"] is not None else float("-inf")),
            r["rule"]["id"],
        )
    )
    return {
        "status": "completed",
        "protocol": {"sha256": protocol_sha256, **protocol.model_dump(mode="json")},
        "source": {
            "path": protocol.source_results,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "study_id": source["study_id"],
            "protocol_name": source["protocol"]["name"],
            "strategy_version_id": source["versions"][protocol.version]["strategy_version_id"],
            "spec_sha256": source["versions"][protocol.version]["spec_sha256"],
            "assets": source["protocol"]["assets"],
            "slippage_bps": str(slippage_bps),
        },
        "code_sha": study_code_identity(),
        "windows": windows,
        "rules": rules,
        "completed_at": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _n(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _yes(value: bool) -> str:
    return "yes" if value else "no"


def render_report(result: dict[str, Any]) -> str:
    protocol, source = result["protocol"], result["source"]
    target = protocol["target_trades_per_month"]
    lines = [
        f"# Publication-filter study: {protocol['name']}",
        "",
        f"Status: `{result['status']}`. Protocol SHA-256 `{protocol['sha256']}`. Code `{result['code_sha']}`.",
        "",
        "## Setup",
        "",
        f"- Version: `{protocol['version']}` (`{source['strategy_version_id']}`, specification `{source['spec_sha256']}`).",
        f"- Source: `{source['path']}` (SHA-256 `{source['sha256']}`), study `{source['study_id']}`. "
        "Its stored signals and trades are read; no backtest is run.",
        f"- Assets: {len(source['assets'])}. Costs as in the source study ({source['slippage_bps']} bps slippage on each fill).",
        *[
            f"- {role.capitalize()}: {w['start']} to {w['end']}; {w['entry_signals']} entry signals in {w['runs']} runs"
            f" ({w['signals_without_outcome']} more had no outcome at the window's end and are left out)."
            for role, w in result["windows"].items()
        ],
        "- The source study's holdout is not read.",
        f"- Target rate: {target['min']} to {target['max']} published trades a month.",
        "- Each rule is applied on its own. A signal a rule holds back is never published later.",
        "",
        "## Reading",
        "",
        "Keeps the edge: mean net R above 0 in every window, and development total net R at least the"
        " development R drawdown. Meets the rate: published trades a month inside the target in every"
        " window. Rules are listed by the lower of their windows' mean net R.",
        "",
        "| Rule | Keeps the edge | Meets the rate | Lower mean net R |",
        "|---|---|---|---|",
        *[
            f"| {r['rule']['id']} | {_yes(r['keeps_edge'])} | {_yes(r['meets_rate'])} | {_n(r['ranking_value'], 3)} |"
            for r in result["rules"]
        ],
        "",
    ]
    for role in result["windows"]:
        lines += [
            f"## {role.capitalize()} window",
            "",
            "| Rule | Published trades/mo | Closed | Held back / withdrawn | Published, expired | Most open at once"
            " | Mean net R | Median net R | Total net R | Win rate | Max R drawdown | Best / worst R | Profitable assets |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for r in result["rules"]:
            w = r["windows"][role]
            m, o = w["risk_metrics"], w["outcomes"]
            win = m["net_r"]["win_rate"]
            lines.append(
                f"| {r['rule']['id']} | {_n(m['trades_per_month'])} | {m['closed_trades']}"
                f" | {o['held_back_open_cap'] + o['withdrawn_low_initial_risk']} | {o['published_expired']}"
                f" | {w['max_open_published']} | {_n(m['net_r']['mean'], 3)} | {_n(w['net_r_median'], 3)}"
                f" | {_n(m['net_r']['total'])} | {'n/a' if win is None else f'{win * 100:.1f}%'}"
                f" | {_n(m['max_r_drawdown'])} | {_n(m['net_r']['best'])} / {_n(m['net_r']['worst'])}"
                f" | {w['profitable_assets']} of {w['assets_with_closed_trades']} |"
            )
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m trading_platform.services.research.publication_filters"
    )
    parser.add_argument("--protocol", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    settings = load_settings()
    if not settings.research.mode or settings.database.name == TRADING_DATABASE:
        print("Refusing: run in research mode against the research database.", file=sys.stderr)
        return 2
    results_path = args.out / "results.json"
    if results_path.exists():
        print(f"Refusing: {results_path} already holds a study result.", file=sys.stderr)
        return 2
    try:
        protocol, digest = load_protocol(args.protocol)
        result = run_publication_study(settings, protocol, protocol_sha256=digest)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"Study refused: {exc}", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    (args.out / "REPORT.md").write_text(render_report(result))
    print(f"status: {result['status']}; wrote {results_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
