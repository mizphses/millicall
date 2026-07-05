# millicall v2 (core)

[![ci](https://github.com/mizphses/millicall/actions/workflows/ci.yml/badge.svg)](https://github.com/mizphses/millicall/actions/workflows/ci.yml)

フレッツ特化ローカルPBX の core サービス（FastAPI + FreeSWITCH 制御）。

## クイックデプロイ（本番 / amd64 Linux）

```bash
curl -fsSL https://raw.githubusercontent.com/mizphses/millicall/main/install.sh | bash
```

インストーラは `~/millicall` に compose と `.env` を配置し、GHCR からプリビルドイメージを pull して起動します。
更新は `millicallctl update`。詳細は [docs/ops/deployment.md](docs/ops/deployment.md)。

初期管理者パスワード（初回起動ログに一度だけ表示）:

```bash
millicallctl logs core | grep 初期管理者
```

## 開発

```bash
uv sync --extra dev
uv run pytest -q
uv run ruff check .
```

## 実行（ローカル）

```bash
uv run uvicorn millicall.main:app --host 0.0.0.0 --port 8000
# もしくは compose でスタック全体 (freeswitch イメージのビルドに時間がかかる)
docker compose up -d --build
```

## イメージ

- `ghcr.io/mizphses/millicall-core` — **amd64 のみ**（現時点）
- `ghcr.io/mizphses/millicall-freeswitch` — **amd64 のみ**（mod_audio_stream 同梱）

タグ: `latest`（stable 最新）、`vX.Y.Z`（stable 固定）、`dev` / `main-<sha>`（main プレビュー）。

> **arm64 について:** runtime base `safarov/freeswitch`（pinned digest `sha256:b31c743f…`）は単一 amd64 マニフェストであり arm64 タグが存在しないため、現時点のスタック全体が amd64 に固定されています。arm64 対応は将来課題です（FreeSWITCH 1.10.x を arm64 向けにソースビルドするか arm64 対応 base イメージを選定する別スパイクが必要）。

詳細な設計は `docs/`（Wiki 同期対象）を参照。
