"""Read-only job-type catalog (ORCH-06, D-20).

Single source for the console's type picker, submission form pre-fill,
and mutation-capability signal. Deliberately NOT nested under the
``/api/v1/jobs`` router: that router already declares a greedy
``GET /{job_id}`` route, so a route mounted here as its own sibling
path avoids any path-shadowing risk. This module reads the registry and
settings only -- it must never import domain service or ORM layers.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from trading_platform.api.dependencies import get_job_registry, get_settings
from trading_platform.core.logging import get_logger
from trading_platform.core.settings import Settings
from trading_platform.jobs.registry import JobRegistry, UnknownJobTypeError

router = APIRouter(prefix="/api/v1/job-types", tags=["job-types"])

logger = get_logger("trading_platform.api.job_types")


@router.get("")
def list_job_types(
    registry: Annotated[JobRegistry, Depends(get_job_registry)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    items: list[dict[str, Any]] = []
    for job_type in registry.list_job_types():
        try:
            spec = registry.resolve_submission_spec(job_type)
        except UnknownJobTypeError:
            # Runner-only registrations (no public submission spec) are an
            # expected, non-exceptional case per registry.py's own
            # docstring -- omit them from the public catalog rather than
            # failing the whole response.
            continue

        entry: dict[str, Any] = {
            "job_type": job_type,
            "description": spec.description,
            "cancellation_mode": spec.cancellation_mode.value,
        }
        # ACCT-01: additive, only when the spec declares what it does at the broker.
        # The declared value is a closed StrEnum; read through ``getattr`` so this
        # module imports no service or ORM layer.
        broker_effect = getattr(spec, "broker_effect", None)
        if broker_effect is not None:
            entry["broker_effect"] = str(getattr(broker_effect, "value", broker_effect))
        try:
            defaults = spec.submission_defaults()
        except Exception as exc:  # noqa: BLE001 - resilience boundary, never fail the catalog
            logger.warning(
                "job_type_catalog_defaults_unavailable",
                extra={"context": {"job_type": job_type, "error_type": type(exc).__name__}},
            )
        else:
            if defaults is not None:
                entry["submission_defaults"] = dict(defaults)
        items.append(entry)

    return {
        "mutations_enabled": settings.orchestration.mutations_enabled,
        "items": items,
    }
