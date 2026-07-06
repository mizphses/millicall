# bookworm 固定: trixie (Debian 13) には OpenJTalk 音響モデル
# hts-voice-nitech-jp-atr503-m001 が存在しない (non-free からも削除済み)。
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# hts-voice-nitech-jp-atr503-m001 (OpenJTalk 音響モデル) は Debian の
# non-free コンポーネント配布のため、デフォルト (main のみ) に追加する。
RUN sed -i 's/Components: main/Components: main contrib non-free non-free-firmware/' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
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
