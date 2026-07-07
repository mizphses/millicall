"""システム管理サービス（Phase 6 Task 8）。

Docker Engine HTTP API を Tecnativa socket-proxy 経由で呼び出す。
core は raw /var/run/docker.sock に一切アクセスしない。
全ての Docker 操作は docker_proxy_url への httpx リクエストとして実行する。

アーキテクチャ:
  core (network_mode: host) → HTTP → docker-proxy (127.0.0.1:2375)
    → /var/run/docker.sock → Docker Engine

socket-proxy が許可する API のみが通過する（CONTAINERS=1, POST=1, INFO=1, VERSION=1）。
EXEC・IMAGES・volumes・swarm・secrets は proxy 側でブロック済み。
"""

import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from millicall.config import Settings

logger = logging.getLogger("millicall.system")

# Docker API レスポンスから返す安全なフィールドのみ（ホスト情報等は除く）
_CONTAINER_SAFE_KEYS = {"Id", "Names", "Image", "State", "Status"}

# system_info で返す Docker /info の安全なキー
_INFO_SAFE_KEYS = {
    "Containers",
    "ContainersRunning",
    "ContainersPaused",
    "ContainersStopped",
    "Images",
    "MemTotal",
    "NCPU",
    "OSType",
    "Architecture",
}

# httpx タイムアウト設定（秒）
_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0)


class DockerProxyDisabledError(Exception):
    """docker_proxy_url が空のため Docker 操作が無効な場合に送出する。"""


class DockerProxyError(Exception):
    """Docker proxy への HTTP リクエスト失敗を表す例外。

    ホスト情報・認証情報等の機密データは含めない。
    """


class ContainerNotAllowedError(Exception):
    """allowlist に含まれないコンテナを操作しようとした場合に送出する。"""

    def __init__(self, name: str) -> None:
        super().__init__(f"コンテナ '{name}' は操作対象の allowlist に含まれていません")
        self.name = name


class SystemService:
    """Docker socket-proxy 経由でコンテナ状態を管理するサービス。

    Args:
        settings: アプリケーション設定。docker_proxy_url・system_managed_containers を参照。
        http_client: テスト用に注入する httpx.AsyncClient。None の場合は内部で生成する。
    """

    def __init__(
        self,
        settings: "Settings",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._client = http_client

    # ------------------------------------------------------------------
    # 内部ヘルパ
    # ------------------------------------------------------------------

    def _proxy_url(self) -> str:
        """docker_proxy_url を返す。空文字ならエラーを送出する。"""
        url = self._settings.docker_proxy_url.rstrip("/")
        if not url:
            raise DockerProxyDisabledError(
                "docker_proxy_url が設定されていません。システム管理機能は無効です。"
            )
        return url

    def _allowed_names(self) -> list[str]:
        """再起動を許可するコンテナ名リストを返す。"""
        return self._settings.split_managed_containers()

    def _make_client(self) -> httpx.AsyncClient:
        """注入クライアントがあればそれを返し、なければ新規生成する。"""
        if self._client is not None:
            return self._client
        return httpx.AsyncClient(timeout=_TIMEOUT)

    async def _get(self, path: str) -> httpx.Response:
        """proxy_url + path に GET リクエストを送る。"""
        base = self._proxy_url()
        url = f"{base}{path}"
        client = self._make_client()
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            raise DockerProxyError(
                f"Docker proxy GET {path} が {exc.response.status_code} を返しました"
            ) from exc
        except httpx.RequestError as exc:
            raise DockerProxyError(
                f"Docker proxy GET {path} への接続に失敗しました: {type(exc).__name__}"
            ) from exc

    async def _post(self, path: str) -> httpx.Response:
        """proxy_url + path に POST リクエストを送る（ボディなし）。"""
        base = self._proxy_url()
        url = f"{base}{path}"
        client = self._make_client()
        try:
            resp = await client.post(url)
            # Docker restart は 204 No Content を返す
            if resp.status_code not in (200, 204):
                resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            raise DockerProxyError(
                f"Docker proxy POST {path} が {exc.response.status_code} を返しました"
            ) from exc
        except httpx.RequestError as exc:
            raise DockerProxyError(
                f"Docker proxy POST {path} への接続に失敗しました: {type(exc).__name__}"
            ) from exc

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------

    async def list_containers(self) -> list[dict]:
        """全コンテナの安全ビューを返す。

        GET /containers/json?all=1 を呼び出し、各コンテナから
        {id, name, image, state, status} の安全なサブセットのみを返す。
        managed_containers に含まれるコンテナには managed=True を付与する。

        Returns:
            コンテナ情報のリスト。

        Raises:
            DockerProxyDisabledError: docker_proxy_url が未設定の場合。
            DockerProxyError: Docker proxy への通信失敗。
        """
        resp = await self._get("/containers/json?all=1")
        raw: list[dict] = resp.json()
        allowed = set(self._allowed_names())

        result = []
        for c in raw:
            # Names フィールドは ["/container_name", ...] 形式。先頭スラッシュを除去する。
            names: list[str] = [n.lstrip("/") for n in (c.get("Names") or [])]
            primary_name = names[0] if names else ""
            entry = {
                "id": (c.get("Id") or "")[:12],  # short ID（12桁）
                "name": primary_name,
                "image": c.get("Image", ""),
                "state": c.get("State", ""),
                "status": c.get("Status", ""),
                "managed": primary_name in allowed,
            }
            result.append(entry)
        return result

    async def restart_container(self, name: str) -> None:
        """コンテナを再起動する。

        Args:
            name: コンテナ名（compose サービス名）。allowlist に含まれる必要がある。

        Raises:
            DockerProxyDisabledError: docker_proxy_url が未設定の場合。
            ContainerNotAllowedError: name が allowlist に含まれない場合。
            DockerProxyError: Docker proxy への通信失敗。
        """
        # allowlist チェック（proxy 呼び出し前に検証 — DockerProxyDisabled より先）
        allowed = set(self._allowed_names())
        if name not in allowed:
            raise ContainerNotAllowedError(name)

        # _post 内で docker_proxy_url を参照するため、ここで disabled チェックが走る
        await self._post(f"/containers/{name}/restart")
        logger.info("コンテナを再起動しました: %s", name)

    async def system_info(self) -> dict:
        """Docker エンジンのバージョン・コンテナ数等の安全なサブセットを返す。

        GET /info と GET /version を呼び出し、機密ホスト情報を除いた
        安全なフィールドのみを返す。

        Returns:
            {info: {...}, version: {...}} の辞書。

        Raises:
            DockerProxyDisabledError: docker_proxy_url が未設定の場合。
            DockerProxyError: Docker proxy への通信失敗。
        """
        info_resp = await self._get("/info")
        version_resp = await self._get("/version")

        raw_info: dict = info_resp.json()
        raw_version: dict = version_resp.json()

        # 安全なキーのみ返す（ホスト名・ID等のホスト固有情報は除外）
        safe_info = {k: raw_info[k] for k in _INFO_SAFE_KEYS if k in raw_info}
        safe_version = {
            k: raw_version[k]
            for k in ("Version", "ApiVersion", "GoVersion", "Os", "Arch")
            if k in raw_version
        }

        return {"info": safe_info, "version": safe_version}
