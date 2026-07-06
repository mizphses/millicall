"""Millicall MCP サーバー（Phase 4a）。

公式 `mcp` Python SDK（FastMCP / Streamable HTTP）で Millicall PBX を MCP 経由に公開する。
モジュール名は SDK の top-level `mcp` パッケージと混同しないよう `mcp_server` とする。

後続タスク向けの契約:
- `build_mcp(app, provider, settings) -> FastMCP`: ツールはここで返る mcp に `@mcp.tool()` で追加。
- `get_app_state(mcp) -> State`: ツール実装から `app.state`（DI コンテナ）を取得。
- `mount_mcp(app)`: FastAPI アプリへ /mcp + OAuth + /mcp-login を取り込む（SPA より前に呼ぶ）。
- `mcp_session_context(app)`: lifespan で session_manager を起動/停止する async context。
"""

from millicall.mcp_server.integration import mcp_session_context, mount_mcp
from millicall.mcp_server.oauth import MillicallOAuthProvider
from millicall.mcp_server.server import build_mcp, get_app_state

__all__ = [
    "MillicallOAuthProvider",
    "build_mcp",
    "get_app_state",
    "mcp_session_context",
    "mount_mcp",
]
