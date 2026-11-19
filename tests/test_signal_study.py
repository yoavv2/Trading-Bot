"""Signals Step 1 evidence: the per-signal metrics in R, the selection rules fixed before
the study, and the study runner (docs/SIGNALS-PLAN.md)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from decimal import Decimal as D
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
from scripts import run_signals_v2_study as study_cli
from tests.support.migrated_db import migrated_database
from tests.support.research_fixtures import make_bars, seed_research_bars, xnys_sessions

from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.models import StrategyRun, StrategyRunStatus
from trading_platform.db.models.research import (
    ResearchRunLink,
    ResearchStudy,
    StrategyVersion,
    StudyRevision,
)
from trading_platform.db.models.research import TestWindowExposure as WindowExposure
from trading_platform.db.models.signal_log import SignalLogEvent, SignalSessionRun
from trading_platform.db.session import session_scope
from trading_platform.services.research import signal_study
from trading_platform.services.research.examples import (
    EXAMPLE_KEYS,
    EXAMPLES_DIR,
    EXAMPLES_V2_DIR,
    load_example_specs,
    seed_example_versions,
)
from trading_platform.services.research.risk_metrics import (
    RiskTrade,
    compute_risk_metrics,
    max_r_drawdown,
    net_r_if_target_first,
)
from trading_platform.services.research.signal_selection import (
    CandidateEvidence,
    CriterionCode,
    HoldoutVerdict,
    SelectionCriteria,
    SelectionVerdict,
    failed_criteria,
    holdout_verdict,
    select,
)
from trading_platform.services.research.signal_study import (
    BLOCKED_BY_CONFORMANCE,
    STATUS_COMPLETED,
    STATUS_FAILED_CONFORMANCE,
    STATUS_PENDING_DATA,
    HoldoutAlreadyExposedError,
    StudyProtocol,
    _reserve_holdout,
    render_report,
    run_signal_study,
)

# ---------------------------------------------------------------------------
# Metrics in R
# ---------------------------------------------------------------------------

START, END = date(2024, 1, 1), date(2024, 12, 31)  # 366 days: 12.02 months


def trade(
    day: int,
    *,
    exit_price: str | None,
    reason: str | None,
    net_pnl: str | None,
    flagged: bool = False,
    asset: str = "AAA",
) -> RiskTrade:
    """Ten shares, rule entry 100, stop 95, target 110: the initial risk is 50."""

    return RiskTrade(
        asset=asset,
        signal_session=date(2024, 1, day),
        exit_session=date(2024, 2, day) if exit_price is not None else None,
        quantity=D(10),
        rule_entry_price=D(100),
        rule_exit_price=D(exit_price) if exit_price is not None else None,
        stop_price=D(95),
        target_price=D(110),
        net_pnl=D(net_pnl) if net_pnl is not None else None,
        exit_reason=reason,
        intrabar_order_unknown=flagged,
    )


def test_risk_metrics_report_rates_expiries_r_and_the_drawdown() -> None:
    trades = [
        trade(2, exit_price="110", reason="target", net_pnl="95"),  # gross 2R, net 1.9R
        trade(3, exit_price="95", reason="stop", net_pnl="-55"),  # gross -1R, net -1.1R
        trade(4, exit_price="95", reason="stop", net_pnl="-55", flagged=True),
        trade(5, exit_price="102", reason="rule", net_pnl="15"),  # gross 0.4R, net 0.3R
        trade(6, exit_price=None, reason=None, net_pnl=None),  # still open
    ]
    metrics = compute_risk_metrics(
        trades=trades,
        expired_by_reason={"expired_reward_risk_below_minimum": 2, "expired_open_at_or_above_target": 1},
        window_start=START,
        window_end=END,
        slippage_bps=D(0),
    )
    months = 366 / (365.25 / 12)
    assert metrics["entry_signals"] == 8
    assert metrics["entry_signals_per_month"] == pytest.approx(8 / months)
    assert (metrics["trades_opened"], metrics["closed_trades"], metrics["open_at_end"]) == (5, 4, 1)
    assert metrics["trades_per_month"] == pytest.approx(5 / months)
    assert metrics["expired_signals"] == 3
    assert metrics["expired_by_reason"] == {
        "expired_open_at_or_below_stop": 0,
        "expired_open_at_or_above_target": 1,
        "expired_reward_risk_below_minimum": 2,
    }
    assert metrics["exits_by_reason"] == {"stop": 2, "target": 1, "rule": 1}
    assert metrics["gross_r"] == {"total": pytest.approx(0.4), "mean": pytest.approx(0.1)}
    assert metrics["net_r"]["total"] == pytest.approx(0.0)
    assert metrics["net_r"]["mean"] == pytest.approx(0.0)
    assert metrics["net_r"]["win_rate"] == 0.5
    assert (metrics["net_r"]["best"], metrics["net_r"]["worst"]) == (pytest.approx(1.9), pytest.approx(-1.1))
    # Cumulative net R by exit date: 1.9, 0.8, -0.3, 0.0. Peak 1.9, trough -0.3.
    assert metrics["max_r_drawdown"] == pytest.approx(2.2)
    assert metrics["net_r_over_max_drawdown"] == pytest.approx(0.0)


def test_the_stop_first_assumption_is_reported_as_a_range() -> None:
    trades = [
        trade(2, exit_price="110", reason="target", net_pnl="100"),
        trade(3, exit_price="95", reason="stop", net_pnl="-50", flagged=True),
    ]
    flag = compute_risk_metrics(
        trades=trades, expired_by_reason={}, window_start=START, window_end=END, slippage_bps=D(0)
    )["intrabar_order_unknown"]
    assert (flag["trades"], flag["share_of_closed_trades"]) == (1, 0.5)
    assert flag["net_r_total_stop_first"] == pytest.approx(1.0)  # 2R - 1R, as recorded
    assert flag["net_r_total_target_first"] == pytest.approx(4.0)  # the flagged trade as a 2R winner
    assert flag["net_r_total_range"] == pytest.approx(3.0)
    assert (flag["net_r_mean_stop_first"], flag["net_r_mean_target_first"]) == (pytest.approx(0.5), pytest.approx(2.0))


def test_the_target_first_bound_keeps_the_exit_slippage() -> None:
    # Exit fills at 0.99 of the rule price: the stop fill is 94.05, the target fill 108.9.
    flagged = trade(3, exit_price="95", reason="stop", net_pnl="-59.5", flagged=True)  # (94.05 - 100) * 10
    assert net_r_if_target_first(flagged, slippage_bps=D(100)) == D("1.78")  # (108.9 - 100) * 10 / 50


def test_no_closed_trades_leaves_r_undefined_not_zero() -> None:
    metrics = compute_risk_metrics(
        trades=[], expired_by_reason={}, window_start=START, window_end=END, slippage_bps=D(5)
    )
    assert metrics["net_r"] == {"total": 0.0, "mean": None, "win_rate": None, "best": None, "worst": None}
    assert metrics["intrabar_order_unknown"]["share_of_closed_trades"] is None
    assert (metrics["max_r_drawdown"], metrics["net_r_over_max_drawdown"]) == (0.0, None)


def test_r_drawdown_is_measured_from_a_curve_that_starts_at_zero() -> None:
    assert max_r_drawdown([D(-1), D(-1), D(3)]) == D(2)
    assert max_r_drawdown([D(2), D(-1), D(2), D(-3)]) == D(3)
    assert max_r_drawdown([]) == D(0)


# ---------------------------------------------------------------------------
# Selection rules
# ---------------------------------------------------------------------------

CRITERIA = SelectionCriteria(
    min_closed_trades_development=30,
    min_closed_trades_validation=10,
    min_profitable_assets_development=3,
    min_trades_per_month_development=1.0,
    min_closed_trades_holdout=8,
)


def window(*, closed: int, mean: float | None, drawdown: float = 2.0, per_month: float = 2.0) -> dict[str, Any]:
    total = (mean or 0.0) * closed
    return {
        "closed_trades": closed,
        "trades_per_month": per_month,
        "net_r": {"mean": mean, "total": total},
        "max_r_drawdown": drawdown,
    }


def candidate(key: str, development: dict[str, Any], validation: dict[str, Any], *, profitable: int = 5) -> CandidateEvidence:
    by_asset = {f"A{i}": (1.0 if i < profitable else -1.0) for i in range(5)}
    return CandidateEvidence(key=key, development=development, validation=validation, development_net_r_by_asset=by_asset)


GOOD = candidate("good", window(closed=40, mean=0.30), window(closed=12, mean=0.20))


def test_a_version_that_meets_every_criterion_qualifies() -> None:
    assert failed_criteria(GOOD, CRITERIA) == ()


@pytest.mark.parametrize(
    ("evidence", "code"),
    [
        (candidate("x", window(closed=29, mean=0.3), window(closed=12, mean=0.2)), CriterionCode.DEVELOPMENT_SAMPLE_TOO_SMALL),
        (candidate("x", window(closed=40, mean=0.3), window(closed=9, mean=0.2)), CriterionCode.VALIDATION_SAMPLE_TOO_SMALL),
        (candidate("x", window(closed=40, mean=0.3), window(closed=12, mean=0.0)), CriterionCode.VALIDATION_MEAN_NET_R_NOT_POSITIVE),
        (candidate("x", window(closed=40, mean=0.3), window(closed=12, mean=None)), CriterionCode.VALIDATION_MEAN_NET_R_NOT_POSITIVE),
        (candidate("x", window(closed=40, mean=0.3, drawdown=12.01), window(closed=12, mean=0.2)), CriterionCode.DEVELOPMENT_NET_R_BELOW_MAX_DRAWDOWN),
        (candidate("x", window(closed=40, mean=0.3), window(closed=12, mean=0.2), profitable=2), CriterionCode.DEVELOPMENT_TOO_FEW_PROFITABLE_ASSETS),
        (candidate("x", window(closed=40, mean=0.3, per_month=0.99), window(closed=12, mean=0.2)), CriterionCode.DEVELOPMENT_TOO_FEW_TRADES_PER_MONTH),
    ],
)
def test_each_criterion_fails_alone_with_its_own_code(evidence: CandidateEvidence, code: CriterionCode) -> None:
    assert failed_criteria(evidence, CRITERIA) == (code,)


def test_a_losing_development_window_fails_the_mean_and_the_drawdown_criteria() -> None:
    losing = candidate("x", window(closed=40, mean=-0.1), window(closed=12, mean=0.2))
    assert set(failed_criteria(losing, CRITERIA)) == {
        CriterionCode.DEVELOPMENT_MEAN_NET_R_NOT_POSITIVE,
        CriterionCode.DEVELOPMENT_NET_R_BELOW_MAX_DRAWDOWN,
    }


def test_qualifying_versions_rank_by_their_weaker_window() -> None:
    # "steady" is weaker in development but its worse window (0.25) beats "good"'s (0.20).
    steady = candidate("steady", window(closed=40, mean=0.25), window(closed=12, mean=0.28))
    failing = candidate("failing", window(closed=40, mean=0.90), window(closed=12, mean=-0.10))
    selection = select([GOOD, failing, steady], CRITERIA)
    assert (selection.verdict, selection.chosen) == (SelectionVerdict.CANDIDATE_SELECTED, "steady")
    assert [c.key for c in selection.candidates] == ["steady", "good", "failing"]
    assert [c.qualifies for c in selection.candidates] == [True, True, False]


def test_no_qualifying_version_leaves_the_choice_open() -> None:
    failing = candidate("failing", window(closed=5, mean=0.9), window(closed=12, mean=0.5))
    selection = select([failing], CRITERIA)
    assert (selection.verdict, selection.chosen) == (SelectionVerdict.NO_CANDIDATE_QUALIFIES, None)


def test_the_holdout_verdict_is_one_of_four() -> None:
    assert holdout_verdict(None, CRITERIA) is HoldoutVerdict.NOT_RUN
    assert holdout_verdict(window(closed=7, mean=0.5), CRITERIA) is HoldoutVerdict.INSUFFICIENT_EVIDENCE
    assert holdout_verdict(window(closed=8, mean=0.01), CRITERIA) is HoldoutVerdict.CONFIRMED
    assert holdout_verdict(window(closed=8, mean=0.0), CRITERIA) is HoldoutVerdict.FAILED


def test_criteria_must_name_exactly_the_known_thresholds() -> None:
    with pytest.raises(ValueError):
        SelectionCriteria.from_mapping({"min_closed_trades_development": 30})


# ---------------------------------------------------------------------------
# The v2 examples
# ---------------------------------------------------------------------------


def test_each_v2_example_keeps_the_v1_rules_and_adds_the_base_risk_block() -> None:
    v1 = {e.key: e.compiled for e in load_example_specs()}
    v2 = {e.key: e.compiled for e in load_example_specs(EXAMPLES_V2_DIR)}
    assert set(v2) == set(EXAMPLE_KEYS)
    for key in EXAMPLE_KEYS:
        assert v2[key].spec_version == 2 and v1[key].spec_version == 1
        assert (v2[key].entry, v2[key].exit) == (v1[key].entry, v1[key].exit)
        assert v2[key].history_required == v1[key].history_required
        risk = v2[key].risk
        assert risk is not None
        assert (risk.stop_percent, risk.target_risk_multiple, risk.min_reward_risk) == (D(5), D(2), D("1.5"))
        assert v2[key].spec_sha256 != v1[key].spec_sha256


# ---------------------------------------------------------------------------
# The study runner
# ---------------------------------------------------------------------------

ASSETS = ["AAA", "BBB"]
SESSIONS = xnys_sessions(date(2022, 1, 3), 420)
HISTORY, DEVELOPMENT, VALIDATION = 40, 220, 80


def _spec(name: str, fast: int, slow: int) -> str:
    return f"""\
spec_version: 2
name: {name}
timeframe: daily
direction: long_only
indicators:
  fast: {{type: sma, source: close, window: {fast}}}
  slow: {{type: sma, source: close, window: {slow}}}
entry:
  all_of:
    - {{left: close, op: gt, right: fast}}
exit:
  any_of:
    - {{left: close, op: lt, right: slow}}
risk:
  stop: {{type: percent, value: 3}}
  target: {{type: risk_multiple, value: 2}}
  min_reward_risk: 1.5
"""


@pytest.fixture()
def examples_directory(tmp_path: Path) -> Path:
    """Four short-history specifications under the example keys the runner seeds."""

    for index, key in enumerate(EXAMPLE_KEYS):
        (tmp_path / f"{key}.yaml").write_text(_spec(f"study fixture {index}", 5 + index, 20 + 5 * index))
    return tmp_path


def _protocol(**criteria: Any) -> StudyProtocol:
    development_end = SESSIONS[HISTORY + DEVELOPMENT - 1]
    validation_end = SESSIONS[HISTORY + DEVELOPMENT + VALIDATION - 1]
    return StudyProtocol.model_validate(
        {
            "name": "study runner test",
            "versions": list(EXAMPLE_KEYS),
            "assets": ASSETS,
            "data": {"provider": "tiingo", "adjusted": True, "history_from": SESSIONS[0]},
            "range": {"start": SESSIONS[HISTORY], "end": SESSIONS[-1]},
            "windows": {
                "development": {"start": SESSIONS[HISTORY], "end": development_end},
                "validation": {"start": SESSIONS[HISTORY + DEVELOPMENT], "end": validation_end},
                "holdout": {"start": SESSIONS[HISTORY + DEVELOPMENT + VALIDATION], "end": SESSIONS[-1]},
            },
            "initial_capital": 100000,
            "quantity_policy": "fractional",
            "costs": {"slippage_bps": 5, "commission_per_order": 1},
            "criteria": {
                "min_closed_trades_development": 5,
                "min_closed_trades_validation": 2,
                "min_profitable_assets_development": 1,
                "min_trades_per_month_development": 0.5,
                "min_closed_trades_holdout": 2,
                **criteria,
            },
        }
    )


@pytest.fixture()
def settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    with migrated_database(monkeypatch, "signal_study"):
        yield load_settings()


def _seed(settings: Settings, assets: list[str], *, sessions: list[date] = SESSIONS) -> None:
    with session_scope(settings) as session:
        for index, asset in enumerate(assets):
            # A steady upward drift, so long trades with a 2R target make money.
            seed_research_bars(session, asset, make_bars(asset, sessions, seed=31 + index, drift=0.004, volatility=0.012))


def _count(settings: Settings, model: Any) -> int:
    with session_scope(settings) as session:
        return session.execute(sa.select(sa.func.count()).select_from(model)).scalar_one()


def test_missing_bars_leave_the_study_pending_and_write_nothing(settings: Settings, examples_directory: Path) -> None:
    _seed(settings, ["AAA"])
    _seed(settings, ["BBB"], sessions=SESSIONS[HISTORY + 10 : -3])  # no history, and the last three sessions missing
    result = run_signal_study(settings, _protocol(), protocol_sha256="0" * 64, examples_directory=examples_directory)
    assert result["status"] == STATUS_PENDING_DATA
    assert result["data_gaps"] == [
        {
            "asset": "BBB",
            "problem": "missing_bars",
            "missing_sessions": 13,
            "first_missing": SESSIONS[HISTORY].isoformat(),
            "last_missing": SESSIONS[-1].isoformat(),
        },
        {
            "asset": "BBB",
            "problem": "missing_history",
            "bars_before_range": 0,
            "bars_needed_before_range": 34,
            "needed_from": SESSIONS[0].isoformat(),
        },
    ]
    assert _count(settings, ResearchStudy) == 0 and _count(settings, StrategyVersion) == 0
    assert "did not run" in render_report(result)


def test_the_study_selects_on_two_windows_and_runs_the_holdout_once_for_the_chosen_version(
    settings: Settings, examples_directory: Path
) -> None:
    _seed(settings, ASSETS)
    result = run_signal_study(settings, _protocol(), protocol_sha256="a" * 64, examples_directory=examples_directory)

    assert result["status"] == STATUS_COMPLETED
    chosen = result["selection"]["chosen"]
    assert chosen in EXAMPLE_KEYS and result["selection"]["verdict"] == "candidate_selected"
    # Every version on every asset in both selection windows; the holdout for the chosen one only.
    selection_runs = [r for r in result["runs"] if r["window_role"] != "final_test"]
    holdout_runs = [r for r in result["runs"] if r["window_role"] == "final_test"]
    assert len(selection_runs) == len(EXAMPLE_KEYS) * len(ASSETS) * 2
    assert [(r["version"], r["asset"]) for r in holdout_runs] == [(chosen, asset) for asset in ASSETS]
    assert set(result["summary"][chosen]) == {"development", "validation", "holdout"}
    assert all(set(result["summary"][key]) == {"development", "validation"} for key in EXAMPLE_KEYS if key != chosen)
    assert result["holdout"]["verdict"] in {"confirmed", "insufficient_evidence", "failed"}
    assert result["pilot_candidate"] == (chosen if result["holdout"]["verdict"] == "confirmed" else None)

    # The daily engine agreed with every run, and its log was rolled back.
    conformance = result["engine_conformance"]
    assert conformance["runs_checked"] == len(result["runs"]) and conformance["runs_not_checked"] == 0
    assert conformance["differences"] == 0 and conformance["closed_trades_compared"] > 20
    assert _count(settings, SignalLogEvent) == 0 and _count(settings, SignalSessionRun) == 0

    # What a rerun needs is recorded: ids, digests, the cost model, the windows.
    revision_id = uuid.UUID(result["study_revision_id"])
    with session_scope(settings) as session:
        links = list(session.execute(sa.select(ResearchRunLink).where(ResearchRunLink.study_revision_id == revision_id)).scalars())
        exposures = list(session.execute(sa.select(WindowExposure)).scalars())
        assert {str(link.run_id) for link in links} == {r["run_id"] for r in result["runs"]}
        assert {(e.asset, str(e.strategy_version_id)) for e in exposures} == {
            (asset, result["versions"][chosen]["strategy_version_id"]) for asset in ASSETS
        }
        assert {(e.asset, str(e.run_id), e.input_digest) for e in exposures} == {
            (run["asset"], run["run_id"], run["input_digest"]) for run in holdout_runs
        }
    assert all(len(r["input_digest"]) == 64 and len(r["results_digest"]) == 64 for r in result["runs"])
    assert all(r["results_digest_version"] == 2 and r["code_sha"] for r in result["runs"])
    assert result["protocol"]["costs"] == {"slippage_bps": "5", "commission_per_order": "1"}

    # The pooled record adds up from the per-asset runs.
    pooled = result["summary"][chosen]["development"]["pooled"]
    mine = [r for r in selection_runs if r["version"] == chosen and r["window_role"] == "development"]
    assert pooled["closed_trades"] == sum(r["risk_metrics"]["closed_trades"] for r in mine)
    assert pooled["net_r"]["total"] == pytest.approx(sum(r["risk_metrics"]["net_r"]["total"] for r in mine))
    assert pooled["expired_signals"] == sum(r["risk_metrics"]["expired_signals"] for r in mine)

    report = render_report(result)
    assert "**0 differences**" in report and chosen in report


def test_no_holdout_run_when_no_version_qualifies(settings: Settings, examples_directory: Path) -> None:
    _seed(settings, ASSETS)
    result = run_signal_study(
        settings,
        _protocol(min_closed_trades_development=10_000),
        protocol_sha256="b" * 64,
        examples_directory=examples_directory,
    )
    assert result["selection"] == {
        "verdict": "no_candidate_qualifies",
        "chosen": None,
        "candidates": result["selection"]["candidates"],
    }
    assert all("development_sample_too_small" in c["failed_criteria"] for c in result["selection"]["candidates"])
    assert [r for r in result["runs"] if r["window_role"] == "final_test"] == []
    assert (result["holdout"], result["pilot_candidate"]) == ({"version": None, "verdict": "not_run"}, None)
    assert _count(settings, WindowExposure) == 0


def test_prior_exposure_blocks_the_holdout_for_every_asset(
    settings: Settings, examples_directory: Path
) -> None:
    _seed(settings, ASSETS)
    protocol = _protocol()
    with session_scope(settings) as session:
        session.add(
            WindowExposure(
                asset=ASSETS[-1],
                range_start=protocol.windows.validation.start,
                range_end=protocol.windows.holdout.start,
                reason="final_test",
                is_rerun=False,
                run_recorded_at=datetime.now(UTC),
            )
        )

    with pytest.raises(HoldoutAlreadyExposedError, match=f"overlaps exposure .* for {ASSETS[-1]}"):
        run_signal_study(settings, protocol, protocol_sha256="c" * 64, examples_directory=examples_directory)
    assert _count(settings, WindowExposure) == 1
    with session_scope(settings) as session:
        assert session.execute(
            sa.select(ResearchRunLink).where(ResearchRunLink.window_role == "final_test")
        ).first() is None


def test_concurrent_overlapping_holdout_reservations_have_one_winner(
    settings: Settings, examples_directory: Path
) -> None:
    with session_scope(settings) as session:
        version = seed_example_versions(session, directory=examples_directory)[0]
        revisions = []
        for name in ("reservation A", "reservation B"):
            study = ResearchStudy(name=name)
            session.add(study)
            session.flush()
            revision = StudyRevision(
                study_id=study.id, revision_no=1, settings_json={}, created_at=datetime.now(UTC)
            )
            session.add(revision)
            session.flush()
            revisions.append((study.id, revision.id))

    protocol = _protocol()
    other_protocol = protocol.model_copy(update={"assets": ["BBB", "CCC"]})
    start = Barrier(2)

    def reserve(study_protocol: StudyProtocol, study_id: uuid.UUID, revision_id: uuid.UUID) -> dict[str, uuid.UUID]:
        start.wait(timeout=10)
        return _reserve_holdout(
            settings,
            study_protocol,
            study_id=study_id,
            revision_id=revision_id,
            strategy_id=version.strategy_id,
            version_id=version.id,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(reserve, study_protocol, *ids)
            for study_protocol, ids in zip((protocol, other_protocol), revisions, strict=True)
        ]
        winners = [future.result() for future in futures if future.exception(timeout=10) is None]
        failures = [future.exception() for future in futures if future.exception() is not None]
    assert len(winners) == len(failures) == 1
    assert isinstance(failures[0], HoldoutAlreadyExposedError)
    assert "for BBB" in str(failures[0])
    with session_scope(settings) as session:
        exposures = session.execute(sa.select(WindowExposure)).scalars()
        assert {exposure.asset: exposure.id for exposure in exposures} == winners[0]


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("assets",), [" brk.b ", "BRK-B"], "unique after ticker normalization"),
        (("assets",), [" "], "nonblank"),
        (("versions",), [EXAMPLE_KEYS[0], f" {EXAMPLE_KEYS[0]} "], "versions must be unique"),
        (("windows", "holdout", "end"), SESSIONS[0], "start must be on or before"),
        (("windows", "development", "start"), SESSIONS[0], "inside the study range"),
        (("windows", "validation", "start"), SESSIONS[HISTORY + DEVELOPMENT - 1], "ordered and disjoint"),
    ],
)
def test_protocol_rejects_ambiguous_run_inputs(path: tuple[str, ...], value: Any, message: str) -> None:
    data = _protocol().model_dump()
    target = data
    for field in path[:-1]:
        target = target[field]
    target[path[-1]] = value

    with pytest.raises(ValueError, match=message):
        StudyProtocol.model_validate(data)


def test_protocol_canonicalizes_tickers() -> None:
    data = _protocol().model_dump()
    data["assets"] = [" brk.b ", "aapl"]
    assert StudyProtocol.model_validate(data).assets == ["BRK-B", "AAPL"]


def test_study_rejects_v1_examples_before_opening_a_database_session(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_session(_settings: Settings) -> None:
        pytest.fail("Study opened a database session for a version outside the v2 study")

    monkeypatch.setattr(signal_study, "session_scope", unexpected_session)
    with pytest.raises(ValueError, match="do not use specification v2"):
        run_signal_study(load_settings(), _protocol(), protocol_sha256="c" * 64, examples_directory=EXAMPLES_DIR)


@pytest.mark.parametrize(
    ("failed_role", "checked"),
    [("development", True), ("validation", False), ("final_test", True)],
)
def test_conformance_failure_blocks_the_corresponding_decision(
    settings: Settings,
    examples_directory: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_role: str,
    checked: bool,
) -> None:
    _seed(settings, ASSETS)
    actual = signal_study._conformance
    failed_window = _protocol().window(failed_role)
    detail = "2022-04-01: exit_price: log 12 != backtest 10"
    reason = "signal_log_not_empty_for_version_and_asset"

    def fake_conformance(*args: Any, **kwargs: Any) -> dict[str, Any]:
        if kwargs["window"] != failed_window or kwargs["asset"] != ASSETS[0]:
            return actual(*args, **kwargs)
        if not checked:
            return {"checked": False, "reason": reason}
        return {**actual(*args, **kwargs), "differences": 1, "difference_details": [detail]}

    def unexpected_decision(*_args: Any) -> None:
        pytest.fail("Study evaluated a decision without passing engine conformance")

    monkeypatch.setattr(signal_study, "_conformance", fake_conformance)
    if failed_role == "final_test":
        monkeypatch.setattr(signal_study, "holdout_verdict", unexpected_decision)
    else:
        monkeypatch.setattr(signal_study, "select_candidate", unexpected_decision)
    result = run_signal_study(settings, _protocol(), protocol_sha256="c" * 64, examples_directory=examples_directory)

    assert result["status"] == STATUS_FAILED_CONFORMANCE
    assert result["pilot_candidate"] is None
    holdout_runs = [r for r in result["runs"] if r["window_role"] == "final_test"]
    if failed_role == "final_test":
        assert result["selection"]["verdict"] == "candidate_selected"
        assert result["holdout"]["verdict"] == BLOCKED_BY_CONFORMANCE
        assert len(holdout_runs) == _count(settings, WindowExposure) == len(ASSETS)
    else:
        assert result["selection"]["verdict"] == BLOCKED_BY_CONFORMANCE
        assert result["holdout"]["verdict"] == "not_run"
        assert holdout_runs == []
        assert _count(settings, WindowExposure) == 0
    evidence = detail if checked else reason
    assert evidence in render_report(result)
    assert evidence in json.dumps(result)


def test_cli_returns_failure_and_preserves_conformance_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        study_cli,
        "load_settings",
        lambda: SimpleNamespace(research=SimpleNamespace(mode=True), database=SimpleNamespace(name="fixture")),
    )
    monkeypatch.setattr(study_cli, "load_protocol", lambda _path: (_protocol(), "a" * 64))
    monkeypatch.setattr(study_cli, "run_signal_study", lambda *args, **kwargs: {"status": STATUS_FAILED_CONFORMANCE})
    monkeypatch.setattr(study_cli, "render_report", lambda _result: "Conformance failed\n")

    args = ["--protocol", "fixture.yaml", "--out", str(tmp_path)]
    assert study_cli.main(args) == 4
    results = tmp_path / "results.json"
    assert json.loads(results.read_text())["status"] == STATUS_FAILED_CONFORMANCE
    assert (tmp_path / "REPORT.md").read_text() == "Conformance failed\n"
    assert study_cli.main(args) == 2

def test_holdout_digest_write_failure_rolls_back_run_completion(
    settings: Settings, examples_directory: Path
) -> None:
    _seed(settings, ASSETS)
    with session_scope(settings) as session:
        session.execute(sa.text(
            "ALTER TABLE test_window_exposures ADD CONSTRAINT reject_completion "
            "CHECK (input_digest IS NULL)"
        ))
    protocol = _protocol().model_copy(update={"versions": [EXAMPLE_KEYS[0]]})
    with pytest.raises(sa.exc.IntegrityError, match="reject_completion"):
        run_signal_study(settings, protocol, protocol_sha256="d" * 64, examples_directory=examples_directory)

    with session_scope(settings) as session:
        exposure = session.scalars(sa.select(WindowExposure).where(WindowExposure.run_id.is_not(None))).one()
        run = session.get(StrategyRun, exposure.run_id)
        link = session.get(ResearchRunLink, exposure.run_id)
        assert run is not None and run.status == StrategyRunStatus.RUNNING
        assert link is not None and link.input_digest is None
        assert exposure.input_digest is None
