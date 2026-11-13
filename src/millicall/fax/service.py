"""FAX 受信の後処理（issue #121）。

FreeSWITCH の rxfax は fax_<number> extension で ``<fax_dir>/<uuid>.tif`` に受信し、
完了時に ``CUSTOM spandsp::rxfaxresult`` を発火する。本サービスはそのイベントを受けて

1. TIFF を PDF に変換（Pillow。複数ページ・縦横解像度差の補正・サイズ/ページ/画素数の上限）
2. faxes テーブルへ記録
3. ``fax.received`` Webhook を発火

する。TIFF は原本として残す。ファイルパスはイベント中のパスを信用せず、
検証済みの call uuid から fax_dir 配下に組み立てる（パストラバーサル防止）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from sqlalchemy import select

from millicall.models import Fax, Trunk
from millicall.webhooks.dispatcher import EVENT_FAX_RECEIVED, utc_iso

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from millicall.crypto import SecretBox
    from millicall.webhooks.dispatcher import WebhookDispatcher

logger = logging.getLogger("millicall.fax")

RXFAX_SUBCLASS = "spandsp::rxfaxresult"
# FreeSWITCH の uuid（標準は 36 文字の UUID）。ファイル名に使うため厳格に制限する。
_UUID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")


def is_rxfax_result(event: dict[str, str]) -> bool:
    return (
        event.get("Event-Name") == "CUSTOM"
        and event.get("Event-Subclass", "") == RXFAX_SUBCLASS
    )


def _field(event: dict[str, str], name: str) -> str:
    """spandsp のイベントヘッダ（fax-xxx）→ チャネル変数（variable_fax_xxx）の順に引く。"""
    value = event.get(name.replace("_", "-"))
    if value:
        return value
    return event.get(f"variable_{name}", "")


def _int(value: str) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def resolve_inside(base: Path, candidate: Path) -> Path:
    """candidate を解決し、base 配下でなければ ValueError を送出する。"""
    base_resolved = base.resolve()
    resolved = candidate.resolve()
    if resolved != base_resolved and base_resolved not in resolved.parents:
        raise ValueError("path is outside of the fax directory")
    return resolved


# 変換の上限（悪意ある/壊れた TIFF によるメモリ・CPU 枯渇対策）。
FAX_MAX_PIXELS = 3456 * 4800 * 2  # A3 スーパーファイン相当の 2 倍
FAX_MAX_PAGES = 100
FAX_MAX_TIFF_BYTES = 50 * 1024 * 1024
# fax_dir の空きがこれ未満なら PDF 変換をスキップする（TIFF 受信用の余地を残す）。
FAX_MIN_FREE_BYTES = 200 * 1024 * 1024
# pdf_url に付けるダウンロードトークンの有効期間。
PDF_TOKEN_TTL_SECONDS = 24 * 60 * 60
_PDF_TOKEN_PURPOSE = "fax_pdf"


class FaxConversionLimitError(ValueError):
    """上限超過で PDF 変換を中止した（受信は失敗扱いにし TIFF は残す）。"""


def tiff_to_pdf(tiff_path: Path, pdf_path: Path) -> int:
    """TIFF（複数ページ可）を PDF に変換し、ページ数を返す。

    - ファイルサイズ・ページ数・1 ページの画素数に上限を設け、超えたら
      FaxConversionLimitError（DecompressionBombWarning もエラー扱い）。
    - ページは 1 枚ずつ読み込んで書き出す（全フレームをメモリに保持しない）。
    - FAX の標準解像度（204x98 dpi）のように縦横で異なる場合は PDF 側の dpi で
      縦横比を補正する（画素は加工しない）。
    - 一時ファイルは fax_dir 内に O_EXCL 相当（mkstemp）で作り、原子的に置き換える。
    """
    import warnings

    from PIL import Image

    size = tiff_path.stat().st_size
    if size > FAX_MAX_TIFF_BYTES:
        raise FaxConversionLimitError(f"TIFF が大きすぎます（{size} bytes）")
    # Pillow はこのサービス（FAX 変換）専用の依存のため、上限をプロセス全体で設定してよい。
    Image.MAX_IMAGE_PIXELS = FAX_MAX_PIXELS
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with Image.open(tiff_path) as im:
                n_frames = int(getattr(im, "n_frames", 1))
                if n_frames < 1:
                    raise FaxConversionLimitError("TIFF にページがありません")
                if n_frames > FAX_MAX_PAGES:
                    raise FaxConversionLimitError(f"ページ数が上限を超えています（{n_frames}）")
                # ヘッダだけでページごとの画素数を検証する（デコードしない）。
                for i in range(n_frames):
                    im.seek(i)
                    if im.width * im.height > FAX_MAX_PIXELS:
                        raise FaxConversionLimitError("ページの画素数が上限を超えています")
                im.seek(0)
                dpi = im.info.get("dpi")
                if dpi and len(dpi) == 2 and dpi[0] and dpi[1]:
                    xdpi, ydpi = float(dpi[0]), float(dpi[1])
                else:
                    xdpi = ydpi = 200.0
                fd, tmp_name = tempfile.mkstemp(
                    dir=pdf_path.parent, prefix=".fax-", suffix=".pdf.tmp"
                )
                tmp = Path(tmp_name)
                try:
                    with os.fdopen(fd, "wb") as fp:
                        # save_all で TIFF の各フレームを 1 枚ずつ読み出して書き込む。
                        im.save(fp, "PDF", save_all=True, dpi=(xdpi, ydpi))
                    tmp.replace(pdf_path)
                except BaseException:
                    tmp.unlink(missing_ok=True)
                    raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise FaxConversionLimitError("ページの画素数が上限を超えています") from exc
    return n_frames


def _free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


class FaxService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        fax_dir: Path,
        *,
        dispatcher: WebhookDispatcher | None = None,
        public_base_url: str = "",
        secret_box: SecretBox | None = None,
        free_bytes: Callable[[Path], int] = _free_bytes,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._fax_dir = Path(fax_dir)
        self._dispatcher = dispatcher
        self._base_url = public_base_url.rstrip("/")
        # pdf_url のダウンロードトークン署名用（None ならトークンなしの URL）。
        self._box = secret_box
        self._free_bytes = free_bytes
        self._tasks: set[asyncio.Task] = set()

    async def handle(self, event: dict[str, str]) -> None:
        """ESL イベントハンドラ。rxfaxresult なら後処理タスクを起動して即座に戻る。"""
        if not is_rxfax_result(event):
            return
        task = asyncio.create_task(self._process_safely(event))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _process_safely(self, event: dict[str, str]) -> None:
        try:
            await self.process(event)
        except Exception:
            logger.exception("FAX 受信結果の処理に失敗しました")

    async def _to_number(self, session: AsyncSession, event: dict[str, str]) -> str:
        trunk_name = event.get("variable_millicall_inbound_trunk", "")
        if trunk_name:
            trunk = await session.scalar(select(Trunk).where(Trunk.name == trunk_name))
            if trunk is not None:
                return trunk.did_number or trunk.username
        return (
            event.get("variable_millicall_fax_receiver")
            or event.get("Caller-Destination-Number", "")
        )

    async def process(self, event: dict[str, str]) -> Fax | None:
        call_uuid = event.get("Unique-ID") or event.get("Channel-Call-UUID") or ""
        if not _UUID_RE.fullmatch(call_uuid):
            logger.warning("rxfaxresult の uuid が不正なため無視します")
            return None
        success = _field(event, "fax_success") == "1"
        result_text = _field(event, "fax_result_text")[:255]
        pages = _int(_field(event, "fax_document_total_pages")) or _int(
            _field(event, "fax_document_transferred_pages")
        )
        remote_station_id = _field(event, "fax_remote_station_id")[:100]

        tiff_path: Path | None = None
        pdf_path: Path | None = None
        try:
            candidate = resolve_inside(self._fax_dir, self._fax_dir / f"{call_uuid}.tif")
        except ValueError:
            candidate = None
        if candidate is not None and candidate.is_file():
            tiff_path = candidate
            target = candidate.with_suffix(".pdf")
            try:
                free = self._free_bytes(self._fax_dir)
            except OSError:
                free = 0
            if free < FAX_MIN_FREE_BYTES:
                logger.warning(
                    "FAX 保存先の空き容量が不足しているため PDF 変換をスキップします"
                    "（空き %d bytes, uuid=%s）",
                    free,
                    call_uuid,
                )
            else:
                try:
                    pages = await asyncio.to_thread(tiff_to_pdf, candidate, target)
                    pdf_path = target
                except FaxConversionLimitError as exc:
                    logger.warning("FAX の PDF 変換を中止しました uuid=%s: %s", call_uuid, exc)
                    success = False
                    result_text = f"PDF 変換を中止: {exc}"[:255]
                except Exception:  # noqa: BLE001 — 壊れた/途中までの TIFF でも記録は残す
                    logger.exception("FAX TIFF → PDF 変換に失敗しました uuid=%s", call_uuid)
        elif success:
            logger.warning("FAX 受信成功だが TIFF が見つかりません uuid=%s", call_uuid)

        async with self._sessionmaker() as session:
            to_number = await self._to_number(session, event)
            fax = Fax(
                call_uuid=call_uuid,
                from_number=event.get("Caller-Caller-ID-Number", "")[:80],
                to_number=to_number[:80],
                remote_station_id=remote_station_id,
                pages=pages,
                success=success,
                result_text=result_text,
                tiff_path=str(tiff_path) if tiff_path else None,
                pdf_path=str(pdf_path) if pdf_path else None,
            )
            session.add(fax)
            await session.commit()
            await session.refresh(fax)

        if self._dispatcher is not None:
            self._dispatcher.emit(EVENT_FAX_RECEIVED, {"fax": self.payload(fax)})
        return fax

    def payload(self, fax: Fax) -> dict[str, Any]:
        return {
            "id": fax.id,
            "call_uuid": fax.call_uuid,
            "from": fax.from_number,
            "to": fax.to_number,
            "remote_station_id": fax.remote_station_id,
            "pages": fax.pages,
            "success": fax.success,
            "result_text": fax.result_text,
            "received_at": utc_iso(fax.received_at),
            "pdf_url": self.pdf_url(fax) if fax.pdf_path else None,
        }

    def pdf_url(self, fax: Fax) -> str:
        url = f"{self._base_url}/api/faxes/{fax.id}/pdf"
        if self._box is None:
            return url
        return f"{url}?{urlencode({'token': issue_pdf_token(self._box, fax)})}"

    async def cleanup(self, retention_days: int, *, now: datetime | None = None) -> int:
        """retention_days より古い FAX（行 + fax_dir 内の TIFF/PDF）を削除し、件数を返す。"""
        if retention_days <= 0:
            return 0
        cutoff = (now or datetime.now(UTC).replace(tzinfo=None)) - timedelta(days=retention_days)
        async with self._sessionmaker() as session:
            old = (await session.scalars(select(Fax).where(Fax.received_at < cutoff))).all()
            files = [p for f in old for p in (f.pdf_path, f.tiff_path) if p]
            for f in old:
                await session.delete(f)
            await session.commit()
        for stored in files:
            try:
                path = resolve_inside(self._fax_dir, Path(stored))
            except (ValueError, OSError):
                continue
            path.unlink(missing_ok=True)
        if old:
            logger.info("保持期間（%d 日）を過ぎた FAX を %d 件削除しました", retention_days, len(old))
        return len(old)


def _pdf_token_claims(fax: Fax) -> dict[str, Any]:
    # id だけでなく call_uuid と受信時刻にも束縛する（行が削除されて同じ id が再利用されても、
    # 別の FAX は開けない）。
    return {
        "p": _PDF_TOKEN_PURPOSE,
        "fax_id": fax.id,
        "call_uuid": fax.call_uuid,
        "received_at": fax.received_at.isoformat() if fax.received_at else None,
    }


def issue_pdf_token(box: SecretBox, fax: Fax) -> str:
    """FAX PDF のダウンロードトークン（Fernet。発行時刻を内包し 24 時間で失効）。"""
    return box.encrypt(json.dumps(_pdf_token_claims(fax)))


def verify_pdf_token(box: SecretBox, token: str, fax: Fax) -> bool:
    try:
        data = json.loads(box.decrypt(token, ttl=PDF_TOKEN_TTL_SECONDS))
    except Exception:  # noqa: BLE001 — 改ざん・期限切れ・形式不正はすべて無効
        return False
    return isinstance(data, dict) and data == _pdf_token_claims(fax)
