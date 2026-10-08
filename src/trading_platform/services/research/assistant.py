"""AI drafting and revision assistant for research strategies (plan S5).

One bounded, synchronous ``messages.create`` per draft or revision request through the
official Anthropic SDK, with a structured output format equal to the specification shape
plus ``unsupported_requests`` and ``note``. The same validator and the same deterministic
explanation as handwritten YAML run on every candidate. The assistant has no tools, no
broker access, no trading-database access, never sees study results, cannot run a study
and cannot approve a version: approval stays the user's separate action.

Bounds (every one enforced here, per attempt, before the provider is called):

* ``research.ai.usable`` (enabled + key + both limits) or the request is refused with a
  closed code naming what is missing; the editor keeps working;
* input size (``max_input_characters``) over the user text and the YAML it revises;
* output tokens (``max_output_tokens``) passed as ``max_tokens``;
* the daily request cap and the concurrency cap, counted in ``provider_request_ledger``
  under the provider advisory lock, so every process shares one allowance and a retry
  is a new admission;
* the revision cap per draft (``max_revisions_per_draft``);
* an overall deadline (``timeout_seconds``) across both attempts; the SDK's implicit
  retries are disabled (``max_retries=0``) so no attempt goes unaccounted.

One automatic retry when the output fails validation; after that the proposal is returned
as ``invalid`` with every finding. Provenance of every attempt lands in ``ai_drafts``
(bodies never contain the key). No monetary guarantee is made: the provider console's
spending controls are a separate control the user sets.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

import yaml  # type: ignore[import-untyped]
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from trading_platform.core.settings import Settings
from trading_platform.db.models.research import (
    AiDraft,
    ProviderRequestLedgerEntry,
    StrategyDraft,
    StrategyVersion,
)
from trading_platform.db.session import session_scope
from trading_platform.services.research.budget import default_process_id, provider_lock_key
from trading_platform.services.research.strategies import (
    MAX_TITLE_LENGTH,
    SOURCE_ASSISTANT,
    SOURCE_MANUAL,
    DraftNotFoundError,
    ValidationOutcome,
    draft_to_dict,
    validate_yaml_text,
)
from trading_platform.strategies.spec.errors import SpecErrorCode
from trading_platform.strategies.spec.schema import (
    _NUMBER,
    MAX_DESCRIPTION,
    MAX_HISTORY,
    MAX_INDICATORS,
    MAX_NAME,
    MAX_SHIFT,
    MAX_WINDOW,
    IndicatorType,
    Operator,
    SeriesName,
    normalize_constant,
)

logger = logging.getLogger(__name__)

PROVIDER = "anthropic"
PURPOSE_ASSISTANT = "assistant"
#: Version of the provider-facing contract: the system prompt AND the structured-output
#: schema together, recorded on every attempt (retries included). History:
#:   s5-v1  2026-10-08  nullable/anyOf schema; "history optional" for ema/rsi (rejected live:
#:                      >16 union-typed parameters; invalid rsi output)
#:   s5-v2  2026-10-08  union-free shape (combine/conditions/subgroups, 0 = unset, "right" as
#:                      text), ema/rsi history stated as required
#: Earlier rows keep their own value; never relabel them.
PROMPT_VERSION = "s5-v2"

KIND_DRAFT = "draft"
KIND_REVISION = "revision"

STATUS_PENDING = "pending"
STATUS_OK = "ok"
STATUS_INVALID = "invalid"
STATUS_REFUSED = "refused"
STATUS_TIMEOUT = "timeout"
STATUS_PROVIDER_ERROR = "provider_error"
STATUS_UNAVAILABLE = "unavailable"
STATUS_AUTH_FAILED = "auth_failed"
STATUS_RATE_LIMITED = "rate_limited"
STATUS_TRUNCATED = "truncated"

MAX_ATTEMPTS = 2
MIN_ATTEMPT_SECONDS = 1.0
#: An in-flight ledger row older than the deadline plus this grace is a crashed attempt and
#: no longer counts against the concurrency cap.
IN_FLIGHT_GRACE = timedelta(seconds=30)
MAX_USER_TEXT_STORED = 20_000

PROVIDER_SPEND_NOTE = (
    "Limits are enforced by this application per attempt (requests per day, output tokens, "
    "input size, revisions per draft, concurrent requests, deadline). Spending limits in the "
    "provider console are a separate control; no monetary guarantee is made here."
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AssistantError(Exception):
    """Refusal or failure with a closed ``code`` (the console copy keys on it)."""

    def __init__(self, code: str, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


class AiDraftNotFoundError(AssistantError):
    def __init__(self, ai_draft_id: uuid.UUID) -> None:
        super().__init__(
            "ai_draft_not_found",
            f"assistant proposal {ai_draft_id} not found",
            ai_draft_id=str(ai_draft_id),
        )


CODE_DISABLED = "ai_disabled"
CODE_NOT_CONFIGURED = "ai_not_configured"
CODE_INPUT_TOO_LONG = "ai_input_too_long"
CODE_DAILY_LIMIT = "ai_daily_limit_reached"
CODE_REVISION_LIMIT = "ai_revision_limit_reached"
CODE_BUSY = "ai_busy"
CODE_TIMEOUT = "ai_timeout"
CODE_REFUSED = "ai_refused"
CODE_OUTPUT_INVALID = "ai_output_invalid"
CODE_OUTPUT_TRUNCATED = "ai_output_truncated"
CODE_PROVIDER_ERROR = "ai_provider_error"
CODE_PROVIDER_UNAVAILABLE = "ai_provider_unavailable"
CODE_AUTH_FAILED = "ai_auth_failed"
CODE_RATE_LIMITED = "ai_rate_limited"
CODE_INVALID_INPUT = "invalid_assistant_input"
CODE_NOT_APPLICABLE = "ai_proposal_not_applicable"

_STATUS_FOR_CODE = {
    CODE_TIMEOUT: STATUS_TIMEOUT,
    CODE_REFUSED: STATUS_REFUSED,
    CODE_PROVIDER_ERROR: STATUS_PROVIDER_ERROR,
    CODE_PROVIDER_UNAVAILABLE: STATUS_UNAVAILABLE,
    CODE_AUTH_FAILED: STATUS_AUTH_FAILED,
    CODE_RATE_LIMITED: STATUS_RATE_LIMITED,
    CODE_OUTPUT_TRUNCATED: STATUS_TRUNCATED,
}
_LEDGER_OUTCOME_FOR_STATUS = {
    STATUS_OK: "ok",
    STATUS_INVALID: "ok",
    STATUS_REFUSED: "refused",
    STATUS_TRUNCATED: "ok",
    STATUS_TIMEOUT: "timeout",
    STATUS_PROVIDER_ERROR: "http_error",
    STATUS_UNAVAILABLE: "transport_error",
    STATUS_AUTH_FAILED: "auth_failed",
    STATUS_RATE_LIMITED: "rate_limited",
}


# ---------------------------------------------------------------------------
# Provider contract (the SDK adapter and the test doubles implement it)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderResult:
    text: str | None
    stop_reason: str | None
    request_id: str | None
    model: str
    input_tokens: int | None
    output_tokens: int | None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None


class ProviderFailure(Exception):
    """The provider call failed; ``code`` is one of the closed assistant codes."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
        request_id: str | None = None,
        provider_message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.request_id = request_id
        #: The provider's own error type and message (never the key); shown to the user and
        #: stored with the attempt so a 4xx can be diagnosed without a second request.
        self.provider_message = provider_message


class AssistantProvider(Protocol):
    def complete(
        self,
        *,
        model: str,
        system_text: str,
        messages: Sequence[Mapping[str, Any]],
        schema: Mapping[str, Any],
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> ProviderResult: ...


def _provider_error_text(exc: Any) -> str | None:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            kind = str(error.get("type") or "error")
            message = str(error.get("message") or "")
            return f"{kind}: {message}"[:1000]
    message = getattr(exc, "message", None)
    return str(message)[:1000] if message else None


class AnthropicProvider:
    """The official SDK with implicit retries disabled; one attempt per call."""

    def __init__(self, settings: Settings, *, http_client: Any | None = None) -> None:
        import anthropic

        ai = settings.research.ai
        kwargs: dict[str, Any] = {
            "api_key": ai.api_key,
            "max_retries": 0,
            "timeout": ai.timeout_seconds,
        }
        if ai.base_url:
            kwargs["base_url"] = ai.base_url
        if http_client is not None:
            kwargs["http_client"] = http_client
        self._client = anthropic.Anthropic(**kwargs)
        self._anthropic = anthropic

    def complete(
        self,
        *,
        model: str,
        system_text: str,
        messages: Sequence[Mapping[str, Any]],
        schema: Mapping[str, Any],
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> ProviderResult:
        anthropic = self._anthropic
        try:
            raw = self._client.messages.with_raw_response.create(
                model=model,
                max_tokens=max_output_tokens,
                system=[
                    {"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}
                ],
                messages=list(messages),
                output_config={"format": {"type": "json_schema", "schema": dict(schema)}},
                timeout=timeout_seconds,
            )
        except anthropic.APITimeoutError as exc:
            raise ProviderFailure(
                CODE_TIMEOUT, "the provider did not answer within the deadline"
            ) from exc
        except anthropic.AuthenticationError as exc:
            raise ProviderFailure(
                CODE_AUTH_FAILED,
                "the provider rejected the API key",
                status_code=exc.status_code,
                request_id=getattr(exc, "request_id", None),
                provider_message=_provider_error_text(exc),
            ) from exc
        except anthropic.RateLimitError as exc:
            raise ProviderFailure(
                CODE_RATE_LIMITED,
                "the provider rate-limited this request",
                status_code=exc.status_code,
                request_id=getattr(exc, "request_id", None),
                provider_message=_provider_error_text(exc),
            ) from exc
        except anthropic.APIStatusError as exc:
            raise ProviderFailure(
                CODE_PROVIDER_ERROR,
                f"the provider answered {exc.status_code}",
                status_code=exc.status_code,
                request_id=getattr(exc, "request_id", None),
                provider_message=_provider_error_text(exc),
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderFailure(
                CODE_PROVIDER_UNAVAILABLE, "the provider could not be reached"
            ) from exc
        message = raw.parse()
        request_id = raw.headers.get("request-id") or message.id
        text_out = "".join(
            getattr(block, "text", "")
            for block in message.content
            if getattr(block, "type", "") == "text"
        )
        usage = message.usage
        return ProviderResult(
            text=text_out or None,
            stop_reason=message.stop_reason,
            request_id=request_id,
            model=message.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", None),
            cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", None),
        )


# ---------------------------------------------------------------------------
# Output format: the specification shape without recursion (provider constraint) and
# without numeric bounds (the validator enforces those and reports them by code)
# ---------------------------------------------------------------------------


def _literal_values(literal: Any) -> list[str]:
    from typing import get_args

    return list(get_args(literal))


INDICATOR_TYPES = _literal_values(IndicatorType)
SERIES = _literal_values(SeriesName)
OPERATORS = _literal_values(Operator)
UNSUPPORTED_CODES = [code.value for code in SpecErrorCode]


def _condition_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["left", "op", "right"],
        "properties": {
            "left": {
                "type": "string",
                "description": "an indicator name or a series (open, high, low, close, volume)",
            },
            "op": {"type": "string", "enum": OPERATORS},
            "right": {
                "type": "string",
                "description": 'an indicator name, a series, or a numeric constant written as a string (e.g. "30", "0.05")',
            },
        },
    }


def _subgroup_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["combine", "conditions"],
        "properties": {
            "combine": {"type": "string", "enum": ["all_of", "any_of"]},
            "conditions": {"type": "array", "items": {"$ref": "#/$defs/condition"}},
        },
    }


def _group_schema() -> dict[str, Any]:
    """A rule: ``combine`` joins the direct ``conditions`` and the ``subgroups`` (each a
    nested combine of conditions). Two levels cover the originals; deeper nesting is a
    hand edit. No union types: the provider limits a schema to 16 union-typed
    parameters and nullable/anyOf shapes blow past that quickly."""

    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["combine", "conditions", "subgroups"],
        "properties": {
            "combine": {"type": "string", "enum": ["all_of", "any_of"]},
            "conditions": {"type": "array", "items": {"$ref": "#/$defs/condition"}},
            "subgroups": {"type": "array", "items": {"$ref": "#/$defs/subgroup"}},
        },
    }


def output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["specification", "unsupported_requests", "note"],
        "$defs": {"condition": _condition_schema(), "subgroup": _subgroup_schema()},
        "properties": {
            "specification": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "description", "indicators", "entry", "exit"],
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "indicators": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "name",
                                "type",
                                "source",
                                "window",
                                "history",
                                "periods",
                                "shift",
                            ],
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "description": "^[a-z][a-z0-9_]{0,31}$, not a series name",
                                },
                                "type": {"type": "string", "enum": INDICATOR_TYPES},
                                "source": {"type": "string", "enum": SERIES},
                                "window": {
                                    "type": "integer",
                                    "description": "0 when the type has no window",
                                },
                                "history": {
                                    "type": "integer",
                                    "description": "0 for the derived default",
                                },
                                "periods": {
                                    "type": "integer",
                                    "description": "0 when the type has no periods",
                                },
                                "shift": {
                                    "type": "integer",
                                    "description": "0 unless a shifted value is needed",
                                },
                            },
                        },
                    },
                    "entry": _group_schema(),
                    "exit": _group_schema(),
                },
            },
            "unsupported_requests": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["code", "detail"],
                    "properties": {
                        "code": {"type": "string", "enum": UNSUPPORTED_CODES},
                        "detail": {"type": "string"},
                    },
                },
            },
            "note": {"type": "string"},
        },
    }


OUTPUT_SCHEMA = output_schema()
OUTPUT_SCHEMA_SHA256 = hashlib.sha256(
    json.dumps(OUTPUT_SCHEMA, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()


# ---------------------------------------------------------------------------
# System prompt (stable text; cached by the provider)
# ---------------------------------------------------------------------------


SYSTEM_PROMPT = f"""You draft and revise daily, long-only trading strategy specifications for a research platform. You write the specification only; a deterministic validator checks it, a deterministic renderer explains it, and a human decides. You have no tools, no market data, no backtest results and no authority to approve anything. Never claim performance.

Output: one JSON object with "specification", "unsupported_requests" and "note" (the response format enforces the shape). In the specification, "indicators" is a list of objects {{name, type, source, window, history, periods, shift}} where integer fields that do not apply are 0; "entry" and "exit" are each {{"combine": "all_of" | "any_of", "conditions": [...], "subgroups": [{{"combine": ..., "conditions": [...]}}]}}; a condition is {{"left", "op", "right"}} and "right" is always a string: an indicator name, a series name, or a numeric constant written as text such as "30" or "0.05".

Specification semantics (version 1):
- timeframe daily, direction long_only, one asset per evaluation. A signal is computed on each session close; fills happen on the next session (the engine's rule, not yours).
- Series: {", ".join(SERIES)}.
- Indicators (at most {MAX_INDICATORS}), each with a unique snake_case name that is not a series name:
  - sma(source, window): simple moving average; window 1..{MAX_WINDOW}.
  - ema(source, window): exponential moving average with smoothing 2/(window+1); window 1..{MAX_WINDOW}; "history" (warm-up bars fed to the recursion) is REQUIRED and must be at least the window (use 3x the window when unsure).
  - rsi(source, window): Wilder RSI 0..100; window 2..200; "history" is REQUIRED and must be at least window+1 (e.g. window 14, history 100).
  - highest(source, window) / lowest(source, window): rolling max / min over the window; "shift" (0..{MAX_SHIFT}) uses the value as of that many bars earlier (shift 1 = preceding-bar channel).
  - lag(source, periods): the value "periods" bars earlier; periods 1..{MAX_WINDOW}.
  - change_pct(source, periods): percent change over "periods" bars, as a fraction (0.05 = 5%).
  - Integer fields that do not apply to a type are 0: "periods" is used only by lag/change_pct (their "window" is 0); "window" by sma/ema/rsi/highest/lowest; "history" only by ema/rsi (0 for every other type); "shift" is 0 unless needed. "history" at most {MAX_HISTORY}.
- Conditions compare "left" with "right" using one of: {", ".join(OPERATORS)}. Both sides are an indicator name, a series name, or (right only) a numeric constant. Units must match: price-like terms (open/high/low/close, sma, ema, highest, lowest, lag of a price) compare with price-like terms or constants; rsi (0..100) and change_pct (fraction) compare with constants or with the same kind. crosses_above / crosses_below refer to the previous bar.
- "entry" and "exit" are each a group: "combine" is all_of (AND) or any_of (OR) over the direct "conditions" plus the "subgroups" (each a combine over its own conditions; leave "subgroups" empty when not needed). At most 16 conditions in total. Exit is evaluated before entry on the same close.
- Name: 1..{MAX_NAME} characters. Description: at most {MAX_DESCRIPTION} characters, plain language, no performance claims.

Not supported (never approximate silently; list each as an unsupported request with the matching code and keep the rest of the strategy): stop-loss / take-profit / trailing prices (stop_or_target_price_not_supported), position sizing or leverage inside the strategy (position_sizing_in_strategy_not_supported), short selling or hedging (direction_not_supported), intraday or weekly timeframes (timeframe_not_supported), indicators outside the list such as MACD, Bollinger bands, ATR, VWAP, volume profiles (unsupported_indicator), fundamentals, news, options, other assets' prices or market-wide filters (multi_asset_condition_not_supported), look-ahead references (lookahead_reference_not_supported), anything else (other_unsupported_request with a short detail). Where a supported approximation is reasonable and faithful (e.g. a 20-day channel breakout for "Donchian"), use it and say so in the note.

Revision requests: start from the given current specification, apply only the requested change, keep everything else identical, and describe the change in the note. If the request is impossible, keep the specification unchanged and explain in unsupported_requests.

The note is short (at most three sentences), factual, and never claims profitability.

Example output for "buy when the 50-day average is above the 200-day average and price is above the 200-day average; sell when price drops below the 50-day average":
{{"specification": {{"name": "Trend following 50/200", "description": "Long when the close is above the 200-day SMA and the 50-day SMA is above the 200-day SMA; exit when the close falls below the 50-day SMA.", "indicators": [{{"name": "sma_fast", "type": "sma", "source": "close", "window": 50, "history": 0, "periods": 0, "shift": 0}}, {{"name": "sma_slow", "type": "sma", "source": "close", "window": 200, "history": 0, "periods": 0, "shift": 0}}], "entry": {{"combine": "all_of", "conditions": [{{"left": "close", "op": "gt", "right": "sma_slow"}}, {{"left": "sma_fast", "op": "gt", "right": "sma_slow"}}], "subgroups": []}}, "exit": {{"combine": "any_of", "conditions": [{{"left": "close", "op": "lt", "right": "sma_fast"}}], "subgroups": []}}}}, "unsupported_requests": [], "note": "A classic moving-average trend filter; exit is evaluated before entry on the same close."}}
"""


# ---------------------------------------------------------------------------
# Output handling: JSON -> specification mapping -> validator -> YAML text
# ---------------------------------------------------------------------------


@dataclass
class ParsedOutput:
    raw_spec: dict[str, Any] | None
    unsupported_requests: list[dict[str, str]]
    note: str
    parse_errors: list[dict[str, str]] = field(default_factory=list)


def _condition(node: Any) -> Any:
    """Trim the constant/reference text; everything else passes through so the validator
    names what is wrong (unknown or missing keys, wrong types)."""

    if isinstance(node, dict):
        out = dict(node)
        right = out.get("right")
        if isinstance(right, str):
            right = right.strip()
            # Numeric text is a constant (the validator's own rule, applied here so the
            # rendered YAML reads ``right: 30`` rather than a quoted string).
            if _NUMBER.match(right):
                right = normalize_constant(Decimal(right))
            out["right"] = right
        return out
    return node


def _group(node: Any, path: str, errors: list[dict[str, str]]) -> Any:
    """``{combine, conditions, subgroups}`` -> the specification's ``{all_of|any_of: [...]}``.

    Structural only: nothing is defaulted, filtered or dropped. A bad ``combine`` or a
    non-object condition becomes a finding and the rest of the rule is kept, so a
    malformed output can never lose a condition silently. The older nested
    ``all_of``/``any_of`` shape is accepted unchanged.
    """

    if not isinstance(node, dict):
        return node
    if "combine" in node or "conditions" in node or "subgroups" in node:
        combine = node.get("combine")
        if combine not in ("all_of", "any_of"):
            errors.append(
                {
                    "code": "invalid_type",
                    "path": f"{path}.combine",
                    "message": "combine must be all_of or any_of",
                }
            )
            combine = "all_of"
        children: list[Any] = []
        conditions = node.get("conditions")
        if conditions is None:
            errors.append(
                {
                    "code": "missing_field",
                    "path": f"{path}.conditions",
                    "message": "conditions is required",
                }
            )
            conditions = []
        if not isinstance(conditions, list):
            errors.append(
                {
                    "code": "invalid_type",
                    "path": f"{path}.conditions",
                    "message": "conditions must be a list",
                }
            )
            conditions = []
        for index, condition in enumerate(conditions):
            if not isinstance(condition, dict):
                errors.append(
                    {
                        "code": "invalid_type",
                        "path": f"{path}.conditions[{index}]",
                        "message": "condition must be an object",
                    }
                )
                continue
            children.append(_condition(condition))
        subgroups = node.get("subgroups") or []
        if not isinstance(subgroups, list):
            errors.append(
                {
                    "code": "invalid_type",
                    "path": f"{path}.subgroups",
                    "message": "subgroups must be a list",
                }
            )
            subgroups = []
        for index, sub in enumerate(subgroups):
            if not isinstance(sub, dict):
                errors.append(
                    {
                        "code": "invalid_type",
                        "path": f"{path}.subgroups[{index}]",
                        "message": "subgroup must be an object",
                    }
                )
                continue
            children.append(_group(sub, f"{path}.subgroups[{index}]", errors))
        return {combine: children}
    if "left" in node or "op" in node or "right" in node:
        return _condition(node)
    out: dict[str, Any] = {}
    for key in ("all_of", "any_of"):
        value = node.get(key)
        if isinstance(value, list):
            out[key] = [
                _group(child, f"{path}.{key}[{i}]", errors) for i, child in enumerate(value)
            ]
    return out if out else node


def _normalize_unsupported(items: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "")
        detail = str(item.get("detail") or "").strip()
        if code not in UNSUPPORTED_CODES:
            detail = f"{code}: {detail}".strip(": ")
            code = SpecErrorCode.OTHER_UNSUPPORTED_REQUEST.value
        out.append({"code": code, "detail": detail})
    return out


def parse_output(text_out: str | None) -> ParsedOutput:
    """Turn the model's JSON into the canonical specification mapping (no validation yet)."""

    if not text_out or not text_out.strip():
        return ParsedOutput(
            None, [], "", [{"code": "invalid_type", "path": "", "message": "the output was empty"}]
        )
    try:
        payload = json.loads(text_out)
    except ValueError as exc:
        return ParsedOutput(
            None,
            [],
            "",
            [{"code": "invalid_type", "path": "", "message": f"the output is not JSON: {exc}"}],
        )
    if not isinstance(payload, dict):
        return ParsedOutput(
            None,
            [],
            "",
            [{"code": "invalid_type", "path": "", "message": "the output is not a JSON object"}],
        )
    unsupported = _normalize_unsupported(payload.get("unsupported_requests"))
    note = str(payload.get("note") or "").strip()
    spec = payload.get("specification")
    if not isinstance(spec, dict):
        return ParsedOutput(
            None,
            unsupported,
            note,
            [
                {
                    "code": "missing_field",
                    "path": "specification",
                    "message": "no specification object in the output",
                }
            ],
        )
    indicators: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    raw_indicators = spec.get("indicators")
    if isinstance(raw_indicators, list):
        for index, item in enumerate(raw_indicators):
            if not isinstance(item, dict):
                errors.append(
                    {
                        "code": "invalid_type",
                        "path": f"indicators[{index}]",
                        "message": "indicator must be an object",
                    }
                )
                continue
            name = str(item.get("name") or "")
            if name in indicators:
                errors.append(
                    {
                        "code": "name_invalid",
                        "path": f"indicators.{name}",
                        "message": "indicator name repeated",
                    }
                )
                continue
            entry: dict[str, Any] = {
                "type": item.get("type"),
                "source": item.get("source") or "close",
            }
            for key in ("window", "history", "periods"):
                if item.get(key) not in (None, 0):
                    entry[key] = item[key]
            if item.get("shift") not in (None, 0):
                entry["shift"] = item["shift"]
            indicators[name] = entry
    elif isinstance(raw_indicators, dict):
        indicators = dict(raw_indicators)
    raw_spec: dict[str, Any] = {
        "spec_version": 1,
        "name": spec.get("name"),
        "description": spec.get("description") or "",
        "timeframe": "daily",
        "direction": "long_only",
        "indicators": indicators,
        "entry": _group(spec.get("entry"), "entry", errors),
        "exit": _group(spec.get("exit"), "exit", errors),
    }
    return ParsedOutput(raw_spec, unsupported, note, errors)


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    dumped = yaml.safe_dump(value, allow_unicode=True, default_style=None, width=10_000)
    dumped = dumped.rstrip("\n")
    if dumped.endswith("\n..."):
        dumped = dumped[: -len("\n...")]
    return dumped


def _flow(mapping: Mapping[str, Any]) -> str:
    return "{" + ", ".join(f"{key}: {_scalar(value)}" for key, value in mapping.items()) + "}"


def _render_node(node: Any, indent: int, out: list[str]) -> None:
    pad = " " * indent
    if isinstance(node, dict) and "left" in node:
        out.append(f"{pad}- {_flow(node)}")
        return
    if isinstance(node, dict):
        for key in ("all_of", "any_of"):
            if key in node:
                out.append(f"{pad}- {key}:")
                for child in node[key]:
                    _render_node(child, indent + 4, out)
                return
    out.append(f"{pad}- {_scalar(node)}")


def _render_group(name: str, node: Any, out: list[str]) -> None:
    out.append(f"{name}:")
    if isinstance(node, dict) and "left" in node:
        out.append(f"  {_flow(node)}")
        return
    if isinstance(node, dict):
        for key in ("all_of", "any_of"):
            if key in node:
                out.append(f"  {key}:")
                for child in node[key]:
                    _render_node(child, 4, out)
                return
    out.append(f"  {_scalar(node)}")


def render_spec_yaml(raw: Mapping[str, Any]) -> str:
    """Deterministic YAML in the editor's house style (flow-style leaves, block groups)."""

    out: list[str] = [
        f"spec_version: {_scalar(raw.get('spec_version', 1))}",
        f"name: {_scalar(raw.get('name') or '')}",
    ]
    description = raw.get("description") or ""
    out.append(f"description: {_scalar(description)}")
    out.append(f"timeframe: {_scalar(raw.get('timeframe', 'daily'))}")
    out.append(f"direction: {_scalar(raw.get('direction', 'long_only'))}")
    indicators = raw.get("indicators") or {}
    if indicators:
        out.append("indicators:")
        for name, spec in indicators.items():
            out.append(f"  {name}: {_flow(spec) if isinstance(spec, Mapping) else _scalar(spec)}")
    else:
        out.append("indicators: {}")
    _render_group("entry", raw.get("entry"), out)
    _render_group("exit", raw.get("exit"), out)
    return "\n".join(out) + "\n"


def evaluate_output(text_out: str | None) -> tuple[ParsedOutput, str | None, ValidationOutcome]:
    """Parse, render to YAML and run the one shared validator on that YAML text."""

    parsed = parse_output(text_out)
    if parsed.raw_spec is None:
        return parsed, None, ValidationOutcome(False, list(parsed.parse_errors), None, None)
    yaml_text = render_spec_yaml(parsed.raw_spec)
    outcome = validate_yaml_text(yaml_text)
    if parsed.parse_errors:
        outcome = ValidationOutcome(
            False,
            parsed.parse_errors + outcome.errors,
            outcome.derived,
            outcome.explanation,
            outcome.compiled,
        )
    return parsed, yaml_text, outcome


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def proposal_to_dict(row: AiDraft) -> dict[str, Any]:
    validation = dict(row.validation_result or {})
    return {
        "ai_draft_id": str(row.id),
        "kind": row.kind,
        "status": row.status,
        "failure_code": row.failure_code,
        "attempt_no": row.attempt_no,
        "retry_of_ai_draft_id": str(row.retry_of_ai_draft_id) if row.retry_of_ai_draft_id else None,
        "parent_ai_draft_id": str(row.parent_ai_draft_id) if row.parent_ai_draft_id else None,
        "draft_id": str(row.draft_id) if row.draft_id else None,
        "request_token": row.request_token,
        "user_text": row.user_text,
        "base_yaml_text": row.base_yaml_text,
        "yaml_text": row.output_yaml_text,
        "note": row.note,
        "unsupported_requests": list(row.unsupported_requests or []),
        "validation": validation if validation and "valid" in validation else None,
        "provider_error": validation.get("provider_error") if validation else None,
        "explanation": validation.get("explanation") if validation else None,
        "spec_sha256": row.output_spec_sha256,
        "provenance": {
            "provider": row.provider,
            "model": row.model,
            "prompt_version": row.prompt_version,
            "request_id": row.request_id,
            "stop_reason": row.stop_reason,
            "input_tokens": row.input_tokens,
            "output_tokens": row.output_tokens,
            "cache_read_input_tokens": row.cache_read_input_tokens,
            "cache_creation_input_tokens": row.cache_creation_input_tokens,
            "deadline_seconds": float(row.deadline_seconds)
            if row.deadline_seconds is not None
            else None,
            "started_at": _iso(row.started_at),
            "completed_at": _iso(row.completed_at),
        },
        "applied_at": _iso(row.applied_at),
        "created_at": _iso(row.created_at),
    }


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ResearchAssistantService:
    """Bounded, accounted, recorded assistant requests on the research database."""

    def __init__(
        self,
        settings: Settings,
        *,
        provider: AssistantProvider | None = None,
        provider_factory: Callable[[Settings], AssistantProvider] | None = None,
        clock: Callable[[], datetime] | None = None,
        process_id: str | None = None,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._provider_factory = provider_factory or (lambda s: AnthropicProvider(s))
        self._clock = clock or (lambda: datetime.now(UTC))
        self._process_id = process_id or default_process_id()

    # -- configuration -------------------------------------------------------

    def missing_configuration(self) -> list[str]:
        ai = self._settings.research.ai
        missing: list[str] = []
        if not ai.enabled:
            missing.append("TRADING_PLATFORM_RESEARCH__AI__ENABLED=true")
        if not ai.api_key:
            missing.append("ANTHROPIC_API_KEY (or TRADING_PLATFORM_RESEARCH__AI__API_KEY)")
        if ai.max_requests_per_day <= 0:
            missing.append("TRADING_PLATFORM_RESEARCH__AI__MAX_REQUESTS_PER_DAY > 0")
        if ai.max_output_tokens <= 0:
            missing.append("TRADING_PLATFORM_RESEARCH__AI__MAX_OUTPUT_TOKENS > 0")
        return missing

    def status(self, *, draft_id: uuid.UUID | None = None) -> dict[str, Any]:
        ai = self._settings.research.ai
        now = self._clock()
        with session_scope(self._settings) as session:
            counts = self._counts(session, now)
            revisions_used = self._revisions_used(session, draft_id) if draft_id else None
        remaining = (
            max(0, ai.max_requests_per_day - counts["day"]) if ai.max_requests_per_day > 0 else 0
        )
        return {
            "enabled": ai.enabled,
            "configured": ai.usable,
            "missing": self.missing_configuration(),
            "provider": ai.provider,
            "model": ai.model,
            "prompt_version": PROMPT_VERSION,
            "output_schema_sha256": OUTPUT_SCHEMA_SHA256,
            "limits": {
                "max_requests_per_day": ai.max_requests_per_day,
                "max_output_tokens": ai.max_output_tokens,
                "max_input_characters": ai.max_input_characters,
                "max_revisions_per_draft": ai.max_revisions_per_draft,
                "max_concurrent_requests": ai.max_concurrent_requests,
                "timeout_seconds": ai.timeout_seconds,
                "automatic_retries_on_invalid_output": MAX_ATTEMPTS - 1,
            },
            "usage": {
                "requests_today": counts["day"],
                "remaining_today": remaining,
                "in_flight": counts["in_flight"],
                "revisions_used": revisions_used,
            },
            "note": PROVIDER_SPEND_NOTE,
        }

    def _require_usable(self) -> None:
        ai = self._settings.research.ai
        if not ai.enabled:
            raise AssistantError(
                CODE_DISABLED, "the assistant is disabled", missing=self.missing_configuration()
            )
        if not ai.usable:
            raise AssistantError(
                CODE_NOT_CONFIGURED,
                "the assistant is enabled but not configured",
                missing=self.missing_configuration(),
            )

    # -- accounting (shared ledger, provider advisory lock) ------------------

    def _in_flight_cutoff(self, now: datetime) -> datetime:
        return now - timedelta(seconds=self._settings.research.ai.timeout_seconds) - IN_FLIGHT_GRACE

    def _counts(self, session: Session, now: datetime) -> dict[str, int]:
        base = (
            select(func.count())
            .select_from(ProviderRequestLedgerEntry)
            .where(
                ProviderRequestLedgerEntry.provider == PROVIDER,
                ProviderRequestLedgerEntry.purpose == PURPOSE_ASSISTANT,
            )
        )
        day = session.execute(
            base.where(ProviderRequestLedgerEntry.attempted_at > now - timedelta(days=1))
        ).scalar_one()
        in_flight = session.execute(
            base.where(
                ProviderRequestLedgerEntry.outcome.is_(None),
                ProviderRequestLedgerEntry.attempted_at > self._in_flight_cutoff(now),
            )
        ).scalar_one()
        return {"day": int(day), "in_flight": int(in_flight)}

    def _admit(self, now: datetime) -> uuid.UUID:
        """One ledger row per attempt, admitted under the provider lock across processes."""

        ai = self._settings.research.ai
        with session_scope(self._settings) as session:
            session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": provider_lock_key(PROVIDER)}
            )
            counts = self._counts(session, now)
            if counts["day"] >= ai.max_requests_per_day:
                raise AssistantError(
                    CODE_DAILY_LIMIT,
                    "the daily assistant request limit is reached",
                    limit=ai.max_requests_per_day,
                    used=counts["day"],
                )
            if counts["in_flight"] >= ai.max_concurrent_requests:
                raise AssistantError(
                    CODE_BUSY,
                    "another assistant request is in flight",
                    limit=ai.max_concurrent_requests,
                    in_flight=counts["in_flight"],
                )
            entry = ProviderRequestLedgerEntry(
                id=uuid.uuid4(),
                provider=PROVIDER,
                symbol=None,
                purpose=PURPOSE_ASSISTANT,
                attempted_at=now,
                process_id=self._process_id,
                job_id=None,
            )
            session.add(entry)
            session.flush()
            return entry.id

    def _complete_ledger(
        self, entry_id: uuid.UUID, *, outcome: str, status_code: int | None
    ) -> None:
        with session_scope(self._settings) as session:
            entry = session.get(ProviderRequestLedgerEntry, entry_id)
            if entry is not None:
                entry.outcome = outcome
                entry.status_code = status_code
                entry.completed_at = self._clock()

    def _revisions_used(self, session: Session, draft_id: uuid.UUID) -> int:
        return int(
            session.execute(
                select(func.count())
                .select_from(AiDraft)
                .where(
                    AiDraft.draft_id == draft_id,
                    AiDraft.kind == KIND_REVISION,
                    AiDraft.attempt_no == 1,
                )
            ).scalar_one()
        )

    def _chain_depth(self, session: Session, ai_draft_id: uuid.UUID | None) -> int:
        depth = 0
        seen: set[uuid.UUID] = set()
        current = ai_draft_id
        while (
            current is not None
            and current not in seen
            and depth <= self._settings.research.ai.max_revisions_per_draft + 1
        ):
            seen.add(current)
            row = session.get(AiDraft, current)
            if row is None:
                break
            depth += 1
            current = row.parent_ai_draft_id
        return depth

    # -- requests ------------------------------------------------------------

    def propose(
        self,
        *,
        user_text: str,
        draft_id: uuid.UUID | None = None,
        base_yaml_text: str | None = None,
        parent_ai_draft_id: uuid.UUID | None = None,
        request_token: str | None = None,
    ) -> dict[str, Any]:
        """Draft (no base) or revise (a draft and/or base YAML) one specification."""

        self._require_usable()
        ai = self._settings.research.ai
        if not isinstance(user_text, str) or not user_text.strip():
            raise AssistantError(
                CODE_INVALID_INPUT,
                "user_text is required",
                field="user_text",
                reason="must be non-empty text",
            )
        if request_token is not None and (
            not isinstance(request_token, str) or not 1 <= len(request_token) <= 64
        ):
            raise AssistantError(
                CODE_INVALID_INPUT,
                "request_token is malformed",
                field="request_token",
                reason="must be 1..64 characters",
            )
        if base_yaml_text is not None and not isinstance(base_yaml_text, str):
            raise AssistantError(
                CODE_INVALID_INPUT,
                "base_yaml_text must be text",
                field="base_yaml_text",
                reason="must be text",
            )

        with session_scope(self._settings) as session:
            if request_token:
                existing = session.execute(
                    select(AiDraft).where(AiDraft.request_token == request_token)
                ).scalar_one_or_none()
                if existing is not None:
                    return self._finish_existing(existing)
            draft: StrategyDraft | None = None
            if draft_id is not None:
                draft = session.get(StrategyDraft, draft_id)
                if draft is None:
                    raise DraftNotFoundError(draft_id)
                if base_yaml_text is None:
                    base_yaml_text = draft.yaml_text
                if parent_ai_draft_id is None:
                    # A revision of a draft continues that draft's provenance chain.
                    parent_ai_draft_id = draft.ai_draft_id
                if self._revisions_used(session, draft_id) >= ai.max_revisions_per_draft:
                    raise AssistantError(
                        CODE_REVISION_LIMIT,
                        "the revision limit of this draft is reached",
                        limit=ai.max_revisions_per_draft,
                        draft_id=str(draft_id),
                    )
            elif (
                parent_ai_draft_id is not None
                and self._chain_depth(session, parent_ai_draft_id) >= ai.max_revisions_per_draft
            ):
                raise AssistantError(
                    CODE_REVISION_LIMIT,
                    "the revision limit of this proposal chain is reached",
                    limit=ai.max_revisions_per_draft,
                )
            if parent_ai_draft_id is not None and session.get(AiDraft, parent_ai_draft_id) is None:
                raise AiDraftNotFoundError(parent_ai_draft_id)

        kind = KIND_REVISION if base_yaml_text else KIND_DRAFT
        total_chars = len(user_text) + len(base_yaml_text or "")
        if total_chars > ai.max_input_characters:
            raise AssistantError(
                CODE_INPUT_TOO_LONG,
                "the request is longer than the configured input limit",
                limit=ai.max_input_characters,
                length=total_chars,
            )

        messages: list[dict[str, Any]] = [
            {"role": "user", "content": self._user_message(kind, user_text, base_yaml_text)}
        ]
        provider = self._provider or self._provider_factory(self._settings)
        started = time.monotonic()
        deadline = ai.timeout_seconds
        retry_of: uuid.UUID | None = None
        last_row_id: uuid.UUID | None = None

        for attempt_no in range(1, MAX_ATTEMPTS + 1):
            remaining = deadline - (time.monotonic() - started)
            if remaining < MIN_ATTEMPT_SECONDS:
                raise AssistantError(
                    CODE_TIMEOUT,
                    "the request deadline passed before the attempt could start",
                    ai_draft_id=str(last_row_id) if last_row_id else None,
                    deadline_seconds=deadline,
                )
            now = self._clock()
            ledger_id = self._admit(now)
            row_id = self._open_row(
                kind=kind,
                attempt_no=attempt_no,
                user_text=user_text,
                base_yaml_text=base_yaml_text,
                draft_id=draft_id,
                parent_ai_draft_id=parent_ai_draft_id,
                retry_of=retry_of,
                request_token=request_token if attempt_no == 1 else None,
                ledger_id=ledger_id,
                started_at=now,
                deadline_seconds=remaining,
            )
            last_row_id = row_id
            try:
                result = provider.complete(
                    model=ai.model,
                    system_text=SYSTEM_PROMPT,
                    messages=messages,
                    schema=OUTPUT_SCHEMA,
                    max_output_tokens=ai.max_output_tokens,
                    timeout_seconds=remaining,
                )
            except ProviderFailure as failure:
                status_ = _STATUS_FOR_CODE[failure.code]
                self._close_row(
                    row_id,
                    status=status_,
                    failure_code=failure.code,
                    request_id=failure.request_id,
                    provider_message=failure.provider_message,
                )
                self._complete_ledger(
                    ledger_id,
                    outcome=_LEDGER_OUTCOME_FOR_STATUS[status_],
                    status_code=failure.status_code,
                )
                logger.warning(
                    "assistant_provider_failure",
                    extra={
                        "context": {
                            "code": failure.code,
                            "status_code": failure.status_code,
                            "provider_message": failure.provider_message,
                            "attempt": attempt_no,
                        }
                    },
                )
                failure_detail: dict[str, Any] = {
                    "ai_draft_id": str(row_id),
                    "provider_status": failure.status_code,
                    "provider_message": failure.provider_message,
                }
                if failure.code == CODE_TIMEOUT:
                    failure_detail["deadline_seconds"] = deadline
                raise AssistantError(
                    failure.code,
                    str(failure),
                    **failure_detail,
                ) from failure
            except Exception as exc:  # the SDK raised something outside its documented set
                self._close_row(
                    row_id,
                    status=STATUS_PROVIDER_ERROR,
                    failure_code=CODE_PROVIDER_ERROR,
                    request_id=None,
                )
                self._complete_ledger(ledger_id, outcome="http_error", status_code=None)
                logger.error(
                    "assistant_unexpected_failure",
                    extra={"context": {"error_type": type(exc).__name__, "attempt": attempt_no}},
                )
                raise AssistantError(
                    CODE_PROVIDER_ERROR,
                    f"unexpected provider failure: {type(exc).__name__}",
                    ai_draft_id=str(row_id),
                ) from exc

            if result.stop_reason == "refusal":
                self._close_row(
                    row_id, status=STATUS_REFUSED, failure_code=CODE_REFUSED, result=result
                )
                self._complete_ledger(ledger_id, outcome="refused", status_code=200)
                raise AssistantError(
                    CODE_REFUSED,
                    "the provider declined to answer this request",
                    ai_draft_id=str(row_id),
                )
            if result.stop_reason == "max_tokens":
                self._close_row(
                    row_id,
                    status=STATUS_TRUNCATED,
                    failure_code=CODE_OUTPUT_TRUNCATED,
                    result=result,
                )
                self._complete_ledger(ledger_id, outcome="ok", status_code=200)
                raise AssistantError(
                    CODE_OUTPUT_TRUNCATED,
                    "the output hit the configured output-token limit",
                    ai_draft_id=str(row_id),
                    limit=ai.max_output_tokens,
                )

            parsed, yaml_text, outcome = evaluate_output(result.text)
            self._complete_ledger(ledger_id, outcome="ok", status_code=200)
            if outcome.valid:
                self._close_row(
                    row_id,
                    status=STATUS_OK,
                    failure_code=None,
                    result=result,
                    parsed=parsed,
                    yaml_text=yaml_text,
                    outcome=outcome,
                )
                return self.get_proposal(row_id)
            self._close_row(
                row_id,
                status=STATUS_INVALID,
                failure_code=CODE_OUTPUT_INVALID,
                result=result,
                parsed=parsed,
                yaml_text=yaml_text,
                outcome=outcome,
            )
            if attempt_no == MAX_ATTEMPTS:
                return self.get_proposal(row_id)
            retry_of = row_id
            messages = messages + [
                {"role": "assistant", "content": result.text or ""},
                {"role": "user", "content": self._retry_message(outcome)},
            ]
        raise AssertionError("unreachable")

    def _finish_existing(self, row: AiDraft) -> dict[str, Any]:
        """Idempotent replay for a repeated request token: the latest attempt of that request."""

        with session_scope(self._settings) as session:
            current = row
            while True:
                nxt = session.execute(
                    select(AiDraft).where(AiDraft.retry_of_ai_draft_id == current.id)
                ).scalar_one_or_none()
                if nxt is None:
                    break
                current = nxt
            if current.status == STATUS_PENDING:
                raise AssistantError(
                    CODE_BUSY,
                    "this request is still in flight",
                    ai_draft_id=str(current.id),
                    request_token=row.request_token,
                )
            if current.failure_code and current.status not in (STATUS_OK, STATUS_INVALID):
                raise AssistantError(
                    current.failure_code,
                    f"this request ended with {current.status}",
                    ai_draft_id=str(current.id),
                    replayed=True,
                )
            return proposal_to_dict(current)

    @staticmethod
    def _user_message(kind: str, user_text: str, base_yaml_text: str | None) -> str:
        if kind == KIND_REVISION:
            return (
                "Current specification (YAML):\n```yaml\n"
                + (base_yaml_text or "").rstrip()
                + "\n```\n\n"
                "Revision request: " + user_text.strip()
            )
        return "Strategy request: " + user_text.strip()

    @staticmethod
    def _retry_message(outcome: ValidationOutcome) -> str:
        findings = "\n".join(
            f"- {e['code']} at '{e['path']}': {e['message']}" for e in outcome.errors
        )
        return (
            "That specification failed the deterministic validator with these findings:\n"
            f"{findings}\n\nReturn a corrected specification that resolves every finding without changing the intent. "
            "If a finding cannot be resolved inside the supported features, list it under unsupported_requests."
        )

    def _open_row(
        self,
        *,
        kind: str,
        attempt_no: int,
        user_text: str,
        base_yaml_text: str | None,
        draft_id: uuid.UUID | None,
        parent_ai_draft_id: uuid.UUID | None,
        retry_of: uuid.UUID | None,
        request_token: str | None,
        ledger_id: uuid.UUID,
        started_at: datetime,
        deadline_seconds: float,
    ) -> uuid.UUID:
        ai = self._settings.research.ai
        with session_scope(self._settings) as session:
            row = AiDraft(
                id=uuid.uuid4(),
                provider=ai.provider,
                model=ai.model,
                prompt_version=PROMPT_VERSION,
                user_text=user_text[:MAX_USER_TEXT_STORED],
                validation_result={},
                kind=kind,
                status=STATUS_PENDING,
                attempt_no=attempt_no,
                request_token=request_token,
                draft_id=draft_id,
                parent_ai_draft_id=parent_ai_draft_id,
                retry_of_ai_draft_id=retry_of,
                ledger_entry_id=ledger_id,
                base_yaml_text=base_yaml_text,
                unsupported_requests=[],
                deadline_seconds=Decimal(f"{deadline_seconds:.2f}"),
                started_at=started_at,
            )
            session.add(row)
            session.flush()
            return row.id

    def _close_row(
        self,
        row_id: uuid.UUID,
        *,
        status: str,
        failure_code: str | None,
        request_id: str | None = None,
        result: ProviderResult | None = None,
        parsed: ParsedOutput | None = None,
        yaml_text: str | None = None,
        outcome: ValidationOutcome | None = None,
        provider_message: str | None = None,
    ) -> None:
        with session_scope(self._settings) as session:
            row = session.get(AiDraft, row_id)
            if row is None:
                return
            row.status = status
            row.failure_code = failure_code
            row.completed_at = self._clock()
            if result is not None:
                row.request_id = result.request_id
                row.model = result.model or row.model
                row.stop_reason = result.stop_reason
                row.input_tokens = result.input_tokens
                row.output_tokens = result.output_tokens
                row.cache_read_input_tokens = result.cache_read_input_tokens
                row.cache_creation_input_tokens = result.cache_creation_input_tokens
            elif request_id:
                row.request_id = request_id
            if provider_message:
                row.validation_result = {"provider_error": provider_message}
            if parsed is not None:
                row.note = parsed.note or None
                row.unsupported_requests = list(parsed.unsupported_requests)
            if yaml_text is not None:
                row.output_yaml_text = yaml_text
            if outcome is not None:
                row.validation_result = outcome.to_dict()
                if outcome.derived:
                    row.output_spec_sha256 = outcome.derived.get("spec_sha256")
                elif yaml_text is not None:
                    row.output_spec_sha256 = hashlib.sha256(yaml_text.encode()).hexdigest()
            session.flush()

    # -- reads and apply -----------------------------------------------------

    def get_proposal(self, ai_draft_id: uuid.UUID) -> dict[str, Any]:
        with session_scope(self._settings) as session:
            row = session.get(AiDraft, ai_draft_id)
            if row is None:
                raise AiDraftNotFoundError(ai_draft_id)
            return proposal_to_dict(row)

    def apply(
        self, ai_draft_id: uuid.UUID, *, draft_id: uuid.UUID | None = None, title: str | None = None
    ) -> dict[str, Any]:
        """Write the proposal's YAML into a draft (new or the given one). The previous draft
        text is untouched until this explicit action; repeating it is a no-op."""

        if title is not None and (
            not isinstance(title, str) or not 1 <= len(title.strip()) <= MAX_TITLE_LENGTH
        ):
            raise AssistantError(
                CODE_INVALID_INPUT,
                "title is malformed",
                field="title",
                reason=f"must be 1..{MAX_TITLE_LENGTH} characters",
            )
        with session_scope(self._settings) as session:
            row = session.get(AiDraft, ai_draft_id)
            if row is None:
                raise AiDraftNotFoundError(ai_draft_id)
            if row.output_yaml_text is None:
                raise AssistantError(
                    CODE_NOT_APPLICABLE,
                    f"this proposal ended with {row.status} and has no specification",
                    ai_draft_id=str(ai_draft_id),
                    status=row.status,
                )
            now = self._clock()
            target_id = draft_id or row.draft_id
            if target_id is not None:
                draft = session.execute(
                    select(StrategyDraft).where(StrategyDraft.id == target_id).with_for_update()
                ).scalar_one_or_none()
                if draft is None:
                    raise DraftNotFoundError(target_id)
                already = draft.ai_draft_id == row.id and draft.yaml_text == row.output_yaml_text
                if not already:
                    draft.yaml_text = row.output_yaml_text
                    draft.ai_draft_id = row.id
                    if draft.source == SOURCE_MANUAL:
                        draft.source = SOURCE_ASSISTANT
                    if title is not None:
                        draft.title = title.strip()
                    draft.updated_at = now
            else:
                name = (row.validation_result or {}).get("derived", {}) or {}
                draft = StrategyDraft(
                    id=uuid.uuid4(),
                    title=(title or name.get("name") or "Assistant draft").strip()[
                        :MAX_TITLE_LENGTH
                    ],
                    yaml_text=row.output_yaml_text,
                    source=SOURCE_ASSISTANT,
                    ai_draft_id=row.id,
                )
                session.add(draft)
                session.flush()  # the draft row must exist before the provenance row points at it
                already = False
            if row.applied_at is None:
                row.applied_at = now
            if row.draft_id is None:
                row.draft_id = draft.id
            session.flush()
            session.refresh(draft)
            return {
                "draft": draft_to_dict(draft),
                "proposal": proposal_to_dict(row),
                "already_applied": already,
            }


# ---------------------------------------------------------------------------
# Provenance carried into approved versions
# ---------------------------------------------------------------------------


def assistant_provenance(
    session: Session, ai_draft_id: uuid.UUID | None, *, approved: bool = False
) -> dict[str, Any] | None:
    """The chain of assistant attempts behind a draft or version, newest first."""

    if ai_draft_id is None:
        return None
    attempts: list[dict[str, Any]] = []
    seen: set[uuid.UUID] = set()
    current: uuid.UUID | None = ai_draft_id
    while current is not None and current not in seen and len(attempts) < 64:
        seen.add(current)
        row = session.get(AiDraft, current)
        if row is None:
            break
        attempts.append(
            {
                "ai_draft_id": str(row.id),
                "kind": row.kind,
                "attempt_no": row.attempt_no,
                "status": row.status,
                "failure_code": row.failure_code,
                "provider": row.provider,
                "model": row.model,
                "prompt_version": row.prompt_version,
                "request_id": row.request_id,
                "input_tokens": row.input_tokens,
                "output_tokens": row.output_tokens,
                "spec_sha256": row.output_spec_sha256,
                "user_text": row.user_text,
                "note": row.note,
                "unsupported_requests": list(row.unsupported_requests or []),
                "started_at": _iso(row.started_at),
                "completed_at": _iso(row.completed_at),
                "applied_at": _iso(row.applied_at),
            }
        )
        current = row.retry_of_ai_draft_id or row.parent_ai_draft_id
    if not attempts:
        return None
    requests = [a for a in attempts if a["attempt_no"] == 1]
    revisions = sum(1 for a in requests if a["kind"] == KIND_REVISION)
    return {
        "summary": (
            f"drafted by assistant, revised {revisions} time(s), "
            + ("approved by user" if approved else "not yet approved")
        ),
        "requests": len(requests),
        "revisions": revisions,
        "attempts": attempts,
    }


def version_provenance(session: Session, version: StrategyVersion) -> dict[str, Any] | None:
    return assistant_provenance(session, version.ai_draft_id, approved=True)
