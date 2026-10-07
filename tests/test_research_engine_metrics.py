"""S3 engine and evaluation units: quantity policies and rounding slack, overspend
prevention, trading defaults preserved, buy-and-hold comparability, and the pinned
metric, evidence and ranking rules with their undefined-value and tie behaviour."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.services.backtesting import (
    QUANTITY_POLICY_FRACTIONAL,
    QUANTITY_POLICY_WHOLE_SHARES,
    EngineOptions,
    _entry_quantity,
)
from trading_platform.services.research.evidence import (
    GRADE_INSUFFICIENT,
    GRADE_MEETS,
    GRADE_THIN,
    LIMITATIONS,
    anchored_clusters,
    grade_from,
)
from trading_platform.services.research.metrics import (
    FLAG_ANNUALISED_FROM_SHORT_WINDOW,
    FLAG_DATA_GAP_AFFECTED,
    EquityPoint,
    TradeRecord,
    compute_metrics,
)
from trading_platform.services.research.ranking import (
    OUTCOME_INSUFFICIENT,
    OUTCOME_MET,
    OUTCOME_NOT_MET,
    STATUS_ELIGIBLE,
    STATUS_INSUFFICIENT_EVIDENCE,
    STATUS_NOT_ELIGIBLE,
    STATUS_NOT_EVALUABLE,
    VERDICT_INSUFFICIENT,
    VERDICT_LEADING,
    VERDICT_NONE,
    final_test_outcome,
    rank_candidates,
)

# ---------------------------------------------------------------------------
# Quantity policy (proposal J.3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("policy", [QUANTITY_POLICY_FRACTIONAL, QUANTITY_POLICY_WHOLE_SHARES])
@pytest.mark.parametrize(("cash", "slot", "price", "commission"), [
    (Decimal("10000"), Decimal("10000"), Decimal("333.333333"), Decimal("1")),
    (Decimal("10000"), Decimal("2500"), Decimal("0.07"), Decimal("0")),
    (Decimal("150"), Decimal("10000"), Decimal("149.5"), Decimal("1")),
    (Decimal("10000"), Decimal("10000"), Decimal("12345.67"), Decimal("0")),
])
def test_quantity_never_overspends_and_slack_is_the_actual_residual(policy, cash, slot, price, commission) -> None:
    quantity, slack = _entry_quantity(cash=cash, slot_notional=slot, fill_price=price, commission=commission, quantity_policy=policy)
    affordable = min(slot, cash - commission)
    assert quantity * price <= affordable
    if quantity > 0:
        assert slack == (affordable - quantity * price).quantize(Decimal("0.000001"))
        if policy == QUANTITY_POLICY_FRACTIONAL:
            assert quantity == quantity.quantize(Decimal("0.000001"))
            assert slack < price * Decimal("0.000001") + Decimal("0.000001")
        else:
            assert quantity == quantity.to_integral_value()
    else:
        assert slack == 0


def test_fractional_quantity_is_scale_invariant_in_invested_fraction() -> None:
    """Scaling the price by k leaves the invested fraction equal within the policy tolerance."""

    cash = Decimal("100000")
    for k in (Decimal("0.01"), Decimal("1"), Decimal("100")):
        price = Decimal("57.19") * k
        quantity, _ = _entry_quantity(cash=cash, slot_notional=cash, fill_price=price, commission=Decimal("0"), quantity_policy=QUANTITY_POLICY_FRACTIONAL)
        invested = quantity * price / cash
        assert Decimal(1) - invested < price * Decimal("0.000001") / cash + Decimal("1e-12")


def test_whole_share_quantity_rounds_to_zero_above_affordable_notional() -> None:
    quantity, slack = _entry_quantity(cash=Decimal("100"), slot_notional=Decimal("100"), fill_price=Decimal("150"), commission=Decimal("0"), quantity_policy=QUANTITY_POLICY_WHOLE_SHARES)
    assert (quantity, slack) == (Decimal(0), Decimal(0))
    fractional, _ = _entry_quantity(cash=Decimal("100"), slot_notional=Decimal("100"), fill_price=Decimal("150"), commission=Decimal("0"), quantity_policy=QUANTITY_POLICY_FRACTIONAL)
    assert fractional == Decimal("0.666666")


def test_trading_engine_options_from_settings_are_the_trading_defaults() -> None:
    clear_settings_cache()
    settings = load_settings()
    options = EngineOptions.from_settings(settings)
    assert options.quantity_policy == QUANTITY_POLICY_WHOLE_SHARES
    assert (options.bar_provider, options.bar_adjusted, options.research) == ("polygon", True, False)
    assert options.initial_capital == Decimal(str(settings.backtest.initial_capital)).quantize(Decimal("0.000001"))
    assert options.max_concurrent_positions == settings.backtest.max_concurrent_positions
    with pytest.raises(ValueError):
        _entry_quantity(cash=Decimal(1), slot_notional=Decimal(1), fill_price=Decimal(1), commission=Decimal(0), quantity_policy="lots")


# ---------------------------------------------------------------------------
# Metrics (proposal I.2)
# ---------------------------------------------------------------------------

D0 = date(2020, 1, 2)


def _equity(values: list[str], exposure: str = "0") -> list[EquityPoint]:
    return [EquityPoint(D0 + timedelta(days=i), Decimal(v), Decimal(exposure), Decimal(0)) for i, v in enumerate(values)]


def _trade(net: str | None, *, days: int = 3, open_: bool = False, i: int = 0, slack: str = "0") -> TradeRecord:
    entry = D0 + timedelta(days=i * 10)
    return TradeRecord(
        status="open" if open_ else "closed",
        quantity=Decimal(1),
        entry_fill_session=entry,
        entry_price=Decimal(100),
        entry_commission=Decimal(1),
        entry_slippage=Decimal("0.05"),
        exit_fill_session=None if open_ else entry + timedelta(days=days),
        exit_price=None if open_ else Decimal(100) + Decimal(net or 0),
        exit_commission=Decimal(0) if open_ else Decimal(1),
        exit_slippage=Decimal(0) if open_ else Decimal("0.05"),
        net_pnl=None if open_ else Decimal(net or 0),
        holding_period_sessions=None if open_ else days,
        rounding_slack=Decimal(slack),
    )


def test_metrics_edge_cases_are_null_with_reasons() -> None:
    m = compute_metrics(initial_capital=Decimal(1000), equity=_equity(["1000"]), trades=[])
    assert m["net_total_return"] == 0.0 and m["cagr"] is None and m["max_drawdown"] == 0.0
    assert m["closed_trades"] == 0 and m["win_rate"] is None and m["profit_factor"] is None and m["expectancy"] is None
    assert m["holding_period"] == {"median_sessions": None, "mean_sessions": None}
    assert m["sharpe_daily_ann"] is None and m["sortino_daily_ann"] is None
    assert "fewer than two returns" in m["notes"]["sharpe_daily_ann"]
    # Rising equity only: drawdown 0, sortino undefined (no negative day), sharpe defined.
    m = compute_metrics(initial_capital=Decimal(1000), equity=_equity(["1000", "1010", "1030", "1035"]), trades=[])
    assert m["max_drawdown"] == 0.0 and m["drawdown_duration"] == 0 and m["sortino_daily_ann"] is None and m["sharpe_daily_ann"] > 0
    assert FLAG_ANNUALISED_FROM_SHORT_WINDOW in m["flags"] and m["cagr"] > 0
    # Constant equity: zero deviation -> sharpe undefined.
    m = compute_metrics(initial_capital=Decimal(1000), equity=_equity(["1000", "1000", "1000"]), trades=[])
    assert m["sharpe_daily_ann"] is None and "zero deviation" in m["notes"]["sharpe_daily_ann"]


def test_metrics_drawdown_duration_costs_slack_and_open_positions() -> None:
    equity = _equity(["1000", "1100", "1050", "1000", "1080", "1120", "1100"], exposure="500")
    trades = [_trade("10"), _trade("-4", days=5, i=1), _trade("6", days=2, i=2), _trade(None, open_=True, i=3, slack="0.5")]
    m = compute_metrics(initial_capital=Decimal(1000), equity=equity, trades=trades, skipped_fills=1)
    assert m["max_drawdown"] == pytest.approx(1000 / 1100 - 1)
    assert m["drawdown_duration"] == 3  # 1050, 1000, 1080 below the 1100 peak
    assert m["closed_trades"] == 3 and m["open_at_end"]["count"] == 1
    assert m["win_rate"] == pytest.approx(2 / 3) and m["profit_factor"] == pytest.approx(16 / 4)
    assert m["expectancy"] == pytest.approx(12 / 3)
    assert m["holding_period"]["median_sessions"] == 3 and m["holding_period"]["mean_sessions"] == pytest.approx(10 / 3)
    assert m["total_costs"]["commission"] == 7.0 and m["total_costs"]["slippage"] == pytest.approx(0.35)
    assert m["rounding_slack"] == 0.5 and m["exposure"] == pytest.approx(sum(500 / v for v in (1000, 1100, 1050, 1000, 1080, 1120, 1100)) / 7)
    assert FLAG_DATA_GAP_AFFECTED in m["flags"] and m["skipped_fills"] == 1
    assert m["turnover"] > 0
    no_losses = compute_metrics(initial_capital=Decimal(1000), equity=equity, trades=[_trade("10"), _trade("5", i=1)])
    assert no_losses["profit_factor"] is None and no_losses["notes"]["profit_factor"] == "undefined: no losing trades"


# ---------------------------------------------------------------------------
# Evidence (proposal I.3)
# ---------------------------------------------------------------------------


def test_anchored_clusters_at_plus_4_5_6_and_every_third_session() -> None:
    sessions = [D0 + timedelta(days=i) for i in range(40)]
    index = {s: i for i, s in enumerate(sessions)}
    assert anchored_clusters([sessions[0], sessions[4]], index) == 1
    assert anchored_clusters([sessions[0], sessions[5]], index) == 1
    assert anchored_clusters([sessions[0], sessions[6]], index) == 2
    assert anchored_clusters([sessions[i] for i in range(0, 30, 3)], index) == 5
    assert anchored_clusters([], index) == 0


def _descriptors(closed: int, clusters: int, ratio: float | None, best_three: float | None) -> dict:
    return {"closed_trades": closed, "clusters": clusters, "holding_ratio": ratio, "concentration": {"best_three_share": best_three}}


@pytest.mark.parametrize(("closed", "clusters", "ratio", "best_three", "expected"), [
    (0, 0, None, None, GRADE_INSUFFICIENT),
    (10, 4, 50.0, 0.2, GRADE_INSUFFICIENT),
    (10, 5, 4.9, 0.2, GRADE_INSUFFICIENT),
    (10, 5, 5.0, 0.2, GRADE_THIN),
    (10, 9, 50.0, 0.2, GRADE_THIN),
    (10, 10, 9.9, 0.2, GRADE_THIN),
    (10, 10, 10.0, 0.51, GRADE_THIN),
    (10, 10, 10.0, 0.5, GRADE_MEETS),
    (30, 12, 40.0, 0.3, GRADE_MEETS),
])
def test_grade_boundaries(closed, clusters, ratio, best_three, expected) -> None:
    grade, _ = grade_from(_descriptors(closed, clusters, ratio, best_three))
    assert grade == expected
    assert "No grade certifies statistical reliability." in LIMITATIONS


# ---------------------------------------------------------------------------
# Ranking, ties, verdicts, final-test outcomes (proposal I.4, I.5)
# ---------------------------------------------------------------------------


def _candidate(name: str, *, ret: float, dd: float, cagr: float = 0.1, grade: str = GRADE_MEETS, bench_ret: float | None = 0.05, profit_factor: float | None = 1.0, dev_dd: float = -0.05) -> dict:
    def window(r, d, g):
        return {"run_id": "r", "metrics": {"net_total_return": r, "max_drawdown": d, "cagr": cagr, "profit_factor": profit_factor, "flags": []}, "evidence": {"grade": g, "grade_reasons": [] if g != GRADE_INSUFFICIENT else ["no closed trades"]}}

    return {
        "strategy_version_id": name,
        "asset": "AAA",
        "windows": {"development": window(ret, dev_dd, GRADE_MEETS), "validation": window(ret, dd, grade)},
        "benchmark": {"development": {"net_total_return": 0.01}, "validation": None if bench_ret is None else {"net_total_return": bench_ret}},
    }


def test_return_first_order_tiebreak_and_co_leading() -> None:
    candidates = [
        _candidate("a", ret=0.20, dd=-0.10, profit_factor=9.0),
        _candidate("b", ret=0.20, dd=-0.05, profit_factor=0.5),
        _candidate("c", ret=0.20, dd=-0.05, profit_factor=None),
        _candidate("d", ret=0.30, dd=-0.40),  # breaches the cap: not eligible, never ranked
        _candidate("e", ret=0.10, dd=-0.02, grade=GRADE_INSUFFICIENT),
        _candidate("f", ret=0.10, dd=-0.02, bench_ret=None),
    ]
    result = rank_candidates(candidates, objective="return_first", constraint_value=0.2)
    assert [r["strategy_version_id"] for r in result["ranking"]] == ["b", "c", "a"]
    assert [r["co_leading"] for r in result["ranking"]] == [True, True, False]
    assert len(result["co_leaders"]) == 2 and result["verdict"] == VERDICT_LEADING
    status = {c["strategy_version_id"]: c["status"] for c in candidates}
    assert status == {"a": STATUS_ELIGIBLE, "b": STATUS_ELIGIBLE, "c": STATUS_ELIGIBLE, "d": STATUS_NOT_ELIGIBLE, "e": STATUS_INSUFFICIENT_EVIDENCE, "f": STATUS_NOT_EVALUABLE}
    assert candidates[0]["windows"]["validation"]["excess_return_vs_benchmark"] == pytest.approx(0.15)
    assert candidates[5]["windows"]["validation"]["excess_return_vs_benchmark"] is None
    assert result["profit_factor_affects_order"] is False


def test_risk_first_order_signed_drawdown_and_development_breach_is_not_a_gate() -> None:
    candidates = [
        _candidate("deep", ret=0.50, dd=-0.30, cagr=0.3),
        _candidate("shallow", ret=0.05, dd=-0.02, cagr=0.04, dev_dd=-0.90),  # breach only in development
        _candidate("flat", ret=0.05, dd=0.0, cagr=0.04),
        _candidate("floor", ret=0.01, dd=-0.01, cagr=-0.10),  # below the cagr floor
    ]
    result = rank_candidates(candidates, objective="risk_first", constraint_value=0.0)
    assert [r["strategy_version_id"] for r in result["ranking"]] == ["flat", "shallow", "deep"]
    assert {c["strategy_version_id"]: c["status"] for c in candidates}["floor"] == STATUS_NOT_ELIGIBLE
    assert {c["strategy_version_id"]: c["status"] for c in candidates}["shallow"] == STATUS_ELIGIBLE


def test_verdicts_never_force_a_winner() -> None:
    insufficient = rank_candidates([_candidate("x", ret=0.1, dd=-0.5), _candidate("y", ret=0.1, dd=-0.1, grade=GRADE_INSUFFICIENT)], objective="return_first", constraint_value=0.2)
    assert insufficient["verdict"] == VERDICT_INSUFFICIENT and insufficient["ranking"] == []
    none = rank_candidates([_candidate("x", ret=0.9, dd=-0.5), _candidate("y", ret=0.8, dd=-0.3)], objective="return_first", constraint_value=0.2)
    assert none["verdict"] == VERDICT_NONE and none["ranking"] == [] and none["co_leaders"] == []


def _metrics(ret: float, dd: float, flags=()) -> dict:
    return {"net_total_return": ret, "max_drawdown": dd, "cagr": ret, "flags": list(flags)}


def test_final_test_outcomes_in_order_and_benchmark_is_separate() -> None:
    acceptance = {"constraint_value": 0.2, "objective_minimum": 0.05}
    meets = {"grade": GRADE_MEETS, "grade_reasons": []}
    thin = {"grade": GRADE_THIN, "grade_reasons": ["clusters 7 < 10"]}
    insufficient = {"grade": GRADE_INSUFFICIENT, "grade_reasons": ["no closed trades"]}
    bench = _metrics(0.30, -0.05)
    out = final_test_outcome(objective="return_first", acceptance=acceptance, candidate_metrics=_metrics(0.10, -0.10), candidate_evidence=meets, benchmark_metrics=bench)
    assert out["outcome"] == OUTCOME_MET and out["thin_evidence"] is False
    assert out["benchmark_comparison"]["return_vs_benchmark"] == "worse" and out["benchmark_comparison"]["drawdown_vs_benchmark"] == "worse"
    assert out["benchmark_comparison"]["excess_return_vs_benchmark"] == pytest.approx(-0.20)
    out = final_test_outcome(objective="return_first", acceptance=acceptance, candidate_metrics=_metrics(0.10, -0.10), candidate_evidence=thin, benchmark_metrics=bench)
    assert out["outcome"] == OUTCOME_MET and out["thin_evidence"] is True
    out = final_test_outcome(objective="return_first", acceptance=acceptance, candidate_metrics=_metrics(0.04, -0.10), candidate_evidence=meets, benchmark_metrics=bench)
    assert out["outcome"] == OUTCOME_NOT_MET and "objective-dimension minimum" in out["outcome_reasons"][0]
    out = final_test_outcome(objective="return_first", acceptance=acceptance, candidate_metrics=_metrics(0.10, -0.25), candidate_evidence=meets, benchmark_metrics=bench)
    assert out["outcome"] == OUTCOME_NOT_MET
    out = final_test_outcome(objective="return_first", acceptance=acceptance, candidate_metrics=_metrics(0.10, -0.10), candidate_evidence=insufficient, benchmark_metrics=bench)
    assert out["outcome"] == OUTCOME_INSUFFICIENT
    out = final_test_outcome(objective="return_first", acceptance=acceptance, candidate_metrics=_metrics(0.10, -0.10, flags=["data_gap_affected"]), candidate_evidence=meets, benchmark_metrics=bench)
    assert out["outcome"] == OUTCOME_INSUFFICIENT and "data_gap_affected" in out["outcome_reasons"]
    out = final_test_outcome(objective="risk_first", acceptance={"constraint_value": 0.0, "objective_minimum": -0.15}, candidate_metrics=_metrics(0.02, -0.10), candidate_evidence=meets, benchmark_metrics=None)
    assert out["outcome"] == OUTCOME_MET and out["benchmark_comparison"] is None
    assert "Beating buy-and-hold is not required" in out["wording"]
