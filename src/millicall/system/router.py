"""システム管理 API（Phase 6 Task 8）。

Docker socket-proxy 経由でコンテナの状態確認・再起動・Docker エンジン情報取得を提供する。

セキュリティルール:
  - 全エンドポイントは admin 権限が必要（require_admin）。
  - POST /restart は CSRF 保護対象（/api/* に適用される CsrfMiddleware が受け持つ）。
  - core は raw docker.sock に一切触れない。SystemService 経由で proxy URL のみ使用する。
  - 再起動対象は system_managed_containers allowlist に限定する（任意コンテナ再起動不可）。
  - docker_proxy_url が未設定の場合は全エンドポイントが 503 を返す（feature disabled）。

core → proxy 接続:
  core は network_mode: host で動作する。
  docker-proxy コンテナは ports で 127.0.0.1:2375:2375 をバインドしているため、
  core から http://127.0.0.1:2375 でアクセスできる。
  環境変数 MILLICALL_DOCKER_PROXY_URL=http://127.0.0.1:2375 を設定すること。
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.audit import get_client_ip, record_audit
from millicall.deps import get_session, require_admin
from millicall.system.service import (
    ContainerNotAllowedError,
    DockerProxyDisabledError,
    DockerProxyError,
    SystemService,
)

router = APIRouter(
    prefix="/api/system",
    tags=["system"],
    dependencies=[Depends(require_admin)],
)


# ---------------------------------------------------------------------------
# 依存関係
# ---------------------------------------------------------------------------


def _get_system_service(request: Request) -> SystemService:
    """app.state.settings から SystemService を生成する。

    テストでは app.state.system_service_override を設定することで
    モック済みの SystemService を注入できる。
    """
    override = getattr(request.app.state, "system_service_override", None)
    if override is not None:
        return override
    return SystemService(request.app.state.settings)


# ---------------------------------------------------------------------------
# エンドポイント
# ---------------------------------------------------------------------------


@router.get("/containers")
async def list_containers(
    service: SystemService = Depends(_get_system_service),
) -> list[dict]:
    """コンテナ一覧を返す。

    Docker socket-proxy 経由で全コンテナを取得し、
    {id, name, image, state, status, managed} の安全なビューで返す。
    managed=True のコンテナのみ /restart で操作できる。

    Returns:
        コンテナ情報リスト。

    Raises:
        503: docker_proxy_url が未設定（システム管理機能 disabled）。
        502: Docker proxy への通信失敗。
    """
    try:
        return await service.list_containers()
    except DockerProxyDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except DockerProxyError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Docker proxy への通信に失敗しました: {exc}",
        ) from exc


@router.post("/containers/{name}/restart", status_code=status.HTTP_204_NO_CONTENT)
async def restart_container(
    name: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    service: SystemService = Depends(_get_system_service),
    current_user=Depends(require_admin),
) -> None:
    """指定コンテナを再起動する。

    allowlist（system_managed_containers 設定）に含まれるコンテナのみ操作可能。
    操作は監査ログ（action="system.container.restart"）に記録される。

    Args:
        name: コンテナ名（compose サービス名）。

    Raises:
        403: name が allowlist に含まれない。
        503: docker_proxy_url が未設定。
        502: Docker proxy への通信失敗。
    """
    try:
        await service.restart_container(name)
    except DockerProxyDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except ContainerNotAllowedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except DockerProxyError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Docker proxy への通信に失敗しました: {exc}",
        ) from exc

    # 監査ログ記録（再起動成功時のみ）
    await record_audit(
        session,
        actor_user_id=current_user.id,
        actor_label=current_user.username,
        action="system.container.restart",
        target_type="container",
        target_id=name,
        detail={"container": name},
        ip_address=get_client_ip(request),
    )
    await session.commit()


@router.get("/info")
async def system_info(
    service: SystemService = Depends(_get_system_service),
) -> dict:
    """Docker エンジン情報の安全なサブセットを返す。

    GET /info と GET /version を呼び出して {info: {...}, version: {...}} 形式で返す。
    機密ホスト情報（HostName・DockerRootDir 等）は除外済み。

    Returns:
        {info: {...}, version: {...}} の辞書。

    Raises:
        503: docker_proxy_url が未設定。
        502: Docker proxy への通信失敗。
    """
    try:
        return await service.system_info()
    except DockerProxyDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except DockerProxyError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Docker proxy への通信に失敗しました: {exc}",
        ) from exc
