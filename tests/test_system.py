"""システム管理 API テスト（Phase 6 Task 8）。

テスト対象:
  - GET /api/system/containers     : コンテナ一覧（安全ビュー）
  - POST /api/system/containers/{name}/restart : 再起動（allowlist・CSRF・admin）
  - GET /api/system/info           : Docker エンジン情報（安全サブセット）

カバレッジ:
  - list_containers: 安全なフィールドのみが返る（Id 短縮・Names スラッシュ除去・managed フラグ）
  - restart: allowlist 内コンテナ → 正しい proxy URL に POST が届く
  - restart: allowlist 外コンテナ → 403、proxy への呼び出しなし
  - docker_proxy_url="" → 全エンドポイント 503
  - 非 admin → 403（require_admin ゲート）
  - 監査ログ: restart 成功時に system.container.restart が記録される
  - raw docker.sock への参照がコード中に存在しないことを確認
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from millicall.auth.security import hash_password
from millicall.config import Settings
from millicall.main import create_app
from millicall.models import User
from millicall.system.service import (
    ContainerNotAllowedError,
    DockerProxyDisabledError,
    SystemService,
)

# ---------------------------------------------------------------------------
# フェイク Docker proxy レスポンス
# ---------------------------------------------------------------------------

_FAKE_CONTAINERS = [
    {
        "Id": "abc123def456789",
        "Names": ["/core"],
        "Image": "ghcr.io/mizphses/millicall-core:latest",
        "State": "running",
        "Status": "Up 2 hours",
    },
    {
        "Id": "fff000aaa111bbb",
        "Names": ["/unknown-service"],
        "Image": "alpine:latest",
        "State": "exited",
        "Status": "Exited (0) 10 minutes ago",
    },
]

_FAKE_INFO = {
    "Containers": 4,
    "ContainersRunning": 3,
    "ContainersPaused": 0,
    "ContainersStopped": 1,
    "Images": 10,
    "MemTotal": 8000000000,
    "NCPU": 4,
    "OSType": "linux",
    "Architecture": "x86_64",
    # 以下は安全フィールドではないので返してはいけない
    "Name": "secret-hostname",
    "DockerRootDir": "/var/lib/docker",
    "ID": "host-id-secret",
}

_FAKE_VERSION = {
    "Version": "24.0.0",
    "ApiVersion": "1.43",
    "GoVersion": "go1.21.0",
    "Os": "linux",
    "Arch": "amd64",
    # 安全フィールドではないので返してはいけない
    "KernelVersion": "5.15.0",
}


def _make_proxy_transport(*, restart_status: int = 204) -> httpx.MockTransport:
    """フェイク Docker proxy レスポンスを返す MockTransport。"""

    def _handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        query = str(request.url.query)
        method = request.method

        if method == "GET" and path == "/containers/json":
            assert "all=1" in query
            return httpx.Response(200, json=_FAKE_CONTAINERS)
        if method == "POST" and path.startswith("/containers/") and path.endswith("/restart"):
            return httpx.Response(restart_status)
        if method == "GET" and path == "/info":
            return httpx.Response(200, json=_FAKE_INFO)
        if method == "GET" and path == "/version":
            return httpx.Response(200, json=_FAKE_VERSION)
        # 想定外のリクエストは 404 を返す（テストで検出しやすくするため）
        return httpx.Response(404, text=f"unexpected: {method} {path}")

    return httpx.MockTransport(_handler)


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------


def _make_settings(tmp_path: Path, *, docker_proxy_url: str = "http://127.0.0.1:2375") -> Settings:
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        fs_config_dir=tmp_path / "fs",
        cookie_secure=False,
        esl_timeout_seconds=1.0,
        docker_proxy_url=docker_proxy_url,
        system_managed_containers="core,freeswitch,netd,docker-proxy",
    )


@pytest_asyncio.fixture
async def proxy_app(tmp_path):
    """docker_proxy_url が設定済みのアプリを起動する。"""
    settings = _make_settings(tmp_path)
    application = create_app(settings)
    # フェイク SystemService を注入する（実 Docker 呼び出しを防ぐ）
    transport = _make_proxy_transport()
    fake_client = httpx.AsyncClient(transport=transport)
    application.state.system_service_override = SystemService(settings, http_client=fake_client)
    async with application.router.lifespan_context(application):
        yield application


@pytest_asyncio.fixture
async def disabled_app(tmp_path):
    """docker_proxy_url="" （機能無効）のアプリを起動する。"""
    settings = _make_settings(tmp_path, docker_proxy_url="")
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest_asyncio.fixture
async def proxy_client(proxy_app):
    transport = ASGITransport(app=proxy_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def disabled_client(disabled_app):
    transport = ASGITransport(app=disabled_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _create_admin(app, username: str = "sysadmin", password: str = "Admin123!") -> tuple[str, str]:
    sm = app.state.sessionmaker
    async with sm() as session:
        session.add(
            User(
                username=username,
                hashed_password=hash_password(password),
                display_name=username,
                role="admin",
                origin="local",
            )
        )
        await session.commit()
    return username, password


async def _create_user(app, username: str = "regular", password: str = "User123!") -> tuple[str, str]:
    sm = app.state.sessionmaker
    async with sm() as session:
        session.add(
            User(
                username=username,
                hashed_password=hash_password(password),
                display_name=username,
                role="user",
                origin="local",
            )
        )
        await session.commit()
    return username, password


def _csrf_headers(client: AsyncClient) -> dict[str, str]:
    """クライアントの cookie jar から CSRF トークンを取り出してヘッダーを組む。"""
    token = client.cookies.get("millicall_csrf")
    return {"X-CSRF-Token": token} if token else {}


async def _login(client: AsyncClient, username: str, password: str) -> None:
    resp = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"login failed: {resp.text}"


# ---------------------------------------------------------------------------
# SystemService ユニットテスト（HTTP モック注入）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_containers_returns_safe_view(tmp_path):
    """list_containers は安全なフィールドのみを返し、Id を 12 桁に短縮する。"""
    settings = _make_settings(tmp_path)
    transport = _make_proxy_transport()
    client = httpx.AsyncClient(transport=transport)
    svc = SystemService(settings, http_client=client)

    result = await svc.list_containers()

    assert len(result) == 2
    core = next(c for c in result if c["name"] == "core")
    unknown = next(c for c in result if c["name"] == "unknown-service")

    # Id は 12 桁に短縮される
    assert core["id"] == "abc123def456"
    assert len(core["id"]) == 12

    # Names の先頭スラッシュが除去される
    assert not core["name"].startswith("/")

    # 安全フィールドが揃っている
    assert core["image"] == "ghcr.io/mizphses/millicall-core:latest"
    assert core["state"] == "running"
    assert core["status"] == "Up 2 hours"

    # managed フラグが正しく設定される（core は allowlist 内）
    assert core["managed"] is True
    assert unknown["managed"] is False


@pytest.mark.asyncio
async def test_restart_allowlisted_container_calls_proxy(tmp_path):
    """allowlist 内コンテナの restart は正しい proxy URL に POST を送る。"""
    settings = _make_settings(tmp_path)
    requested_paths: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(f"{request.method} {request.url.path}")
        if request.method == "POST":
            return httpx.Response(204)
        return httpx.Response(200, json=[])

    transport = httpx.MockTransport(_handler)
    client = httpx.AsyncClient(transport=transport)
    svc = SystemService(settings, http_client=client)

    await svc.restart_container("core")

    assert any("/containers/core/restart" in p for p in requested_paths), (
        f"proxy へ /containers/core/restart の POST が届かなかった: {requested_paths}"
    )


@pytest.mark.asyncio
async def test_restart_non_allowlisted_container_raises_403(tmp_path):
    """allowlist 外のコンテナを restart しようとすると ContainerNotAllowedError。"""
    settings = _make_settings(tmp_path)
    proxy_called = []

    def _handler(request: httpx.Request) -> httpx.Response:
        proxy_called.append(request.url.path)
        return httpx.Response(204)

    transport = httpx.MockTransport(_handler)
    client = httpx.AsyncClient(transport=transport)
    svc = SystemService(settings, http_client=client)

    with pytest.raises(ContainerNotAllowedError):
        await svc.restart_container("evil-container")

    # proxy への呼び出しが一切発生していないことを確認
    assert not proxy_called, f"allowlist 外コンテナで proxy が呼ばれた: {proxy_called}"


@pytest.mark.asyncio
async def test_disabled_list_containers_raises(tmp_path):
    """docker_proxy_url="" の場合 list_containers は DockerProxyDisabledError を送出する。"""
    settings = _make_settings(tmp_path, docker_proxy_url="")
    svc = SystemService(settings)

    with pytest.raises(DockerProxyDisabledError):
        await svc.list_containers()


@pytest.mark.asyncio
async def test_disabled_restart_raises(tmp_path):
    """docker_proxy_url="" の場合 restart_container は DockerProxyDisabledError を送出する。
    ただし allowlist チェックは先に走り、allowlist 外なら ContainerNotAllowedError が優先。
    """
    settings = _make_settings(tmp_path, docker_proxy_url="")
    svc = SystemService(settings)

    # allowlist 内コンテナ → disabled エラー（proxy URL チェックが後）
    # ※ allowlist チェックが先に走るため、非 allowlist は ContainerNotAllowedError が優先される
    with pytest.raises(ContainerNotAllowedError):
        await svc.restart_container("evil")

    # allowlist 内コンテナ + disabled → DockerProxyDisabledError
    with pytest.raises(DockerProxyDisabledError):
        await svc.restart_container("core")


@pytest.mark.asyncio
async def test_system_info_returns_safe_subset(tmp_path):
    """system_info は機密ホスト情報を除いた安全サブセットのみを返す。"""
    settings = _make_settings(tmp_path)
    transport = _make_proxy_transport()
    client = httpx.AsyncClient(transport=transport)
    svc = SystemService(settings, http_client=client)

    result = await svc.system_info()

    assert "info" in result
    assert "version" in result

    info = result["info"]
    version = result["version"]

    # 安全フィールドが含まれる
    assert info["Containers"] == 4
    assert info["OSType"] == "linux"
    assert version["Version"] == "24.0.0"
    assert version["ApiVersion"] == "1.43"

    # 機密フィールドが含まれない
    assert "Name" not in info           # ホスト名
    assert "DockerRootDir" not in info  # ファイルシステムパス
    assert "ID" not in info             # Docker デーモン ID
    assert "KernelVersion" not in version


# ---------------------------------------------------------------------------
# HTTP API 統合テスト（ASGI + 認証 + CSRF）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_containers_endpoint(proxy_app, proxy_client):
    """GET /api/system/containers は admin 認証済みで 200 を返す。"""
    await _create_admin(proxy_app)
    await _login(proxy_client, "sysadmin", "Admin123!")
    resp = await proxy_client.get("/api/system/containers")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 2


@pytest.mark.asyncio
async def test_list_containers_non_admin_returns_403(proxy_app, proxy_client):
    """GET /api/system/containers は非 admin だと 403。"""
    await _create_user(proxy_app)
    await _login(proxy_client, "regular", "User123!")
    resp = await proxy_client.get("/api/system/containers")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_list_containers_unauthenticated_returns_401(proxy_client):
    """GET /api/system/containers は未認証だと 401。"""
    resp = await proxy_client.get("/api/system/containers")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_containers_disabled_returns_503(disabled_app, disabled_client):
    """docker_proxy_url="" の場合 GET /api/system/containers は 503。"""
    await _create_admin(disabled_app)
    await _login(disabled_client, "sysadmin", "Admin123!")
    resp = await disabled_client.get("/api/system/containers")
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_restart_allowlisted_returns_204(proxy_app, proxy_client):
    """allowlist 内コンテナの POST /restart は 204 を返す。"""
    await _create_admin(proxy_app)
    await _login(proxy_client, "sysadmin", "Admin123!")
    csrf = _csrf_headers(proxy_client)
    resp = await proxy_client.post(
        "/api/system/containers/core/restart", headers=csrf
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_restart_non_allowlisted_returns_403(proxy_app, proxy_client):
    """allowlist 外コンテナの POST /restart は 403 を返す。"""
    await _create_admin(proxy_app)
    await _login(proxy_client, "sysadmin", "Admin123!")
    csrf = _csrf_headers(proxy_client)
    resp = await proxy_client.post(
        "/api/system/containers/evil-container/restart", headers=csrf
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_restart_disabled_returns_503(disabled_app, disabled_client):
    """docker_proxy_url="" の場合 POST /restart は 503 を返す（allowlist 内コンテナ）。"""
    await _create_admin(disabled_app)
    await _login(disabled_client, "sysadmin", "Admin123!")
    csrf = _csrf_headers(disabled_client)
    resp = await disabled_client.post(
        "/api/system/containers/core/restart", headers=csrf
    )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_restart_non_admin_returns_403(proxy_app, proxy_client):
    """非 admin ユーザーの POST /restart は 403。"""
    await _create_user(proxy_app)
    await _login(proxy_client, "regular", "User123!")
    csrf = _csrf_headers(proxy_client)
    resp = await proxy_client.post(
        "/api/system/containers/core/restart", headers=csrf
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_system_info_endpoint(proxy_app, proxy_client):
    """GET /api/system/info は admin 認証済みで 200 を返す。"""
    await _create_admin(proxy_app)
    await _login(proxy_client, "sysadmin", "Admin123!")
    resp = await proxy_client.get("/api/system/info")
    assert resp.status_code == 200
    data = resp.json()
    assert "info" in data
    assert "version" in data


@pytest.mark.asyncio
async def test_system_info_disabled_returns_503(disabled_app, disabled_client):
    """docker_proxy_url="" の場合 GET /api/system/info は 503。"""
    await _create_admin(disabled_app)
    await _login(disabled_client, "sysadmin", "Admin123!")
    resp = await disabled_client.get("/api/system/info")
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# 監査ログ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restart_records_audit(proxy_app, proxy_client):
    """コンテナ再起動成功時に system.container.restart が監査ログに記録される。"""
    from sqlalchemy import select

    from millicall.models import AuditLog

    await _create_admin(proxy_app)
    await _login(proxy_client, "sysadmin", "Admin123!")
    csrf = _csrf_headers(proxy_client)
    resp = await proxy_client.post(
        "/api/system/containers/freeswitch/restart", headers=csrf
    )
    assert resp.status_code == 204

    sm = proxy_app.state.sessionmaker
    async with sm() as session:
        log = await session.scalar(
            select(AuditLog).where(AuditLog.action == "system.container.restart")
        )
    assert log is not None
    assert log.target_id == "freeswitch"
    assert log.actor_label == "sysadmin"


# ---------------------------------------------------------------------------
# セキュリティ: raw docker.sock への参照チェック
# ---------------------------------------------------------------------------


def _non_comment_lines(source: str) -> str:
    """Python ソースからコメント行・docstring 行を除いたコードのみを返す。

    完全なパーサーではなく、# コメント行と「"」3つで囲まれたブロックを除く簡易版。
    grep 的なチェックに十分な精度を持つ。
    """
    in_docstring = False
    filtered: list[str] = []
    for line in source.splitlines():
        stripped = line.strip()
        if not in_docstring:
            if stripped.startswith('"""') or stripped.startswith("'''"):
                in_docstring = True
                # docstring 開始行は除外
                # 1行完結型（"""..."""）のチェック
                quote = stripped[:3]
                rest = stripped[3:]
                if rest.endswith(quote) and len(rest) >= 3:
                    # 1行完結 docstring
                    in_docstring = False
                continue
            if stripped.startswith("#"):
                continue
            filtered.append(line)
        else:
            if '"""' in stripped or "'''" in stripped:
                in_docstring = False
    return "\n".join(filtered)


def test_no_raw_docker_sock_in_service_code():
    """system/service.py の実行コードに raw docker.sock・シェルアウトの参照がないことを確認する。

    コメント・docstring 内の言及はセキュリティ上の説明として許容する。
    実行コード（import 文・関数本体等）に含まれていないことを検証する。
    """
    service_path = Path(__file__).parent.parent / "src" / "millicall" / "system" / "service.py"
    source = service_path.read_text(encoding="utf-8")
    code_only = _non_comment_lines(source)

    # シェルアウトの禁止: 実行コード内に subprocess・os.system・os.popen が含まれてはならない
    assert "import subprocess" not in code_only, "service.py に subprocess インポートが見つかりました"
    assert "subprocess." not in code_only, "service.py に subprocess 呼び出しが見つかりました"
    assert "os.system" not in code_only, "service.py に os.system が見つかりました"
    assert "os.popen" not in code_only, "service.py に os.popen が見つかりました"
    # raw socket マウントパスのハードコードを禁止（コード中）
    assert "/var/run/docker.sock" not in code_only, (
        "service.py の実行コードに /var/run/docker.sock のハードコードが見つかりました"
    )


def test_no_raw_docker_sock_in_router_code():
    """system/router.py の実行コードに raw docker.sock・シェルアウトの参照がないことを確認する。"""
    router_path = Path(__file__).parent.parent / "src" / "millicall" / "system" / "router.py"
    source = router_path.read_text(encoding="utf-8")
    code_only = _non_comment_lines(source)

    assert "import subprocess" not in code_only, "router.py に subprocess インポートが見つかりました"
    assert "subprocess." not in code_only, "router.py に subprocess 呼び出しが見つかりました"
    assert "os.system" not in code_only, "router.py に os.system が見つかりました"
    assert "/var/run/docker.sock" not in code_only, (
        "router.py の実行コードに /var/run/docker.sock のハードコードが見つかりました"
    )


# ---------------------------------------------------------------------------
# config バリデータ
# ---------------------------------------------------------------------------


def test_split_managed_containers_default():
    """split_managed_containers はデフォルト値を正しく分割する。"""
    settings = Settings(
        data_dir="/tmp",
        database_url="sqlite+aiosqlite:///test.db",
        cookie_secure=False,
    )
    result = settings.split_managed_containers()
    assert "core" in result
    assert "freeswitch" in result
    assert "netd" in result
    assert "docker-proxy" in result


def test_split_managed_containers_custom():
    """split_managed_containers はカスタム値を正しく分割する。"""
    settings = Settings(
        data_dir="/tmp",
        database_url="sqlite+aiosqlite:///test.db",
        cookie_secure=False,
        system_managed_containers="core, freeswitch , netd",
    )
    result = settings.split_managed_containers()
    assert result == ["core", "freeswitch", "netd"]


def test_docker_proxy_url_default_empty():
    """docker_proxy_url のデフォルト値は空文字（機能無効）。"""
    settings = Settings(
        data_dir="/tmp",
        database_url="sqlite+aiosqlite:///test.db",
        cookie_secure=False,
    )
    assert settings.docker_proxy_url == ""
