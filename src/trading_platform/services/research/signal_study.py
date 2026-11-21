"""The first signals study (signals plan, Step 1): v2 versions, run and judged by a protocol.

``run_signal_study`` reads a protocol that was written before the run
(``config/research/signals_v2_study.yaml``) and:

1. refuses to start, with the exact gaps, when the bars the protocol needs are missing
   (status ``pending_data``; nothing is written to the database);
2. seeds the v2 example versions and records a study revision with the settings;
3. runs every version on every asset over the development and validation windows
   through ``run_research_backtest`` (run id, input digest and results digest per run);
4. checks each run against the daily signal engine on the same sessions. The engine
   writes its log inside a transaction that is rolled back, so the comparison leaves the
   append-only ``signal_log`` empty for the pilot;
5. pools each version's trades per window and applies ``signal_selection.select`` only
   when every selection run passes engine conformance;
6. runs the holdout window for the chosen version only, once, records a test-window
   exposure per asset, and gives the holdout verdict if every holdout run passes
   engine conformance.

The returned mapping is everything needed to repeat the comparison: version ids and
digests, run ids, windows, assets, the cost model, the code identity and the protocol
digest. ``render_report`` turns it into Markdown.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select, text

from trading_platform.core.settings import Settings
from trading_platform.db.models import BacktestTrade
from trading_platform.db.models.daily_bar import DailyBar
from trading_platform.db.models.research import StrategyVersion, TestWindowExposure
from trading_platform.db.models.signal_log import SignalLogEvent
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import get_session_factory, session_scope
from trading_platform.services.market_data_access import persisted_session_dates
from trading_platform.services.research.backtest import (
    ResearchRunRequest,
    ResearchRunResult,
    risk_trades,
    run_research_backtest,
)
from trading_platform.services.research.catalog import normalize_ticker
from trading_platform.services.research.code_identity import study_code_identity
from trading_platform.services.research.examples import (
    EXAMPLES_V2_DIR,
    example_strategy_id,
    load_example_specs,
    seed_example_versions,
)
from trading_platform.services.research.risk_metrics import RiskTrade, compute_risk_metrics, net_r
from trading_platform.services.research.signal_selection import (
    CandidateEvidence,
    HoldoutVerdict,
    SelectionCriteria,
    holdout_verdict,
)
from trading_platform.services.research.signal_selection import (
    select as select_candidate,
)
from trading_platform.services.research.studies import StudyService
from trading_platform.services.signals.comparison import (
    differences,
    outcomes_from_backtest,
    outcomes_from_log,
)
from trading_platform.services.signals.engine import run_signal_sessions

STATUS_PENDING_DATA = "pending_data"
STATUS_COMPLETED = "completed"
STATUS_FAILED_CONFORMANCE = "failed_conformance"
BLOCKED_BY_CONFORMANCE = "blocked_by_conformance"
SELECTION_WINDOWS = ("development", "validation")
HOLDOUT_ROLE = "final_test"  # the research run link's name for the holdout window
EXPOSURE_REASON = "signals_study_holdout"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProtocolWindow(_Strict):
    start: date
    end: date

    @model_validator(mode="after")
    def _ordered_bounds(self) -> ProtocolWindow:
        if self.start > self.end:
            raise ValueError("window start must be on or before its end")
        return self


class ProtocolWindows(_Strict):
    development: ProtocolWindow
    validation: ProtocolWindow
    holdout: ProtocolWindow


class ProtocolData(_Strict):
    provider: str
    adjusted: bool
    history_from: date


class ProtocolCosts(_Strict):
    slippage_bps: Decimal = Field(ge=0)
    commission_per_order: Decimal = Field(ge=0)


class StudyProtocol(_Strict):
    name: str = Field(min_length=1, max_length=120)
    versions: list[str] = Field(min_length=1)
    assets: list[str] = Field(min_length=1)
    data: ProtocolData
    range: ProtocolWindow
    windows: ProtocolWindows
    initial_capital: Decimal = Field(gt=0)
    quantity_policy: Literal["fractional", "whole_shares"]
    costs: ProtocolCosts
    criteria: dict[str, Any]

    @field_validator("assets")
    @classmethod
    def _canonical_assets(cls, values: list[str]) -> list[str]:
        assets = [normalize_ticker(value) for value in values]
        if any(not asset for asset in assets):
            raise ValueError("assets must be nonblank")
        if len(set(assets)) != len(assets):
            raise ValueError("assets must be unique after ticker normalization")
        return assets

    @field_validator("versions")
    @classmethod
    def _unique_versions(cls, values: list[str]) -> list[str]:
        versions = [value.strip() for value in values]
        if any(not version for version in versions):
            raise ValueError("versions must be nonblank")
        if len(set(versions)) != len(versions):
            raise ValueError("versions must be unique")
        return versions

    @model_validator(mode="after")
    def _ordered_windows(self) -> StudyProtocol:
        prior_end: date | None = None
        for role in (*SELECTION_WINDOWS, "holdout"):
            window: ProtocolWindow = getattr(self.windows, role)
            if window.start < self.range.start or window.end > self.range.end:
                raise ValueError(f"{role} window must lie inside the study range")
            if prior_end is not None and window.start <= prior_end:
                raise ValueError("development, validation and holdout windows must be ordered and disjoint")
            prior_end = window.end
        if self.data.history_from > self.range.start:
            raise ValueError("history_from must be on or before the study range start")
        return self

    def window(self, role: str) -> ProtocolWindow:
        return self.windows.holdout if role == HOLDOUT_ROLE else getattr(self.windows, role)


def load_protocol(path: Path) -> tuple[StudyProtocol, str]:
    """The protocol and the SHA-256 of the file it was read from."""

    raw = path.read_bytes()
    return StudyProtocol.model_validate(yaml.safe_load(raw)), hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------------------
# Data coverage
# ---------------------------------------------------------------------------


def coverage_gaps(session, settings: Settings, protocol: StudyProtocol, *, history_required: int) -> list[dict[str, Any]]:
    """What the protocol needs and the database lacks, per asset. Empty when complete."""

    exchange = settings.market_data.calendar.exchange
    sessions = persisted_session_dates(session, start=protocol.range.start, end=protocol.range.end, exchange=exchange)
    gaps: list[dict[str, Any]] = []
    if not sessions:
        return [{"asset": "*", "problem": "no_market_sessions", "range": [str(protocol.range.start), str(protocol.range.end)]}]
    for asset in protocol.assets:
        symbol_id = session.execute(select(Symbol.id).where(Symbol.ticker == asset)).scalar_one_or_none()
        have: set[date] = set()
        history = 0
        if symbol_id is not None:
            base = select(DailyBar.session_date).where(
                DailyBar.symbol_id == symbol_id,
                DailyBar.provider == protocol.data.provider,
                DailyBar.adjusted.is_(protocol.data.adjusted),
            )
            have = set(
                session.execute(
                    base.where(DailyBar.session_date >= protocol.range.start, DailyBar.session_date <= protocol.range.end)
                ).scalars()
            )
            history = session.execute(
                select(func.count()).select_from(base.where(DailyBar.session_date < protocol.range.start).subquery())
            ).scalar_one()
        missing = [d for d in sessions if d not in have]
        if missing:
            gaps.append(
                {
                    "asset": asset,
                    "problem": "missing_bars",
                    "missing_sessions": len(missing),
                    "first_missing": missing[0].isoformat(),
                    "last_missing": missing[-1].isoformat(),
                }
            )
        # The evaluation at ``range.start`` reads ``history_required`` bars ending on it.
        if history < history_required - 1:
            gaps.append(
                {
                    "asset": asset,
                    "problem": "missing_history",
                    "bars_before_range": history,
                    "bars_needed_before_range": history_required - 1,
                    "needed_from": protocol.data.history_from.isoformat(),
                }
            )
    return gaps


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


def _conformance(settings: Settings, *, version_id: uuid.UUID, asset: str, run_id: uuid.UUID, window: ProtocolWindow, bar_source: tuple[str, bool]) -> dict[str, Any]:
    """Run the daily engine over the window and compare it with the backtest run. The
    engine's rows are rolled back; ``signal_log`` is left as it was."""

    session = get_session_factory(settings)()
    try:
        existing = session.execute(
            select(func.count()).select_from(SignalLogEvent).where(
                SignalLogEvent.strategy_version_id == version_id, SignalLogEvent.asset == asset
            )
        ).scalar_one()
        if existing:
            return {"checked": False, "reason": "signal_log_not_empty_for_version_and_asset"}
        version = session.get(StrategyVersion, version_id)
        assert version is not None
        outcomes = run_signal_sessions(
            session,
            settings,
            version=version,
            asset=asset,
            start=window.start,
            end=window.end,
            bar_source=bar_source,
        )
        if any(outcome.skipped is not None for outcome in outcomes):
            return {"checked": False, "reason": "daily_engine_skipped_a_session"}
        log = outcomes_from_log(session, strategy_version_id=version_id, asset=asset, through=window.end)
        backtest = outcomes_from_backtest(session, run_id=run_id, asset=asset)
        found = differences(log, backtest)
        return {
            "checked": True,
            "signals_compared": len(backtest),
            "closed_trades_compared": sum(1 for o in backtest if o.gross_r is not None),
            "differences": len(found),
            "first_differences": found[:5],
            "difference_details": found,
        }
    finally:
        session.rollback()
        session.close()


def _summarise_conformance(runs: list[dict[str, Any]]) -> dict[str, int]:
    checked = [run["engine_conformance"] for run in runs if run["engine_conformance"]["checked"]]
    return {
        "runs_checked": len(checked),
        "runs_not_checked": len(runs) - len(checked),
        "signals_compared": sum(check["signals_compared"] for check in checked),
        "closed_trades_compared": sum(check["closed_trades_compared"] for check in checked),
        "differences": sum(check["differences"] for check in checked),
    }


def _pooled(settings: Settings, runs: list[dict[str, Any]], window: ProtocolWindow, slippage_bps: Decimal) -> tuple[dict[str, Any], dict[str, float]]:
    """Pooled risk metrics of one version in one window, and total net R per asset."""

    trades: list[RiskTrade] = []
    expired: dict[str, int] = {}
    with session_scope(settings) as session:
        for run in runs:
            rows = list(
                session.execute(select(BacktestTrade).where(BacktestTrade.strategy_run_id == uuid.UUID(run["run_id"]))).scalars()
            )
            trades.extend(risk_trades(run["asset"], rows))
            for reason, count in run["expired_by_reason"].items():
                expired[reason] = expired.get(reason, 0) + int(count)
    pooled = compute_risk_metrics(
        trades=trades, expired_by_reason=expired, window_start=window.start, window_end=window.end, slippage_bps=slippage_bps
    )
    by_asset = {run["asset"]: 0.0 for run in runs}
    for trade in trades:
        if trade.closed:
            by_asset[trade.asset] += float(net_r(trade))
    return pooled, by_asset


class HoldoutAlreadyExposedError(ValueError):
    """A recorded final-test exposure overlaps the proposed holdout."""


def _reserve_holdout(
    settings: Settings,
    protocol: StudyProtocol,
    *,
    study_id: uuid.UUID,
    revision_id: uuid.UUID,
    strategy_id: uuid.UUID,
    version_id: uuid.UUID,
) -> dict[str, uuid.UUID]:
    window = protocol.windows.holdout
    assets = sorted(set(protocol.assets))
    reservations = {asset: uuid.uuid4() for asset in assets}
    with session_scope(settings) as session:
        # Acquire in asset order so concurrent reservations sharing several assets
        # cannot deadlock. The committed ledger remains authoritative after release.
        for asset in assets:
            digest = hashlib.sha256(f"signals-study-holdout:{asset}".encode()).digest()
            session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": int.from_bytes(digest[:8], "big", signed=True)},
            )
        exposure = session.execute(
            select(TestWindowExposure)
            .where(
                TestWindowExposure.asset.in_(assets),
                TestWindowExposure.range_start <= window.end,
                TestWindowExposure.range_end >= window.start,
            )
            .order_by(TestWindowExposure.asset, TestWindowExposure.range_start, TestWindowExposure.id)
            .limit(1)
        ).scalar_one_or_none()
        if exposure is not None:
            raise HoldoutAlreadyExposedError(
                f"Holdout {window.start} through {window.end} overlaps exposure {exposure.id} "
                f"for {exposure.asset} ({exposure.range_start} through {exposure.range_end})."
            )
        recorded_at = datetime.now(UTC)
        session.add_all(
            TestWindowExposure(
                id=exposure_id,
                asset=asset,
                range_start=window.start,
                range_end=window.end,
                study_id=study_id,
                study_revision_id=revision_id,
                strategy_id=strategy_id,
                strategy_version_id=version_id,
                reason=EXPOSURE_REASON,
                is_rerun=False,
                run_recorded_at=recorded_at,
            )
            for asset, exposure_id in reservations.items()
        )
    return reservations


def run_signal_study(
    settings: Settings,
    protocol: StudyProtocol,
    *,
    protocol_sha256: str,
    examples_directory: Path = EXAMPLES_V2_DIR,
    progress: Callable[[str], None] = lambda _message: None,
) -> dict[str, Any]:
    criteria = SelectionCriteria.from_mapping(protocol.criteria)
    examples = {e.key: e for e in load_example_specs(examples_directory)}
    unknown = [key for key in protocol.versions if key not in examples]
    if unknown:
        raise ValueError(f"protocol names versions with no v2 example: {unknown}")
    non_v2 = [key for key in protocol.versions if examples[key].compiled.spec_version != 2]
    if non_v2:
        raise ValueError(f"protocol names examples that do not use specification v2: {non_v2}")
    bar_source = (protocol.data.provider, protocol.data.adjusted)
    result: dict[str, Any] = {
        "protocol": {"name": protocol.name, "sha256": protocol_sha256, **protocol.model_dump(mode="json")},
        "code_sha": study_code_identity(),
        "started_at": datetime.now(UTC).isoformat(),
    }

    with session_scope(settings) as session:
        gaps = coverage_gaps(
            session, settings, protocol, history_required=max(examples[k].compiled.history_required for k in protocol.versions)
        )
    if gaps:
        return {**result, "status": STATUS_PENDING_DATA, "data_gaps": gaps}

    with session_scope(settings) as session:
        seed_example_versions(session, directory=examples_directory)
    versions: dict[str, dict[str, Any]] = {}
    with session_scope(settings) as session:
        for key in protocol.versions:
            row = session.execute(
                select(StrategyVersion).where(
                    StrategyVersion.strategy_id == example_strategy_id(key),
                    StrategyVersion.spec_sha256 == examples[key].compiled.spec_sha256,
                )
            ).scalar_one()
            risk = examples[key].compiled.risk
            assert risk is not None
            versions[key] = {
                "strategy_version_id": str(row.id),
                "strategy_id": str(row.strategy_id),
                "version_no": row.version_no,
                "name": row.name,
                "spec_sha256": row.spec_sha256,
                "history_required": row.history_required,
                "risk": {
                    "stop_percent": str(risk.stop_percent),
                    "target_risk_multiple": str(risk.target_risk_multiple),
                    "min_reward_risk": str(risk.min_reward_risk),
                },
            }

    created = StudyService(settings).create_study(
        name=protocol.name,
        kind="substantive",
        settings={
            "strategy_version_ids": [versions[k]["strategy_version_id"] for k in protocol.versions],
            "assets": protocol.assets,
            "range": protocol.range.model_dump(mode="json"),
            "windows": {
                "development": protocol.windows.development.model_dump(mode="json"),
                "validation": protocol.windows.validation.model_dump(mode="json"),
                "final_test": protocol.windows.holdout.model_dump(mode="json"),
            },
            "initial_capital": str(protocol.initial_capital),
            "quantity_policy": protocol.quantity_policy,
            "costs": protocol.costs.model_dump(mode="json"),
            "provider": protocol.data.provider,
            "adjusted": protocol.data.adjusted,
            "note": (
                f"Signals plan Step 1. Run by scripts/run_signals_v2_study.py from protocol sha256 {protocol_sha256}. "
                "Selection by services/research/signal_selection.py on pooled net R, not by the study ranking."
            ),
        },
    )
    study_id = created["study"]["study_id"]
    revision_id = uuid.UUID(created["revision"]["revision_id"])
    result.update({"study_id": study_id, "study_revision_id": str(revision_id), "versions": versions})

    def run_one(key: str, asset: str, role: str, *, exposure_id: uuid.UUID | None = None) -> dict[str, Any]:
        window = protocol.window(role)
        version_id = uuid.UUID(versions[key]["strategy_version_id"])
        outcome: ResearchRunResult = run_research_backtest(
            settings,
            ResearchRunRequest(
                study_revision_id=revision_id,
                strategy_version_id=version_id,
                asset=asset,
                window_role=role,
                window_start=window.start,
                window_end=window.end,
                initial_capital=protocol.initial_capital,
                commission_per_order=protocol.costs.commission_per_order,
                slippage_bps=protocol.costs.slippage_bps,
                quantity_policy=protocol.quantity_policy,
                bar_provider=protocol.data.provider,
                bar_adjusted=protocol.data.adjusted,
            ),
            exposure_id=exposure_id,
        )
        assert outcome.risk_metrics is not None
        conformance = _conformance(
            settings, version_id=version_id, asset=asset, run_id=outcome.run_id, window=window, bar_source=bar_source
        )
        progress(
            f"{key} {asset} {role}: {outcome.risk_metrics['closed_trades']} closed trades, "
            f"engine differences {conformance.get('differences')}"
        )
        return {
            "version": key,
            "asset": asset,
            "window_role": role,
            "window": window.model_dump(mode="json"),
            "run_id": str(outcome.run_id),
            "input_digest": outcome.input_digest,
            "results_digest": outcome.results_digest,
            "results_digest_version": outcome.results_digest_version,
            "code_sha": outcome.code_sha,
            "expired_by_reason": outcome.risk_metrics["expired_by_reason"],
            "risk_metrics": outcome.risk_metrics,
            "equity": {
                "max_drawdown": outcome.metrics["max_drawdown"],
                "net_total_return": outcome.metrics["net_total_return"],
            },
            "engine_conformance": conformance,
        }

    def summarise(key: str, role: str, runs: list[dict[str, Any]]) -> dict[str, Any]:
        mine = [r for r in runs if r["version"] == key and r["window_role"] == role]
        pooled, by_asset = _pooled(settings, mine, protocol.window(role), protocol.costs.slippage_bps)
        return {
            "pooled": pooled,
            "net_r_total_by_asset": by_asset,
            "worst_asset_equity_drawdown": min(r["equity"]["max_drawdown"] for r in mine),
        }

    runs: list[dict[str, Any]] = []
    for key in protocol.versions:
        for asset in protocol.assets:
            for role in SELECTION_WINDOWS:
                runs.append(run_one(key, asset, role))

    conformance = _summarise_conformance(runs)
    if conformance["differences"] or conformance["runs_not_checked"]:
        return {
            **result,
            "status": STATUS_FAILED_CONFORMANCE,
            "runs": runs,
            "summary": {},
            "selection": {"verdict": BLOCKED_BY_CONFORMANCE, "chosen": None, "candidates": []},
            "holdout": {"version": None, "verdict": HoldoutVerdict.NOT_RUN.value},
            "pilot_candidate": None,
            "engine_conformance": conformance,
            "completed_at": datetime.now(UTC).isoformat(),
        }

    summary: dict[str, dict[str, Any]] = {
        key: {role: summarise(key, role, runs) for role in SELECTION_WINDOWS} for key in protocol.versions
    }
    selection = select_candidate(
        [
            CandidateEvidence(
                key=key,
                development=summary[key]["development"]["pooled"],
                validation=summary[key]["validation"]["pooled"],
                development_net_r_by_asset=summary[key]["development"]["net_r_total_by_asset"],
            )
            for key in protocol.versions
        ],
        criteria,
    )
    result["selection"] = {
        "verdict": selection.verdict.value,
        "chosen": selection.chosen,
        "candidates": [
            {
                "version": c.key,
                "qualifies": c.qualifies,
                "failed_criteria": [code.value for code in c.failed],
                "ranking_value": c.ranking_value,
            }
            for c in selection.candidates
        ],
    }

    holdout_pooled: dict[str, Any] | None = None
    if selection.chosen is not None:
        key = selection.chosen
        reservations = _reserve_holdout(
            settings,
            protocol,
            study_id=uuid.UUID(study_id),
            revision_id=revision_id,
            strategy_id=uuid.UUID(versions[key]["strategy_id"]),
            version_id=uuid.UUID(versions[key]["strategy_version_id"]),
        )
        for asset in protocol.assets:
            runs.append(run_one(key, asset, HOLDOUT_ROLE, exposure_id=reservations[asset]))
        summary[key]["holdout"] = summarise(key, HOLDOUT_ROLE, runs)
        holdout_pooled = summary[key]["holdout"]["pooled"]

    conformance = _summarise_conformance(runs)
    failed = bool(conformance["differences"] or conformance["runs_not_checked"])
    verdict = BLOCKED_BY_CONFORMANCE if failed else holdout_verdict(holdout_pooled, criteria).value
    result.update(
        {
            "status": STATUS_FAILED_CONFORMANCE if failed else STATUS_COMPLETED,
            "runs": runs,
            "summary": summary,
            "holdout": {"version": selection.chosen, "verdict": verdict},
            "pilot_candidate": selection.chosen if verdict == HoldoutVerdict.CONFIRMED.value else None,
            "engine_conformance": conformance,
            "completed_at": datetime.now(UTC).isoformat(),
        }
    )
    return result


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _n(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def render_report(result: dict[str, Any]) -> str:
    protocol = result["protocol"]
    lines = [
        f"# Signals study: {protocol['name']}",
        "",
        f"Status: `{result['status']}`. Protocol SHA-256 `{protocol['sha256']}`. Code `{result['code_sha']}`.",
        "",
    ]
    if result["status"] == STATUS_PENDING_DATA:
        lines += ["The study did not run. Missing data:", ""]
        lines += [f"- `{gap}`" for gap in result["data_gaps"]]
        return "\n".join(lines) + "\n"

    costs = protocol["costs"]
    lines += [
        "## Setup",
        "",
        f"- Assets: {', '.join(protocol['assets'])} ({protocol['data']['provider']}, "
        f"{'adjusted' if protocol['data']['adjusted'] else 'unadjusted'} bars).",
        *[f"- {role.capitalize()}: {w['start']} to {w['end']}." for role, w in protocol["windows"].items()],
        f"- Costs: {costs['slippage_bps']} bps slippage on each fill, {costs['commission_per_order']} USD per order; "
        f"capital {protocol['initial_capital']}, {protocol['quantity_policy']} quantity, one asset per run.",
        f"- Study `{result['study_id']}`, revision `{result['study_revision_id']}`.",
        "",
        "| Version | Version id | Specification SHA-256 |",
        "|---|---|---|",
        *[f"| {key} | `{v['strategy_version_id']}` | `{v['spec_sha256']}` |" for key, v in result["versions"].items()],
        "",
    ]

    conformance = result["engine_conformance"]
    lines += [
        "## Daily engine against the backtest",
        "",
        f"{conformance['runs_checked']} runs checked, {conformance['signals_compared']} signals and "
        f"{conformance['closed_trades_compared']} closed trades compared, "
        f"**{conformance['differences']} differences**. {conformance['runs_not_checked']} runs not checked.",
        "",
    ]
    for run in result["runs"]:
        check = run["engine_conformance"]
        if check["checked"] and check["differences"] == 0:
            continue
        label = f"{run['version']} {run['asset']} {run['window_role']} (run `{run['run_id']}`)"
        if check["checked"]:
            lines.append(f"- {label} has {check['differences']} differences.")
            details = check.get("difference_details", check.get("first_differences", []))
            lines.extend(f"  - `{detail}`" for detail in details)
        else:
            lines.append(f"- {label} could not be checked. Reason `{check['reason']}`.")
    lines.append("")

    for role in ("development", "validation", "holdout"):
        rows = [(key, s[role]) for key, s in result["summary"].items() if role in s]
        if not rows:
            continue
        lines += [
            f"## {role.capitalize()} window, pooled over the assets",
            "",
            "| Version | Signals/mo | Trades/mo | Closed | Expired: below stop / above target / reward-risk | "
            "Exits: stop / target / rule | Mean net R | Total net R | Mean gross R | Win rate | Max R drawdown | "
            "Worst asset equity drawdown | Flagged trades | Total net R if target first |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for key, block in rows:
            m = block["pooled"]
            expired = m["expired_by_reason"]
            exits = m["exits_by_reason"]
            flag = m["intrabar_order_unknown"]
            lines.append(
                f"| {key} | {_n(m['entry_signals_per_month'])} | {_n(m['trades_per_month'])} | {m['closed_trades']} | "
                f"{expired['expired_open_at_or_below_stop']} / {expired['expired_open_at_or_above_target']} / "
                f"{expired['expired_reward_risk_below_minimum']} | "
                f"{exits['stop']} / {exits['target']} / {exits['rule']} | {_n(m['net_r']['mean'], 3)} | "
                f"{_n(m['net_r']['total'])} | {_n(m['gross_r']['mean'], 3)} | {_pct(m['net_r']['win_rate'])} | "
                f"{_n(m['max_r_drawdown'])} | {_pct(block['worst_asset_equity_drawdown'])} | "
                f"{flag['trades']} ({_pct(flag['share_of_closed_trades'])}) | {_n(flag['net_r_total_target_first'])} |"
            )
        lines += ["", "Total net R per asset:", "", "| Version | " + " | ".join(protocol["assets"]) + " |", "|---|" + "---|" * len(protocol["assets"])]
        for key, block in rows:
            lines.append(f"| {key} | " + " | ".join(_n(block["net_r_total_by_asset"][a]) for a in protocol["assets"]) + " |")
        lines.append("")

    selection = result["selection"]
    lines += ["## Selection", "", f"Verdict: `{selection['verdict']}`.", "", "| Version | Qualifies | Ranking value | Failed criteria |", "|---|---|---|---|"]
    for c in selection["candidates"]:
        lines.append(
            f"| {c['version']} | {'yes' if c['qualifies'] else 'no'} | {_n(c['ranking_value'], 3)} | "
            f"{', '.join(c['failed_criteria']) or 'none'} |"
        )
    lines += [
        "",
        "## Holdout",
        "",
        f"Version run on the holdout: `{result['holdout']['version']}`. Verdict: `{result['holdout']['verdict']}`.",
        f"Pilot candidate: `{result['pilot_candidate']}`.",
        "",
        "## Runs",
        "",
        "| Version | Asset | Window | Run id | Input digest | Results digest | Engine differences |",
        "|---|---|---|---|---|---|---|",
    ]
    for run in result["runs"]:
        lines.append(
            f"| {run['version']} | {run['asset']} | {run['window_role']} | `{run['run_id']}` | "
            f"`{run['input_digest'][:16]}` | `{run['results_digest'][:16]}` | {run['engine_conformance'].get('differences', 'not checked')} |"
        )
    return "\n".join(lines) + "\n"
