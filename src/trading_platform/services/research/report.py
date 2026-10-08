"""Static report renderer (proposal Part N): ``report.html`` and ``report.md`` from a
revision's ``comparison.json`` and run curves, standard library only, inline SVG for
the equity and drawdown charts. Everything the results page shows is in the report,
with the same labels: the mode label, the cost model and quantity policy, the data
source and freeze, per-asset Return and Risk tables with the benchmark beside each
candidate, evidence grades with their limitations, the ranking with statuses and
reasons, the final-test block with its separate benchmark comparison and the
exposures, the study limitations and the reproducibility manifest.
"""

from __future__ import annotations

import html
from typing import Any

SVG_WIDTH = 640
SVG_HEIGHT = 180
MARGIN = 28


def _pct(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value * 100:.{digits}f}%"


def _num(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _esc(value: Any) -> str:
    return html.escape(str(value))


def svg_line_chart(points: list[dict[str, Any]], *, key: str, title: str, color: str = "#2563eb") -> str:
    """One inline SVG polyline with a labelled min/max axis. Pure, deterministic."""

    values = [float(p[key]) for p in points if p.get(key) is not None]
    if len(values) < 2:
        return f'<svg width="{SVG_WIDTH}" height="{SVG_HEIGHT}" role="img" aria-label="{_esc(title)}"><text x="{MARGIN}" y="{MARGIN}" font-size="12">{_esc(title)}: no series</text></svg>'
    low, high = min(values), max(values)
    span = (high - low) or 1.0
    inner_w, inner_h = SVG_WIDTH - 2 * MARGIN, SVG_HEIGHT - 2 * MARGIN
    coords = []
    for index, value in enumerate(values):
        x = MARGIN + inner_w * index / (len(values) - 1)
        y = MARGIN + inner_h * (1 - (value - low) / span)
        coords.append(f"{x:.1f},{y:.1f}")
    first, last = points[0]["session_date"], points[-1]["session_date"]
    return (
        f'<svg width="{SVG_WIDTH}" height="{SVG_HEIGHT}" viewBox="0 0 {SVG_WIDTH} {SVG_HEIGHT}" role="img" aria-label="{_esc(title)}">'
        f'<text x="{MARGIN}" y="16" font-size="12" font-weight="bold">{_esc(title)}</text>'
        f'<line x1="{MARGIN}" y1="{MARGIN}" x2="{MARGIN}" y2="{SVG_HEIGHT - MARGIN}" stroke="#999"/>'
        f'<line x1="{MARGIN}" y1="{SVG_HEIGHT - MARGIN}" x2="{SVG_WIDTH - MARGIN}" y2="{SVG_HEIGHT - MARGIN}" stroke="#999"/>'
        f'<polyline fill="none" stroke="{color}" stroke-width="1.5" points="{" ".join(coords)}"/>'
        f'<text x="{MARGIN + 2}" y="{MARGIN - 4}" font-size="10">{high:,.2f}</text>'
        f'<text x="{MARGIN + 2}" y="{SVG_HEIGHT - MARGIN - 4}" font-size="10">{low:,.2f}</text>'
        f'<text x="{MARGIN}" y="{SVG_HEIGHT - 8}" font-size="10">{_esc(first)}</text>'
        f'<text x="{SVG_WIDTH - MARGIN - 70}" y="{SVG_HEIGHT - 8}" font-size="10">{_esc(last)}</text>'
        "</svg>"
    )


def _metrics_rows(metrics: dict[str, Any] | None, benchmark: dict[str, Any] | None) -> list[tuple[str, str, str]]:
    def pick(source: dict[str, Any] | None, key: str, formatter) -> str:
        return "n/a" if source is None or source.get(key) is None else formatter(source[key])

    return [
        ("Net total return", pick(metrics, "net_total_return", _pct), pick(benchmark, "net_total_return", _pct)),
        ("CAGR", pick(metrics, "cagr", _pct), pick(benchmark, "cagr", _pct)),
        ("Max drawdown", pick(metrics, "max_drawdown", _pct), pick(benchmark, "max_drawdown", _pct)),
        ("Drawdown duration (sessions)", pick(metrics, "drawdown_duration", str), pick(benchmark, "drawdown_duration", str)),
        ("Sharpe (daily, annualised)", pick(metrics, "sharpe_daily_ann", _num), pick(benchmark, "sharpe_daily_ann", _num)),
        ("Sortino (daily, annualised)", pick(metrics, "sortino_daily_ann", _num), pick(benchmark, "sortino_daily_ann", _num)),
        ("Closed trades", pick(metrics, "closed_trades", str), pick(benchmark, "closed_trades", str)),
        ("Open at end", "n/a" if metrics is None else str(metrics["open_at_end"]["count"]), "n/a" if benchmark is None else str(benchmark["open_at_end"]["count"])),
        ("Win rate", pick(metrics, "win_rate", _pct), pick(benchmark, "win_rate", _pct)),
        ("Profit factor (report only)", pick(metrics, "profit_factor", _num), pick(benchmark, "profit_factor", _num)),
        ("Expectancy", pick(metrics, "expectancy", _num), pick(benchmark, "expectancy", _num)),
        ("Exposure", pick(metrics, "exposure", _pct), pick(benchmark, "exposure", _pct)),
        ("Turnover", pick(metrics, "turnover", _num), pick(benchmark, "turnover", _num)),
        ("Total costs", "n/a" if metrics is None else _num(metrics["total_costs"]["currency"]), "n/a" if benchmark is None else _num(benchmark["total_costs"]["currency"])),
        ("Rounding slack", pick(metrics, "rounding_slack", _num), pick(benchmark, "rounding_slack", _num)),
        ("Flags", "n/a" if metrics is None else ", ".join(metrics.get("flags", [])) or "none", "n/a" if benchmark is None else ", ".join(benchmark.get("flags", [])) or "none"),
    ]


def render_markdown(comparison: dict[str, Any], curves: dict[str, dict[str, Any]] | None = None) -> str:
    settings = comparison.get("settings", {})
    lines: list[str] = []
    study = comparison.get("study") or {}
    revision = comparison.get("revision") or {}
    lines.append(f"# Study report: {study.get('name', 'study')} (revision {revision.get('revision_no', '?')})")
    lines.append("")
    lines.append(f"Verdict: **{comparison.get('verdict_label', comparison.get('verdict'))}** ({comparison.get('verdict')}). {comparison.get('mode_label', '')}.")
    lines.append("")
    lines.append("## Settings")
    lines.append(f"- Objective: {settings.get('objective')}; constraint value: {settings.get('constraint_value')}")
    lines.append(f"- Capital: {settings.get('initial_capital')}; quantity policy: {settings.get('quantity_policy')}")
    costs = settings.get("costs") or {}
    lines.append(f"- Costs: slippage {costs.get('slippage_bps')} bps per side, commission {costs.get('commission_per_order')} per order (entered by the user)")
    data = comparison.get("data") or {}
    lines.append(f"- Data: provider {data.get('provider')}, adjusted {data.get('adjusted')}; freeze {data.get('data_freeze_id')}; input digest {data.get('input_digest')}; calendar start {data.get('calendar_start')}")
    for role, window in (comparison.get("windows") or {}).items():
        lines.append(f"- Window {role}: {window['start']} to {window['end']}")
    lines.append(f"- Code: {comparison.get('code_sha')}")
    lines.append("")
    lines.append("## Ranking")
    ranking = comparison.get("ranking") or {}
    lines.append(f"Key: {ranking.get('ranking_key')}. Profit factor affects the order: {ranking.get('profit_factor_affects_order')}.")
    lines.append("")
    lines.append("| Rank | Strategy version | Asset | Net return | Max drawdown | CAGR | Excess vs benchmark | Grade | Co-leading |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for row in ranking.get("ranking", []):
        lines.append(f"| {row['rank']} | {row['strategy_version_id']} | {row['asset']} | {_pct(row['net_total_return'])} | {_pct(row['max_drawdown'])} | {_pct(row['cagr'])} | {_pct(row['excess_return_vs_benchmark'])} | {row['grade']} | {row['co_leading']} |")
    lines.append("")
    lines.append("## Candidates (per asset; benchmark beside each candidate)")
    for candidate in comparison.get("candidates", []):
        lines.append("")
        lines.append(f"### {candidate.get('strategy_name')} v{candidate.get('version_no')} on {candidate['asset']} — status {candidate.get('status')}")
        if candidate.get("status_reasons"):
            lines.append(f"Reasons: {'; '.join(candidate['status_reasons'])}")
        for role, window in candidate["windows"].items():
            lines.append("")
            lines.append(f"#### {role} window")
            if window is None:
                lines.append("No result.")
                continue
            lines.append("| Metric | Candidate | Buy-and-hold benchmark |")
            lines.append("| --- | --- | --- |")
            for label, value, bench in _metrics_rows(window["metrics"], candidate["benchmark"].get(role)):
                lines.append(f"| {label} | {value} | {bench} |")
            lines.append(f"Excess return vs benchmark: {_pct(window.get('excess_return_vs_benchmark'))} (display only, never a gate).")
            evidence = window["evidence"]
            lines.append(f"Evidence grade: {evidence['grade']}" + (f" ({'; '.join(evidence['grade_reasons'])})" if evidence["grade_reasons"] else ""))
            d = evidence["descriptors"]
            lines.append(f"Descriptors: closed trades {d['closed_trades']}, open at end {d['open_at_end']}, measured sessions {d['measured_sessions']}, median holding {d['median_holding_sessions']}, clusters {d['clusters']} (span {d['cluster_span_sessions']}), best three trades share {_pct(d['concentration']['best_three_share'])}")
    lines.append("")
    lines.append("## Evidence limitations")
    for text in comparison.get("evidence_limitations", []):
        lines.append(f"- {text}")
    lines.append("")
    lines.append("## Final test")
    final = comparison.get("final_test") or {}
    if final.get("state") != "evaluated":
        lines.append(f"State: {final.get('state')}. {final.get('note', '')}")
    else:
        lines.append(f"Outcome: **{final['outcome']}**" + (" (thin evidence)" if final.get("thin_evidence") else ""))
        for reason in final.get("outcome_reasons", []):
            lines.append(f"- {reason}")
        acceptance = final.get("acceptance") or {}
        lines.append(f"Frozen acceptance: constraint value {acceptance.get('constraint_value')}, objective minimum {acceptance.get('objective_minimum')}")
        bench = final.get("benchmark_comparison")
        if bench:
            lines.append(f"Benchmark comparison (separate, not part of the outcome): return {bench['return_vs_benchmark']}, drawdown {bench['drawdown_vs_benchmark']}, excess {_pct(bench['excess_return_vs_benchmark'])}")
        lines.append(final.get("wording", ""))
        repro = final.get("reproducibility")
        if repro:
            lines.append(f"Reproducibility: {repro['status']} (byte identical: {repro['byte_identical']})")
        exposures = final.get("exposures") or {}
        lines.append(f"Recorded exposures of the test window: {exposures.get('count', 0)} ({exposures.get('by_state')})")
        lines.append(final.get("outside_visibility_limitation", ""))
    lines.append("")
    lines.append("## Limitations")
    for text in comparison.get("limitations", []):
        lines.append(f"- {text}")
    lines.append("")
    lines.append(f"Benchmark note: {comparison.get('benchmark_note', '')}")
    lines.append("")
    lines.append(f"Generated {comparison.get('generated_at')} from comparison.json schema {comparison.get('schema_version')}.")
    return "\n".join(lines) + "\n"


def render_html(comparison: dict[str, Any], curves: dict[str, dict[str, Any]] | None = None) -> str:
    """Standalone HTML: the Markdown structure as elements plus inline SVG charts per run."""

    curves = curves or {}
    study = comparison.get("study") or {}
    revision = comparison.get("revision") or {}
    parts: list[str] = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>Study report: {_esc(study.get('name', 'study'))}</title>",
        "<style>body{font-family:system-ui,sans-serif;margin:24px;max-width:960px;color:#111}table{border-collapse:collapse;margin:8px 0}td,th{border:1px solid #ccc;padding:4px 8px;font-size:13px;text-align:left}h1,h2,h3{margin-top:24px}.note{color:#444;font-size:13px}.label{display:inline-block;background:#eee;padding:2px 6px;border-radius:4px;font-size:12px}</style></head><body>",
        f"<h1>Study report: {_esc(study.get('name', 'study'))} <span class='label'>revision {_esc(revision.get('revision_no', '?'))}</span></h1>",
        f"<p><strong>Verdict: {_esc(comparison.get('verdict_label', comparison.get('verdict')))}</strong> ({_esc(comparison.get('verdict'))}). <span class='label'>{_esc(comparison.get('mode_label', ''))}</span></p>",
    ]
    settings = comparison.get("settings", {})
    costs = settings.get("costs") or {}
    data = comparison.get("data") or {}
    parts.append("<h2>Settings</h2><ul>")
    parts.append(f"<li>Objective: {_esc(settings.get('objective'))}; constraint value: {_esc(settings.get('constraint_value'))}</li>")
    parts.append(f"<li>Capital: {_esc(settings.get('initial_capital'))}; quantity policy: {_esc(settings.get('quantity_policy'))}</li>")
    parts.append(f"<li>Costs (entered by the user): slippage {_esc(costs.get('slippage_bps'))} bps per side, commission {_esc(costs.get('commission_per_order'))} per order</li>")
    parts.append(f"<li>Data: provider {_esc(data.get('provider'))}, adjusted {_esc(data.get('adjusted'))}; freeze {_esc(data.get('data_freeze_id'))}; input digest <code>{_esc(data.get('input_digest'))}</code>; calendar start {_esc(data.get('calendar_start'))}</li>")
    for role, window in (comparison.get("windows") or {}).items():
        parts.append(f"<li>Window {_esc(role)}: {_esc(window['start'])} to {_esc(window['end'])}</li>")
    parts.append(f"<li>Code: <code>{_esc(comparison.get('code_sha'))}</code></li></ul>")
    ranking = comparison.get("ranking") or {}
    parts.append(f"<h2>Ranking</h2><p class='note'>Key: {_esc(ranking.get('ranking_key'))}. Profit factor affects the order: {_esc(ranking.get('profit_factor_affects_order'))}.</p>")
    parts.append("<table><tr><th>Rank</th><th>Strategy version</th><th>Asset</th><th>Net return</th><th>Max drawdown</th><th>CAGR</th><th>Excess vs benchmark</th><th>Grade</th><th>Co-leading</th></tr>")
    for row in ranking.get("ranking", []):
        parts.append(f"<tr><td>{row['rank']}</td><td>{_esc(row['strategy_version_id'])}</td><td>{_esc(row['asset'])}</td><td>{_pct(row['net_total_return'])}</td><td>{_pct(row['max_drawdown'])}</td><td>{_pct(row['cagr'])}</td><td>{_pct(row['excess_return_vs_benchmark'])}</td><td>{_esc(row['grade'])}</td><td>{row['co_leading']}</td></tr>")
    parts.append("</table>")
    parts.append("<h2>Candidates (per asset; benchmark beside each candidate)</h2>")
    for candidate in comparison.get("candidates", []):
        parts.append(f"<h3>{_esc(candidate.get('strategy_name'))} v{_esc(candidate.get('version_no'))} on {_esc(candidate['asset'])} <span class='label'>{_esc(candidate.get('status'))}</span></h3>")
        if candidate.get("status_reasons"):
            parts.append(f"<p class='note'>Reasons: {_esc('; '.join(candidate['status_reasons']))}</p>")
        for role, window in candidate["windows"].items():
            parts.append(f"<h4>{_esc(role)} window</h4>")
            if window is None:
                parts.append("<p>No result.</p>")
                continue
            parts.append("<table><tr><th>Metric</th><th>Candidate</th><th>Buy-and-hold benchmark</th></tr>")
            for label, value, bench in _metrics_rows(window["metrics"], candidate["benchmark"].get(role)):
                parts.append(f"<tr><td>{_esc(label)}</td><td>{_esc(value)}</td><td>{_esc(bench)}</td></tr>")
            parts.append("</table>")
            parts.append(f"<p class='note'>Excess return vs benchmark: {_pct(window.get('excess_return_vs_benchmark'))} (display only, never a gate).</p>")
            evidence = window["evidence"]
            d = evidence["descriptors"]
            parts.append(f"<p>Evidence grade: <strong>{_esc(evidence['grade'])}</strong>" + (f" ({_esc('; '.join(evidence['grade_reasons']))})" if evidence["grade_reasons"] else "") + "</p>")
            parts.append(f"<p class='note'>Closed trades {d['closed_trades']}, open at end {d['open_at_end']}, measured sessions {d['measured_sessions']}, median holding {_esc(d['median_holding_sessions'])}, clusters {d['clusters']} (span {d['cluster_span_sessions']}), best three trades share {_pct(d['concentration']['best_three_share'])}</p>")
            run_id = window.get("run_id")
            curve = curves.get(run_id) if run_id else None
            if curve:
                parts.append(svg_line_chart(curve["points"], key="total_equity", title=f"Equity, {candidate['asset']} {role}"))
                parts.append(svg_line_chart(curve["points"], key="drawdown", title=f"Drawdown, {candidate['asset']} {role}", color="#b91c1c"))
    parts.append("<h2>Evidence limitations</h2><ul>" + "".join(f"<li>{_esc(t)}</li>" for t in comparison.get("evidence_limitations", [])) + "</ul>")
    final = comparison.get("final_test") or {}
    parts.append("<h2>Final test</h2>")
    if final.get("state") != "evaluated":
        parts.append(f"<p>State: {_esc(final.get('state'))}. {_esc(final.get('note', ''))}</p>")
    else:
        parts.append(f"<p>Outcome: <strong>{_esc(final['outcome'])}</strong>{' (thin evidence)' if final.get('thin_evidence') else ''}</p>")
        if final.get("outcome_reasons"):
            parts.append("<ul>" + "".join(f"<li>{_esc(r)}</li>" for r in final["outcome_reasons"]) + "</ul>")
        acceptance = final.get("acceptance") or {}
        parts.append(f"<p>Frozen acceptance: constraint value {_esc(acceptance.get('constraint_value'))}, objective minimum {_esc(acceptance.get('objective_minimum'))}</p>")
        bench = final.get("benchmark_comparison")
        if bench:
            parts.append(f"<p>Benchmark comparison (separate, not part of the outcome): return {_esc(bench['return_vs_benchmark'])}, drawdown {_esc(bench['drawdown_vs_benchmark'])}, excess {_pct(bench['excess_return_vs_benchmark'])}</p>")
        parts.append(f"<p class='note'>{_esc(final.get('wording', ''))}</p>")
        repro = final.get("reproducibility")
        if repro:
            parts.append(f"<p>Reproducibility: {_esc(repro['status'])} (byte identical: {repro['byte_identical']})</p>")
        exposures = final.get("exposures") or {}
        parts.append(f"<p>Recorded exposures of the test window: {exposures.get('count', 0)} {_esc(exposures.get('by_state'))}</p>")
        parts.append(f"<p class='note'>{_esc(final.get('outside_visibility_limitation', ''))}</p>")
        for run_key in ("candidate", "benchmark"):
            block = final.get(run_key) or {}
            curve = curves.get(block.get("run_id")) if block.get("run_id") else None
            if curve:
                parts.append(svg_line_chart(curve["points"], key="total_equity", title=f"Final test equity, {run_key}"))
    parts.append("<h2>Limitations</h2><ul>" + "".join(f"<li>{_esc(t)}</li>" for t in comparison.get("limitations", [])) + "</ul>")
    parts.append(f"<p class='note'>Benchmark note: {_esc(comparison.get('benchmark_note', ''))}</p>")
    parts.append(f"<p class='note'>Generated {_esc(comparison.get('generated_at'))} from comparison.json schema {_esc(comparison.get('schema_version'))}.</p>")
    parts.append("</body></html>")
    return "".join(parts)
