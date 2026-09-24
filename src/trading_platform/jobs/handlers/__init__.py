"""Concrete Job type handlers and submission specifications (Phase 19+).

Unlike the queue-framework modules under ``trading_platform.jobs`` (queue,
lifecycle, runner, dependencies, cancellation, context, contracts,
progress), modules in this package implement one specific, publicly
registrable Job type. Per ``trading_platform.jobs.contracts.JobHandler``'s
own docstring, a handler module MAY import and call
``trading_platform.services.*`` -- that is the whole point of this
package's boundary: framework modules stay free of domain imports, while
handler modules are exactly where the Job framework meets domain services.
"""

from __future__ import annotations
