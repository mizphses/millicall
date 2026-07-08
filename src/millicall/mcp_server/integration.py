"""FastAPI への MCP マウント + lifespan 統合。

設計（旧実装 `../millicall-pbx/src/millicall/main.py:386-403` の弱点を回避）:
- `mcp.streamable_http_app()` が生成する Starlette アプリ（root に /.well-known/*, /authorize,
  /token, /register, /mcp を持つ）を **丸ごと "/" マウントすると SPA catch-all を食い潰す**ため、
  その `routes` と `user_middleware`（Bearer 認証 + AuthContext）を **既存 FastAPI アプリへ取り込む**。
  これにより /.well-known/* と /mcp は本来のルート（root）に現れ、SPA fallback より前に評価される。
- session_manager は Starlette アプリの lifespan では自動起動しないので、本体 lifespan で
  `run()` する（`mcp_session_context` を使用）。
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

from millicall.mcp_server.login import router as login_router
from millicall.mcp_server.oauth import MillicallOAuthProvider
from millicall.mcp_server.server import build_mcp

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI


def mount_mcp(app: FastAPI) -> None:
    """MCP（Streamable HTTP + OAuth 2.1）を FastAPI アプリに取り込む。

    `settings.mcp_enabled` が False の場合は何もしない。SPA catch-all より前に呼ぶこと。
    lifespan は `app.state.mcp_session_manager`（存在時）を `run()` すること。
    """
    settings = app.state.settings
    if not settings.mcp_enabled:
        return

    provider = MillicallOAuthProvider(settings.mcp_issuer_url)
    app.state.mcp_oauth_provider = provider

    mcp = build_mcp(app, provider, settings)
    app.state.mcp = mcp

    # streamable_http_app() 呼び出しで session_manager が遅延生成される。
    mcp_app = mcp.streamable_http_app()
    app.state.mcp_session_manager = mcp.session_manager

    # /.well-known/*, /authorize, /token, /register, /mcp を本体アプリの root に取り込む
    # （SPA catch-all より前に評価される順で追加）。
    app.router.routes.extend(mcp_app.routes)

    # Bearer 認証（BearerAuthBackend）+ AuthContext ミドルウェアを引き継ぐ。
    # 未取り込みだと /mcp の RequireAuthMiddleware が認証コンテキストを得られず全拒否になる。
    for mw in mcp_app.user_middleware:
        app.add_middleware(mw.cls, *mw.args, **mw.kwargs)

    # ログインページ（GET /mcp-login, POST /mcp-login/callback）を登録。
    app.include_router(login_router)


@contextlib.asynccontextmanager
async def mcp_session_context(app: FastAPI) -> AsyncIterator[None]:
    """MCP StreamableHTTPSessionManager を lifespan 内で起動/停止する。

    `manager.run()` は anyio タスクグループ（cancel scope）を張るため、その enter/exit は
    必ず同一タスク内で行う必要がある。lifespan の enter/exit がテストランナー等で別タスクに
    なっても壊れないよう、run() 専用の子タスクにスコープを閉じ込め、停止は Event で合図する。

    `mount_mcp` が無効（mcp_enabled=False）なら no-op。
    """
    manager = getattr(app.state, "mcp_session_manager", None)
    if manager is None:
        yield
        return

    started = asyncio.Event()
    stop = asyncio.Event()

    async def _runner() -> None:
        async with manager.run():
            started.set()
            await stop.wait()

    task = asyncio.create_task(_runner())

    # 起動完了（started）か、起動中の例外（task 完了）のどちらか早い方を待つ。
    started_wait = asyncio.ensure_future(started.wait())
    done, _ = await asyncio.wait({task, started_wait}, return_when=asyncio.FIRST_COMPLETED)
    if task in done:  # started 前に終了 = 起動失敗
        started_wait.cancel()
        task.result()  # 例外を送出
    started_wait.cancel()

    try:
        yield
    finally:
        stop.set()
        await task
