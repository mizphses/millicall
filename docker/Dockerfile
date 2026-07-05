FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

RUN apt-get update && apt-get install -y --no-install-recommends \
        open-jtalk open-jtalk-mecab-naist-jdic hts-voice-nitech-jp-atr503-m001 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml ./
COPY uv.lock ./
COPY alembic.ini ./
COPY alembic ./alembic
COPY src ./src

RUN uv sync --no-dev --frozen

EXPOSE 8000

CMD ["uv", "run", "--no-dev", "uvicorn", "millicall.main:app", "--host", "0.0.0.0", "--port", "8000"]
