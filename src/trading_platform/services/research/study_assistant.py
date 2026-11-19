"""Study proposal assistant (a second contract on the S5 assistant infrastructure).

Turns a free-text request ("test my two trend strategies on AAPL and MSFT from 2015 to 2024,
5 bps slippage") into a *proposal* of Study settings. It reuses the assistant's provider,
configuration gate, shared usage ledger (daily and concurrency caps, advisory lock), closed
error codes and the two-attempt deadline loop; it persists nothing but the ledger entries
(no ``ai_drafts`` rows, no migration). A human reviews the proposal and applies it in the
console; the assistant creates no Study, runs nothing and approves nothing.

Rules enforced here, deterministically and after the provider answered:

* the output must satisfy :class:`StudyProposalOutput` (types, ISO dates, numeric text,
  split sums, window ordering and containment, the same ticker normalisation as
  ``StudySettings``); one automatic retry lists the findings, then ``ai_output_invalid``;
* strategy ids are resolved against the approved versions only (by id, then by family name);
  anything else is reported as unresolved, never invented;
* tickers are checked against the asset catalog (``in_catalog``); presence never proves
  usable coverage, readiness checks the bars.

Numbers travel as strings and empty string means "not given": the structured-output format
allows no union types, and a missing cost or constraint must stay missing rather than
becoming a default.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from trading_platform.services.research.asset_lists import AssetCatalogService
from trading_platform.services.research.assistant import (
    _LEDGER_OUTCOME_FOR_STATUS,
    _STATUS_FOR_CODE,
    CODE_INPUT_TOO_LONG,
    CODE_INVALID_INPUT,
    CODE_OUTPUT_INVALID,
    CODE_OUTPUT_TRUNCATED,
    CODE_PROVIDER_ERROR,
    CODE_REFUSED,
    CODE_TIMEOUT,
    MAX_ATTEMPTS,
    MIN_ATTEMPT_SECONDS,
    AssistantError,
    ProviderFailure,
    ProviderResult,
    ResearchAssistantService,
)
from trading_platform.services.research.strategies import ResearchStrategyService

logger = logging.getLogger(__name__)

#: Version of the provider-facing contract (system prompt AND output schema together).
STUDY_PROMPT_VERSION = "study-v1"

MAX_PROMPT_VERSION_LINES = 300
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DECIMAL = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$")
_WINDOW_ROLES = ("development", "validation", "final_test")


# ---------------------------------------------------------------------------
# Output format (union-free; numbers as strings, "" = not given)
# ---------------------------------------------------------------------------


def _obj(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def _text(description: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "string"}
    if description:
        out["description"] = description
    return out


def _window_schema() -> dict[str, Any]:
    return _obj(
        {
            "start": _text('YYYY-MM-DD, or "" when the user did not state the windows'),
            "end": _text('YYYY-MM-DD, or "" when the user did not state the windows'),
        }
    )


def study_output_schema() -> dict[str, Any]:
    return _obj(
        {
            "summary": _text("one to three sentences: what you understood the user to ask for"),
            "missing": {
                "type": "array",
                "items": _obj(
                    {
                        "field": _text("the setting the user did not give"),
                        "detail": _text("what is needed, in a short phrase"),
                    }
                ),
            },
            "unsupported_requests": {
                "type": "array",
                "items": _obj(
                    {
                        "code": _text("short snake_case label for the unsupported thing"),
                        "detail": _text("what was asked and why a Study cannot express it"),
                    }
                ),
            },
            "name": _text('a short Study name, or "" when none can be inferred'),
            "strategies": {
                "type": "array",
                "items": _obj(
                    {
                        "reference": _text("the user's own words for the strategy"),
                        "version_id": _text(
                            'copied verbatim from the approved list, or "" when nothing matches'
                        ),
                    }
                ),
            },
            "assets": {"type": "array", "items": {"type": "string"}},
            "range_start": _text('YYYY-MM-DD or ""'),
            "range_end": _text('YYYY-MM-DD or ""'),
            "split": _obj(
                {
                    "development_pct": {"type": "integer"},
                    "validation_pct": {"type": "integer"},
                    "final_test_pct": {"type": "integer"},
                }
            ),
            "windows": _obj({role: _window_schema() for role in _WINDOW_ROLES}),
            "initial_capital": _text('decimal text or ""'),
            "quantity_policy": {"type": "string", "enum": ["", "fractional", "whole_shares"]},
            "slippage_bps": _text('decimal text or ""'),
            "commission_per_order": _text('decimal text or ""'),
            "objective": {"type": "string", "enum": ["", "return_first", "risk_first"]},
            "constraint_value": _text('decimal text or ""'),
            "note": _text("short factual note for the reviewer, or an empty string"),
            "kind": {"type": "string", "enum": ["", "substantive", "smoke"]},
        }
    )


STUDY_OUTPUT_SCHEMA = study_output_schema()


STUDY_SYSTEM_PROMPT = """You propose Study settings for a strategy research platform. A Study tests approved strategy versions on a list of assets over a date range split into development, validation and final-test windows. You only propose: a human reviews your proposal and applies it, and nothing is created, run or approved by you. You have no tools, no market data and no results. Never claim or predict performance.

Output: one JSON object (the response format enforces the shape). Numbers are written as text; an empty string means "not given".

Rules:
- Never invent anything the user did not say. In particular never invent: slippage_bps, commission_per_order, constraint_value, initial_capital, an objective, dates, or a strategy id. Leave those fields "" (or the split all 0, the windows empty) and list each under "missing" with a short detail. A mentioned-but-unspecified item (for example "with realistic costs") is missing, not a number you choose.
- "strategies": one entry per strategy the user refers to, "reference" being the user's own words. "version_id" must be copied verbatim from the approved list in the request, or "" when no listed strategy matches. Prefer the version the user names; when only a name is given and several versions exist, use the one marked latest. Never fabricate a version_id.
- "assets": ticker symbols, upper-case (BRK-B style for class shares). Only tickers the user named or unambiguously identified. Respect the maximum number of assets stated in the request; if the user asked for more, keep the first ones and list the rest under "unsupported_requests".
- Dates are ISO calendar dates (YYYY-MM-DD) and inclusive on both ends. "range_start"/"range_end" is the whole date range. Windows must lie inside the range, be ordered strictly development < validation < final_test (development.end before validation.start, validation.end before final_test.start) and must not overlap.
- If the user gives only a range, leave "windows" empty (all six dates "") and "split" all 0 unless they state percentages. Do not choose a split yourself: the console applies its 60/20/20 preset as a convenience, not as a methodology. If the user states percentages, put them in "split" (three integers summing to 100) and leave "windows" empty unless they also give explicit dates. If the user gives explicit window dates, fill all six dates.
- The initial run covers the development and validation windows; the final test is a separate action taken after the candidate is frozen. Do not schedule or propose a final-test run.
- "objective": "return_first" ranks by net return and pairs with a maximum-drawdown cap; "risk_first" ranks by drawdown and pairs with a CAGR floor. "constraint_value" is that cap or floor as a decimal fraction written as text: a 25 percent drawdown cap is "0.25" (positive), a 5 percent CAGR floor is "0.05". Leave both "" if the user did not state them.
- "quantity_policy": "fractional" or "whole_shares" only when stated, otherwise "".
- "kind": "smoke" when the user says this is an integration or pipeline test of the platform, otherwise "substantive" when the intent is a real research question, or "" when unclear.
- Anything a Study cannot express (short selling, intraday data, options, stop-loss inside a Study, per-trade sizing rules, portfolio-level combined runs, fundamentals) goes under "unsupported_requests" with a short snake_case "code" and a "detail"; keep the rest of the proposal.
- "summary" is one to three plain sentences describing what you understood. "note" is optional, short and factual, with no performance claims.

Revision requests include the current proposal as JSON: start from it, apply only the requested change, keep every other field identical (including its missing list unless the change resolves an item), and describe the change in "summary".
"""


# ---------------------------------------------------------------------------
# Validation of the provider's output
# ---------------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class MissingItem(_Strict):
    field: str
    detail: str


class UnsupportedItem(_Strict):
    code: str
    detail: str


class StrategyRef(_Strict):
    reference: str
    version_id: str


class Split(_Strict):
    development_pct: int
    validation_pct: int
    final_test_pct: int


class WindowText(_Strict):
    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def _iso(cls, value: str) -> str:
        return _check_date(value)


class WindowsText(_Strict):
    development: WindowText
    validation: WindowText
    final_test: WindowText


def _check_date(value: str) -> str:
    value = value.strip()
    if value == "":
        return ""
    if not _ISO_DATE.match(value):
        raise ValueError("must be an ISO date YYYY-MM-DD or empty")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("is not a real calendar date") from exc
    return value


def _check_decimal(value: str, *, positive: bool = False, non_negative: bool = False) -> str:
    value = value.strip()
    if value == "":
        return ""
    if not _DECIMAL.match(value):
        raise ValueError("must be decimal text or empty")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:  # pragma: no cover - the regex already excludes this
        raise ValueError("must be decimal text or empty") from exc
    if not number.is_finite():
        raise ValueError("must be a finite number")
    if positive and number <= 0:
        raise ValueError("must be greater than 0")
    if non_negative and number < 0:
        raise ValueError("must be 0 or greater")
    return value


class StudyProposalOutput(_Strict):
    summary: str
    missing: list[MissingItem]
    unsupported_requests: list[UnsupportedItem]
    name: str
    strategies: list[StrategyRef]
    assets: list[str]
    range_start: str
    range_end: str
    split: Split
    windows: WindowsText
    initial_capital: str
    quantity_policy: Literal["", "fractional", "whole_shares"]
    slippage_bps: str
    commission_per_order: str
    objective: Literal["", "return_first", "risk_first"]
    constraint_value: str
    note: str
    kind: Literal["", "substantive", "smoke"]

    @field_validator("range_start", "range_end")
    @classmethod
    def _iso(cls, value: str) -> str:
        return _check_date(value)

    @field_validator("initial_capital")
    @classmethod
    def _capital(cls, value: str) -> str:
        return _check_decimal(value, positive=True)

    @field_validator("slippage_bps", "commission_per_order")
    @classmethod
    def _cost(cls, value: str) -> str:
        return _check_decimal(value, non_negative=True)

    @field_validator("constraint_value")
    @classmethod
    def _constraint(cls, value: str) -> str:
        return _check_decimal(value)

    @field_validator("assets", mode="before")
    @classmethod
    def _normalize_assets(cls, value: Any) -> list[str]:
        # Same normalisation as StudySettings.assets.
        if not isinstance(value, list):
            raise ValueError("assets must be a list")
        out: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("assets must be nonblank strings")
            ticker = item.strip().upper().replace(".", "-")
            if ticker not in out:
                out.append(ticker)
        return out

    @model_validator(mode="after")
    def _cross_checks(self) -> StudyProposalOutput:
        pcts = (self.split.development_pct, self.split.validation_pct, self.split.final_test_pct)
        if any(pcts):
            if any(p < 0 or p > 100 for p in pcts):
                raise ValueError("split: each percentage must be between 0 and 100")
            if sum(pcts) != 100:
                raise ValueError("split: the percentages must sum to 100 (or all be 0)")
        if self.range_start and self.range_end and self.range_start > self.range_end:
            raise ValueError("range_start is after range_end")
        dates = [
            getattr(getattr(self.windows, role), edge)
            for role in _WINDOW_ROLES
            for edge in ("start", "end")
        ]
        if any(dates):
            if not all(dates):
                raise ValueError("windows: either all six window dates are given or none of them")
            parsed = {
                role: (
                    date.fromisoformat(getattr(self.windows, role).start),
                    date.fromisoformat(getattr(self.windows, role).end),
                )
                for role in _WINDOW_ROLES
            }
            for role, (start, end) in parsed.items():
                if start > end:
                    raise ValueError(f"windows.{role}: start is after end")
                if self.range_start and start < date.fromisoformat(self.range_start):
                    raise ValueError(f"windows.{role}: starts before range_start")
                if self.range_end and end > date.fromisoformat(self.range_end):
                    raise ValueError(f"windows.{role}: ends after range_end")
            dev, val, fin = (parsed[r] for r in _WINDOW_ROLES)
            if not (dev[1] < val[0] and val[1] < fin[0]):
                raise ValueError(
                    "windows: development must end before validation starts and validation "
                    "must end before final_test starts"
                )
        return self


def _findings(exc: ValidationError) -> list[str]:
    out: list[str] = []
    for error in exc.errors():
        where = ".".join(str(p) for p in error["loc"]) or "proposal"
        message = str(error["msg"])
        if message.startswith("Value error, "):
            message = message[len("Value error, ") :]
        out.append(f"{where}: {message}")
    return out


def parse_study_output(text_out: str | None) -> tuple[StudyProposalOutput | None, list[str]]:
    """The model's JSON -> the validated contract, or the findings that rejected it."""

    if not text_out or not text_out.strip():
        return None, ["the output was empty"]
    try:
        payload = json.loads(text_out)
    except ValueError as exc:
        return None, [f"the output is not valid JSON: {exc}"]
    if not isinstance(payload, dict):
        return None, ["the output is not a JSON object"]
    try:
        return StudyProposalOutput.model_validate(payload), []
    except ValidationError as exc:
        return None, _findings(exc)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


def _collapse(text_value: str) -> str:
    return " ".join(text_value.split()).casefold()


class StudyAssistantService(ResearchAssistantService):
    """Bounded, accounted study-proposal requests; nothing but ledger rows is stored."""

    # -- context -------------------------------------------------------------

    @staticmethod
    def _approved_index(versions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Each version with ``latest`` (highest version_no in its strategy family)."""

        newest: dict[str, int] = {}
        for version in versions:
            sid = version["strategy_id"]
            newest[sid] = max(newest.get(sid, 0), version["version_no"])
        return [{**v, "latest": v["version_no"] == newest[v["strategy_id"]]} for v in versions]

    @staticmethod
    def _approved_lines(index: list[dict[str, Any]]) -> list[str]:
        ordered = sorted(
            index, key=lambda v: (not v["latest"], _collapse(v["name"]), -v["version_no"])
        )
        lines = [
            f"{v['version_id']} | {' '.join(v['name'].split())} | v{v['version_no']} | "
            f"{'latest' if v['latest'] else 'older'} | approved {(v['approved_at'] or '')[:10]}"
            for v in ordered[:MAX_PROMPT_VERSION_LINES]
        ]
        if len(ordered) > MAX_PROMPT_VERSION_LINES:
            lines.append(
                f"(truncated: {len(ordered) - MAX_PROMPT_VERSION_LINES} more approved versions "
                "are not listed)"
            )
        return lines

    def _study_user_message(
        self,
        user_text: str,
        base_json: str | None,
        index: list[dict[str, Any]],
    ) -> str:
        today = self._clock().date().isoformat()
        limit = self._settings.research.max_assets_per_study
        lines = self._approved_lines(index) or ["(none: no strategy version is approved yet)"]
        head = (
            f"Today (UTC): {today}\n"
            f"Maximum assets per study: {limit}\n\n"
            "Approved strategy versions (version_id | name | version | latest or older | "
            "approval date):\n" + "\n".join(lines) + "\n\n"
        )
        if base_json is not None:
            return (
                head
                + "Current proposal (JSON):\n```json\n"
                + base_json
                + "\n```\n\nRevision request: "
                + user_text.strip()
            )
        return head + "Study request: " + user_text.strip()

    @staticmethod
    def _study_retry_message(findings: list[str]) -> str:
        listed = "\n".join(f"- {f}" for f in findings)
        return (
            "That proposal failed the deterministic contract check with these findings:\n"
            f"{listed}\n\nReturn a corrected proposal that resolves every finding without "
            "changing the intent. If something cannot be resolved from what the user said, "
            "leave the field empty and list it under missing."
        )

    # -- resolution ----------------------------------------------------------

    @staticmethod
    def _resolve_strategies(
        refs: list[StrategyRef], index: list[dict[str, Any]]
    ) -> dict[str, list[dict[str, Any]]]:
        by_id = {v["version_id"]: v for v in index}
        families: dict[str, list[dict[str, Any]]] = {}
        for v in index:
            families.setdefault(v["strategy_id"], []).append(v)
        by_name: dict[str, list[dict[str, Any]]] = {}
        for members in families.values():
            latest = max(members, key=lambda v: v["version_no"])
            by_name.setdefault(_collapse(latest["name"]), []).append(latest)

        resolved: list[dict[str, Any]] = []
        unresolved: list[dict[str, str]] = []
        seen: set[str] = set()
        for ref in refs:
            version_id = ref.version_id.strip()
            match: dict[str, Any] | None = None
            matched_by = ""
            if version_id in by_id:
                match, matched_by = by_id[version_id], "version_id"
            else:
                candidates = by_name.get(_collapse(ref.reference), []) if ref.reference else []
                if len(candidates) == 1:
                    match, matched_by = candidates[0], "name"
                elif len(candidates) > 1:
                    unresolved.append(
                        {
                            "reference": ref.reference,
                            "reason": "several approved strategies share this name",
                        }
                    )
                    continue
            if match is None:
                unresolved.append(
                    {"reference": ref.reference, "reason": "no approved version matches"}
                )
                continue
            if match["version_id"] in seen:
                continue
            seen.add(match["version_id"])
            resolved.append(
                {
                    "version_id": match["version_id"],
                    "strategy_id": match["strategy_id"],
                    "name": match["name"],
                    "version_no": match["version_no"],
                    "reference": ref.reference,
                    "matched_by": matched_by,
                }
            )
        return {"resolved": resolved, "unresolved": unresolved}

    def _resolve_assets(self, tickers: list[str]) -> list[dict[str, Any]]:
        catalog = AssetCatalogService(self._settings)
        out: list[dict[str, Any]] = []
        for ticker in tickers:
            entry = catalog.asset(ticker)
            out.append(
                {
                    "ticker": ticker,
                    "in_catalog": entry is not None,
                    "name": entry.get("name") if entry else None,
                }
            )
        return out

    def _build_result(
        self,
        proposal: StudyProposalOutput,
        index: list[dict[str, Any]],
        *,
        attempt_no: int,
        result: ProviderResult,
        started_at: datetime,
        deadline_seconds: float,
    ) -> dict[str, Any]:
        ai = self._settings.research.ai
        pcts = proposal.split
        split = (
            {
                "development_pct": pcts.development_pct,
                "validation_pct": pcts.validation_pct,
                "final_test_pct": pcts.final_test_pct,
            }
            if (pcts.development_pct or pcts.validation_pct or pcts.final_test_pct)
            else None
        )
        windows: dict[str, Any] | None = None
        if any(
            getattr(getattr(proposal.windows, role), edge)
            for role in _WINDOW_ROLES
            for edge in ("start", "end")
        ):
            windows = {
                role: {
                    "start": getattr(proposal.windows, role).start,
                    "end": getattr(proposal.windows, role).end,
                }
                for role in _WINDOW_ROLES
            }
        return {
            "status": "ok",
            "prompt_version": STUDY_PROMPT_VERSION,
            "attempt_no": attempt_no,
            "summary": proposal.summary.strip(),
            "missing": [m.model_dump() for m in proposal.missing],
            "unsupported_requests": [u.model_dump() for u in proposal.unsupported_requests],
            "settings": {
                "name": proposal.name.strip(),
                "assets": self._resolve_assets(proposal.assets),
                "strategies": self._resolve_strategies(proposal.strategies, index),
                "range_start": proposal.range_start,
                "range_end": proposal.range_end,
                "split": split,
                "windows": windows,
                "initial_capital": proposal.initial_capital,
                "quantity_policy": proposal.quantity_policy,
                "slippage_bps": proposal.slippage_bps,
                "commission_per_order": proposal.commission_per_order,
                "objective": proposal.objective,
                "constraint_value": proposal.constraint_value,
                "note": proposal.note.strip(),
                "kind": proposal.kind,
            },
            "provenance": {
                "provider": ai.provider,
                "model": result.model or ai.model,
                "prompt_version": STUDY_PROMPT_VERSION,
                "request_id": result.request_id,
                "stop_reason": result.stop_reason,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "cache_read_input_tokens": result.cache_read_input_tokens,
                "cache_creation_input_tokens": result.cache_creation_input_tokens,
                "deadline_seconds": deadline_seconds,
                "started_at": started_at.isoformat(),
                "completed_at": self._clock().isoformat(),
            },
        }

    # -- request -------------------------------------------------------------

    def propose_study(
        self, *, user_text: str, base_proposal: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Propose Study settings from free text, or revise ``base_proposal``."""

        self._require_usable()
        ai = self._settings.research.ai
        if not isinstance(user_text, str) or not user_text.strip():
            raise AssistantError(
                CODE_INVALID_INPUT,
                "user_text is required",
                field="user_text",
                reason="must be non-empty text",
            )
        base_json: str | None = None
        if base_proposal is not None:
            if not isinstance(base_proposal, dict):
                raise AssistantError(
                    CODE_INVALID_INPUT,
                    "base_proposal must be an object",
                    field="base_proposal",
                    reason="must be a JSON object",
                )
            try:
                base_json = json.dumps(base_proposal, ensure_ascii=False, indent=2, sort_keys=True)
            except (TypeError, ValueError) as exc:
                raise AssistantError(
                    CODE_INVALID_INPUT,
                    "base_proposal must be JSON",
                    field="base_proposal",
                    reason="must be JSON serialisable",
                ) from exc
        total_chars = len(user_text) + len(base_json or "")
        if total_chars > ai.max_input_characters:
            raise AssistantError(
                CODE_INPUT_TOO_LONG,
                "the request is longer than the configured input limit",
                limit=ai.max_input_characters,
                length=total_chars,
            )

        index = self._approved_index(ResearchStrategyService(self._settings).list_versions())
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": self._study_user_message(user_text, base_json, index)}
        ]
        provider = self._provider or self._provider_factory(self._settings)
        started = time.monotonic()
        deadline = ai.timeout_seconds
        findings: list[str] = []

        for attempt_no in range(1, MAX_ATTEMPTS + 1):
            remaining = deadline - (time.monotonic() - started)
            if remaining < MIN_ATTEMPT_SECONDS:
                raise AssistantError(
                    CODE_TIMEOUT,
                    "the request deadline passed before the attempt could start",
                    deadline_seconds=deadline,
                )
            now = self._clock()
            ledger_id = self._admit(now)
            try:
                result = provider.complete(
                    model=ai.model,
                    system_text=STUDY_SYSTEM_PROMPT,
                    messages=messages,
                    schema=STUDY_OUTPUT_SCHEMA,
                    max_output_tokens=ai.max_output_tokens,
                    timeout_seconds=remaining,
                )
            except ProviderFailure as failure:
                self._complete_ledger(
                    ledger_id,
                    outcome=_LEDGER_OUTCOME_FOR_STATUS[_STATUS_FOR_CODE[failure.code]],
                    status_code=failure.status_code,
                )
                logger.warning(
                    "study_assistant_provider_failure",
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
                    "provider_status": failure.status_code,
                    "provider_message": failure.provider_message,
                }
                if failure.code == CODE_TIMEOUT:
                    failure_detail["deadline_seconds"] = deadline
                raise AssistantError(failure.code, str(failure), **failure_detail) from failure
            except Exception as exc:  # the SDK raised something outside its documented set
                self._complete_ledger(ledger_id, outcome="http_error", status_code=None)
                logger.error(
                    "study_assistant_unexpected_failure",
                    extra={"context": {"error_type": type(exc).__name__, "attempt": attempt_no}},
                )
                raise AssistantError(
                    CODE_PROVIDER_ERROR, f"unexpected provider failure: {type(exc).__name__}"
                ) from exc

            if result.stop_reason == "refusal":
                self._complete_ledger(ledger_id, outcome="refused", status_code=200)
                raise AssistantError(CODE_REFUSED, "the provider declined to answer this request")
            if result.stop_reason == "max_tokens":
                self._complete_ledger(ledger_id, outcome="ok", status_code=200)
                raise AssistantError(
                    CODE_OUTPUT_TRUNCATED,
                    "the output hit the configured output-token limit",
                    limit=ai.max_output_tokens,
                )

            self._complete_ledger(ledger_id, outcome="ok", status_code=200)
            proposal, findings = parse_study_output(result.text)
            if proposal is not None:
                return self._build_result(
                    proposal,
                    index,
                    attempt_no=attempt_no,
                    result=result,
                    started_at=now,
                    deadline_seconds=remaining,
                )
            messages = messages + [
                {"role": "assistant", "content": result.text or ""},
                {"role": "user", "content": self._study_retry_message(findings)},
            ]
        raise AssistantError(
            CODE_OUTPUT_INVALID,
            "the assistant's output did not match the study proposal contract",
            findings=findings,
            attempts=MAX_ATTEMPTS,
        )
