# --- SPA (管理 GUI) ビルド ---------------------------------------------------
# frontend/ を dist にビルドする。gen:api は実行しない
# (schema.d.ts はコミット済みが正。openapi.json 生成に Python が要るため)。
# node_modules/dist/styled-system は .dockerignore で除外され、npm ci と
# panda codegen (npm run build 内) で再生成される。
FROM node:22-slim AS webbuilder
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
# --ignore-scripts: prepare(panda codegen) はソース未 COPY 段階では失敗するため抑止。
# panda codegen は後続の npm run build 内で実行される。
RUN npm ci --ignore-scripts
COPY frontend/ ./
RUN npm run build

# --- core (FastAPI) ----------------------------------------------------------
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

# stt-google extra を含めて同期する。google_stt プロバイダ（google-cloud-speech）を
# 出荷イメージに同梱し、管理画面で STT に google_stt を選んだ場合に動作させる。
RUN uv sync --no-dev --frozen --extra stt-google

# ビルド済み SPA を配置 (settings.static_dir の既定 /app/static)。
# 存在すると main.py が StaticFiles + SPA fallback を自動で有効化する。
COPY --from=webbuilder /web/dist /app/static

# core は平文 HTTP を待受ける。既定 80。TLS/443 はフロント(Cloudflare Tunnel /
# Tailscale Serve)に委譲する。ポートは MILLICALL_HTTP_PORT で上書き可。
EXPOSE 80

# shell 形式で MILLICALL_HTTP_PORT / MILLICALL_HTTP_BIND を uvicorn に渡す。
# MILLICALL_HTTP_BIND デフォルト: 0.0.0.0（nftables INPUT フィルタが LAN 限定ゲートになる）。
# CF-Tunnel / Tailscale 専用デプロイでは 127.0.0.1 に設定し LAN/WAN 露出を完全排除できる。
CMD ["sh", "-c", "uv run --no-dev uvicorn millicall.main:app --host ${MILLICALL_HTTP_BIND:-0.0.0.0} --port ${MILLICALL_HTTP_PORT:-80}"]
