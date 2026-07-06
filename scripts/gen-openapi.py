#!/usr/bin/env python
"""FastAPI アプリの OpenAPI スキーマを JSON として標準出力へ書き出す。

フロントエンドの `npm run gen:api`（openapi-typescript）が消費する。
DB 接続や lifespan は起動しないため、依存なしで冪等に実行できる。

使い方（リポジトリルートから）:
    uv run python scripts/gen-openapi.py > frontend/src/api/openapi.json
"""

import json
import sys

from millicall.main import create_app


def main() -> None:
    app = create_app()
    schema = app.openapi()
    json.dump(schema, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
