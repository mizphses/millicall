"""FastMCP サーバー骨格（Streamable HTTP）。

- `build_mcp(app, provider, settings)` で FastMCP インスタンスを生成する。
- 後続タスク（Task 2–6）はここで返る `FastMCP` に `@mcp.tool()` / `@mcp.resource()` を足す。
- ツールは `app.state` 経由で依存（sessionmaker / secrets / esl_command / esl_command_lock /
  esl_reconnect / session_registry / settings）にアクセスする。DI は「create_app 時に渡した
  `app` を build_mcp のクロージャで束縛し、ツール実行時に `app.state.*` を読む」方式で確立する
  （app.state は lifespan で埋まる可変オブジェクトなので、束縛時点で未設定でも実行時には有効）。
  ツール内では `get_app_state(mcp)` ヘルパで State を取得できる。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import TransportSecuritySettings
from pydantic import AnyHttpUrl

if TYPE_CHECKING:
    from fastapi import FastAPI
    from starlette.datastructures import State

    from millicall.config import Settings
    from millicall.mcp_server.oauth import MillicallOAuthProvider

# サーバー全体指示（旧実装 verbatim — 契約 §「FastMCP instructions」）。
INSTRUCTIONS = """\
Millicall PBX — IP電話の発信・通話制御ツール。

## 電話をかける時
常に `converse` ツールを最優先で使ってください。
converseは発信→自律会話→切電まで全自動で行います。
あなたは目的(purpose)と要点(key_points)を渡すだけです。

dial, say, say_and_listen, listen, hangup はユーザーが明示的に手動制御を指示した場合のみ使用してください。
デフォルトでは常にconverseを選んでください。

## converseの使い方
- purpose: 会話の目的を具体的に書く（例: "ラーメンを1杯注文する"）
- key_points: 伝えるべき情報を改行区切りで書く（例: "味噌ラーメン\\n大盛り"）
- your_name: 名乗る名前（任意）

## その他のツール
- `list_contacts` / `add_contact` / `delete_contact`: 電話帳
- `list_extensions` / `list_trunks`: PBX情報

## 禁止事項
- ユーザーの明示的な指示なしに電話をかけないでください。
- ユーザーに確認せず勝手にかけ直さないでください。
"""


def build_mcp(
    app: FastAPI,
    provider: MillicallOAuthProvider,
    settings: Settings,
) -> FastMCP:
    """FastMCP（Streamable HTTP + OAuth 2.1）を生成する。

    tools は本関数で登録するダミー `ping` のみ（Task 6 で 15 tools + guide を追加）。
    `app` をクロージャで束縛し、ツールは `app.state` から依存を取得する（DI 契約）。
    """
    mcp = FastMCP(
        "Millicall PBX",
        instructions=INSTRUCTIONS,
        transport_security=TransportSecuritySettings(
            allowed_hosts=list(settings.mcp_allowed_hosts),
        ),
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(settings.mcp_issuer_url),
            resource_server_url=AnyHttpUrl(settings.mcp_issuer_url),
            client_registration_options=ClientRegistrationOptions(enabled=True),
        ),
    )

    # ツールが app.state へアクセスするためのブリッジを FastMCP に保持させる。
    # 後続タスクのツールは `get_app_state(mcp)` でこの State を取得する。
    mcp._millicall_app = app  # type: ignore[attr-defined]

    @mcp.tool()
    def ping() -> str:
        """疎通確認用のダミーツール。"pong" を返します。"""
        return "pong"

    # 契約 §1–§15 の 15 ツール + guide://outbound-calling リソースを登録（Task 6）。
    # 循環 import 回避のため関数内 import。
    from millicall.mcp_server.tools import register_tools

    register_tools(mcp)

    return mcp


def get_app_state(mcp: FastMCP) -> State:
    """ツール実装から FastAPI `app.state`（DI コンテナ）を取得する（DI 契約）。"""
    app = mcp._millicall_app  # type: ignore[attr-defined]
    return app.state
