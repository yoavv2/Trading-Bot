"""Strategy authoring: drafts, validation, explanation, approval into immutable versions,
version listing and lineage (plan S2, proposal Part F).

Rules this module enforces:

* a draft is mutable; approval is an explicit action that copies it into an
  append-only ``strategy_versions`` row (the 0031 trigger refuses UPDATE/DELETE);
* an invalid draft cannot be approved (``DraftInvalidError`` carries every finding);
* approval stores the canonical specification, its ``spec_sha256``, the explanation and
  the derived values from one ``validate`` call, in one transaction, and deletes the
  draft in that same transaction, so a second approval of the same draft finds nothing;
* version numbers are allocated under a per-family transaction advisory lock, so two
  concurrent approvals of edits of the same version never collide on
  ``(strategy_id, version_no)``;
* editing an approved version opens a draft in its family (``edit_of_version``);
  duplicating one opens a draft that approves into a new family
  (``duplicate_of_version``), both with ``parent_version_id`` for lineage;
* nothing here starts a study, enqueues a Job or touches the broker: the only tables
  written are ``strategy_drafts`` and ``strategy_versions``.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from trading_platform.core.settings import Settings
from trading_platform.db.models.research import StrategyDraft, StrategyVersion
from trading_platform.db.session import session_scope
from trading_platform.strategies.spec.errors import SpecValidationError
from trading_platform.strategies.spec.explain import explain
from trading_platform.strategies.spec.validate import CompiledSpec, validate_yaml

MAX_TITLE_LENGTH = 120
MAX_YAML_LENGTH = 200_000

SOURCE_MANUAL = "manual"
SOURCE_ASSISTANT = "assistant"
SOURCE_EDIT_OF_VERSION = "edit_of_version"
SOURCE_DUPLICATE_OF_VERSION = "duplicate_of_version"
DRAFT_SOURCES: frozenset[str] = frozenset(
    {SOURCE_MANUAL, SOURCE_ASSISTANT, SOURCE_EDIT_OF_VERSION, SOURCE_DUPLICATE_OF_VERSION}
)

#: Namespace that turns a strategy family id into the advisory-lock key for approvals.
_FAMILY_LOCK_NAMESPACE = b"research.strategy_versions.family:"


# ---------------------------------------------------------------------------
# Errors (closed codes)
# ---------------------------------------------------------------------------


class ResearchStrategyError(Exception):
    code = "research_strategy_error"


class DraftNotFoundError(ResearchStrategyError):
    code = "draft_not_found"

    def __init__(self, draft_id: uuid.UUID) -> None:
        self.draft_id = draft_id
        super().__init__(f"Draft {draft_id} not found.")


class VersionNotFoundError(ResearchStrategyError):
    code = "version_not_found"

    def __init__(self, version_id: uuid.UUID) -> None:
        self.version_id = version_id
        super().__init__(f"Strategy version {version_id} not found.")


class InvalidDraftInputError(ResearchStrategyError):
    code = "invalid_draft_input"

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"{field}: {reason}")


class DraftInvalidError(ResearchStrategyError):
    """The draft's YAML fails the shared validator; approval is refused."""

    code = "draft_invalid"

    def __init__(self, draft_id: uuid.UUID, errors: list[dict[str, str]]) -> None:
        self.draft_id = draft_id
        self.errors = list(errors)
        super().__init__(f"Draft {draft_id} is invalid: {len(errors)} finding(s).")


# ---------------------------------------------------------------------------
# Validation (pure; shared by the editor, approval and, later, the assistant)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationOutcome:
    valid: bool
    errors: list[dict[str, str]]
    derived: dict[str, Any] | None
    explanation: str | None
    compiled: CompiledSpec | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": list(self.errors),
            "derived": dict(self.derived) if self.derived is not None else None,
            "explanation": self.explanation,
        }


def derived_values(compiled: CompiledSpec) -> dict[str, Any]:
    return {
        "name": compiled.spec.name,
        "spec_sha256": compiled.spec_sha256,
        "history_required": compiled.history_required,
        "history_minimum": compiled.history_minimum,
        "scale_class": compiled.scale_class,
        "terms_used": list(compiled.terms_used),
        "operators_used": list(compiled.operators_used),
        "pine_equivalent_available": compiled.pine_equivalent_available,
    }


def validate_yaml_text(yaml_text: str) -> ValidationOutcome:
    """Run the one shared validator and the deterministic explanation on YAML text."""

    try:
        compiled = validate_yaml(yaml_text)
    except SpecValidationError as exc:
        return ValidationOutcome(False, [e.to_dict() for e in exc.errors], None, None)
    return ValidationOutcome(True, [], derived_values(compiled), explain(compiled), compiled)


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def draft_to_dict(draft: StrategyDraft) -> dict[str, Any]:
    return {
        "draft_id": str(draft.id),
        "title": draft.title,
        "yaml_text": draft.yaml_text,
        "source": draft.source,
        "parent_version_id": str(draft.parent_version_id) if draft.parent_version_id else None,
        "ai_draft_id": str(draft.ai_draft_id) if draft.ai_draft_id else None,
        "created_at": _iso(draft.created_at),
        "updated_at": _iso(draft.updated_at),
    }


def version_to_dict(version: StrategyVersion, *, include_text: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "version_id": str(version.id),
        "strategy_id": str(version.strategy_id),
        "version_no": version.version_no,
        "name": version.name,
        "spec_sha256": version.spec_sha256,
        "history_required": version.history_required,
        "history_minimum": version.history_minimum,
        "scale_class": version.scale_class,
        "source": version.source,
        "parent_version_id": str(version.parent_version_id) if version.parent_version_id else None,
        "ai_draft_id": str(version.ai_draft_id) if version.ai_draft_id else None,
        "behaviour_differs_from_original": version.behaviour_differs_from_original,
        "approved_at": _iso(version.approved_at),
        "created_at": _iso(version.created_at),
    }
    if include_text:
        payload["yaml_text"] = version.yaml_text
        payload["spec_json"] = version.spec_json
        payload["explanation"] = version.explanation
    return payload


# ---------------------------------------------------------------------------
# Input checks
# ---------------------------------------------------------------------------


def _check_title(title: Any) -> str:
    if not isinstance(title, str):
        raise InvalidDraftInputError("title", "must be a string")
    trimmed = title.strip()
    if not trimmed or len(trimmed) > MAX_TITLE_LENGTH or "\x00" in trimmed:
        raise InvalidDraftInputError("title", f"must be 1..{MAX_TITLE_LENGTH} characters")
    return trimmed


def _check_yaml_text(yaml_text: Any) -> str:
    if not isinstance(yaml_text, str):
        raise InvalidDraftInputError("yaml_text", "must be a string")
    if not yaml_text.strip() or len(yaml_text) > MAX_YAML_LENGTH or "\x00" in yaml_text:
        raise InvalidDraftInputError(
            "yaml_text", f"must be nonblank and at most {MAX_YAML_LENGTH} characters"
        )
    return yaml_text


def family_lock_key(strategy_id: uuid.UUID) -> int:
    """Signed 64-bit key for ``pg_advisory_xact_lock`` derived from the family id."""

    digest = hashlib.sha256(_FAMILY_LOCK_NAMESPACE + strategy_id.bytes).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ResearchStrategyService:
    """Synchronous authoring writes on the research database (research mode only)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    # -- drafts --------------------------------------------------------------

    def list_drafts(self) -> list[dict[str, Any]]:
        with session_scope(self._settings) as session:
            drafts = session.execute(
                select(StrategyDraft).order_by(StrategyDraft.updated_at.desc(), StrategyDraft.id)
            ).scalars()
            return [draft_to_dict(d) for d in drafts]

    def get_draft(self, draft_id: uuid.UUID) -> dict[str, Any]:
        from trading_platform.services.research.assistant import assistant_provenance

        with session_scope(self._settings) as session:
            draft = self._load_draft(session, draft_id)
            payload = draft_to_dict(draft)
            payload["assistant"] = assistant_provenance(session, draft.ai_draft_id)
            return payload

    def create_draft(
        self, *, title: str, yaml_text: str, source: str = SOURCE_MANUAL
    ) -> dict[str, Any]:
        if source not in (SOURCE_MANUAL, SOURCE_ASSISTANT):
            raise InvalidDraftInputError("source", "must be manual or assistant")
        checked_title = _check_title(title)
        checked_yaml = _check_yaml_text(yaml_text)
        with session_scope(self._settings) as session:
            draft = StrategyDraft(
                id=uuid.uuid4(), title=checked_title, yaml_text=checked_yaml, source=source
            )
            session.add(draft)
            session.flush()
            session.refresh(draft)
            return draft_to_dict(draft)

    def update_draft(
        self, draft_id: uuid.UUID, *, title: str | None = None, yaml_text: str | None = None
    ) -> dict[str, Any]:
        if title is None and yaml_text is None:
            raise InvalidDraftInputError("body", "nothing to update")
        checked_title = _check_title(title) if title is not None else None
        checked_yaml = _check_yaml_text(yaml_text) if yaml_text is not None else None
        with session_scope(self._settings) as session:
            draft = self._load_draft(session, draft_id, for_update=True)
            if checked_title is not None:
                draft.title = checked_title
            if checked_yaml is not None:
                draft.yaml_text = checked_yaml
            draft.updated_at = datetime.now(UTC)
            session.flush()
            session.refresh(draft)
            return draft_to_dict(draft)

    def delete_draft(self, draft_id: uuid.UUID) -> dict[str, Any]:
        with session_scope(self._settings) as session:
            draft = self._load_draft(session, draft_id, for_update=True)
            session.delete(draft)
            session.flush()
            return {"draft_id": str(draft_id), "deleted": True}

    def duplicate_draft(self, draft_id: uuid.UUID) -> dict[str, Any]:
        """A fresh manual draft with the same text; lineage of the original is kept."""

        with session_scope(self._settings) as session:
            original = self._load_draft(session, draft_id)
            copy = StrategyDraft(
                id=uuid.uuid4(),
                title=_check_title(f"{original.title} (copy)"[:MAX_TITLE_LENGTH]),
                yaml_text=original.yaml_text,
                source=SOURCE_MANUAL,
                parent_version_id=original.parent_version_id,
            )
            session.add(copy)
            session.flush()
            session.refresh(copy)
            return draft_to_dict(copy)

    def validate_draft(self, draft_id: uuid.UUID) -> dict[str, Any]:
        with session_scope(self._settings) as session:
            draft = self._load_draft(session, draft_id)
            outcome = validate_yaml_text(draft.yaml_text)
            return {"draft_id": str(draft.id), **outcome.to_dict()}

    # -- versions ------------------------------------------------------------

    def draft_from_version(self, version_id: uuid.UUID, *, mode: str) -> dict[str, Any]:
        """``mode="edit"``: next version of the same family on approval.
        ``mode="duplicate"``: a new family on approval. Both keep ``parent_version_id``."""

        if mode == "edit":
            source, suffix = SOURCE_EDIT_OF_VERSION, " (edit)"
        elif mode == "duplicate":
            source, suffix = SOURCE_DUPLICATE_OF_VERSION, " (copy)"
        else:
            raise InvalidDraftInputError("mode", "must be edit or duplicate")
        with session_scope(self._settings) as session:
            version = self._load_version(session, version_id)
            draft = StrategyDraft(
                id=uuid.uuid4(),
                title=_check_title(f"{version.name}{suffix}"[:MAX_TITLE_LENGTH]),
                yaml_text=version.yaml_text,
                source=source,
                parent_version_id=version.id,
            )
            session.add(draft)
            session.flush()
            session.refresh(draft)
            return draft_to_dict(draft)

    def approve_draft(self, draft_id: uuid.UUID, *, now: datetime | None = None) -> dict[str, Any]:
        """Validate, allocate the version number under the family lock, insert the
        immutable version and delete the draft, all in one transaction."""

        with session_scope(self._settings) as session:
            draft = self._load_draft(session, draft_id, for_update=True)
            outcome = validate_yaml_text(draft.yaml_text)
            if not outcome.valid or outcome.compiled is None:
                raise DraftInvalidError(draft.id, outcome.errors)
            compiled = outcome.compiled

            parent: StrategyVersion | None = None
            if draft.parent_version_id is not None:
                parent = session.get(StrategyVersion, draft.parent_version_id)
            if draft.source == SOURCE_EDIT_OF_VERSION and parent is not None:
                family = parent.strategy_id
            else:
                family = uuid.uuid4()

            session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": family_lock_key(family)}
            )
            latest = session.execute(
                select(func.max(StrategyVersion.version_no)).where(
                    StrategyVersion.strategy_id == family
                )
            ).scalar_one()
            approved_at = now or datetime.now(UTC)
            version = StrategyVersion(
                id=uuid.uuid4(),
                strategy_id=family,
                version_no=(latest or 0) + 1,
                name=compiled.spec.name,
                yaml_text=draft.yaml_text,
                spec_json=compiled.spec.model_dump(mode="json"),
                spec_sha256=compiled.spec_sha256,
                history_required=compiled.history_required,
                history_minimum=compiled.history_minimum,
                scale_class=compiled.scale_class,
                explanation=outcome.explanation or explain(compiled),
                source=draft.source,
                parent_version_id=parent.id if parent is not None else None,
                ai_draft_id=draft.ai_draft_id,
                approved_at=approved_at,
                created_at=approved_at,
            )
            session.add(version)
            session.delete(draft)
            session.flush()
            session.refresh(version)
            return version_to_dict(version)

    def list_versions(self, *, strategy_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
        with session_scope(self._settings) as session:
            stmt = select(StrategyVersion).order_by(
                StrategyVersion.strategy_id, StrategyVersion.version_no
            )
            if strategy_id is not None:
                stmt = stmt.where(StrategyVersion.strategy_id == strategy_id)
            return [version_to_dict(v, include_text=False) for v in session.execute(stmt).scalars()]

    def list_families(self) -> list[dict[str, Any]]:
        """One row per strategy family: latest version and the version count."""

        with session_scope(self._settings) as session:
            versions = list(
                session.execute(
                    select(StrategyVersion).order_by(
                        StrategyVersion.strategy_id, StrategyVersion.version_no
                    )
                ).scalars()
            )
        families: dict[uuid.UUID, dict[str, Any]] = {}
        for version in versions:
            entry = families.setdefault(
                version.strategy_id,
                {"strategy_id": str(version.strategy_id), "version_count": 0, "latest": None},
            )
            entry["version_count"] += 1
            entry["latest"] = version_to_dict(version, include_text=False)
        return sorted(families.values(), key=lambda f: (f["latest"]["name"], f["strategy_id"]))

    def get_version(self, version_id: uuid.UUID) -> dict[str, Any]:
        from trading_platform.services.research.assistant import assistant_provenance

        with session_scope(self._settings) as session:
            version = self._load_version(session, version_id)
            payload = version_to_dict(version)
            payload["assistant"] = assistant_provenance(session, version.ai_draft_id, approved=True)
            return payload

    def lineage(self, version_id: uuid.UUID) -> list[dict[str, Any]]:
        """Ancestors from the root to the version itself (root first)."""

        with session_scope(self._settings) as session:
            version = self._load_version(session, version_id)
            chain = [version]
            seen = {version.id}
            current = version
            while current.parent_version_id is not None and current.parent_version_id not in seen:
                parent = session.get(StrategyVersion, current.parent_version_id)
                if parent is None:
                    break
                chain.append(parent)
                seen.add(parent.id)
                current = parent
            return [version_to_dict(v, include_text=False) for v in reversed(chain)]

    # -- loaders -------------------------------------------------------------

    @staticmethod
    def _load_draft(
        session: Session, draft_id: uuid.UUID, *, for_update: bool = False
    ) -> StrategyDraft:
        stmt = select(StrategyDraft).where(StrategyDraft.id == draft_id)
        if for_update:
            stmt = stmt.with_for_update()
        draft = session.execute(stmt).scalar_one_or_none()
        if draft is None:
            raise DraftNotFoundError(draft_id)
        return draft

    @staticmethod
    def _load_version(session: Session, version_id: uuid.UUID) -> StrategyVersion:
        version = session.get(StrategyVersion, version_id)
        if version is None:
            raise VersionNotFoundError(version_id)
        return version
