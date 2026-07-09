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

CMD ["uvicorn", "trading_platform.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

