"""アプリ設定 API（管理者専用）。

GET /api/settings  — 実効設定を返す。秘密値は実値を返さず「設定済みか否か」のみ返す。
PUT /api/settings  — allowlist キーの上書き保存 / リセット。変更は監査ログに記録する
                     （秘密値の実値はログに残さない）。
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.app_settings.service import (
    EDITABLE_SETTINGS,
    SECRET_KEYS,
    TELEPHONY_REGEN_KEYS,
    SettingsService,
    SettingValidationError,
)
from millicall.audit import get_client_ip, record_audit
from millicall.deps import get_session, require_admin
from millicall.models import User

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsRead(BaseModel):
    """実効設定のレスポンス。

    values には秘密キーを含めない。秘密キーは secrets に「設定済みか（非空か）」のみ返す。
    overridden は DB で上書きされているキーの一覧（env デフォルトとの区別用）。
    """

    values: dict[str, Any]
    overridden: list[str]
    secrets: dict[str, bool]


class SettingsUpdate(BaseModel):
    """設定更新リクエスト。

    values: 上書きするキーと値（秘密キーは平文文字列を渡すと暗号化保存される）。
    reset: 上書きを削除して env デフォルトへ戻すキー。
    """

    values: dict[str, Any] = Field(default_factory=dict)
    reset: list[str] = Field(default_factory=list)


def _get_service(request: Request) -> SettingsService:
    return request.app.state.settings_service


async def _build_read(svc: SettingsService) -> SettingsRead:
    """実効 Settings から GET レスポンスを組み立てる（秘密値はマスク）。"""
    eff = await svc.effective()
    return SettingsRead(
        values={k: getattr(eff, k) for k in EDITABLE_SETTINGS if k not in SECRET_KEYS},
        overridden=sorted(await svc.overridden_keys()),
        secrets={k: bool(getattr(eff, k)) for k in SECRET_KEYS},
    )


@router.get("", response_model=SettingsRead)
async def get_settings_api(
    request: Request,
    _admin: User = Depends(require_admin),
) -> SettingsRead:
    """実効設定を取得する（管理者専用）。"""
    return await _build_read(_get_service(request))


@router.put("", response_model=SettingsRead)
async def update_settings_api(
    body: SettingsUpdate,
    request: Request,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> SettingsRead:
    """設定を更新する（管理者専用）。

    allowlist 外のキー・型不正・レンジ外は 400。変更内容は監査ログに記録する
    （秘密値は "***" にマスク）。反映は再起動不要（キャッシュ無効化で即時）。
    """
    svc = _get_service(request)
    try:
        validated = await svc.apply_update(session, body.values, body.reset)
    except SettingValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if validated or body.reset:
        await record_audit(
            session,
            actor_user_id=admin.id,
            actor_label=admin.username,
            action="settings.update",
            target_type="app_settings",
            detail={
                "updated": {k: "***" if k in SECRET_KEYS else v for k, v in validated.items()},
                "reset": body.reset,
            },
            ip_address=get_client_ip(request),
        )
    await session.commit()
    svc.invalidate()

    # 国際発信 allowlist / 匿名着信拒否は FreeSWITCH dialplan に展開されるため、
    # 変更時は設定ファイルを再生成して reloadxml する（ESL 不達でも保存自体は成功扱い）。
    changed = set(validated) | set(body.reset)
    if changed & TELEPHONY_REGEN_KEYS:
        eff = await svc.effective()
        listener = request.app.state.change_listener
        listener.update_outbound_policy(
            [p.strip() for p in eff.outbound_international_allow.split(",") if p.strip()],
            eff.sip_reject_anonymous,
        )
        await listener.notify(session)

    return await _build_read(svc)
