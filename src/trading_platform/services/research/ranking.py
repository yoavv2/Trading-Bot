"""Objectives, constraints, candidate status, ranking, verdicts, final-test outcomes and
the ``comparison.json`` document (proposal Parts I.4, I.5, H.2), pure.

Return and risk are reported side by side and never combined; profit factor never
enters the order; the buy-and-hold comparison is always shown and never a gate.
"""

from __future__ import annotations

from typing import Any

OBJECTIVE_RETURN_FIRST = "return_first"
OBJECTIVE_RISK_FIRST = "risk_first"
OBJECTIVES = (OBJECTIVE_RETURN_FIRST, OBJECTIVE_RISK_FIRST)

STATUS_NOT_EVALUABLE = "not_evaluable"
STATUS_INSUFFICIENT_EVIDENCE = "insufficient_evidence"
STATUS_NOT_ELIGIBLE = "not_eligible"
STATUS_ELIGIBLE = "eligible"

VERDICT_LEADING = "leading_candidate_identified"
VERDICT_INSUFFICIENT = "insufficient_evidence"
VERDICT_NONE = "no_candidate_qualifies"

OUTCOME_INSUFFICIENT = "insufficient_evidence"
OUTCOME_MET = "frozen_criteria_met"
OUTCOME_NOT_MET = "frozen_criteria_not_met"

COMPARISON_SCHEMA_VERSION = 1
MODE_LABEL = "independent tests, one asset each; not a portfolio"
BENCHMARK_NOTE = (
    "Buy-and-hold of the same asset with the same dates, capital, costs and quantity policy, "
    "shown for comparison only: it is never a selection gate, a ranking key or a final-test criterion."
)
STUDY_LIMITATIONS = (
    "Prices are the provider's adjusted series at fetch time; absolute levels depend on later corporate actions.",
    "Assets were chosen with hindsight; the study cannot correct for that selection.",
    "One validation window grades the evidence; it is not an out-of-sample guarantee.",
    "Evidence grades are pre-registered heuristics, not statistical tests.",
    "Only research done inside this application is recorded; outside exposure to the test window cannot be observed.",
)


def constraint_holds(objective: str, constraint_value: float, metrics: dict[str, Any]) -> bool | None:
    """``return_first``: ``max_drawdown >= -cap``; ``risk_first``: ``cagr >= floor``.
    ``None`` when the metric is undefined."""

    if objective == OBJECTIVE_RETURN_FIRST:
        value = metrics.get("max_drawdown")
        return None if value is None else value >= -abs(constraint_value)
    value = metrics.get("cagr")
    return None if value is None else value >= constraint_value


def ranking_key(objective: str, metrics: dict[str, Any]) -> tuple[float, float]:
    """Sort key (ascending sort): return-first ranks by net return desc then drawdown
    desc (closer to zero first); risk-first ranks by drawdown desc then return desc."""

    ret = metrics.get("net_total_return") or 0.0
    dd = metrics.get("max_drawdown") or 0.0
    if objective == OBJECTIVE_RETURN_FIRST:
        return (-ret, -dd)
    return (-dd, -ret)


def objective_dimension(objective: str, metrics: dict[str, Any]) -> float | None:
    return metrics.get("net_total_return") if objective == OBJECTIVE_RETURN_FIRST else metrics.get("max_drawdown")


def candidate_status(candidate: dict[str, Any], *, objective: str, constraint_value: float) -> tuple[str, list[str]]:
    reasons: list[str] = []
    validation = candidate["windows"].get("validation")
    if validation is None or validation.get("metrics") is None:
        return STATUS_NOT_EVALUABLE, ["no validation-window result"]
    if candidate["benchmark"].get("validation") is None:
        return STATUS_NOT_EVALUABLE, ["no validation-window benchmark"]
    if validation["evidence"]["grade"] == "insufficient":
        return STATUS_INSUFFICIENT_EVIDENCE, validation["evidence"]["grade_reasons"]
    holds = constraint_holds(objective, constraint_value, validation["metrics"])
    if holds is None:
        return STATUS_NOT_EVALUABLE, ["constraint metric undefined on the validation window"]
    if not holds:
        name = "max_drawdown cap" if objective == OBJECTIVE_RETURN_FIRST else "cagr floor"
        reasons.append(f"{name} {constraint_value} not met on the validation window")
        return STATUS_NOT_ELIGIBLE, reasons
    return STATUS_ELIGIBLE, []


def rank_candidates(candidates: list[dict[str, Any]], *, objective: str, constraint_value: float) -> dict[str, Any]:
    """Assign statuses, order the eligible candidates, mark co-leaders, derive the verdict."""

    for candidate in candidates:
        status, reasons = candidate_status(candidate, objective=objective, constraint_value=constraint_value)
        candidate["status"] = status
        candidate["status_reasons"] = reasons
        for role, window in candidate["windows"].items():
            bench = candidate["benchmark"].get(role)
            if window is not None and window.get("metrics") is not None:
                window["excess_return_vs_benchmark"] = (
                    window["metrics"]["net_total_return"] - bench["net_total_return"]
                    if bench is not None and bench.get("net_total_return") is not None and window["metrics"]["net_total_return"] is not None
                    else None
                )
    eligible = [c for c in candidates if c["status"] == STATUS_ELIGIBLE]
    ordered = sorted(eligible, key=lambda c: ranking_key(objective, c["windows"]["validation"]["metrics"]))
    leader_key = ranking_key(objective, ordered[0]["windows"]["validation"]["metrics"]) if ordered else None
    ranking: list[dict[str, Any]] = []
    for position, candidate in enumerate(ordered, start=1):
        key = ranking_key(objective, candidate["windows"]["validation"]["metrics"])
        co_leading = key == leader_key
        candidate["rank"] = position
        candidate["co_leading"] = co_leading
        ranking.append(
            {
                "rank": position,
                "strategy_version_id": candidate["strategy_version_id"],
                "asset": candidate["asset"],
                "co_leading": co_leading,
                "net_total_return": candidate["windows"]["validation"]["metrics"]["net_total_return"],
                "max_drawdown": candidate["windows"]["validation"]["metrics"]["max_drawdown"],
                "cagr": candidate["windows"]["validation"]["metrics"]["cagr"],
                "excess_return_vs_benchmark": candidate["windows"]["validation"]["excess_return_vs_benchmark"],
                "grade": candidate["windows"]["validation"]["evidence"]["grade"],
            }
        )
    for candidate in candidates:
        candidate.setdefault("rank", None)
        candidate.setdefault("co_leading", False)
    if eligible:
        verdict = VERDICT_LEADING
    elif any(c["status"] in (STATUS_INSUFFICIENT_EVIDENCE, STATUS_NOT_EVALUABLE) for c in candidates):
        verdict = VERDICT_INSUFFICIENT
    else:
        verdict = VERDICT_NONE
    return {
        "objective": objective,
        "constraint_value": constraint_value,
        "ranking_key": "net_total_return desc, then max_drawdown desc (closer to zero first), then co_leading"
        if objective == OBJECTIVE_RETURN_FIRST
        else "max_drawdown desc (closer to zero first), then net_total_return desc, then co_leading",
        "ranking": ranking,
        "co_leaders": [r for r in ranking if r["co_leading"]],
        "verdict": verdict,
        "verdict_label": {
            VERDICT_LEADING: "leading candidate under these criteria",
            VERDICT_INSUFFICIENT: "insufficient evidence",
            VERDICT_NONE: "no candidate qualifies",
        }[verdict],
        "profit_factor_affects_order": False,
    }


def final_test_outcome(
    *,
    objective: str,
    acceptance: dict[str, Any],
    candidate_metrics: dict[str, Any] | None,
    candidate_evidence: dict[str, Any] | None,
    benchmark_metrics: dict[str, Any] | None,
) -> dict[str, Any]:
    """I.5 step 3 in order; the benchmark block is separate and never part of the outcome."""

    benchmark_comparison: dict[str, Any] | None = None
    if candidate_metrics is not None and benchmark_metrics is not None:
        ret_c, ret_b = candidate_metrics.get("net_total_return"), benchmark_metrics.get("net_total_return")
        dd_c, dd_b = candidate_metrics.get("max_drawdown"), benchmark_metrics.get("max_drawdown")
        benchmark_comparison = {
            "return_vs_benchmark": None if ret_c is None or ret_b is None else ("better" if ret_c > ret_b else "worse" if ret_c < ret_b else "equal"),
            "drawdown_vs_benchmark": None if dd_c is None or dd_b is None else ("better" if dd_c > dd_b else "worse" if dd_c < dd_b else "equal"),
            "excess_return_vs_benchmark": None if ret_c is None or ret_b is None else ret_c - ret_b,
            "benchmark_net_total_return": ret_b,
            "benchmark_max_drawdown": dd_b,
            "note": BENCHMARK_NOTE,
        }
    if candidate_metrics is None or candidate_evidence is None:
        outcome, reasons, thin = OUTCOME_INSUFFICIENT, ["candidate not evaluable on the test window"], False
    elif candidate_evidence["grade"] == "insufficient" or "data_gap_affected" in candidate_metrics.get("flags", []):
        outcome, reasons, thin = OUTCOME_INSUFFICIENT, list(candidate_evidence["grade_reasons"]) + (["data_gap_affected"] if "data_gap_affected" in candidate_metrics.get("flags", []) else []), False
    else:
        holds = constraint_holds(objective, float(acceptance["constraint_value"]), candidate_metrics)
        dimension = objective_dimension(objective, candidate_metrics)
        minimum = float(acceptance["objective_minimum"])
        dimension_ok = dimension is not None and dimension >= minimum
        thin = candidate_evidence["grade"] == "thin"
        if holds and dimension_ok:
            outcome, reasons = OUTCOME_MET, []
        else:
            outcome, reasons = OUTCOME_NOT_MET, []
            if not holds:
                reasons.append("constraint value not met on the test window")
            if not dimension_ok:
                reasons.append("objective-dimension minimum not met on the test window")
    return {
        "outcome": outcome,
        "outcome_reasons": reasons,
        "thin_evidence": thin,
        "acceptance": dict(acceptance),
        "benchmark_comparison": benchmark_comparison,
        "wording": (
            "The outcome states whether the candidate met the user's pre-registered conditions on one "
            "held-out window. It does not certify statistical reliability or future profitability. "
            "Beating buy-and-hold is not required."
        ),
    }
