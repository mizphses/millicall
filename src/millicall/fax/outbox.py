"""FAX 送信（issue #141）。

管理画面からアップロードされた PDF を FAX 用 TIFF に変換し（:mod:`millicall.fax.convert`）、
送信ジョブ（fax_outbox）として登録する。ワーカーは送信待ちジョブを取り出し、ESL で

    originate {origination_uuid=<uuid>,...,absolute_codec_string=PCMU,fax_enable_t38=false,...}
        sofia/gateway/<trunk>/<number> &txfax(<fax_dir>/out/<id>.tif)

を発行する。結果は CHANNEL_HANGUP_COMPLETE のチャネル変数（variable_fax_success 等）から
origination_uuid で対応付けて反映し、``fax.sent`` / ``fax.failed`` Webhook を発火する。

- 失敗（話中・無応答・送信中のエラー等）は 1 分・5 分の間隔で最大 3 回まで再試行する。
- 結果イベントが届かない（発信自体が失敗した、イベント接続が切れていた）場合はワーカーの
  見張りが ``uuid_exists`` で通話の有無を確かめ、通話が無ければ失敗として扱う。
- core 再起動時に sending のまま残ったジョブは、通話が続いていれば見張りを再開し、
  無ければ「結果不明」で失敗に落とす（重複送信を避けるため自動では再送しない）。
- HGW（ひかり電話）は T.38 非対応のため G.711（PCMU）パススルーのみで送る。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import uuid as _uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from sqlalchemy import func, select

from millicall.fax.convert import convert_pdf_to_tiff, looks_like_pdf
from millicall.fax.service import FAX_MAX_PAGES, FAX_MIN_FREE_BYTES, resolve_inside
from millicall.models import FaxOutbox, Trunk
from millicall.webhooks.dispatcher import EVENT_FAX_FAILED, EVENT_FAX_SENT, utc_iso

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from millicall.crypto import SecretBox
    from millicall.webhooks.dispatcher import WebhookDispatcher

logger = logging.getLogger("millicall.fax.outbox")

STATUS_QUEUED = "queued"
STATUS_SENDING = "sending"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
FINAL_STATUSES = (STATUS_SENT, STATUS_FAILED, STATUS_CANCELLED)

# 宛先は国内の一般番号に限る（国際発信・特番・184/186 前置は不可）。
FAX_TO_NUMBER_RE = re.compile(r"^0\d{9,10}$")
# originate に補間する値の形式（多層防御。fsconfig の _SAFE_TRUNK_NAME_RE と同じ）。
_TRUNK_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,50}$")
_CALLER_ID_RE = re.compile(r"^\+?[0-9]{1,30}$")
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
_UUID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")

DEFAULT_MAX_ATTEMPTS = 3
# 再試行の間隔（1 回目の失敗後 1 分、2 回目の失敗後 5 分）。
RETRY_DELAYS_SECONDS: tuple[float, ...] = (60.0, 300.0)
# originate の応答待ち（originate_timeout）。
ORIGINATE_TIMEOUT_SECONDS = 60
# 応答待ちを過ぎてから結果イベント無しで通話の有無を確かめ始めるまでの余裕。
_WATCHDOG_GRACE_SECONDS = 30
# 通話が見当たらない状態がこの秒数続いたら失敗とみなす（結果イベントの遅延到着を待つ）。
_MISSING_CONFIRM_SECONDS = 10
# 同時に走らせる PDF 変換の数（アップロード連打による CPU 枯渇対策）。
_CONVERT_CONCURRENCY = 2

# 再試行する切断理由（相手側の一時的な状態・回線混雑など）。
RETRIABLE_CAUSES = frozenset(
    {
        "USER_BUSY",
        "NO_ANSWER",
        "NO_USER_RESPONSE",
        "ALLOTTED_TIMEOUT",
        "PROGRESS_TIMEOUT",
        "MEDIA_TIMEOUT",
        "RECOVERY_ON_TIMER_EXPIRE",
        "NORMAL_TEMPORARY_FAILURE",
        "NORMAL_CIRCUIT_CONGESTION",
        "SWITCH_CONGESTION",
        "DESTINATION_OUT_OF_ORDER",
        "NETWORK_OUT_OF_ORDER",
        "ORIGINATOR_CANCEL",
    }
)

# 失敗理由の日本語表記（未知の理由はそのまま表示する）。
_CAUSE_LABELS = {
    "USER_BUSY": "話中",
    "NO_ANSWER": "応答なし",
    "NO_USER_RESPONSE": "応答なし",
    "CALL_REJECTED": "着信拒否",
    "UNALLOCATED_NUMBER": "この番号は使われていません",
    "INVALID_NUMBER_FORMAT": "番号の形式が不正です",
    "NORMAL_TEMPORARY_FAILURE": "一時的な障害",
    "NORMAL_CIRCUIT_CONGESTION": "回線混雑",
    "SWITCH_CONGESTION": "回線混雑",
    "INCOMPATIBLE_DESTINATION": "相手先と接続条件が合いません",
}

_PDF_TOKEN_PURPOSE = "fax_outbox_pdf"
PDF_TOKEN_TTL_SECONDS = 24 * 60 * 60


class FaxSendError(ValueError):
    """送信ジョブを登録できない（入力不正）。http_status は API 応答のステータス。"""

    def __init__(self, message: str, http_status: int = 422) -> None:
        super().__init__(message)
        self.http_status = http_status


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _int(value: str | None) -> int:
    try:
        return max(int(value or ""), 0)
    except (TypeError, ValueError):
        return 0


def _free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def cause_label(cause: str) -> str:
    label = _CAUSE_LABELS.get(cause)
    return f"{label}（{cause}）" if label else cause


def build_originate(
    *,
    call_uuid: str,
    trunk_name: str,
    to_number: str,
    caller_id: str,
    tiff_path: str,
    job_id: int,
) -> str:
    """txfax 送信の originate コマンド文字列を組み立てる（補間する値はすべて形式検証する）。"""
    if not _UUID_RE.fullmatch(call_uuid):
        raise ValueError("invalid call uuid")
    if not _TRUNK_NAME_RE.fullmatch(trunk_name):
        raise ValueError("invalid trunk name")
    if not FAX_TO_NUMBER_RE.fullmatch(to_number):
        raise ValueError("invalid fax number")
    if caller_id and not _CALLER_ID_RE.fullmatch(caller_id):
        raise ValueError("invalid caller id")
    if not _SAFE_PATH_RE.fullmatch(tiff_path):
        raise ValueError("unsafe tiff path")
    variables = [f"origination_uuid={call_uuid}"]
    if caller_id:
        variables += [
            f"origination_caller_id_number={caller_id}",
            f"origination_caller_id_name={caller_id}",
        ]
    variables += [
        # HGW（ひかり電話）は PCMU のみ・T.38 非対応。G.711 パススルーで送る。
        "absolute_codec_string=PCMU",
        "fax_enable_t38=false",
        "fax_verbose=false",
        "ignore_early_media=true",
        f"originate_timeout={ORIGINATE_TIMEOUT_SECONDS}",
        f"millicall_fax_outbox_id={int(job_id)}",
    ]
    return (
        f"originate {{{','.join(variables)}}}sofia/gateway/{trunk_name}/{to_number} "
        f"&txfax({tiff_path})"
    )


def _trunk_caller_id(trunk: Trunk) -> str:
    for value in (trunk.caller_id, trunk.did_number):
        if value and _CALLER_ID_RE.fullmatch(value):
            return value
    return ""


async def resolve_send_trunk(session: AsyncSession, trunk_name: str | None) -> Trunk:
    """送信に使うトランク。未指定なら既定の発信トランク（有効なトランクの名前順先頭）。"""
    rows = list(
        await session.scalars(
            select(Trunk).where(Trunk.enabled.is_(True)).order_by(Trunk.name)
        )
    )
    if trunk_name:
        match = next((t for t in rows if t.name == trunk_name), None)
        if match is None:
            raise FaxSendError(f"トランク {trunk_name} が見つからないか無効です")
        return match
    if not rows:
        raise FaxSendError("送信に使える外線トランクがありません")
    return rows[0]


class FaxOutboxService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        fax_dir: Path,
        *,
        esl_bgapi: Callable[[str], Awaitable[Any]],
        esl_api: Callable[[str], Awaitable[str]],
        dispatcher: WebhookDispatcher | None = None,
        public_base_url: str = "",
        secret_box: SecretBox | None = None,
        concurrency: int = 1,
        max_duration_seconds: int = 1800,
        retry_delays: tuple[float, ...] = RETRY_DELAYS_SECONDS,
        poll_interval: float = 5.0,
        clock: Callable[[], datetime] = _utcnow,
        free_bytes: Callable[[Path], int] = _free_bytes,
        converter: Callable[..., Awaitable[int]] = convert_pdf_to_tiff,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._fax_dir = Path(fax_dir)
        self._bgapi = esl_bgapi
        self._api = esl_api
        self._dispatcher = dispatcher
        self._base_url = public_base_url.rstrip("/")
        self._box = secret_box
        self._concurrency = max(1, int(concurrency))
        self._max_duration = max(60, int(max_duration_seconds))
        self._retry_delays = retry_delays or RETRY_DELAYS_SECONDS
        self._poll_interval = poll_interval
        self._now = clock
        self._free_bytes = free_bytes
        self._convert = converter
        self._convert_sem = asyncio.Semaphore(_CONVERT_CONCURRENCY)
        # 送信中の call_uuid → ジョブ id（CHANNEL_HANGUP_COMPLETE の対応付け用）。
        self._inflight: dict[str, int] = {}
        # 通話が見当たらなくなった時刻（結果イベントの遅延到着を待つため）。
        self._missing_since: dict[str, datetime] = {}
        self._tasks: set[asyncio.Task] = set()
        self._wake = asyncio.Event()
        self._worker: asyncio.Task | None = None

    # ------------------------------------------------------------------ パス

    @property
    def out_dir(self) -> Path:
        return self._fax_dir / "out"

    def _safe(self, stored: str | None) -> Path | None:
        if not stored:
            return None
        try:
            return resolve_inside(self._fax_dir, Path(stored))
        except (ValueError, OSError):
            return None

    # ------------------------------------------------------------------ 登録

    async def submit(
        self,
        *,
        data: bytes,
        to_number: str,
        trunk_name: str | None,
        user_id: int | None,
    ) -> FaxOutbox:
        """PDF を検証・変換して送信ジョブを登録する（失敗は FaxSendError）。"""
        if not FAX_TO_NUMBER_RE.fullmatch(to_number):
            raise FaxSendError("宛先は 0 から始まる 10〜11 桁の国内番号で指定してください")
        if not looks_like_pdf(data[:1024]):
            raise FaxSendError("PDF ファイルを指定してください", http_status=415)
        async with self._sessionmaker() as session:
            trunk = await resolve_send_trunk(session, trunk_name)
            resolved_trunk = trunk.name
        if not _TRUNK_NAME_RE.fullmatch(resolved_trunk):
            raise FaxSendError("トランク名に使用できない文字が含まれています")

        out_dir = self.out_dir
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(out_dir, 0o755)
        except OSError as exc:
            logger.exception("FAX 送信用ディレクトリを作成できません: %s", out_dir)
            raise FaxSendError("FAX の保存先を準備できません", http_status=503) from exc
        if not _SAFE_PATH_RE.fullmatch(str(out_dir.resolve())):
            # txfax の引数に埋め込むため、空白・クォート等を含むパスは使えない。
            raise FaxSendError("FAX 保存先のパスに使用できない文字が含まれています", http_status=503)
        try:
            free = self._free_bytes(out_dir)
        except OSError:
            free = 0
        if free < FAX_MIN_FREE_BYTES + len(data) * 4:
            raise FaxSendError("FAX 保存先の空き容量が不足しています", http_status=507)

        # ファイル名はサーバー採番のみ（アップロード時の名前は使わない）。
        token = _uuid.uuid4().hex
        tmp_pdf = out_dir / f".upload-{token}.pdf"
        tmp_tif = out_dir / f".upload-{token}.tif"
        try:
            await asyncio.to_thread(self._write_file, tmp_pdf, data)
            async with self._convert_sem:
                pages = await self._convert(tmp_pdf, tmp_tif, max_pages=FAX_MAX_PAGES)
            async with self._sessionmaker() as session:
                job = FaxOutbox(
                    to_number=to_number,
                    trunk_name=resolved_trunk,
                    pages=pages,
                    status=STATUS_QUEUED,
                    max_attempts=DEFAULT_MAX_ATTEMPTS,
                    created_by=user_id,
                )
                session.add(job)
                await session.flush()
                pdf_path = out_dir / f"{job.id}.pdf"
                tif_path = out_dir / f"{job.id}.tif"
                tmp_pdf.replace(pdf_path)
                tmp_tif.replace(tif_path)
                job.pdf_path = str(pdf_path.resolve())
                job.tiff_path = str(tif_path.resolve())
                try:
                    await session.commit()
                except BaseException:
                    pdf_path.unlink(missing_ok=True)
                    tif_path.unlink(missing_ok=True)
                    raise
                await session.refresh(job)
        finally:
            tmp_pdf.unlink(missing_ok=True)
            tmp_tif.unlink(missing_ok=True)
        logger.info("FAX 送信ジョブを登録しました id=%s pages=%s trunk=%s", job.id, pages, resolved_trunk)
        self.wake()
        return job

    @staticmethod
    def _write_file(path: Path, data: bytes) -> None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, "wb") as fp:
            fp.write(data)

    # ------------------------------------------------------------------ ワーカー

    def wake(self) -> None:
        self._wake.set()

    async def start(self) -> None:
        """送信ワーカーを起動する（起動時の復旧はワーカー内で行い、core の起動を待たせない）。"""
        self._worker = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None
        await self.drain()

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _run(self) -> None:
        try:
            await self.recover()
        except Exception:
            logger.exception("FAX 送信ジョブの起動時復旧に失敗しました")
        while True:
            self._wake.clear()
            try:
                await self.tick()
            except Exception:
                logger.exception("FAX 送信ワーカーでエラーが発生しました")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self._poll_interval)

    async def tick(self) -> None:
        """1 周分の処理: 送信中ジョブの見張り → 送信待ちジョブの発信。"""
        await self._watch_sending()
        await self._dispatch_queued()

    async def recover(self) -> None:
        """core 再起動時、sending のまま残ったジョブを整理する。"""
        async with self._sessionmaker() as session:
            rows = list(
                await session.scalars(select(FaxOutbox).where(FaxOutbox.status == STATUS_SENDING))
            )
            stale: list[FaxOutbox] = []
            for job in rows:
                if job.call_uuid and await self._uuid_exists(job.call_uuid):
                    # FreeSWITCH 側で送信が続いている。結果イベントを待つ。
                    self._inflight[job.call_uuid] = job.id
                    continue
                job.status = STATUS_FAILED
                job.finished_at = self._now()
                job.last_error = "core の再起動により送信結果が不明です。必要なら再送してください"
                stale.append(job)
            await session.commit()
        for job in stale:
            logger.warning("送信結果が不明な FAX ジョブを失敗にしました id=%s", job.id)
            self._emit(EVENT_FAX_FAILED, job)

    async def _uuid_exists(self, call_uuid: str) -> bool | None:
        """通話の有無（ESL に問い合わせできなければ None）。"""
        if not _UUID_RE.fullmatch(call_uuid):
            return False
        try:
            reply = await self._api(f"uuid_exists {call_uuid}")
        except Exception:  # noqa: BLE001 — ESL 未接続など
            logger.warning("uuid_exists の問い合わせに失敗しました uuid=%s", call_uuid)
            return None
        return str(reply).strip().lower() == "true"

    async def _watch_sending(self) -> None:
        now = self._now()
        async with self._sessionmaker() as session:
            rows = list(
                await session.scalars(select(FaxOutbox).where(FaxOutbox.status == STATUS_SENDING))
            )
        check_after = timedelta(seconds=ORIGINATE_TIMEOUT_SECONDS + _WATCHDOG_GRACE_SECONDS)
        hard_limit = check_after + timedelta(seconds=self._max_duration + 60)
        for job in rows:
            call_uuid = job.call_uuid or ""
            if call_uuid and call_uuid not in self._inflight:
                self._inflight[call_uuid] = job.id
            started = job.started_at or now
            if now - started < check_after:
                continue
            if now - started >= hard_limit:
                with contextlib.suppress(Exception):
                    if _UUID_RE.fullmatch(call_uuid):
                        await self._api(f"uuid_kill {call_uuid}")
                await self._fail_attempt(
                    job.id, call_uuid, "送信が制限時間を超えたため切断しました", retriable=True
                )
                continue
            exists = await self._uuid_exists(call_uuid) if call_uuid else False
            if exists is None:
                continue
            if exists:
                self._missing_since.pop(call_uuid, None)
                continue
            first_missing = self._missing_since.setdefault(call_uuid, now)
            if now - first_missing >= timedelta(seconds=_MISSING_CONFIRM_SECONDS):
                await self._fail_attempt(
                    job.id,
                    call_uuid,
                    "発信できませんでした（FreeSWITCH から結果を受け取れませんでした）",
                    retriable=True,
                )

    async def _dispatch_queued(self) -> None:
        now = self._now()
        async with self._sessionmaker() as session:
            sending = await session.scalar(
                select(func.count())
                .select_from(FaxOutbox)
                .where(FaxOutbox.status == STATUS_SENDING)
            )
            slots = self._concurrency - int(sending or 0)
            if slots <= 0:
                return
            ids = list(
                await session.scalars(
                    select(FaxOutbox.id)
                    .where(
                        FaxOutbox.status == STATUS_QUEUED,
                        (FaxOutbox.next_attempt_at.is_(None)) | (FaxOutbox.next_attempt_at <= now),
                    )
                    .order_by(FaxOutbox.id)
                    .limit(slots)
                )
            )
        for job_id in ids:
            await self._start(job_id)

    async def _start(self, job_id: int) -> None:
        now = self._now()
        call_uuid = str(_uuid.uuid4())
        final_error: str | None = None
        async with self._sessionmaker() as session:
            job = await session.get(FaxOutbox, job_id)
            if job is None or job.status != STATUS_QUEUED:
                return
            trunk = await session.scalar(
                select(Trunk).where(Trunk.name == job.trunk_name, Trunk.enabled.is_(True))
            )
            tiff = self._safe(job.tiff_path)
            command = ""
            if trunk is None:
                final_error = f"トランク {job.trunk_name} が見つからないか無効です"
            elif tiff is None or not tiff.is_file():
                final_error = "送信用の TIFF がありません（保持期間を過ぎて削除された可能性があります）"
            else:
                try:
                    command = build_originate(
                        call_uuid=call_uuid,
                        trunk_name=trunk.name,
                        to_number=job.to_number,
                        caller_id=_trunk_caller_id(trunk),
                        tiff_path=str(tiff),
                        job_id=job.id,
                    )
                except ValueError as exc:
                    final_error = f"発信コマンドを組み立てられません: {exc}"
            if final_error is not None:
                job.status = STATUS_FAILED
                job.finished_at = now
                job.last_error = final_error[:255]
                await session.commit()
                await session.refresh(job)
                self._emit(EVENT_FAX_FAILED, job)
                return
            job.status = STATUS_SENDING
            job.attempts += 1
            job.started_at = now
            job.finished_at = None
            job.next_attempt_at = None
            job.call_uuid = call_uuid
            await session.commit()
            attempt = job.attempts
        self._inflight[call_uuid] = job_id
        logger.info("FAX 送信を開始します id=%s attempt=%s uuid=%s", job_id, attempt, call_uuid)
        try:
            await self._bgapi(command)
        except Exception:
            logger.exception("FAX 送信の originate に失敗しました id=%s", job_id)
            await self._fail_attempt(
                job_id, call_uuid, "FreeSWITCH に発信を依頼できませんでした", retriable=True
            )

    # ------------------------------------------------------------------ 結果

    async def handle(self, event: dict[str, str]) -> None:
        """ESL イベントハンドラ。送信中ジョブの CHANNEL_HANGUP_COMPLETE なら結果を反映する。"""
        if event.get("Event-Name") != "CHANNEL_HANGUP_COMPLETE":
            return
        call_uuid = event.get("Unique-ID") or ""
        if call_uuid not in self._inflight:
            return
        task = asyncio.create_task(self._process_safely(event))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _process_safely(self, event: dict[str, str]) -> None:
        try:
            await self.process_result(event)
        except Exception:
            logger.exception("FAX 送信結果の処理に失敗しました")

    async def process_result(self, event: dict[str, str]) -> FaxOutbox | None:
        call_uuid = event.get("Unique-ID") or ""
        job_id = self._inflight.get(call_uuid)
        if job_id is None:
            return None
        success = event.get("variable_fax_success") == "1"
        result_code = event.get("variable_fax_result_code", "")
        result_text = event.get("variable_fax_result_text", "")[:255]
        remote = event.get("variable_fax_remote_station_id", "")[:100]
        cause = event.get("Hangup-Cause") or event.get("variable_hangup_cause", "")
        if success:
            async with self._sessionmaker() as session:
                job = await session.get(FaxOutbox, job_id)
                if job is None or job.status != STATUS_SENDING or job.call_uuid != call_uuid:
                    self._forget(call_uuid)
                    return None
                job.status = STATUS_SENT
                job.finished_at = self._now()
                job.result_text = result_text
                job.remote_station_id = remote
                job.last_error = ""
                transferred = _int(event.get("variable_fax_document_transferred_pages"))
                if transferred:
                    job.pages = transferred
                await session.commit()
                await session.refresh(job)
            self._forget(call_uuid)
            logger.info("FAX 送信に成功しました id=%s", job_id)
            self._emit(EVENT_FAX_SENT, job)
            self.wake()
            return job
        # 応答後に txfax が動いた（結果コードがある）なら送信中のエラーとして再試行する。
        attempted = bool(result_code) and result_code != "0"
        retriable = attempted or cause in RETRIABLE_CAUSES
        if attempted and result_text:
            error = f"送信エラー: {result_text}"
        elif cause:
            error = cause_label(cause)
        else:
            error = result_text or "送信に失敗しました"
        return await self._fail_attempt(
            job_id,
            call_uuid,
            error,
            retriable=retriable,
            result_text=result_text,
            remote_station_id=remote,
        )

    def _forget(self, call_uuid: str) -> None:
        self._inflight.pop(call_uuid, None)
        self._missing_since.pop(call_uuid, None)

    async def _fail_attempt(
        self,
        job_id: int,
        call_uuid: str,
        error: str,
        *,
        retriable: bool,
        result_text: str = "",
        remote_station_id: str = "",
    ) -> FaxOutbox | None:
        """1 回の試行の失敗を反映する。再試行の余地があれば queued に戻し、無ければ failed。"""
        now = self._now()
        final = False
        async with self._sessionmaker() as session:
            job = await session.get(FaxOutbox, job_id)
            if job is None or job.status != STATUS_SENDING or job.call_uuid != call_uuid:
                # 既に別経路（結果イベント/見張り）で反映済み。
                self._forget(call_uuid)
                return None
            job.last_error = error[:255]
            if result_text:
                job.result_text = result_text[:255]
            if remote_station_id:
                job.remote_station_id = remote_station_id[:100]
            if retriable and job.attempts < job.max_attempts:
                index = min(max(job.attempts - 1, 0), len(self._retry_delays) - 1)
                job.status = STATUS_QUEUED
                job.next_attempt_at = now + timedelta(seconds=self._retry_delays[index])
            else:
                job.status = STATUS_FAILED
                job.finished_at = now
                final = True
            await session.commit()
            await session.refresh(job)
        self._forget(call_uuid)
        if final:
            logger.warning("FAX 送信に失敗しました id=%s: %s", job_id, error)
            self._emit(EVENT_FAX_FAILED, job)
        else:
            logger.info(
                "FAX 送信に失敗したため再試行します id=%s next=%s: %s",
                job_id,
                job.next_attempt_at,
                error,
            )
        self.wake()
        return job

    # ------------------------------------------------------------------ Webhook / URL

    def _emit(self, name: str, job: FaxOutbox) -> None:
        if self._dispatcher is not None:
            self._dispatcher.emit(name, {"fax": self.payload(job)})

    def payload(self, job: FaxOutbox) -> dict[str, Any]:
        return {
            "id": job.id,
            "direction": "outbound",
            "to": job.to_number,
            "trunk": job.trunk_name,
            "pages": job.pages,
            "success": job.status == STATUS_SENT,
            "result_text": job.result_text,
            "error": job.last_error or None,
            "remote_station_id": job.remote_station_id,
            "attempts": job.attempts,
            "finished_at": utc_iso(job.finished_at) if job.finished_at else None,
            "pdf_url": self.pdf_url(job) if job.pdf_path else None,
        }

    def pdf_url(self, job: FaxOutbox) -> str:
        url = f"{self._base_url}/api/faxes/outbox/{job.id}/pdf"
        if self._box is None:
            return url
        return f"{url}?{urlencode({'token': issue_outbox_pdf_token(self._box, job)})}"

    # ------------------------------------------------------------------ 保持期間

    async def cleanup(self, retention_days: int, *, now: datetime | None = None) -> int:
        """retention_days より古い終了済みジョブ（行 + PDF/TIFF）を削除し、件数を返す。"""
        if retention_days <= 0:
            return 0
        cutoff = (now or self._now()) - timedelta(days=retention_days)
        async with self._sessionmaker() as session:
            old = list(
                await session.scalars(
                    select(FaxOutbox).where(
                        FaxOutbox.status.in_(FINAL_STATUSES), FaxOutbox.created_at < cutoff
                    )
                )
            )
            files = [p for j in old for p in (j.pdf_path, j.tiff_path) if p]
            for job in old:
                await session.delete(job)
            await session.commit()
        for stored in files:
            path = self._safe(stored)
            if path is not None:
                path.unlink(missing_ok=True)
        if old:
            logger.info(
                "保持期間（%d 日）を過ぎた送信 FAX を %d 件削除しました", retention_days, len(old)
            )
        return len(old)


def _outbox_token_claims(job: FaxOutbox) -> dict[str, Any]:
    # id だけでなく作成時刻にも束縛する（受信 FAX のトークンとは purpose で区別する）。
    return {
        "p": _PDF_TOKEN_PURPOSE,
        "outbox_id": job.id,
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }


def issue_outbox_pdf_token(box: SecretBox, job: FaxOutbox) -> str:
    """送信 FAX の元 PDF のダウンロードトークン（Fernet。24 時間で失効）。"""
    return box.encrypt(json.dumps(_outbox_token_claims(job)))


def verify_outbox_pdf_token(box: SecretBox, token: str, job: FaxOutbox) -> bool:
    try:
        data = json.loads(box.decrypt(token, ttl=PDF_TOKEN_TTL_SECONDS))
    except Exception:  # noqa: BLE001 — 改ざん・期限切れ・形式不正はすべて無効
        return False
    return isinstance(data, dict) and data == _outbox_token_claims(job)
