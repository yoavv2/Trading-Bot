"""Generic Job framework package.

This package contains the operation-agnostic Job execution framework
(contracts, registry, queue, lifecycle, dependencies, cancellation) --
it contains no domain behavior. Domain behavior lives exclusively in
``trading_platform.services``; Job handlers registered here call into
that layer but never implement business logic themselves.

Deliberately minimal: this ``__init__.py`` exports nothing beyond this
docstring. Every parallel Phase 17 plan adds its own sibling module
(``contracts.py``, ``registry.py``, ``queue.py``, ``lifecycle.py``,
``dependencies.py``, ``cancellation.py``, ...) under this package. A
re-export list here would force every one of those plans to edit this
same shared file, creating an avoidable merge-conflict hotspot. Import
directly from the sibling module you need instead
(e.g. ``from trading_platform.jobs.contracts import JobContext``).
"""
