"""S5 assistant: bounded, accounted, recorded drafting and revision (acceptance 20).

A stub provider stands in for the SDK in most tests; one test drives the real
``AnthropicProvider`` through the SDK against an ``httpx.MockTransport`` so the request
shape (``output_config.format``, cached system block, ``max_retries=0``) and the response
mapping (refusal, usage, request id) are exercised on the official client.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx2 as httpx
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from tests.support.migrated_db import migrated_database

import trading_platform.api.app as api_app
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models.research import (
    AiDraft,
    ProviderRequestLedgerEntry,
    StrategyDraft,
    StrategyVersion,
)
from trading_platform.db.session import session_scope
from trading_platform.services.research import assistant as mod
from trading_platform.services.research.assistant import (
    OUTPUT_SCHEMA,
    SYSTEM_PROMPT,
    AnthropicProvider,
    AssistantError,
    ProviderFailure,
    ProviderResult,
    ResearchAssistantService,
    evaluate_output,
    render_spec_yaml,
)
from trading_platform.services.research.strategies import ResearchStrategyService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

GOOD_SPEC = {
    "name": "Trend following 50/200",
    "description": "Long above both averages; exit below the fast one.",
    "indicators": [
        {
            "name": "sma_fast",
            "type": "sma",
            "source": "close",
            "window": 50,
            "history": 0,
            "periods": 0,
            "shift": 0,
        },
        {
            "name": "sma_slow",
            "type": "sma",
            "source": "close",
            "window": 200,
            "history": 0,
            "periods": 0,
            "shift": 0,
        },
    ],
    "entry": {
        "combine": "all_of",
        "conditions": [
            {"left": "close", "op": "gt", "right": "sma_slow"},
            {"left": "sma_fast", "op": "gt", "right": "sma_slow"},
        ],
        "subgroups": [],
    },
    "exit": {
        "combine": "any_of",
        "conditions": [{"left": "close", "op": "lt", "right": "sma_fast"}],
        "subgroups": [],
    },
}
BAD_SPEC = {
    **GOOD_SPEC,
    "entry": {
        "combine": "all_of",
        "conditions": [{"left": "close", "op": "gt", "right": "nope"}],
        "subgroups": [],
    },
}


def output(
    spec: dict[str, Any], *, unsupported: list[dict[str, str]] | None = None, note: str = "A note."
) -> str:
    return json.dumps(
        {"specification": spec, "unsupported_requests": unsupported or [], "note": note}
    )


class StubProvider:
    """Scripted provider: each call pops the next scripted outcome; records every request."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []
        self.delay = 0.0
        self.lock = threading.Lock()

    def complete(self, **kwargs: Any) -> ProviderResult:
        with self.lock:
            self.calls.append(kwargs)
            item = self.script.pop(0)
        if self.delay:
            time.sleep(self.delay)
        if isinstance(item, Exception):
            raise item
        return item


def ok(text: str, *, stop: str = "end_turn") -> ProviderResult:
    return ProviderResult(
        text=text,
        stop_reason=stop,
        request_id=f"req_{uuid.uuid4().hex[:8]}",
        model="claude-haiku-5-5",
        input_tokens=1200,
        output_tokens=300,
        cache_read_input_tokens=1000,
        cache_creation_input_tokens=0,
    )


def _configure(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    env = {
        "TRADING_PLATFORM_RESEARCH__MODE": "true",
        "TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED": "true",
        "TRADING_PLATFORM_RESEARCH__AI__ENABLED": "true",
        "TRADING_PLATFORM_RESEARCH__AI__API_KEY": "sk-ant-test-not-a-real-key",
        "TRADING_PLATFORM_RESEARCH__AI__MAX_REQUESTS_PER_DAY": "6",
        "TRADING_PLATFORM_RESEARCH__AI__MAX_OUTPUT_TOKENS": "2000",
        "TRADING_PLATFORM_RESEARCH__AI__MAX_REVISIONS_PER_DRAFT": "2",
        "TRADING_PLATFORM_RESEARCH__AI__TIMEOUT_SECONDS": "5",
        **overrides,
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    clear_settings_cache()


@pytest.fixture()
def research_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with migrated_database(monkeypatch, "research_s5") as name:
        _configure(monkeypatch)
        yield name


def service_with(script: list[Any]) -> tuple[ResearchAssistantService, StubProvider]:
    provider = StubProvider(script)
    return ResearchAssistantService(
        load_settings(), provider=provider, process_id="test:1"
    ), provider


def _rows(model: type) -> list[Any]:
    with session_scope(load_settings()) as session:
        return list(session.execute(sa.select(model)).scalars())


def _count(model: type) -> int:
    return len(_rows(model))


# ---------------------------------------------------------------------------
# Pure pieces
# ---------------------------------------------------------------------------


def test_output_schema_meets_provider_constraints() -> None:
    """No recursion, no numeric/string bounds, additionalProperties false everywhere, every
    object lists all its properties as required (the documented structured-output rules)."""

    forbidden = {"minimum", "maximum", "multipleOf", "minLength", "maxLength", "pattern"}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            assert not (set(node) & forbidden), node
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
                assert set(node.get("required", [])) == set(node["properties"]), node[
                    "properties"
                ].keys()
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    unions = 0

    def count_unions(node: Any) -> None:
        nonlocal unions
        if isinstance(node, dict):
            if "anyOf" in node or isinstance(node.get("type"), list):
                unions += 1
            for value in node.values():
                count_unions(value)
        elif isinstance(node, list):
            for item in node:
                count_unions(item)

    walk(OUTPUT_SCHEMA)
    count_unions(OUTPUT_SCHEMA)
    # Live 400 on 2026-10-08: "Schemas contains too many parameters with union types (31 ...
    # limit: 16)". The shape carries no union at all.
    assert unions == 0
    assert OUTPUT_SCHEMA["$defs"]["condition"]["properties"]["op"]["enum"] == [
        "gt",
        "ge",
        "lt",
        "le",
        "crosses_above",
        "crosses_below",
    ]
    assert (
        "other_unsupported_request"
        in OUTPUT_SCHEMA["properties"]["unsupported_requests"]["items"]["properties"]["code"][
            "enum"
        ]
    )
    assert (
        "stop_or_target_price_not_supported" in SYSTEM_PROMPT
        and "never claims profitability" in SYSTEM_PROMPT
    )


def test_output_pipeline_uses_the_shared_validator_and_house_style_yaml() -> None:
    parsed, yaml_text, outcome = evaluate_output(output(GOOD_SPEC))
    assert outcome.valid and yaml_text is not None
    assert yaml_text.splitlines()[0] == "spec_version: 1"
    assert "  sma_fast: {type: sma, source: close, window: 50}" in yaml_text
    assert "    - {left: close, op: gt, right: sma_slow}" in yaml_text
    assert outcome.derived is not None and outcome.derived["history_required"] == 200
    assert parsed.note == "A note."

    _parsed, _yaml, invalid = evaluate_output(output(BAD_SPEC))
    assert not invalid.valid and invalid.errors[0]["code"] == "undefined_reference"

    _parsed, _yaml, garbage = evaluate_output("not json at all")
    assert not garbage.valid and garbage.errors[0]["code"] == "invalid_type"

    nested = {
        **GOOD_SPEC,
        "entry": {
            "combine": "all_of",
            "conditions": [],
            "subgroups": [
                {
                    "combine": "any_of",
                    "conditions": [
                        {"left": "close", "op": "gt", "right": "sma_slow"},
                        {"left": "close", "op": "crosses_above", "right": "sma_fast"},
                    ],
                }
            ],
        },
    }
    _parsed, nested_yaml, nested_outcome = evaluate_output(output(nested))
    assert nested_outcome.valid, nested_outcome.errors
    assert "    - any_of:\n        - {left: close, op: gt, right: sma_slow}" in (nested_yaml or "")

    numeric = {
        **GOOD_SPEC,
        "indicators": [
            {
                "name": "rsi14",
                "type": "rsi",
                "source": "close",
                "window": 14,
                "history": 100,
                "periods": 0,
                "shift": 0,
            }
        ],
        "entry": {
            "combine": "all_of",
            "conditions": [{"left": "rsi14", "op": "lt", "right": "30"}],
            "subgroups": [],
        },
        "exit": {
            "combine": "any_of",
            "conditions": [{"left": "rsi14", "op": "gt", "right": "70"}],
            "subgroups": [],
        },
    }
    _parsed, numeric_yaml, numeric_outcome = evaluate_output(output(numeric))
    assert numeric_outcome.valid, numeric_outcome.errors
    assert "  rsi14: {type: rsi, source: close, window: 14, history: 100}" in (numeric_yaml or "")
    assert (
        "right: 30}" in (numeric_yaml or "") and numeric_outcome.derived["history_required"] == 100
    )


def test_render_quotes_awkward_scalars() -> None:
    text = render_spec_yaml(
        {
            "spec_version": 1,
            "name": "a: b #c",
            "description": "",
            "timeframe": "daily",
            "direction": "long_only",
            "indicators": {},
            "entry": {"all_of": [{"left": "close", "op": "gt", "right": 30.5}]},
            "exit": {"any_of": [{"left": "close", "op": "lt", "right": "30"}]},
        }
    )
    assert "name: 'a: b #c'" in text
    assert (
        "right: 30.5}" in text and "right: '30'}" in text
    )  # render_spec_yaml itself never reinterprets strings


# ---------------------------------------------------------------------------
# Service: disabled / unconfigured / bounds
# ---------------------------------------------------------------------------


def test_disabled_and_unconfigured_refuse_with_what_is_missing(
    research_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__AI__ENABLED", "false")
    clear_settings_cache()
    service, provider = service_with([])
    status_ = service.status()
    assert status_["enabled"] is False and status_["configured"] is False
    assert status_["missing"][0] == "TRADING_PLATFORM_RESEARCH__AI__ENABLED=true"
    with pytest.raises(AssistantError) as excinfo:
        service.propose(user_text="buy low sell high")
    assert excinfo.value.code == "ai_disabled"

    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__AI__ENABLED", "true")
    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__AI__MAX_REQUESTS_PER_DAY", "0")
    clear_settings_cache()
    service, provider = service_with([])
    with pytest.raises(AssistantError) as excinfo:
        service.propose(user_text="buy low sell high")
    assert excinfo.value.code == "ai_not_configured"
    assert excinfo.value.detail["missing"] == [
        "TRADING_PLATFORM_RESEARCH__AI__MAX_REQUESTS_PER_DAY > 0"
    ]
    assert provider.calls == [] and _count(AiDraft) == 0 and _count(ProviderRequestLedgerEntry) == 0


def test_draft_request_records_provenance_and_charges_the_ledger(research_db: str) -> None:
    service, provider = service_with(
        [
            ok(
                output(
                    GOOD_SPEC,
                    unsupported=[
                        {
                            "code": "stop_or_target_price_not_supported",
                            "detail": "a 5% stop was requested",
                        }
                    ],
                    note="Stop dropped.",
                )
            )
        ]
    )
    proposal = service.propose(user_text="50/200 trend with a 5% stop", request_token="tok-1")
    assert (
        proposal["status"] == "ok" and proposal["kind"] == "draft" and proposal["attempt_no"] == 1
    )
    assert proposal["validation"]["valid"] is True and proposal["explanation"]
    assert proposal["unsupported_requests"] == [
        {"code": "stop_or_target_price_not_supported", "detail": "a 5% stop was requested"}
    ]
    assert (
        proposal["note"] == "Stop dropped."
        and proposal["spec_sha256"] == proposal["validation"]["derived"]["spec_sha256"]
    )
    prov = proposal["provenance"]
    assert (
        prov["provider"] == "anthropic"
        and prov["model"] == "claude-haiku-5-5"
        and prov["prompt_version"] == "s5-v2"
    )
    assert (
        prov["request_id"].startswith("req_")
        and prov["input_tokens"] == 1200
        and prov["cache_read_input_tokens"] == 1000
    )
    assert (
        prov["started_at"]
        and prov["completed_at"]
        and prov["deadline_seconds"] == pytest.approx(5.0, abs=0.2)
    )
    # request shape
    call = provider.calls[0]
    assert (
        call["model"] == "claude-haiku-5-5"
        and call["max_output_tokens"] == 2000
        and call["schema"] is OUTPUT_SCHEMA
    )
    assert call["messages"] == [
        {"role": "user", "content": "Strategy request: 50/200 trend with a 5% stop"}
    ]
    assert 0 < call["timeout_seconds"] <= 5
    # ledger: one attempt, completed ok, purpose assistant, no symbol
    ledger = _rows(ProviderRequestLedgerEntry)
    assert (
        len(ledger) == 1
        and ledger[0].purpose == "assistant"
        and ledger[0].outcome == "ok"
        and ledger[0].status_code == 200
    )
    usage = service.status()["usage"]
    assert usage == {
        "requests_today": 1,
        "remaining_today": 5,
        "in_flight": 0,
        "revisions_used": None,
    }
    # idempotent replay of the same request token: no new call, no new rows
    again = service.propose(user_text="whatever", request_token="tok-1")
    assert (
        again["ai_draft_id"] == proposal["ai_draft_id"]
        and len(provider.calls) == 1
        and _count(AiDraft) == 1
    )


def test_invalid_output_retries_once_then_returns_invalid_with_findings(research_db: str) -> None:
    service, provider = service_with([ok(output(BAD_SPEC)), ok(output(BAD_SPEC, note="still bad"))])
    proposal = service.propose(user_text="broken please")
    assert (
        proposal["status"] == "invalid"
        and proposal["failure_code"] == "ai_output_invalid"
        and proposal["attempt_no"] == 2
    )
    assert (
        proposal["validation"]["valid"] is False
        and proposal["validation"]["errors"][0]["code"] == "undefined_reference"
    )
    assert proposal["yaml_text"] and "right: nope" in proposal["yaml_text"]
    assert proposal["retry_of_ai_draft_id"] is not None
    assert len(provider.calls) == 2
    retry_messages = provider.calls[1]["messages"]
    assert retry_messages[1]["role"] == "assistant" and retry_messages[2]["role"] == "user"
    assert (
        "undefined_reference at 'entry.all_of[0].right'" in retry_messages[2]["content"]
        or "undefined_reference" in retry_messages[2]["content"]
    )
    # both attempts charged; both rows kept
    assert _count(ProviderRequestLedgerEntry) == 2 and _count(AiDraft) == 2
    assert service.status()["usage"]["requests_today"] == 2
    # an invalid-but-present YAML can still be applied (drafts may be invalid); approval is refused later by the validator
    applied = service.apply(uuid.UUID(proposal["ai_draft_id"]))
    assert applied["draft"]["source"] == "assistant"


def test_valid_on_retry_is_accepted(research_db: str) -> None:
    service, provider = service_with([ok(output(BAD_SPEC)), ok(output(GOOD_SPEC))])
    proposal = service.propose(user_text="fix on second try")
    assert proposal["status"] == "ok" and proposal["attempt_no"] == 2 and len(provider.calls) == 2


@pytest.mark.parametrize(
    ("failure", "code", "status_", "outcome"),
    [
        (ProviderFailure("ai_timeout", "slow"), "ai_timeout", "timeout", "timeout"),
        (
            ProviderFailure(
                "ai_provider_error",
                "boom",
                status_code=500,
                request_id="req_x",
                provider_message="api_error: boom",
            ),
            "ai_provider_error",
            "provider_error",
            "http_error",
        ),
        (
            ProviderFailure("ai_provider_unavailable", "down"),
            "ai_provider_unavailable",
            "unavailable",
            "transport_error",
        ),
        (
            ProviderFailure("ai_auth_failed", "key", status_code=401),
            "ai_auth_failed",
            "auth_failed",
            "auth_failed",
        ),
        (
            ProviderFailure("ai_rate_limited", "429", status_code=429),
            "ai_rate_limited",
            "rate_limited",
            "rate_limited",
        ),
    ],
)
def test_provider_failures_are_recorded_charged_and_not_retried(
    research_db: str, failure: Exception, code: str, status_: str, outcome: str
) -> None:
    service, provider = service_with([failure, ok(output(GOOD_SPEC))])
    with pytest.raises(AssistantError) as excinfo:
        service.propose(user_text="anything")
    assert excinfo.value.code == code
    assert len(provider.calls) == 1, "a provider failure is never retried automatically"
    row = _rows(AiDraft)[0]
    assert row.status == status_ and row.failure_code == code and row.output_yaml_text is None
    assert excinfo.value.detail["ai_draft_id"] == str(row.id)
    if isinstance(failure, ProviderFailure) and failure.provider_message:
        assert excinfo.value.detail["provider_message"] == failure.provider_message
        assert row.validation_result == {"provider_error": failure.provider_message}
        assert service.get_proposal(row.id)["provider_error"] == failure.provider_message
    ledger = _rows(ProviderRequestLedgerEntry)[0]
    assert ledger.outcome == outcome
    with pytest.raises(AssistantError) as not_applicable:
        service.apply(row.id)
    assert not_applicable.value.code == "ai_proposal_not_applicable"


def test_refusal_and_truncation_surface_as_their_own_codes(research_db: str) -> None:
    service, _provider = service_with(
        [ok("", stop="refusal"), ok('{"specification": {"name": "x"', stop="max_tokens")]
    )
    with pytest.raises(AssistantError) as refused:
        service.propose(user_text="something the provider declines")
    assert refused.value.code == "ai_refused"
    with pytest.raises(AssistantError) as truncated:
        service.propose(user_text="very long")
    assert truncated.value.code == "ai_output_truncated" and truncated.value.detail["limit"] == 2000
    statuses = sorted(r.status for r in _rows(AiDraft))
    assert statuses == ["refused", "truncated"]
    assert sorted(r.outcome for r in _rows(ProviderRequestLedgerEntry)) == ["ok", "refused"]


def test_input_limit_daily_cap_and_revision_cap(
    research_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__AI__MAX_INPUT_CHARACTERS", "40")
    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__AI__MAX_REQUESTS_PER_DAY", "3")
    clear_settings_cache()
    service, provider = service_with([ok(output(GOOD_SPEC))] * 5)
    with pytest.raises(AssistantError) as too_long:
        service.propose(user_text="x" * 41)
    assert too_long.value.code == "ai_input_too_long" and too_long.value.detail == {
        "limit": 40,
        "length": 41,
    }
    assert provider.calls == []
    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__AI__MAX_INPUT_CHARACTERS", "20000")
    clear_settings_cache()
    service = ResearchAssistantService(load_settings(), provider=provider, process_id="test:1")

    strategies = ResearchStrategyService(load_settings())
    draft = strategies.create_draft(title="t", yaml_text="spec_version: 1\nname: seed\n")
    draft_id = uuid.UUID(draft["draft_id"])
    first = service.propose(user_text="make it a 50/200 trend", draft_id=draft_id)
    assert first["kind"] == "revision" and first["draft_id"] == draft["draft_id"]
    assert provider.calls[0]["messages"][0]["content"].startswith(
        "Current specification (YAML):\n```yaml\nspec_version: 1\nname: seed"
    )
    # the draft text is untouched until the proposal is applied
    assert strategies.get_draft(draft_id)["yaml_text"] == "spec_version: 1\nname: seed\n"
    second = service.propose(user_text="again", draft_id=draft_id)
    assert service.status(draft_id=draft_id)["usage"]["revisions_used"] == 2
    with pytest.raises(AssistantError) as capped:
        service.propose(user_text="third", draft_id=draft_id)
    assert capped.value.code == "ai_revision_limit_reached" and capped.value.detail["limit"] == 2
    # daily cap (3): two used by the revisions above; the third draft request passes, the fourth is refused
    service.propose(user_text="fresh")
    with pytest.raises(AssistantError) as daily:
        service.propose(user_text="one more")
    assert daily.value.code == "ai_daily_limit_reached" and daily.value.detail == {
        "limit": 3,
        "used": 3,
    }
    assert service.status()["usage"]["remaining_today"] == 0
    assert second["ai_draft_id"] != first["ai_draft_id"]


def test_concurrency_cap_is_shared_and_in_flight_rows_expire(research_db: str) -> None:
    service, provider = service_with([ok(output(GOOD_SPEC)), ok(output(GOOD_SPEC))])
    provider.delay = 1.0
    results: dict[str, Any] = {}

    def run(name: str) -> None:
        try:
            results[name] = service.propose(user_text=f"concurrent {name}")
        except AssistantError as exc:
            results[name] = exc

    threads = [threading.Thread(target=run, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    outcomes = sorted(type(v).__name__ for v in results.values())
    assert outcomes == ["AssistantError", "dict"], results
    refused = next(v for v in results.values() if isinstance(v, AssistantError))
    assert refused.code == "ai_busy" and refused.detail == {"limit": 1, "in_flight": 1}
    assert len(provider.calls) == 1
    # a crashed attempt (ledger row never completed) stops counting after deadline + grace
    with session_scope(load_settings()) as session:
        session.add(
            ProviderRequestLedgerEntry(
                id=uuid.uuid4(),
                provider="anthropic",
                symbol=None,
                purpose="assistant",
                attempted_at=datetime(2020, 1, 1, tzinfo=UTC),
                process_id="dead:1",
                job_id=None,
            )
        )
    assert service.status()["usage"]["in_flight"] == 0


def test_deadline_is_shared_across_both_attempts(
    research_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRADING_PLATFORM_RESEARCH__AI__TIMEOUT_SECONDS", "1.5")
    clear_settings_cache()
    service, provider = service_with([ok(output(BAD_SPEC)), ok(output(GOOD_SPEC))])
    provider.delay = 1.0
    with pytest.raises(AssistantError) as excinfo:
        service.propose(user_text="slow and wrong")
    assert excinfo.value.code == "ai_timeout"
    assert len(provider.calls) == 1, "no second attempt starts with under a second left"
    assert provider.calls[0]["timeout_seconds"] <= 1.5


# ---------------------------------------------------------------------------
# Apply and approve
# ---------------------------------------------------------------------------


def test_apply_is_explicit_idempotent_and_approval_carries_provenance(research_db: str) -> None:
    service, _provider = service_with(
        [ok(output(GOOD_SPEC)), ok(output({**GOOD_SPEC, "name": "Trend following 60/200"}))]
    )
    proposal = service.propose(user_text="trend")
    assert _count(StrategyDraft) == 0, "a proposal never creates a draft by itself"
    applied = service.apply(uuid.UUID(proposal["ai_draft_id"]), title="My trend")
    draft = applied["draft"]
    assert (
        draft["title"] == "My trend"
        and draft["source"] == "assistant"
        and draft["ai_draft_id"] == proposal["ai_draft_id"]
    )
    assert applied["already_applied"] is False and applied["proposal"]["applied_at"]
    assert service.status(draft_id=uuid.UUID(draft["draft_id"]))["usage"]["revisions_used"] == 0, (
        "applying a draft-kind proposal is not a revision"
    )
    repeat = service.apply(uuid.UUID(proposal["ai_draft_id"]))
    assert (
        repeat["already_applied"] is True
        and repeat["draft"]["draft_id"] == draft["draft_id"]
        and _count(StrategyDraft) == 1
    )

    draft_id = uuid.UUID(draft["draft_id"])
    revision = service.propose(
        user_text="use 60 instead of 50",
        draft_id=draft_id,
        parent_ai_draft_id=uuid.UUID(proposal["ai_draft_id"]),
    )
    strategies = ResearchStrategyService(load_settings())
    assert strategies.get_draft(draft_id)["yaml_text"] == proposal["yaml_text"], (
        "revision proposals do not touch the draft"
    )
    service.apply(uuid.UUID(revision["ai_draft_id"]), draft_id=draft_id)
    assert "Trend following 60/200" in strategies.get_draft(draft_id)["yaml_text"]
    provenance = strategies.get_draft(draft_id)["assistant"]
    assert provenance["summary"] == "drafted by assistant, revised 1 time(s), not yet approved"
    assert [a["kind"] for a in provenance["attempts"]] == ["revision", "draft"]

    version = strategies.approve_draft(draft_id)
    detail = strategies.get_version(uuid.UUID(version["version_id"]))
    assert detail["source"] == "assistant" and detail["ai_draft_id"] == revision["ai_draft_id"]
    assert (
        detail["assistant"]["revisions"] == 1 and detail["assistant"]["attempts"][0]["request_id"]
    )
    assert detail["assistant"]["attempts"][0]["model"] == "claude-haiku-5-5"
    assert _count(StrategyVersion) == 1 and _count(StrategyDraft) == 0


def test_new_attempts_carry_the_current_contract_version_and_history_is_untouched(
    research_db: str,
) -> None:
    """Contract s5-v2 (prompt + schema) is stamped on every new attempt, retries included;
    an earlier s5-v1 row keeps its label and stays readable in the provenance chain."""

    historical_id = uuid.uuid4()
    with session_scope(load_settings()) as session:
        session.add(
            AiDraft(
                id=historical_id,
                provider="anthropic",
                model="claude-haiku-5-5",
                prompt_version="s5-v1",
                request_id="req_history",
                user_text="old request",
                validation_result={
                    "valid": True,
                    "errors": [],
                    "derived": None,
                    "explanation": None,
                },
                kind="draft",
                status="ok",
                attempt_no=1,
                output_yaml_text="spec_version: 1\nname: old\n",
                unsupported_requests=[],
            )
        )
    assert mod.PROMPT_VERSION == "s5-v2"
    service, _provider = service_with([ok(output(BAD_SPEC)), ok(output(GOOD_SPEC))])
    proposal = service.propose(user_text="new request", parent_ai_draft_id=historical_id)
    assert proposal["status"] == "ok" and proposal["attempt_no"] == 2
    assert proposal["provenance"]["prompt_version"] == "s5-v2"
    rows = {str(r.id): r for r in _rows(AiDraft)}
    assert len(rows) == 3
    assert (
        rows[str(historical_id)].prompt_version == "s5-v1"
        and rows[str(historical_id)].request_id == "req_history"
    )
    new_rows = [r for r in rows.values() if r.id != historical_id]
    assert sorted(r.attempt_no for r in new_rows) == [1, 2]
    assert {r.prompt_version for r in new_rows} == {"s5-v2"}, "the retry carries the version too"
    with session_scope(load_settings()) as session:
        chain = mod.assistant_provenance(session, uuid.UUID(proposal["ai_draft_id"]))
    assert chain is not None
    assert [(a["attempt_no"], a["prompt_version"]) for a in chain["attempts"]] == [
        (2, "s5-v2"),
        (1, "s5-v2"),
        (1, "s5-v1"),
    ]
    status_ = service.status()
    assert status_["prompt_version"] == "s5-v2" and len(status_["output_schema_sha256"]) == 64
    assert status_["output_schema_sha256"] == mod.OUTPUT_SCHEMA_SHA256


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


@pytest.fixture()
def research_api(
    research_db: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, StubProvider]]:
    settings = load_settings()
    monkeypatch.setattr(api_app, "enforce_startup_config", lambda **kwargs: settings)
    provider = StubProvider(
        [
            ok(output(GOOD_SPEC)),
            ProviderFailure("ai_timeout", "slow"),
            ok(output(BAD_SPEC)),
            ok(output(BAD_SPEC)),
        ]
    )
    app = api_app.create_app()
    app.state.research_assistant_service = ResearchAssistantService(
        settings, provider=provider, process_id="api:1"
    )
    with TestClient(app) as client:
        yield client, provider


def test_http_surface(research_api: tuple[TestClient, StubProvider]) -> None:
    client, _provider = research_api
    status_ = client.get("/api/v1/research/assistant").json()["assistant"]
    assert (
        status_["configured"] is True
        and "api_key" not in json.dumps(status_)
        and "sk-ant" not in json.dumps(status_)
    )
    assert status_["limits"]["max_requests_per_day"] == 6 and status_["note"].startswith(
        "Limits are enforced"
    )

    bad = client.post("/api/v1/research/assistant/proposals", json={"user_text": ""})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "invalid_assistant_input"

    created = client.post(
        "/api/v1/research/assistant/proposals", json={"user_text": "trend", "request_token": "ui-1"}
    )
    assert created.status_code == 200
    proposal = created.json()["proposal"]
    assert proposal["status"] == "ok" and proposal["validation"]["valid"]
    fetched = client.get(f"/api/v1/research/assistant/proposals/{proposal['ai_draft_id']}").json()[
        "proposal"
    ]
    assert fetched["spec_sha256"] == proposal["spec_sha256"]

    timed_out = client.post("/api/v1/research/assistant/proposals", json={"user_text": "slow"})
    assert timed_out.status_code == 504 and timed_out.json()["detail"]["code"] == "ai_timeout"
    assert timed_out.json()["detail"]["ai_draft_id"]

    invalid = client.post("/api/v1/research/assistant/proposals", json={"user_text": "bad"}).json()[
        "proposal"
    ]
    assert invalid["status"] == "invalid" and invalid["failure_code"] == "ai_output_invalid"

    applied = client.post(
        f"/api/v1/research/assistant/proposals/{proposal['ai_draft_id']}/apply",
        json={"title": "Via HTTP"},
    )
    assert applied.status_code == 200 and applied.json()["draft"]["source"] == "assistant"
    draft_id = applied.json()["draft"]["draft_id"]
    assert (
        client.get(f"/api/v1/research/strategies/drafts/{draft_id}").json()["draft"]["assistant"][
            "requests"
        ]
        == 1
    )
    missing = client.post(f"/api/v1/research/assistant/proposals/{uuid.uuid4()}/apply", json={})
    assert missing.status_code == 404 and missing.json()["detail"]["code"] == "ai_draft_not_found"
    # the daily cap counts every attempt (1 + 1 + 2 = 4 of 6 used)
    assert (
        client.get("/api/v1/research/assistant").json()["assistant"]["usage"]["requests_today"] == 4
    )


def test_http_requests_overlap_and_the_second_is_refused_as_busy(
    research_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route must not block the event loop while the provider answers: two concurrent
    HTTP requests reach the service together, so the shared concurrency cap refuses one
    (a blocking async handler would serialize them and both would succeed)."""

    settings = load_settings()
    monkeypatch.setattr(api_app, "enforce_startup_config", lambda **kwargs: settings)
    provider = StubProvider([ok(output(GOOD_SPEC)), ok(output(GOOD_SPEC))])
    provider.delay = 1.0
    app = api_app.create_app()
    app.state.research_assistant_service = ResearchAssistantService(
        settings, provider=provider, process_id="api:2"
    )
    with TestClient(app) as client:
        results: list[tuple[int, str]] = []

        def post(text: str) -> None:
            response = client.post("/api/v1/research/assistant/proposals", json={"user_text": text})
            results.append(
                (response.status_code, (response.json().get("detail") or {}).get("code", "ok"))
            )

        threads = [threading.Thread(target=post, args=(f"slow {i}",)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        health = client.get("/health")
    assert sorted(results) == [(200, "ok"), (409, "ai_busy")], results
    assert health.status_code == 200 and len(provider.calls) == 1


# ---------------------------------------------------------------------------
# The official SDK against a mock transport
# ---------------------------------------------------------------------------


def _sdk_response(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    assert request.url.path == "/v1/messages"
    assert request.headers["x-api-key"] == "sk-ant-test-not-a-real-key"
    assert body["output_config"] == {"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}}
    assert (
        body["system"][0]["cache_control"] == {"type": "ephemeral"}
        and body["system"][0]["text"] == SYSTEM_PROMPT
    )
    assert body["max_tokens"] == 2000 and "tools" not in body
    if "REFUSE" in body["messages"][0]["content"]:
        return httpx.Response(
            200,
            headers={"request-id": "req_refusal"},
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-haiku-5-5",
                "content": [],
                "stop_reason": "refusal",
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 0},
            },
        )
    if "OVERLOAD" in body["messages"][0]["content"]:
        return httpx.Response(
            529,
            headers={"request-id": "req_overload"},
            json={"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
        )
    return httpx.Response(
        200,
        headers={"request-id": "req_sdk_1"},
        json={
            "id": "msg_2",
            "type": "message",
            "role": "assistant",
            "model": "claude-haiku-5-5",
            "content": [{"type": "text", "text": output(GOOD_SPEC)}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {
                "input_tokens": 1500,
                "output_tokens": 400,
                "cache_read_input_tokens": 1400,
                "cache_creation_input_tokens": 0,
            },
        },
    )


def test_sdk_adapter_request_shape_and_response_mapping(research_db: str) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return _sdk_response(request)

    provider = AnthropicProvider(
        load_settings(), http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    result = provider.complete(
        model="claude-haiku-5-5",
        system_text=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": "Strategy request: trend"}],
        schema=OUTPUT_SCHEMA,
        max_output_tokens=2000,
        timeout_seconds=5,
    )
    assert (
        result.request_id == "req_sdk_1"
        and result.stop_reason == "end_turn"
        and result.cache_read_input_tokens == 1400
    )
    assert json.loads(result.text or "")["specification"]["name"] == "Trend following 50/200"

    refused = provider.complete(
        model="claude-haiku-5-5",
        system_text=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": "REFUSE"}],
        schema=OUTPUT_SCHEMA,
        max_output_tokens=2000,
        timeout_seconds=5,
    )
    assert refused.stop_reason == "refusal" and refused.text is None

    with pytest.raises(ProviderFailure) as excinfo:
        provider.complete(
            model="claude-haiku-5-5",
            system_text=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": "OVERLOAD"}],
            schema=OUTPUT_SCHEMA,
            max_output_tokens=2000,
            timeout_seconds=5,
        )
    assert excinfo.value.code == "ai_provider_error" and excinfo.value.status_code == 529
    assert excinfo.value.provider_message == "overloaded_error: Overloaded"
    assert calls.count("/v1/messages") == 3, "max_retries=0: the 529 was not retried by the SDK"

    # end to end through the service with the SDK adapter
    service = ResearchAssistantService(load_settings(), provider=provider, process_id="sdk:1")
    proposal = service.propose(user_text="trend")
    assert proposal["status"] == "ok" and proposal["provenance"]["request_id"] == "req_sdk_1"


def test_key_never_appears_in_stored_bodies_or_proposal(research_db: str) -> None:
    service, _provider = service_with([ok(output(GOOD_SPEC))])
    proposal = service.propose(user_text="trend")
    assert "sk-ant" not in json.dumps(proposal)
    with session_scope(load_settings()) as session:
        rows = (
            session.execute(sa.text("SELECT row_to_json(a)::text FROM ai_drafts a")).scalars().all()
        )
    assert all("sk-ant" not in r for r in rows)
    assert mod.PROMPT_VERSION == "s5-v2"
