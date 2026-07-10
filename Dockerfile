FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
COPY config ./config
COPY src ./src

RUN pip install --upgrade pip && pip install .

# Alembic config + migration scripts must live in the image so migrations can
# run at deploy time (e.g. Render's chained start command) against the prod DB.
# Copied after the install layer so dependency caching is preserved.
COPY alembic.ini ./
COPY alembic ./alembic

# Free tier has no preDeploy hook, so migrations are chained into the start
# command: apply the schema (idempotent — a no-op once the DB is at head), then
# bind uvicorn to Render's $PORT (falling back to 8000 for local runs).
# Exec form with an explicit `sh -c` so the shell — not Render's dockerCommand
# string-parser — owns `&&` and `$PORT`. `exec` makes uvicorn PID 1 for clean
# SIGTERM on shutdown.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn trading_platform.api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]

