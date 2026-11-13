"""FAX 受信の後処理（issue #121）。

FreeSWITCH の rxfax は fax_<number> extension で ``<fax_dir>/<uuid>.tif`` に受信し、
完了時に ``CUSTOM spandsp::rxfaxresult`` を発火する。本サービスはそのイベントを受けて

1. TIFF を PDF に変換（Pillow。複数ページ・ファイン/標準の縦横解像度差を補正）
2. faxes テーブルへ記録
3. ``fax.received`` Webhook を発火

する。TIFF は原本として残す。ファイルパスはイベント中のパスを信用せず、
検証済みの call uuid から fax_dir 配下に組み立てる（パストラバーサル防止）。
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from millicall.models import Fax, Trunk
from millicall.webhooks.dispatcher import EVENT_FAX_RECEIVED, utc_iso

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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


def tiff_to_pdf(tiff_path: Path, pdf_path: Path) -> int:
    """TIFF（複数ページ可）を PDF に変換し、ページ数を返す。

    FAX の標準解像度は 204x98 dpi のように縦横で異なるため、縦方向を引き伸ばして
    正方画素にしてから PDF にする（そのままだと縦に潰れて見える）。
    """
    from PIL import Image, ImageSequence

    pages: list[Image.Image] = []
    resolution = 200.0
    with Image.open(tiff_path) as im:
        for frame in ImageSequence.Iterator(im):
            page = frame.copy()
            dpi = frame.info.get("dpi")
            if dpi and len(dpi) == 2 and dpi[0] and dpi[1]:
                xdpi, ydpi = float(dpi[0]), float(dpi[1])
                resolution = xdpi
                if abs(xdpi - ydpi) > 1:
                    new_height = max(int(round(page.height * xdpi / ydpi)), 1)
                    page = page.resize((page.width, new_height))
            if page.mode not in ("1", "L", "RGB"):
                page = page.convert("L")
            pages.append(page)
    if not pages:
        raise ValueError("TIFF にページがありません")
    tmp = pdf_path.with_suffix(".pdf.tmp")
    pages[0].save(
        tmp, "PDF", save_all=True, append_images=pages[1:], resolution=resolution
    )
    tmp.replace(pdf_path)
    return len(pages)


class FaxService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        fax_dir: Path,
        *,
        dispatcher: WebhookDispatcher | None = None,
        public_base_url: str = "",
    ) -> None:
        self._sessionmaker = sessionmaker
        self._fax_dir = Path(fax_dir)
        self._dispatcher = dispatcher
        self._base_url = public_base_url.rstrip("/")
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
                pages = await asyncio.to_thread(tiff_to_pdf, candidate, target)
                pdf_path = target
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
            "pdf_url": f"{self._base_url}/api/faxes/{fax.id}/pdf" if fax.pdf_path else None,
        }
