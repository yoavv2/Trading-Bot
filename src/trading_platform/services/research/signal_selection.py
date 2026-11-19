"""Choosing the first strategy version for the signals pilot (signals plan, Step 1), pure.

The rules are fixed before any run and read from the study protocol
(``config/research/signals_v2_study.yaml``):

* Selection uses the development and the validation window only. A version is measured
  on the pool of its trades across the study's assets, in net R.
* A version qualifies when it fails no criterion; every failure is a code from a closed
  list. Qualifying versions are ranked by the lower of their two windows' mean net R.
* Only the top-ranked version is run on the holdout window, once. A holdout result that
  is too small or not positive leaves the choice open; no second version is tried on the
  holdout, because that would turn the holdout into a selection window.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class CriterionCode(StrEnum):
    DEVELOPMENT_SAMPLE_TOO_SMALL = "development_sample_too_small"
    VALIDATION_SAMPLE_TOO_SMALL = "validation_sample_too_small"
    DEVELOPMENT_MEAN_NET_R_NOT_POSITIVE = "development_mean_net_r_not_positive"
    VALIDATION_MEAN_NET_R_NOT_POSITIVE = "validation_mean_net_r_not_positive"
    DEVELOPMENT_NET_R_BELOW_MAX_DRAWDOWN = "development_net_r_below_max_drawdown"
    DEVELOPMENT_TOO_FEW_PROFITABLE_ASSETS = "development_too_few_profitable_assets"
    DEVELOPMENT_TOO_FEW_TRADES_PER_MONTH = "development_too_few_trades_per_month"


class SelectionVerdict(StrEnum):
    CANDIDATE_SELECTED = "candidate_selected"
    NO_CANDIDATE_QUALIFIES = "no_candidate_qualifies"


class HoldoutVerdict(StrEnum):
    CONFIRMED = "confirmed"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    FAILED = "failed"
    NOT_RUN = "not_run"


@dataclass(frozen=True)
class SelectionCriteria:
    min_closed_trades_development: int
    min_closed_trades_validation: int
    min_profitable_assets_development: int
    min_trades_per_month_development: float
    min_closed_trades_holdout: int

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "SelectionCriteria":
        expected = set(cls.__dataclass_fields__)
        if set(payload) != expected:
            raise ValueError(f"criteria must have exactly the keys {sorted(expected)}; got {sorted(payload)}")
        return cls(
            min_closed_trades_development=int(payload["min_closed_trades_development"]),
            min_closed_trades_validation=int(payload["min_closed_trades_validation"]),
            min_profitable_assets_development=int(payload["min_profitable_assets_development"]),
            min_trades_per_month_development=float(payload["min_trades_per_month_development"]),
            min_closed_trades_holdout=int(payload["min_closed_trades_holdout"]),
        )


@dataclass(frozen=True)
class CandidateEvidence:
    """One version: pooled ``compute_risk_metrics`` output per window, and the development
    total net R of each asset."""

    key: str
    development: dict[str, Any]
    validation: dict[str, Any]
    development_net_r_by_asset: dict[str, float]


@dataclass(frozen=True)
class CandidateResult:
    key: str
    failed: tuple[CriterionCode, ...]
    ranking_value: float | None  # min(development mean net R, validation mean net R)

    @property
    def qualifies(self) -> bool:
        return not self.failed


@dataclass(frozen=True)
class Selection:
    verdict: SelectionVerdict
    chosen: str | None
    candidates: tuple[CandidateResult, ...]  # qualifying first, in rank order


def _mean(metrics: dict[str, Any]) -> float | None:
    return metrics["net_r"]["mean"]


def failed_criteria(candidate: CandidateEvidence, criteria: SelectionCriteria) -> tuple[CriterionCode, ...]:
    development, validation = candidate.development, candidate.validation
    failed: list[CriterionCode] = []
    if development["closed_trades"] < criteria.min_closed_trades_development:
        failed.append(CriterionCode.DEVELOPMENT_SAMPLE_TOO_SMALL)
    if validation["closed_trades"] < criteria.min_closed_trades_validation:
        failed.append(CriterionCode.VALIDATION_SAMPLE_TOO_SMALL)
    development_mean, validation_mean = _mean(development), _mean(validation)
    if development_mean is None or development_mean <= 0:
        failed.append(CriterionCode.DEVELOPMENT_MEAN_NET_R_NOT_POSITIVE)
    if validation_mean is None or validation_mean <= 0:
        failed.append(CriterionCode.VALIDATION_MEAN_NET_R_NOT_POSITIVE)
    total = development["net_r"]["total"] or 0.0
    if total <= 0 or total < development["max_r_drawdown"]:
        failed.append(CriterionCode.DEVELOPMENT_NET_R_BELOW_MAX_DRAWDOWN)
    profitable = sum(1 for value in candidate.development_net_r_by_asset.values() if value > 0)
    if profitable < criteria.min_profitable_assets_development:
        failed.append(CriterionCode.DEVELOPMENT_TOO_FEW_PROFITABLE_ASSETS)
    if development["trades_per_month"] < criteria.min_trades_per_month_development:
        failed.append(CriterionCode.DEVELOPMENT_TOO_FEW_TRADES_PER_MONTH)
    return tuple(failed)


def select(candidates: list[CandidateEvidence], criteria: SelectionCriteria) -> Selection:
    results: list[tuple[CandidateResult, float]] = []
    for candidate in candidates:
        failed = failed_criteria(candidate, criteria)
        development_mean, validation_mean = _mean(candidate.development), _mean(candidate.validation)
        ranking = (
            min(development_mean, validation_mean)
            if development_mean is not None and validation_mean is not None
            else None
        )
        tie_break = candidate.development["net_r"]["total"] or 0.0
        results.append((CandidateResult(candidate.key, failed, ranking), tie_break))
    ordered = sorted(
        results,
        key=lambda item: (
            not item[0].qualifies,
            -(item[0].ranking_value if item[0].ranking_value is not None else float("-inf")),
            -item[1],
            item[0].key,
        ),
    )
    ranked = tuple(result for result, _ in ordered)
    chosen = ranked[0].key if ranked and ranked[0].qualifies else None
    return Selection(
        verdict=SelectionVerdict.CANDIDATE_SELECTED if chosen else SelectionVerdict.NO_CANDIDATE_QUALIFIES,
        chosen=chosen,
        candidates=ranked,
    )


def holdout_verdict(holdout: dict[str, Any] | None, criteria: SelectionCriteria) -> HoldoutVerdict:
    """The verdict on the one version that reached the holdout window."""

    if holdout is None:
        return HoldoutVerdict.NOT_RUN
    if holdout["closed_trades"] < criteria.min_closed_trades_holdout:
        return HoldoutVerdict.INSUFFICIENT_EVIDENCE
    mean = _mean(holdout)
    return HoldoutVerdict.CONFIRMED if mean is not None and mean > 0 else HoldoutVerdict.FAILED
