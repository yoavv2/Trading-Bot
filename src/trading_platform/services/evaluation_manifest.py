"""Evaluation input manifest: data provenance for risk evaluations (PROV-01).

D-25: a risk evaluation records every read request made through the shared
market-data accessors -- (accessor, parameters, RESOLVED as-of bound, SHA-256
result digest), empty results included -- plus a digest of the strategy SIGNAL
settings and a manifest version, in the risk run's existing ``result_summary``
(no schema change). ``verify_manifest`` re-executes the recorded requests AS
RECORDED (recorded symbols, ranges and resolved bounds) and reports whether the
evaluation still reflects the currently valid data.

D-26: provenance is NOT current portfolio risk. The settings digest covers
signal settings only ({strategy_id, version, universe, indicators, exits}); the
strategy ``risk`` section, portfolio settings, cash, positions and open orders
are never part of it, and valuation reads are re-run with the recorded symbols
(never re-derived from today's positions). Those are checked fresh at execution
(20.1-11/15). ``updated_at`` is never used: a no-op re-ingest is not stale.

Verification is read-only and batched: one statement per bar-window group, one
per (date, adjusted, provider) group shared by missing-bar and valuation
requests, and one per time-relative lookup -- independent of the symbol count.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from trading_platform.services.market_data_access import (
    SessionBar,
    bars_for_session_date,
    bars_for_sessions_many,
    latest_completed_session,
    persisted_session_dates,
)
from trading_platform.services.read_recording import (
    KIND_BARS_FOR_SESSION_DATE,
    KIND_BARS_FOR_SESSIONS,
    KIND_LATEST_COMPLETED_SESSION,
    KIND_MISSING_BARS_FOR_SESSION,
    KIND_PERSISTED_SESSION_DATES,
    canonical_json,
    result_digest,
    sha256_hex,
    suspended,
)

if TYPE_CHECKING:
    from trading_platform.strategies.base import StrategyMetadata

MANIFEST_VERSION = 1


class ManifestFormatError(ValueError):
    """The stored manifest JSON is malformed or of an unknown version."""


class ManifestRequestKind(StrEnum):
    """Closed set of recorded read kinds (the five shared accessors)."""

    BARS_FOR_SESSIONS = KIND_BARS_FOR_SESSIONS
    MISSING_BARS_FOR_SESSION = KIND_MISSING_BARS_FOR_SESSION
    BARS_FOR_SESSION_DATE = KIND_BARS_FOR_SESSION_DATE
    LATEST_COMPLETED_SESSION = KIND_LATEST_COMPLETED_SESSION
    PERSISTED_SESSION_DATES = KIND_PERSISTED_SESSION_DATES


class ManifestVerificationStatus(StrEnum):
    MATCHES = "matches"
    EVALUATION_DATA_CHANGED = "evaluation_data_changed"
    STRATEGY_SETTINGS_CHANGED = "strategy_settings_changed"
    MANIFEST_MISSING = "manifest_missing"


@dataclass(frozen=True)
class ManifestRequest:
    kind: ManifestRequestKind
    params: dict[str, Any]
    digest: str
    count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "params": dict(self.params),
            "digest": self.digest,
            "count": self.count,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> ManifestRequest:
        if not isinstance(raw, Mapping):
            raise ManifestFormatError("manifest request must be an object")
        try:
            kind = ManifestRequestKind(raw["kind"])
            params = raw["params"]
            digest = raw["digest"]
            count = raw["count"]
        except (KeyError, ValueError) as exc:
            raise ManifestFormatError(f"malformed manifest request: {exc}") from exc
        if not isinstance(params, Mapping) or not isinstance(digest, str) or not isinstance(count, int):
            raise ManifestFormatError("malformed manifest request fields")
        request = cls(kind=kind, params=dict(params), digest=digest, count=count)
        _parse_params(request)  # validates the parameter shape for its kind
        return request


@dataclass(frozen=True)
class EvaluationManifest:
    version: int
    requests: tuple[ManifestRequest, ...]
    settings_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "settings_digest": self.settings_digest,
            "requests": [request.to_dict() for request in self.requests],
        }

    @classmethod
    def from_dict(cls, raw: Any) -> EvaluationManifest:
        if not isinstance(raw, Mapping):
            raise ManifestFormatError("manifest must be an object")
        if raw.get("version") != MANIFEST_VERSION:
            raise ManifestFormatError(f"unsupported manifest version {raw.get('version')!r}")
        settings_digest = raw.get("settings_digest")
        requests = raw.get("requests")
        if not isinstance(settings_digest, str) or not isinstance(requests, list):
            raise ManifestFormatError("manifest requires settings_digest and a requests list")
        return cls(
            version=MANIFEST_VERSION,
            requests=tuple(ManifestRequest.from_dict(item) for item in requests),
            settings_digest=settings_digest,
        )


@dataclass(frozen=True)
class ManifestVerification:
    status: ManifestVerificationStatus
    mismatched_request: ManifestRequest | None = None


class ManifestRecorder:
    """``ReadRecorder`` that accumulates requests (identical repeats collapse)."""

    def __init__(self) -> None:
        self._requests: list[ManifestRequest] = []
        self._seen: set[tuple[str, str, str]] = set()

    def record(self, kind: str, params: Mapping[str, Any], digest: str, count: int) -> None:
        key = (kind, canonical_json(params), digest)
        if key in self._seen:
            return
        self._seen.add(key)
        self._requests.append(
            ManifestRequest(
                kind=ManifestRequestKind(kind), params=dict(params), digest=digest, count=count
            )
        )

    def build(self, settings_digest: str) -> EvaluationManifest:
        return EvaluationManifest(
            version=MANIFEST_VERSION,
            requests=tuple(self._requests),
            settings_digest=settings_digest,
        )


def compute_settings_digest(metadata: StrategyMetadata) -> str:
    """Digest of the SIGNAL settings only (D-26): the strategy ``risk`` section,
    display fields and portfolio settings are current policy, not provenance."""

    return sha256_hex(
        {
            "strategy_id": metadata.strategy_id,
            "version": metadata.version,
            "universe": sorted(metadata.universe),
            "indicators": metadata.indicators,
            "exits": metadata.exits,
        }
    )


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def _parse_params(request: ManifestRequest) -> dict[str, Any]:
    """Typed parameters of one recorded request (raises ``ManifestFormatError``)."""

    p = request.params
    try:
        kind = request.kind
        if kind is ManifestRequestKind.BARS_FOR_SESSIONS:
            return {
                "symbol": str(p["symbol"]),
                "n_sessions": int(p["n_sessions"]),
                "as_of": date.fromisoformat(p["as_of"]),
                "exchange": str(p["exchange"]),
                "adjusted": bool(p["adjusted"]),
                "provider": str(p["provider"]),
            }
        if kind in (
            ManifestRequestKind.MISSING_BARS_FOR_SESSION,
            ManifestRequestKind.BARS_FOR_SESSION_DATE,
        ):
            symbols = p["symbols"]
            if symbols is not None and not isinstance(symbols, list):
                raise ValueError("symbols must be a list or null")
            return {
                "session_date": date.fromisoformat(p["session_date"]),
                "symbols": None if symbols is None else [str(item) for item in symbols],
                "adjusted": bool(p["adjusted"]),
                "provider": str(p["provider"]),
            }
        if kind is ManifestRequestKind.LATEST_COMPLETED_SESSION:
            bound = p["as_of_bound"]
            return {
                "exchange": str(p["exchange"]),
                "as_of_bound": None if bound is None else date.fromisoformat(bound),
            }
        return {
            "start": date.fromisoformat(p["start"]),
            "end": date.fromisoformat(p["end"]),
            "exchange": str(p["exchange"]),
        }
    except (KeyError, ValueError, TypeError) as exc:
        raise ManifestFormatError(f"malformed parameters for {request.kind.value}: {exc}") from exc


def verify_manifest(
    session: Session,
    manifest: EvaluationManifest | None,
    *,
    current_settings_digest: str,
) -> ManifestVerification:
    """Re-execute the recorded requests as recorded and compare digests.

    Precedence: settings digest mismatch -> ``strategy_settings_changed`` (no data
    read), then data re-execution -> ``evaluation_data_changed``, else ``matches``.
    Read-only; the statement count is bounded by the number of request groups,
    never by the number of symbols.
    """

    if manifest is None:
        return ManifestVerification(ManifestVerificationStatus.MANIFEST_MISSING)
    if manifest.settings_digest != current_settings_digest:
        return ManifestVerification(ManifestVerificationStatus.STRATEGY_SETTINGS_CHANGED)

    parsed = [(request, _parse_params(request)) for request in manifest.requests]
    with suspended():  # verification reads must never be recorded
        current = _current_digests(session, parsed)
    for index, (request, _params) in enumerate(parsed):
        if current[index] != request.digest:
            return ManifestVerification(
                ManifestVerificationStatus.EVALUATION_DATA_CHANGED, mismatched_request=request
            )
    return ManifestVerification(ManifestVerificationStatus.MATCHES)


def _current_digests(
    session: Session,
    parsed: list[tuple[ManifestRequest, dict[str, Any]]],
) -> dict[int, str]:
    digests: dict[int, str] = {}

    # Bar windows: one batched statement per (n_sessions, as_of, exchange, adjusted, provider).
    window_groups: dict[tuple[Any, ...], list[int]] = defaultdict(list)
    # Per-date reads (missing-bar checks and valuation prices) share one statement
    # per (session_date, adjusted, provider).
    date_groups: dict[tuple[Any, ...], list[int]] = defaultdict(list)
    for index, (request, params) in enumerate(parsed):
        kind = request.kind
        if kind is ManifestRequestKind.BARS_FOR_SESSIONS:
            key = (
                params["n_sessions"],
                params["as_of"],
                params["exchange"],
                params["adjusted"],
                params["provider"],
            )
            window_groups[key].append(index)
        elif kind in (
            ManifestRequestKind.MISSING_BARS_FOR_SESSION,
            ManifestRequestKind.BARS_FOR_SESSION_DATE,
        ):
            date_groups[(params["session_date"], params["adjusted"], params["provider"])].append(index)
        elif kind is ManifestRequestKind.LATEST_COMPLETED_SESSION:
            latest = latest_completed_session(
                session, exchange=params["exchange"], as_of=params["as_of_bound"]
            )
            digests[index] = result_digest(KIND_LATEST_COMPLETED_SESSION, latest)
        else:
            dates = persisted_session_dates(
                session, start=params["start"], end=params["end"], exchange=params["exchange"]
            )
            digests[index] = result_digest(KIND_PERSISTED_SESSION_DATES, dates)

    for (n_sessions, as_of, exchange, adjusted, provider), indexes in window_groups.items():
        symbols = sorted({parsed[i][1]["symbol"] for i in indexes})
        bars = bars_for_sessions_many(
            session, symbols, n_sessions, as_of, exchange=exchange, adjusted=adjusted, provider=provider
        )
        for i in indexes:
            digests[i] = result_digest(KIND_BARS_FOR_SESSIONS, bars[parsed[i][1]["symbol"]])

    for (session_date, adjusted, provider), indexes in date_groups.items():
        wanted: list[list[str] | None] = [parsed[i][1]["symbols"] for i in indexes]
        # one unfiltered read when any request was unfiltered; else the union
        group_symbols: list[str] | None = None
        if all(item is not None for item in wanted):
            group_symbols = sorted({name for item in wanted for name in (item or [])})
        available: Mapping[str, SessionBar] = bars_for_session_date(
            session, session_date, symbols=group_symbols, adjusted=adjusted, provider=provider
        )
        for i in indexes:
            request, params = parsed[i]
            requested = params["symbols"]
            if request.kind is ManifestRequestKind.MISSING_BARS_FOR_SESSION:
                missing = sorted(set(requested or []) - set(available))
                digests[i] = result_digest(KIND_MISSING_BARS_FOR_SESSION, missing)
            else:
                subset = (
                    dict(available)
                    if requested is None
                    else {symbol: available[symbol] for symbol in requested if symbol in available}
                )
                digests[i] = result_digest(KIND_BARS_FOR_SESSION_DATE, subset)
    return digests
