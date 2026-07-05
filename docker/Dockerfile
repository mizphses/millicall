FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

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
