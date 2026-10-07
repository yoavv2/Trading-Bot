"""Research-mode HTTP surface (``/api/v1/research/*``).

Kept outside ``api/routes`` on purpose: that package is the trading surface whose
mutating-route allowlist is pinned exactly (D-12). ``create_app`` mounts this package
only when ``settings.research.mode`` is on, and never mounts the trading routers then.
"""
