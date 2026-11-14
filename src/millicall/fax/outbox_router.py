"""FAX 送信 API（issue #141）。

- POST /api/faxes/outbox                : PDF をアップロードして送信ジョブを登録（202）
- GET  /api/faxes/outbox                : 送信ジョブの一覧（新しい順・ページング）
- GET  /api/faxes/outbox/{id}           : 送信ジョブの詳細
- POST /api/faxes/outbox/{id}/retry     : 失敗・取消したジョブを再送（試行回数をリセット）
- POST /api/faxes/outbox/{id}/cancel    : 送信待ち（queued）のジョブを取り消す
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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile as StarletteUploadFile

from millicall.audit import get_client_ip, record_audit
from millicall.config import Settings
from millicall.crypto import SecretBox
from millicall.deps import get_current_user, get_secret_box, get_session, get_settings_dep
from millicall.fax.convert import FAX_MAX_UPLOAD_BYTES
from millicall.fax.outbox import (
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_QUEUED,
    FaxOutboxService,
    FaxSendError,
    verify_outbox_pdf_token,
)
from millicall.fax.service import resolve_inside
from millicall.models import FaxOutbox, User

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


def _to_read(job: FaxOutbox) -> FaxOutboxRead:
    return FaxOutboxRead(
        id=job.id,
        to_number=job.to_number,
        trunk_name=job.trunk_name,
        pages=job.pages,
        status=job.status,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
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


# multipart の本文はハンドラ内で解析する（FastAPI の File/Form 引数にすると、認証や
# サイズ確認より前に本文全体が一時ファイルへ書き出されるため）。OpenAPI には手書きで載せる。
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
                        "description": "宛先 FAX 番号（0 から始まる 10〜11 桁の国内番号）",
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


def _too_large() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        detail=f"PDF が大きすぎます（上限 {FAX_MAX_UPLOAD_BYTES // (1024 * 1024)} MB）",
    )


def _form_text(value: object, name: str, max_length: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > max_length:
        raise HTTPException(status_code=422, detail=f"{name} の形式が不正です")
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
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="multipart/form-data で送信してください",
        )
    # 認証済みでも本文を読む前に宣言サイズで弾く（Content-Length を超えて読むことはない）。
    length = request.headers.get("content-length", "")
    if not length.isdigit():
        raise HTTPException(status_code=status.HTTP_411_LENGTH_REQUIRED, detail="Content-Length が必要です")
    if int(length) > FAX_MAX_UPLOAD_BYTES + _MULTIPART_OVERHEAD:
        raise _too_large()
    form = await request.form(max_files=1, max_fields=5)
    try:
        upload = form.get("file")
        if not isinstance(upload, StarletteUploadFile):
            raise HTTPException(status_code=422, detail="PDF ファイルを指定してください")
        to_number = _form_text(form.get("to_number"), "to_number", 20)
        trunk_name = _form_text(form.get("trunk_name"), "trunk_name", 50)
        data = await upload.read(FAX_MAX_UPLOAD_BYTES + 1)
    finally:
        await form.close()
    if len(data) > FAX_MAX_UPLOAD_BYTES:
        raise _too_large()
    if not data:
        raise HTTPException(status_code=422, detail="PDF ファイルが空です")
    try:
        job = await service.submit(
            data=data, to_number=to_number, trunk_name=trunk_name or None, user_id=user.id
        )
    except FaxSendError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from None
    except ValueError as exc:
        # 変換エラー（FaxConvertError / 上限超過）。メッセージは日本語の利用者向け文言。
        raise HTTPException(status_code=422, detail=str(exc)) from None
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
    tiff = _safe_path(settings, job.tiff_path)
    if tiff is None or not tiff.is_file():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="送信用のファイルが残っていないため再送できません。PDF をアップロードし直してください",
        )
    job.status = STATUS_QUEUED
    job.attempts = 0
    job.next_attempt_at = None
    job.finished_at = None
    job.last_error = ""
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
    user: User = Depends(require_fax_sender),
) -> FaxOutboxRead:
    job = await _get_job(session, job_id, user)
    if job.status != STATUS_QUEUED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="送信待ちのジョブだけ取り消せます"
        )
    job.status = STATUS_CANCELLED
    job.finished_at = _utcnow()
    job.next_attempt_at = None
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
    else:
        user = await get_current_user(request, session)
        await require_fax_sender(user, settings)
        job = await _get_job(session, job_id, user)
    path = _safe_path(settings, job.pdf_path)
    if path is None or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PDF がありません")
    stamp = job.created_at.strftime("%Y%m%d-%H%M%S")
    return FileResponse(path, media_type="application/pdf", filename=f"fax-out-{job.id}-{stamp}.pdf")
