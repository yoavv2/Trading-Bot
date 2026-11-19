"""Study proposal assistant: a validated contract on the shared assistant infrastructure.

A stub provider stands in for the SDK. The service persists nothing but the usage-ledger
rows (no ``ai_drafts``); strategy ids are resolved server-side against approved versions
only, tickers are flagged against the asset catalog, and output that breaks the contract
(types, ISO dates, split sums, window ordering) is retried once and then refused with
``ai_output_invalid``.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from tests.support.migrated_db import migrated_database
from tests.test_research_assistant import StubProvider, ok
from tests.test_research_strategies_api import EDITED_YAML, VALID_YAML

import trading_platform.api.app as api_app
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models.research import (
    AiDraft,
    AssetCatalogEntry,
    ProviderRequestLedgerEntry,
)
from trading_platform.db.session import session_scope
from trading_platform.services.research.assistant import AssistantError, ProviderFailure
from trading_platform.services.research.strategies import ResearchStrategyService
from trading_platform.services.research.study_assistant import (
    STUDY_OUTPUT_SCHEMA,
    STUDY_PROMPT_VERSION,
    STUDY_SYSTEM_PROMPT,
    StudyAssistantService,
    StudyProposalOutput,
    parse_study_output,
)

URL = "/api/v1/research/assistant/study-proposals"


def _configure(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    env = {
        "TRADING_PLATFORM_RESEARCH__MODE": "true",
        "TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED": "true",
        "TRADING_PLATFORM_RESEARCH__AI__ENABLED": "true",
        "TRADING_PLATFORM_RESEARCH__AI__API_KEY": "sk-ant-test-not-a-real-key",
        "TRADING_PLATFORM_RESEARCH__AI__MAX_REQUESTS_PER_DAY": "6",
        "TRADING_PLATFORM_RESEARCH__AI__MAX_OUTPUT_TOKENS": "2000",
        "TRADING_PLATFORM_RESEARCH__AI__TIMEOUT_SECONDS": "5",
        **overrides,
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    clear_settings_cache()


def proposal(**overrides: Any) -> dict[str, Any]:
    """A complete provider output with nothing stated (every optional field empty)."""

    base: dict[str, Any] = {
        "summary": "Test two trend strategies on a few assets.",
        "missing": [],
        "unsupported_requests": [],
        "name": "",
        "strategies": [],
        "assets": [],
        "range_start": "",
        "range_end": "",
        "split": {"development_pct": 0, "validation_pct": 0, "final_test_pct": 0},
        "windows": {
            role: {"start": "", "end": ""} for role in ("development", "validation", "final_test")
        },
        "initial_capital": "",
        "quantity_policy": "",
        "slippage_bps": "",
        "commission_per_order": "",
        "objective": "",
        "constraint_value": "",
        "note": "",
        "kind": "",
    }
    base.update(overrides)
    return base


def out(**overrides: Any) -> str:
    return json.dumps(proposal(**overrides))


def _windows(*dates: str) -> dict[str, dict[str, str]]:
    roles = ("development", "validation", "final_test")
    return {role: {"start": dates[2 * i], "end": dates[2 * i + 1]} for i, role in enumerate(roles)}


GOOD_WINDOWS = _windows(
    "2015-01-02", "2019-12-31", "2020-01-02", "2021-12-31", "2022-01-03", "2023-12-29"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def study_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    with migrated_database(monkeypatch, "research_study_assistant") as name:
        _configure(monkeypatch)
        settings = load_settings()
        strategies = ResearchStrategyService(settings)
        v1 = strategies.approve_draft(
            uuid.UUID(strategies.create_draft(title="a", yaml_text=VALID_YAML)["draft_id"])
        )
        edit = strategies.draft_from_version(uuid.UUID(v1["version_id"]), mode="edit")
        strategies.update_draft(uuid.UUID(edit["draft_id"]), yaml_text=EDITED_YAML)
        v2 = strategies.approve_draft(uuid.UUID(edit["draft_id"]))
        other = strategies.approve_draft(
            uuid.UUID(
                strategies.create_draft(
                    title="b",
                    yaml_text=VALID_YAML.replace("Trend following 50/200", "Channel breakout"),
                )["draft_id"]
            )
        )
        with session_scope(settings) as session:
            session.add(
                AssetCatalogEntry(
                    id=uuid.uuid4(),
                    provider="tiingo",
                    ticker="AAPL",
                    exchange="NASDAQ",
                    asset_type="Stock",
                    currency="USD",
                    catalog_start=date(1990, 1, 2),
                    catalog_end=date(2026, 10, 6),
                    name="Apple Inc",
                    name_source="nasdaq_trader",
                    synced_at=datetime.now(UTC),
                )
            )
        yield {"name": name, "v1": v1, "v2": v2, "other": other}


def service_with(script: list[Any]) -> tuple[StudyAssistantService, StubProvider]:
    provider = StubProvider(script)
    return StudyAssistantService(load_settings(), provider=provider, process_id="test:1"), provider


def _rows(model: type) -> list[Any]:
    with session_scope(load_settings()) as session:
        return list(session.execute(sa.select(model)).scalars())


@pytest.fixture()
def study_api(
    study_db: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, StubProvider, dict[str, Any]]]:
    settings = load_settings()
    monkeypatch.setattr(api_app, "enforce_startup_config", lambda **kwargs: settings)
    provider = StubProvider([])
    app = api_app.create_app()
    app.state.study_assistant_service = StudyAssistantService(
        settings, provider=provider, process_id="api:1"
    )
    with TestClient(app) as client:
        yield client, provider, study_db


# ---------------------------------------------------------------------------
# Pure pieces
# ---------------------------------------------------------------------------


def test_output_schema_meets_provider_constraints() -> None:
    """Same structured-output rules as the strategy contract: no recursion, no bounds,
    additionalProperties false everywhere, every object lists all its properties as
    required, and no union types at all."""

    forbidden = {"minimum", "maximum", "multipleOf", "minLength", "maxLength", "pattern", "$ref"}
    unions = 0

    def walk(node: Any) -> None:
        nonlocal unions
        if isinstance(node, dict):
            assert not (set(node) & forbidden), node
            if "anyOf" in node or isinstance(node.get("type"), list):
                unions += 1
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
                assert set(node.get("required", [])) == set(node["properties"]), list(
                    node["properties"]
                )
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(STUDY_OUTPUT_SCHEMA)
    assert unions == 0
    props = STUDY_OUTPUT_SCHEMA["properties"]
    assert props["quantity_policy"]["enum"] == ["", "fractional", "whole_shares"]
    assert props["objective"]["enum"] == ["", "return_first", "risk_first"]
    assert props["kind"]["enum"] == ["", "substantive", "smoke"]
    assert set(props) == set(proposal())
    assert STUDY_PROMPT_VERSION == "study-v1"
    assert "never invent" in STUDY_SYSTEM_PROMPT.lower() and "60/20/20" in STUDY_SYSTEM_PROMPT


def test_contract_accepts_the_complete_empty_proposal_and_normalises_assets() -> None:
    parsed, findings = parse_study_output(
        out(
            assets=[" aapl", "AAPL", "brk.b", "msft"],
            windows=GOOD_WINDOWS,
            range_start="2015-01-02",
            range_end="2023-12-29",
            initial_capital="100000",
            slippage_bps="0",
            commission_per_order="1.5",
            constraint_value="0.25",
            objective="return_first",
            quantity_policy="fractional",
            kind="smoke",
        )
    )
    assert findings == [] and isinstance(parsed, StudyProposalOutput)
    assert parsed.assets == ["AAPL", "BRK-B", "MSFT"]
    empty, findings = parse_study_output(out())
    assert empty is not None and findings == []


@pytest.mark.parametrize(
    ("overrides", "needle"),
    [
        ({"range_start": "2015/01/02"}, "range_start"),
        ({"range_start": "2015-02-30"}, "calendar date"),
        ({"range_start": "2020-01-01", "range_end": "2019-01-01"}, "after range_end"),
        ({"initial_capital": "0"}, "greater than 0"),
        ({"initial_capital": "1e5"}, "decimal"),
        ({"slippage_bps": "-1"}, "0 or greater"),
        ({"commission_per_order": "abc"}, "decimal"),
        ({"constraint_value": "NaN"}, "decimal"),
        (
            {"split": {"development_pct": 60, "validation_pct": 30, "final_test_pct": 20}},
            "sum to 100",
        ),
        (
            {"split": {"development_pct": 120, "validation_pct": -10, "final_test_pct": -10}},
            "between 0 and 100",
        ),
        ({"assets": ["AAPL", " "]}, "nonblank"),
        ({"quantity_policy": "lots"}, "quantity_policy"),
        ({"kind": "other"}, "kind"),
        ({"strategies": [{"reference": "x", "version_id": 5}]}, "version_id"),
        ({"unexpected": 1}, "unexpected"),
    ],
)
def test_contract_findings(overrides: dict[str, Any], needle: str) -> None:
    parsed, findings = parse_study_output(out(**overrides))
    assert parsed is None and any(needle in f for f in findings), findings


def test_contract_window_rules() -> None:
    def findings_for(**overrides: Any) -> list[str]:
        parsed, findings = parse_study_output(out(**overrides))
        assert parsed is None
        return findings

    # a window violation: validation starts on the day development ends (strict ordering)
    overlap = _windows(
        "2015-01-02", "2019-12-31", "2019-12-31", "2021-12-31", "2022-01-03", "2023-12-29"
    )
    assert any("must end before" in f for f in findings_for(windows=overlap))
    unordered = _windows(
        "2020-01-02", "2021-12-31", "2015-01-02", "2019-12-31", "2022-01-03", "2023-12-29"
    )
    assert any("must end before" in f for f in findings_for(windows=unordered))
    backwards = _windows(
        "2019-12-31", "2015-01-02", "2020-01-02", "2021-12-31", "2022-01-03", "2023-12-29"
    )
    assert any("development: start is after end" in f for f in findings_for(windows=backwards))
    partial = _windows("2015-01-02", "2019-12-31", "", "", "", "")
    assert any("all six window dates" in f for f in findings_for(windows=partial))
    outside = findings_for(windows=GOOD_WINDOWS, range_start="2016-01-01", range_end="2023-12-29")
    assert any("before range_start" in f for f in outside)
    outside_end = findings_for(
        windows=GOOD_WINDOWS, range_start="2015-01-02", range_end="2023-06-01"
    )
    assert any("after range_end" in f for f in outside_end)
    ok_parsed, ok_findings = parse_study_output(
        out(windows=GOOD_WINDOWS, range_start="2015-01-02", range_end="2023-12-29")
    )
    assert ok_parsed is not None and ok_findings == []

    for garbage in ("", "not json", "[1]"):
        assert parse_study_output(garbage)[0] is None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


def test_happy_path_resolves_strategies_flags_assets_and_stores_only_ledger_rows(
    study_db: dict[str, Any],
) -> None:
    v1, v2, other = study_db["v1"], study_db["v2"], study_db["other"]
    assert v1["strategy_id"] == v2["strategy_id"] != other["strategy_id"]
    service, provider = service_with(
        [
            ok(
                out(
                    summary="Trend 50/200 by id, the channel one by name, plus an unknown one.",
                    missing=[{"field": "slippage_bps", "detail": "costs were not stated"}],
                    unsupported_requests=[{"code": "short_selling", "detail": "shorting"}],
                    name="Trend vs channel",
                    strategies=[
                        {"reference": "my 50/200 trend", "version_id": v1["version_id"]},
                        {"reference": "  Channel   BREAKOUT ", "version_id": ""},
                        {"reference": "mean reversion", "version_id": str(uuid.uuid4())},
                        {"reference": "again the trend", "version_id": v1["version_id"]},
                    ],
                    assets=["aapl", "zzzz"],
                    range_start="2015-01-02",
                    range_end="2023-12-29",
                    initial_capital="100000",
                    objective="risk_first",
                    constraint_value="0.05",
                    kind="substantive",
                )
            )
        ]
    )
    result = service.propose_study(user_text="compare my trend and channel strategies")

    assert result["status"] == "ok" and result["prompt_version"] == "study-v1"
    assert result["attempt_no"] == 1 and result["summary"].startswith("Trend 50/200")
    assert result["missing"] == [{"field": "slippage_bps", "detail": "costs were not stated"}]
    assert result["unsupported_requests"] == [{"code": "short_selling", "detail": "shorting"}]
    settings = result["settings"]
    assert settings["name"] == "Trend vs channel"
    resolved = settings["strategies"]["resolved"]
    assert [(r["version_id"], r["matched_by"]) for r in resolved] == [
        (v1["version_id"], "version_id"),
        (other["version_id"], "name"),
    ], "the repeated version collapses; the family name resolves to its latest version"
    assert resolved[0]["strategy_id"] == v1["strategy_id"] and resolved[0]["version_no"] == 1
    assert resolved[1] == {
        "version_id": other["version_id"],
        "strategy_id": other["strategy_id"],
        "name": "Channel breakout",
        "version_no": 1,
        "reference": "  Channel   BREAKOUT ",
        "matched_by": "name",
    }
    assert settings["strategies"]["unresolved"] == [
        {"reference": "mean reversion", "reason": "no approved version matches"}
    ]
    assert settings["assets"] == [
        {"ticker": "AAPL", "in_catalog": True, "name": "Apple Inc"},
        {"ticker": "ZZZZ", "in_catalog": False, "name": None},
    ]
    assert settings["split"] is None and settings["windows"] is None
    assert (settings["range_start"], settings["range_end"]) == ("2015-01-02", "2023-12-29")
    assert (settings["initial_capital"], settings["objective"], settings["constraint_value"]) == (
        "100000",
        "risk_first",
        "0.05",
    )
    assert (settings["slippage_bps"], settings["commission_per_order"]) == ("", "")
    assert settings["quantity_policy"] == "" and settings["kind"] == "substantive"
    provenance = result["provenance"]
    assert provenance["provider"] == "anthropic" and provenance["model"] == "claude-haiku-5-5"
    assert provenance["prompt_version"] == "study-v1" and provenance["request_id"].startswith(
        "req_"
    )
    assert provenance["input_tokens"] == 1200 and provenance["output_tokens"] == 300
    assert provenance["cache_read_input_tokens"] == 1000 and provenance["deadline_seconds"] > 0
    assert provenance["started_at"] and provenance["completed_at"]

    # one provider call, with the study prompt and schema; nothing stored but the ledger
    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call["system_text"] == STUDY_SYSTEM_PROMPT and call["schema"] == STUDY_OUTPUT_SCHEMA
    assert call["max_output_tokens"] == 2000
    message = call["messages"][0]["content"]
    assert message.startswith(f"Today (UTC): {datetime.now(UTC).date().isoformat()}")
    assert "Maximum assets per study: 10" in message
    assert message.rstrip().endswith("Study request: compare my trend and channel strategies")
    assert f"{v2['version_id']} | Trend following 60/200 | v2 | latest | approved " in message
    assert f"{v1['version_id']} | Trend following 50/200 | v1 | older | approved " in message
    assert f"{other['version_id']} | Channel breakout | v1 | latest | approved " in message
    assert len(_rows(AiDraft)) == 0
    ledger = _rows(ProviderRequestLedgerEntry)
    assert [(r.purpose, r.outcome, r.status_code) for r in ledger] == [("assistant", "ok", 200)]
    assert service.status()["usage"]["requests_today"] == 1


def test_name_resolves_to_the_family_latest_and_split_and_windows_are_reported(
    study_db: dict[str, Any],
) -> None:
    v2 = study_db["v2"]
    service, _provider = service_with(
        [
            ok(
                out(
                    strategies=[
                        {"reference": "trend following 60/200", "version_id": "not-a-uuid"},
                        {"reference": "Trend following 50/200", "version_id": ""},
                    ],
                    split={"development_pct": 50, "validation_pct": 25, "final_test_pct": 25},
                )
            ),
            ok(out(windows=GOOD_WINDOWS, range_start="2015-01-02", range_end="2023-12-29")),
        ]
    )
    first = service.propose_study(user_text="by name")
    assert first["settings"]["split"] == {
        "development_pct": 50,
        "validation_pct": 25,
        "final_test_pct": 25,
    }
    assert first["settings"]["windows"] is None
    assert [r["version_id"] for r in first["settings"]["strategies"]["resolved"]] == [
        v2["version_id"]
    ]
    assert first["settings"]["strategies"]["resolved"][0]["matched_by"] == "name"
    # an older version's former name does not match; the family is known by its latest name
    assert first["settings"]["strategies"]["unresolved"] == [
        {"reference": "Trend following 50/200", "reason": "no approved version matches"}
    ]
    second = service.propose_study(user_text="windows")
    assert second["settings"]["split"] is None
    assert second["settings"]["windows"] == GOOD_WINDOWS


def test_invalid_output_retries_once_with_findings_then_fails_with_two_ledger_rows(
    study_db: dict[str, Any],
) -> None:
    bad_windows = _windows(
        "2015-01-02", "2019-12-31", "2019-12-31", "2021-12-31", "2022-01-03", "2023-12-29"
    )
    service, provider = service_with([ok(out(windows=bad_windows)), ok("not json")])
    with pytest.raises(AssistantError) as excinfo:
        service.propose_study(user_text="windows that overlap")
    assert excinfo.value.code == "ai_output_invalid"
    assert len(excinfo.value.detail["findings"]) == 1
    assert excinfo.value.detail["findings"][0].startswith("the output is not valid JSON")
    assert len(provider.calls) == 2
    retry = provider.calls[1]["messages"]
    assert [m["role"] for m in retry] == ["user", "assistant", "user"]
    assert "windows: development must end before validation starts" in retry[2]["content"]
    assert len(_rows(ProviderRequestLedgerEntry)) == 2 and len(_rows(AiDraft)) == 0
    assert service.status()["usage"]["requests_today"] == 2


def test_valid_on_retry_is_accepted_and_reports_attempt_two(study_db: dict[str, Any]) -> None:
    service, provider = service_with(
        [ok(out(initial_capital="-5")), ok(out(initial_capital="5000"))]
    )
    result = service.propose_study(user_text="small account")
    assert result["attempt_no"] == 2 and result["settings"]["initial_capital"] == "5000"
    assert "initial_capital: must be greater than 0" in provider.calls[1]["messages"][2]["content"]
    assert len(_rows(ProviderRequestLedgerEntry)) == 2


def test_refusal_truncation_and_provider_failures_use_the_shared_codes(
    study_db: dict[str, Any],
) -> None:
    service, _provider = service_with(
        [
            ok("", stop="refusal"),
            ok('{"summary": "x"', stop="max_tokens"),
            ProviderFailure("ai_timeout", "slow"),
            ProviderFailure("ai_rate_limited", "busy", status_code=429, provider_message="rate"),
            RuntimeError("sdk blew up"),
        ]
    )
    with pytest.raises(AssistantError) as refused:
        service.propose_study(user_text="declined")
    assert refused.value.code == "ai_refused"
    with pytest.raises(AssistantError) as truncated:
        service.propose_study(user_text="long")
    assert truncated.value.code == "ai_output_truncated" and truncated.value.detail == {
        "limit": 2000
    }
    with pytest.raises(AssistantError) as timed_out:
        service.propose_study(user_text="slow")
    assert timed_out.value.code == "ai_timeout" and timed_out.value.detail["deadline_seconds"] == 5
    with pytest.raises(AssistantError) as limited:
        service.propose_study(user_text="rate")
    assert limited.value.code == "ai_rate_limited"
    assert limited.value.detail == {"provider_status": 429, "provider_message": "rate"}
    with pytest.raises(AssistantError) as unexpected:
        service.propose_study(user_text="boom")
    assert unexpected.value.code == "ai_provider_error"
    outcomes = sorted(r.outcome for r in _rows(ProviderRequestLedgerEntry))
    assert outcomes == ["http_error", "ok", "rate_limited", "refused", "timeout"]
    assert all(r.completed_at is not None for r in _rows(ProviderRequestLedgerEntry))
    assert len(_rows(AiDraft)) == 0


def test_input_validation_and_length_limit(
    study_db: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__AI__MAX_INPUT_CHARACTERS", "60")
    clear_settings_cache()
    service, provider = service_with([ok(out())] * 3)
    for bad in ("", "   ", None, 5):
        with pytest.raises(AssistantError) as excinfo:
            service.propose_study(user_text=bad)  # type: ignore[arg-type]
        assert (
            excinfo.value.code == "invalid_assistant_input"
            and excinfo.value.detail["field"] == "user_text"
        )
    with pytest.raises(AssistantError) as not_object:
        service.propose_study(user_text="x", base_proposal=["nope"])  # type: ignore[arg-type]
    assert not_object.value.code == "invalid_assistant_input"
    assert not_object.value.detail["field"] == "base_proposal"
    with pytest.raises(AssistantError) as too_long:
        service.propose_study(user_text="x" * 61)
    assert too_long.value.code == "ai_input_too_long" and too_long.value.detail == {
        "limit": 60,
        "length": 61,
    }
    with pytest.raises(AssistantError) as base_counts:
        service.propose_study(user_text="x" * 40, base_proposal={"summary": "y" * 40})
    assert base_counts.value.code == "ai_input_too_long" and base_counts.value.detail["length"] > 80
    assert provider.calls == [] and _rows(ProviderRequestLedgerEntry) == []


def test_daily_limit_counts_study_requests_in_the_shared_ledger(
    study_db: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__AI__MAX_REQUESTS_PER_DAY", "2")
    clear_settings_cache()
    service, provider = service_with([ok(out())] * 3)
    service.propose_study(user_text="one")
    service.propose_study(user_text="two")
    with pytest.raises(AssistantError) as daily:
        service.propose_study(user_text="three")
    assert daily.value.code == "ai_daily_limit_reached" and daily.value.detail == {
        "limit": 2,
        "used": 2,
    }
    assert len(provider.calls) == 2 and service.status()["usage"]["remaining_today"] == 0


def test_disabled_and_unconfigured_refuse_before_any_provider_call(
    study_db: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__AI__ENABLED", "false")
    clear_settings_cache()
    service, provider = service_with([])
    with pytest.raises(AssistantError) as disabled:
        service.propose_study(user_text="anything")
    assert disabled.value.code == "ai_disabled"
    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__AI__ENABLED", "true")
    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__AI__API_KEY", "")
    clear_settings_cache()
    service, provider = service_with([])
    with pytest.raises(AssistantError) as unconfigured:
        service.propose_study(user_text="anything")
    assert unconfigured.value.code == "ai_not_configured"
    assert provider.calls == [] and _rows(ProviderRequestLedgerEntry) == []


def test_base_proposal_revision_includes_the_current_json_in_the_user_message(
    study_db: dict[str, Any],
) -> None:
    service, provider = service_with([ok(out(slippage_bps="5"))])
    base = {"name": "Old name", "assets": [{"ticker": "AAPL"}], "slippage_bps": ""}
    result = service.propose_study(user_text="set slippage to 5 bps", base_proposal=base)
    assert result["settings"]["slippage_bps"] == "5"
    message = provider.calls[0]["messages"][0]["content"]
    assert "Current proposal (JSON):\n```json\n" in message
    assert '"name": "Old name"' in message and '"ticker": "AAPL"' in message
    assert message.rstrip().endswith("Revision request: set slippage to 5 bps")
    assert "Study request:" not in message


def test_no_approved_versions_is_stated_in_the_prompt(
    study_db: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # strategy_versions is append-only in the database, so the empty catalogue is simulated.
    monkeypatch.setattr(ResearchStrategyService, "list_versions", lambda self, **kw: [])
    service, provider = service_with([ok(out(strategies=[{"reference": "x", "version_id": ""}]))])
    result = service.propose_study(user_text="anything")
    assert (
        "(none: no strategy version is approved yet)" in provider.calls[0]["messages"][0]["content"]
    )
    assert result["settings"]["strategies"] == {
        "resolved": [],
        "unresolved": [{"reference": "x", "reason": "no approved version matches"}],
    }


def test_long_version_lists_are_capped_with_latest_first(
    study_db: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def many(self: object, **kw: object) -> list[dict[str, Any]]:
        return [
            {
                "version_id": str(uuid.uuid4()),
                "strategy_id": str(uuid.uuid4()),
                "version_no": 1,
                "name": f"Strategy {i:03d}",
                "approved_at": "2026-10-01T12:00:00+00:00",
            }
            for i in range(305)
        ]

    monkeypatch.setattr(ResearchStrategyService, "list_versions", many)
    service, provider = service_with([ok(out())])
    service.propose_study(user_text="anything")
    message = provider.calls[0]["messages"][0]["content"]
    assert message.count(" | latest | approved 2026-10-01") == 300
    assert "(truncated: 5 more approved versions are not listed)" in message


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


def test_route_happy_path_and_request_validation(
    study_api: tuple[TestClient, StubProvider, dict[str, Any]],
) -> None:
    client, provider, db = study_api
    provider.script = [
        ok(out(strategies=[{"reference": "x", "version_id": db["v1"]["version_id"]}]))
    ]
    response = client.post(URL, json={"user_text": "test my trend"})
    assert response.status_code == 200
    body = response.json()
    assert body["as_of"] and body["proposal"]["status"] == "ok"
    assert (
        body["proposal"]["settings"]["strategies"]["resolved"][0]["version_id"]
        == db["v1"]["version_id"]
    )

    assert client.post(URL, json={}).json()["detail"]["code"] == "invalid_request"
    assert client.post(URL, json={"user_text": "x", "extra": 1}).status_code == 422
    empty = client.post(URL, json={"user_text": " "})
    assert empty.status_code == 422 and empty.json()["detail"]["code"] == "invalid_assistant_input"
    not_object = client.post(URL, json={"user_text": "x", "base_proposal": "text"})
    assert not_object.status_code == 422
    assert not_object.json()["detail"]["code"] == "invalid_assistant_input"
    assert not_object.json()["detail"]["field"] == "base_proposal"
    assert len(provider.calls) == 1


def test_route_invalid_output_is_422_with_findings_and_two_ledger_rows(
    study_api: tuple[TestClient, StubProvider, dict[str, Any]],
) -> None:
    client, provider, _db = study_api
    provider.script = [
        ok(out(split={"development_pct": 1, "validation_pct": 1, "final_test_pct": 1})),
        ok("{}"),
    ]
    response = client.post(URL, json={"user_text": "bad split"})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "ai_output_invalid" and detail["findings"] and detail["attempts"] == 2
    assert "study proposal contract" in detail["message"]
    assert len(_rows(ProviderRequestLedgerEntry)) == 2


def test_route_maps_refusal_limits_and_disabled(
    study_api: tuple[TestClient, StubProvider, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, provider, _db = study_api
    provider.script = [ok("", stop="refusal"), ProviderFailure("ai_timeout", "slow")]
    refused = client.post(URL, json={"user_text": "declined"})
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "ai_refused"
    timed_out = client.post(URL, json={"user_text": "slow"})
    assert timed_out.status_code == 504 and timed_out.json()["detail"]["code"] == "ai_timeout"

    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__AI__MAX_INPUT_CHARACTERS", "10")
    clear_settings_cache()
    app = client.app
    app.state.study_assistant_service = StudyAssistantService(  # type: ignore[attr-defined]
        load_settings(), provider=provider, process_id="api:2"
    )
    too_long = client.post(URL, json={"user_text": "x" * 11})
    assert too_long.status_code == 422 and too_long.json()["detail"]["code"] == "ai_input_too_long"

    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__AI__MAX_INPUT_CHARACTERS", "20000")
    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__AI__MAX_REQUESTS_PER_DAY", "2")
    clear_settings_cache()
    app.state.study_assistant_service = StudyAssistantService(  # type: ignore[attr-defined]
        load_settings(), provider=provider, process_id="api:3"
    )
    limited = client.post(URL, json={"user_text": "over the cap"})
    assert (
        limited.status_code == 429 and limited.json()["detail"]["code"] == "ai_daily_limit_reached"
    )

    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__AI__ENABLED", "false")
    clear_settings_cache()
    app.state.study_assistant_service = StudyAssistantService(  # type: ignore[attr-defined]
        load_settings(), provider=provider, process_id="api:4"
    )
    disabled = client.post(URL, json={"user_text": "anything"})
    assert disabled.status_code == 409 and disabled.json()["detail"]["code"] == "ai_disabled"
    assert disabled.json()["detail"]["missing"][0] == "TRADING_PLATFORM_RESEARCH__AI__ENABLED=true"
