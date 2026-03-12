FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
COPY config ./config
COPY src ./src

RUN pip install --upgrade pip && pip install .

CMD ["uvicorn", "trading_platform.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

