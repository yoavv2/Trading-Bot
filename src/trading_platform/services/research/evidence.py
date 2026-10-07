"""Evidence descriptors and heuristic grades (proposal Part I.3), pure.

The grade is a pre-registered heuristic, never a statistical claim. Every output
carries ``LIMITATIONS`` verbatim so the caveats travel with the number.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from trading_platform.services.research.metrics import TradeRecord

CLUSTER_SPAN_SESSIONS = 5
GRADE_INSUFFICIENT = "insufficient"
GRADE_THIN = "thin"
GRADE_MEETS = "meets_predefined_study_conditions"

INSUFFICIENT_MIN_CLUSTERS = 5
INSUFFICIENT_MIN_HOLDING_RATIO = 5
THIN_MIN_CLUSTERS = 10
THIN_MIN_HOLDING_RATIO = 10
THIN_MAX_BEST_THREE_SHARE = 0.5

LIMITATIONS = (
    "The cluster span of 5 sessions and the grade thresholds are heuristics fixed before any result was seen.",
    "Event clusters group nearby entries; they do not establish independent observations.",
    "No grade certifies statistical reliability.",
    "The median holding period is measured in the same window it grades.",
    "Only the 'insufficient' grade excludes a candidate from ranking.",
)


def anchored_clusters(entry_sessions: list[date], session_index: dict[date, int]) -> int:
    """A cluster opens at an entry fill and absorbs entries within the next
    ``CLUSTER_SPAN_SESSIONS`` sessions (by session index, not calendar days)."""

    indices = sorted(session_index[s] for s in entry_sessions if s in session_index)
    clusters = 0
    anchor: int | None = None
    for index in indices:
        if anchor is None or index - anchor > CLUSTER_SPAN_SESSIONS:
            clusters += 1
            anchor = index
    return clusters


def _shares(closed: list[TradeRecord]) -> dict[str, float | None]:
    profits = [t.net_pnl for t in closed if t.net_pnl is not None and t.net_pnl > 0]
    gross_profit = sum(profits, Decimal(0))
    if gross_profit <= 0:
        return {"best_trade_share": None, "best_three_share": None, "best_month_share": None}
    ordered = sorted(profits, reverse=True)
    by_month: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for t in closed:
        if t.net_pnl is not None and t.net_pnl > 0 and t.exit_fill_session is not None:
            by_month[t.exit_fill_session.strftime("%Y-%m")] += t.net_pnl
    best_month = max(by_month.values(), default=Decimal(0))
    return {
        "best_trade_share": float(ordered[0] / gross_profit),
        "best_three_share": float(sum(ordered[:3], Decimal(0)) / gross_profit),
        "best_month_share": float(best_month / gross_profit),
    }


def grade_from(descriptors: dict[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    closed = descriptors["closed_trades"]
    clusters = descriptors["clusters"]
    ratio = descriptors["holding_ratio"]
    best_three = descriptors["concentration"]["best_three_share"]
    if closed == 0:
        reasons.append("no closed trades")
    if clusters < INSUFFICIENT_MIN_CLUSTERS:
        reasons.append(f"clusters {clusters} < {INSUFFICIENT_MIN_CLUSTERS}")
    if ratio is None or ratio < INSUFFICIENT_MIN_HOLDING_RATIO:
        reasons.append(f"holding ratio {ratio} < {INSUFFICIENT_MIN_HOLDING_RATIO}")
    if reasons:
        return GRADE_INSUFFICIENT, reasons
    if clusters < THIN_MIN_CLUSTERS:
        reasons.append(f"clusters {clusters} < {THIN_MIN_CLUSTERS}")
    if ratio < THIN_MIN_HOLDING_RATIO:
        reasons.append(f"holding ratio {ratio} < {THIN_MIN_HOLDING_RATIO}")
    if best_three is not None and best_three > THIN_MAX_BEST_THREE_SHARE:
        reasons.append(f"best three trades {best_three:.0%} of gross profit > 50%")
    if reasons:
        return GRADE_THIN, reasons
    return GRADE_MEETS, []


def compute_evidence(
    *,
    trades: list[TradeRecord],
    metrics: dict[str, Any],
    session_index: dict[date, int],
) -> dict[str, Any]:
    closed = [t for t in trades if t.status == "closed" and t.net_pnl is not None]
    measured = metrics["measured_sessions"]
    median_holding = metrics["holding_period"]["median_sessions"]
    ratio = (measured / median_holding) if median_holding else None
    descriptors: dict[str, Any] = {
        "closed_trades": len(closed),
        "open_at_end": metrics["open_at_end"]["count"],
        "measured_sessions": measured,
        "median_holding_sessions": median_holding,
        "mean_holding_sessions": metrics["holding_period"]["mean_sessions"],
        "holding_ratio": ratio,
        "exposure": metrics["exposure"],
        "concentration": _shares(closed),
        "clusters": anchored_clusters([t.entry_fill_session for t in trades], session_index),
        "cluster_span_sessions": CLUSTER_SPAN_SESSIONS,
        "total_costs": metrics["total_costs"],
    }
    grade, reasons = grade_from(descriptors)
    return {
        "descriptors": descriptors,
        "grade": grade,
        "grade_reasons": reasons,
        "thresholds": {
            "insufficient": {"min_clusters": INSUFFICIENT_MIN_CLUSTERS, "min_holding_ratio": INSUFFICIENT_MIN_HOLDING_RATIO},
            "thin": {"min_clusters": THIN_MIN_CLUSTERS, "min_holding_ratio": THIN_MIN_HOLDING_RATIO, "max_best_three_share": THIN_MAX_BEST_THREE_SHARE},
        },
        "limitations": list(LIMITATIONS),
    }
