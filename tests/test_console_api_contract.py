"""Console -> API route contract.

Every ``/api/v1/...`` endpoint the console issues must resolve to a route the
FastAPI app actually registers, with the right method. A wrong frontend path,
a router prefix drift, or a router that ``create_app`` forgets to include all
surface here as a failing (method, path) pair instead of a runtime HTTP 404 on
a console page.

This cannot detect an API *process* that predates the code (a stale server
answers 404 for routes the source defines) -- that is an environment check,
not a source invariant.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_CONSOLE_SRC = _ROOT / "console/src"

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
# `//` not preceded by `:` (URLs) or a quote/backtick (string contents).
_LINE_COMMENT = re.compile(r"(?<![:\"'`])//[^\n]*")
_API_LITERAL = re.compile(r"([\"'`])(/api/v1[^\"'`]*)\1")
# Mutations are only issued through the api.ts helpers (SC6 single fetch site);
# the endpoint literal may sit on the line after the call's opening paren.
_MUTATION_CALL = re.compile(r"\b(postJson|putJson)\s*<[^>]*>\s*\(\s*([\"'`])(/api/v1[^\"'`]*)\2")
_MUTATION_METHOD = {"postJson": "POST", "putJson": "PUT"}
_WHOLE_SEGMENT_PARAM = re.compile(r"(?<=/)\$\{[^}]*\}(?=/|$)")
_TRAILING_INTERPOLATION = re.compile(r"\$\{[^}]*\}")
_PARAM_TOKEN = "__param__"


def _normalize(raw_path: str) -> str:
    """Strip the query string; a ``${...}`` filling a whole segment becomes a
    path-param token, one glued onto a segment (``logs${query}``) is a query
    suffix and is dropped."""

    path = raw_path.split("?", 1)[0]
    path = _WHOLE_SEGMENT_PARAM.sub(_PARAM_TOKEN, path)
    return _TRAILING_INTERPOLATION.sub("", path)


def _console_source_files() -> list[Path]:
    return sorted(
        path
        for pattern in ("*.ts", "*.tsx")
        for path in _CONSOLE_SRC.rglob(pattern)
        if ".test." not in path.name
    )


def _console_endpoints() -> set[tuple[str, str]]:
    endpoints: set[tuple[str, str]] = set()
    for path in _console_source_files():
        source = _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", path.read_text()))
        mutation_offsets: set[int] = set()
        for match in _MUTATION_CALL.finditer(source):
            mutation_offsets.add(match.start(2))
            endpoints.add((_MUTATION_METHOD[match.group(1)], _normalize(match.group(3))))
        for match in _API_LITERAL.finditer(source):
            if match.start(1) not in mutation_offsets:
                # fetchApi/useApiQuery only ever issue GET.
                endpoints.add(("GET", _normalize(match.group(2))))
    return endpoints


def _registered_routes() -> list[tuple[str, re.Pattern[str]]]:
    from trading_platform.api.app import create_app

    routes: list[tuple[str, re.Pattern[str]]] = []
    for route in create_app().routes:
        candidates = (
            route.effective_candidates() if hasattr(route, "effective_candidates") else [route]
        )
        for candidate in candidates:
            template = str(getattr(candidate, "path", ""))
            if not template.startswith("/api/"):
                continue
            pattern = re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", template) + "$")
            for method in getattr(candidate, "methods", set()):
                routes.append((method, pattern))
    return routes


def _unmatched(
    endpoints: set[tuple[str, str]], routes: list[tuple[str, re.Pattern[str]]]
) -> list[tuple[str, str]]:
    return sorted(
        (method, path)
        for method, path in endpoints
        if not any(
            route_method == method and pattern.match(path) for route_method, pattern in routes
        )
    )


def test_every_console_endpoint_resolves_to_a_registered_route() -> None:
    endpoints = _console_endpoints()
    routes = _registered_routes()

    assert routes, "create_app() registered no /api/ routes -- route walk is broken"
    assert _unmatched(endpoints, routes) == []


def test_console_endpoint_extraction_is_not_vacuous() -> None:
    endpoints = _console_endpoints()

    # The /controls page reads and mutations (the Phase 20 surface) plus the
    # multi-line postJson calls must all be picked up by the scan.
    assert {
        ("GET", f"/api/v1/controls/strategies/{_PARAM_TOKEN}"),
        ("PUT", f"/api/v1/controls/strategies/{_PARAM_TOKEN}"),
        ("PUT", "/api/v1/controls/kill-switch"),
        ("GET", "/api/v1/system/kill-switch"),
        ("POST", "/api/v1/jobs"),
        ("POST", f"/api/v1/jobs/{_PARAM_TOKEN}/cancel"),
        ("POST", f"/api/v1/jobs/{_PARAM_TOKEN}/retry"),
        ("GET", f"/api/v1/jobs/{_PARAM_TOKEN}/logs"),
    } <= endpoints
    assert len(endpoints) >= 20
    # Comments are stripped before scanning, and mutation literals are not
    # double-counted as GETs.
    assert ("GET", "/api/v1/controls/kill-switch") not in endpoints
    assert ("GET", f"/api/v1/jobs/{_PARAM_TOKEN}/retry") not in endpoints


def test_contract_rejects_wrong_path_method_and_prefix() -> None:
    routes = _registered_routes()

    assert _unmatched(
        {
            ("GET", f"/api/v1/controls/strategy/{_PARAM_TOKEN}"),
            ("POST", f"/api/v1/controls/strategies/{_PARAM_TOKEN}"),
            ("GET", f"/api/v1/strategies/controls/{_PARAM_TOKEN}"),
            ("GET", f"/api/controls/strategies/{_PARAM_TOKEN}"),
        },
        routes,
    ) == sorted(
        {
            ("GET", f"/api/v1/controls/strategy/{_PARAM_TOKEN}"),
            ("POST", f"/api/v1/controls/strategies/{_PARAM_TOKEN}"),
            ("GET", f"/api/v1/strategies/controls/{_PARAM_TOKEN}"),
            ("GET", f"/api/controls/strategies/{_PARAM_TOKEN}"),
        }
    )


def test_normalize_handles_params_queries_and_suffix_interpolation() -> None:
    assert _normalize("/api/v1/jobs/${encodeURIComponent(jobId)}/logs${query}") == (
        f"/api/v1/jobs/{_PARAM_TOKEN}/logs"
    )
    assert _normalize("/api/v1/runs?${params.toString()}") == "/api/v1/runs"
    assert _normalize("/api/v1/analytics/strategies/${strategyId}?backtest_run_id=${runId}") == (
        f"/api/v1/analytics/strategies/{_PARAM_TOKEN}"
    )
