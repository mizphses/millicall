# millicall v2 (core)

フレッツ特化ローカルPBX の core サービス（FastAPI + FreeSWITCH 制御）。

## 開発

```bash
uv sync --extra dev
uv run pytest -q
uv run ruff check .
```

## 実行（ローカル）

```bash
uv run uvicorn millicall.main:app --host 0.0.0.0 --port 8000
```

詳細な設計は `docs/`（Wiki 同期対象）を参照。
