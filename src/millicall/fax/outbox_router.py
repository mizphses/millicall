"""FAX 送信 API（issue #141）。

- POST /api/faxes/outbox                : PDF をアップロードして送信ジョブを登録（202。変換は非同期）
- GET  /api/faxes/outbox                : 送信ジョブの一覧（新しい順・ページング）
- GET  /api/faxes/outbox/trunks         : 送信に使えるトランク（名前・表示名・種別・既定）
- GET  /api/faxes/outbox/{id}           : 送信ジョブの詳細
- POST /api/faxes/outbox/{id}/retry     : 失敗・取消したジョブを再送（試行回数をリセット。上限あり）
- POST /api/faxes/outbox/{id}/cancel    : 変換中・送信待ちのジョブを取り消す
- GET  /api/faxes/outbox/{id}/pdf       : 元 PDF（署名付きトークン or セッション）

権限: 管理者。設定 fax_send_allow_users=true のときは一般ユーザーも送信でき、
自分が登録したジョブだけを参照・操作できる。

このルーターは受信 FAX の ``/api/faxes/{fax_id}`` より先に登録すること
（``/api/faxes/outbox`` が fax_id として解釈されないように）。
"""

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile as StarletteUploadFile

from millicall.audit import get_client_ip, record_audit
from millicall.config import Settings
from millicall.crypto import SecretBox
from millicall.deps import get_current_user, get_secret_box, get_session, get_settings_dep
from millicall.fax.convert import FAX_MAX_UPLOAD_BYTES
from millicall.fax.outbox import (
    MAX_MANUAL_RETRIES,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_QUEUED,
    FaxOutboxService,
    FaxSendError,
    send_trunk_candidates,
    verify_outbox_pdf_token,
)
from millicall.fax.service import resolve_inside
from millicall.models import FaxOutbox, User
from millicall.telephony.outbound_routes import select_default_outbound_trunk

router = APIRouter(prefix="/api/faxes/outbox", tags=["faxes"])


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class FaxOutboxRead(BaseModel):
    id: int
    to_number: str
    trunk_name: str
    pages: int
    status: str
    attempts: int
    max_attempts: int
    retry_count: int
    can_retry: bool
    last_error: str
    result_text: str
    remote_station_id: str
    has_pdf: bool
    created_by: int | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    next_attempt_at: datetime | None


class FaxOutboxList(BaseModel):
    items: list[FaxOutboxRead]
    total: int


class FaxSendTrunk(BaseModel):
    name: str
    display_name: str
    trunk_type: str
    is_default: bool


def _to_read(job: FaxOutbox) -> FaxOutboxRead:
    return FaxOutboxRead(
        id=job.id,
        to_number=job.to_number,
        trunk_name=job.trunk_name,
        pages=job.pages,
        status=job.status,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        retry_count=job.retry_count,
        can_retry=job.status in (STATUS_FAILED, STATUS_CANCELLED)
        and job.retry_count < MAX_MANUAL_RETRIES
        and bool(job.tiff_path),
        last_error=job.last_error,
        result_text=job.result_text,
        remote_station_id=job.remote_station_id,
        has_pdf=bool(job.pdf_path),
        created_by=job.created_by,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        next_attempt_at=job.next_attempt_at,
    )


def _allowed(user: User, settings: Settings) -> bool:
    return user.role == "admin" or (user.role == "user" and settings.fax_send_allow_users)


async def require_fax_sender(
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings_dep),
) -> User:
    if not _allowed(user, settings):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="FAX 送信の権限がありません")
    return user


def get_fax_outbox_service(request: Request) -> FaxOutboxService:
    return request.app.state.fax_outbox_service


async def _get_job(session: AsyncSession, job_id: int, user: User) -> FaxOutbox:
    job = await session.get(FaxOutbox, job_id)
    # 一般ユーザーには他人のジョブの有無も見せない（404 に揃える）。
    if job is None or (user.role != "admin" and job.created_by != user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="送信ジョブが見つかりません")
    return job


async def _audit(
    session: AsyncSession, request: Request, user: User, action: str, job: FaxOutbox
) -> None:
    # 宛先番号・トランク・ページ数のみ記録する（ファイル内容は記録しない）。
    await record_audit(
        session,
        actor_user_id=user.id,
        actor_label=user.username,
        action=action,
        target_type="fax_outbox",
        target_id=str(job.id),
        detail={"to": job.to_number, "trunk": job.trunk_name, "pages": job.pages},
        ip_address=get_client_ip(request),
    )


async def _reject(
    session: AsyncSession,
    request: Request,
    user: User,
    http_status: int,
    detail: str,
    *,
    to_number: str = "",
) -> HTTPException:
    """登録を拒否した送信要求を監査ログに残し、返す HTTPException を作る。"""
    await record_audit(
        session,
        actor_user_id=user.id,
        actor_label=user.username,
        action="fax.send_rejected",
        target_type="fax_outbox",
        target_id=None,
        # 番号は記録する（入力値なので長さだけ制限する）。ファイル内容は記録しない。
        detail={"to": to_number[:30], "status": http_status, "reason": detail[:200]},
        ip_address=get_client_ip(request),
    )
    await session.commit()
    return HTTPException(status_code=http_status, detail=detail)


# multipart の本文はハンドラ内で Starlette（python-multipart）に解析させる。FastAPI の
# File/Form 引数にすると、認証やサイズ確認より前に本文全体が一時ファイルへ書き出されるため。
# OpenAPI には手書きで載せる。
_SEND_REQUEST_BODY = {
    "required": True,
    "content": {
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "title": "FaxSendForm",
                "required": ["file", "to_number"],
                "properties": {
                    "file": {
                        "type": "string",
                        "format": "binary",
                        "description": "送信する PDF（20 MB・100 ページまで）",
                    },
                    "to_number": {
                        "type": "string",
                        "maxLength": 20,
                        "description": (
                            "宛先 FAX 番号（0 から始まる 10〜11 桁の国内番号。"
                            "先頭に 186/184 を付けても可）"
                        ),
                    },
                    "trunk_name": {
                        "type": "string",
                        "maxLength": 50,
                        "description": "送信に使うトランク名（省略時は既定の発信トランク）",
                    },
                },
            }
        }
    },
}
# multipart の境界・フィールド分の余裕。
_MULTIPART_OVERHEAD = 64 * 1024
_TOO_LARGE = f"PDF が大きすぎます（上限 {FAX_MAX_UPLOAD_BYTES // (1024 * 1024)} MB）"


def _form_text(value: object, max_length: int) -> str | None:
    """フォームの文字列フィールド（不正なら None）。"""
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > max_length:
        return None
    return value.strip()


@router.post(
    "",
    response_model=FaxOutboxRead,
    status_code=status.HTTP_202_ACCEPTED,
    openapi_extra={"requestBody": _SEND_REQUEST_BODY},
)
async def send_fax(
    request: Request,
    session: AsyncSession = Depends(get_session),
    service: FaxOutboxService = Depends(get_fax_outbox_service),
    user: User = Depends(require_fax_sender),
) -> FaxOutboxRead:
    if not request.headers.get("content-type", "").startswith("multipart/form-data"):
        raise await _reject(
            session, request, user, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "multipart/form-data で送信してください",
        )
    # 本文を読む前に宣言サイズで弾く（Content-Length を超えて読むことはない）。
    length = request.headers.get("content-length", "")
    if not length.isdigit():
        raise await _reject(
            session, request, user, status.HTTP_411_LENGTH_REQUIRED, "Content-Length が必要です"
        )
    if int(length) > FAX_MAX_UPLOAD_BYTES + _MULTIPART_OVERHEAD:
        raise await _reject(session, request, user, status.HTTP_413_CONTENT_TOO_LARGE, _TOO_LARGE)
    to_number: str | None = ""
    try:
        async with service.upload_slot():
            form = await request.form(max_files=1, max_fields=5)
            try:
                upload = form.get("file")
                to_number = _form_text(form.get("to_number"), 20)
                trunk_name = _form_text(form.get("trunk_name"), 50)
                if to_number is None or trunk_name is None:
                    raise await _reject(session, request, user, 422, "入力の形式が不正です")
                if not isinstance(upload, StarletteUploadFile):
                    raise await _reject(
                        session, request, user, 422, "PDF ファイルを指定してください",
                        to_number=to_number,
                    )
                data = await upload.read(FAX_MAX_UPLOAD_BYTES + 1)
            finally:
                await form.close()
            if len(data) > FAX_MAX_UPLOAD_BYTES:
                raise await _reject(
                    session, request, user, status.HTTP_413_CONTENT_TOO_LARGE, _TOO_LARGE,
                    to_number=to_number,
                )
            if not data:
                raise await _reject(
                    session, request, user, 422, "PDF ファイルが空です", to_number=to_number
                )
            job = await service.submit(
                data=data, to_number=to_number, trunk_name=trunk_name or None, user_id=user.id
            )
    except FaxSendError as exc:
        raise await _reject(
            session, request, user, exc.http_status, str(exc),
            to_number=to_number or "",
        ) from None
    await _audit(session, request, user, "fax.send", job)
    await session.commit()
    return _to_read(job)


@router.get("", response_model=FaxOutboxList)
async def list_outbox(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_fax_sender),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> FaxOutboxList:
    stmt = select(FaxOutbox)
    count = select(func.count()).select_from(FaxOutbox)
    if user.role != "admin":
        stmt = stmt.where(FaxOutbox.created_by == user.id)
        count = count.where(FaxOutbox.created_by == user.id)
    total = await session.scalar(count) or 0
    rows = await session.scalars(
        stmt.order_by(FaxOutbox.created_at.desc(), FaxOutbox.id.desc()).offset(offset).limit(limit)
    )
    return FaxOutboxList(items=[_to_read(j) for j in rows], total=total)


@router.get("/trunks", response_model=list[FaxSendTrunk])
async def list_send_trunks(
    session: AsyncSession = Depends(get_session),
    service: FaxOutboxService = Depends(get_fax_outbox_service),
    _user: User = Depends(require_fax_sender),
) -> list[FaxSendTrunk]:
    """送信フォームのトランク選択肢（/api/trunks は管理者専用のため、送信者向けに名前だけ返す）。

    発信に使うトランク（outbound_enabled）だけを返し、既定の発信トランクに is_default を付ける。
    """
    rows = await send_trunk_candidates(session)
    default = select_default_outbound_trunk(rows, await service.default_trunk_id())
    return [
        FaxSendTrunk(
            name=t.name,
            display_name=t.display_name,
            trunk_type=t.trunk_type,
            is_default=t is default,
        )
        for t in rows
    ]


@router.get("/{job_id}", response_model=FaxOutboxRead)
async def get_outbox_job(
    job_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_fax_sender),
) -> FaxOutboxRead:
    return _to_read(await _get_job(session, job_id, user))


@router.post("/{job_id}/retry", response_model=FaxOutboxRead)
async def retry_outbox_job(
    job_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    service: FaxOutboxService = Depends(get_fax_outbox_service),
    user: User = Depends(require_fax_sender),
) -> FaxOutboxRead:
    job = await _get_job(session, job_id, user)
    if job.status not in (STATUS_FAILED, STATUS_CANCELLED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="失敗または取消したジョブだけ再送できます"
        )
    if job.retry_count >= MAX_MANUAL_RETRIES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"再送は {MAX_MANUAL_RETRIES} 回までです。PDF をアップロードし直してください",
        )
    tiff = _safe_path(settings, job.tiff_path)
    if tiff is None or not tiff.is_file():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="送信用のファイルが残っていないため再送できません。PDF をアップロードし直してください",
        )
    # 条件付き UPDATE（同時の再送・状態変化と後勝ちにならないように）。
    result = await session.execute(
        update(FaxOutbox)
        .where(
            FaxOutbox.id == job_id,
            FaxOutbox.status.in_((STATUS_FAILED, STATUS_CANCELLED)),
            FaxOutbox.retry_count < MAX_MANUAL_RETRIES,
        )
        .values(
            status=STATUS_QUEUED,
            attempts=0,
            retry_count=FaxOutbox.retry_count + 1,
            next_attempt_at=None,
            finished_at=None,
            last_error="",
        )
    )
    if not result.rowcount:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="このジョブは再送できません")
    await _audit(session, request, user, "fax.retry", job)
    await session.commit()
    await session.refresh(job)
    service.wake()
    return _to_read(job)


@router.post("/{job_id}/cancel", response_model=FaxOutboxRead)
async def cancel_outbox_job(
    job_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    service: FaxOutboxService = Depends(get_fax_outbox_service),
    user: User = Depends(require_fax_sender),
) -> FaxOutboxRead:
    job = await _get_job(session, job_id, user)
    # 送信ワーカーの発信開始と競合しないよう、サービス側の条件付き UPDATE で取り消す。
    if not await service.cancel(job_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="変換中・送信待ちのジョブだけ取り消せます"
        )
    await _audit(session, request, user, "fax.cancel", job)
    await session.commit()
    await session.refresh(job)
    return _to_read(job)


def _safe_path(settings: Settings, stored: str | None) -> Path | None:
    """DB に記録したパスを fax_dir 配下に限って返す（範囲外・未記録は None）。"""
    if not stored:
        return None
    try:
        return resolve_inside(settings.fax_dir, Path(stored))
    except (ValueError, OSError):
        return None


# 元 PDF のダウンロードは Webhook 受信側からも取得できるよう、署名付きトークン
# （24 時間有効・ジョブ id と作成時刻に束縛）があればセッション不要にする。
@router.get("/{job_id}/pdf", response_class=FileResponse)
async def download_outbox_pdf(
    job_id: int,
    request: Request,
    token: str | None = Query(default=None, max_length=2048),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    box: SecretBox = Depends(get_secret_box),
) -> FileResponse:
    if token is not None:
        job = await session.get(FaxOutbox, job_id)
        # 存在しない id も同じ 403 にする（トークンで id の有無を探らせない）。
        if job is None or not verify_outbox_pdf_token(box, token, job):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")
        # セッションを伴わない取得なので監査ログに残す。
        await record_audit(
            session,
            actor_user_id=None,
            actor_label="token",
            action="fax.pdf_download",
            target_type="fax_outbox",
            target_id=str(job.id),
            detail={"to": job.to_number, "via": "signed_token"},
            ip_address=get_client_ip(request),
        )
        await session.commit()
    else:
        user = await get_current_user(request, session)
        await require_fax_sender(user, settings)
        job = await _get_job(session, job_id, user)
    path = _safe_path(settings, job.pdf_path)
    if path is None or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PDF がありません")
    stamp = job.created_at.strftime("%Y%m%d-%H%M%S")
    return FileResponse(path, media_type="application/pdf", filename=f"fax-out-{job.id}-{stamp}.pdf")
