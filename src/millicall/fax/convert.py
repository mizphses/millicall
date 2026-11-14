"""FAX 送信用の PDF → TIFF 変換（issue #141）。

core イメージに Ghostscript を入れず、pypdfium2（PDFium 同梱 wheel）でページをラスタライズし、
Pillow で FAX 用の複数ページ TIFF（1bit・CCITT G4、幅 1728 px、204×196 dpi = ファイン相当）に保存する。

PDFium は信頼できない PDF を扱うため、本番経路（:func:`convert_pdf_to_tiff`）は
**別プロセス**（``python -m millicall.fax.convert``）で実行し、

- 壁時計のタイムアウト（超えたら kill）
- 子プロセス側の RLIMIT_DATA / RLIMIT_CPU（Linux）

で core 本体のハング・クラッシュ・メモリ枯渇を防ぐ。PDFium 内部でクラッシュしても
子プロセスが落ちるだけで core は影響を受けない。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import tempfile
from pathlib import Path

# FAX の主走査幅（A4/B4 共通の 1728 画素 = 約 215 mm）と解像度（ファイン: 204×196 dpi）。
FAX_WIDTH_PX = 1728
FAX_XDPI = 204
FAX_YDPI = 196
# 1 ページの最大走査線数（約 62 cm。これより長いページは縮小して収める）。
FAX_MAX_LINES = 4800
# アップロード PDF の上限。
FAX_MAX_UPLOAD_BYTES = 20 * 1024 * 1024
# 変換の壁時計タイムアウト（秒）と子プロセスのメモリ上限。
CONVERT_TIMEOUT_SECONDS = 120.0
CONVERT_MEMORY_LIMIT_BYTES = 1024 * 1024 * 1024

PDF_MAGIC = b"%PDF-"

# 子プロセスの終了コード。
_EXIT_LIMIT = 2
_EXIT_INVALID = 3


class FaxConvertError(ValueError):
    """PDF を FAX 用 TIFF に変換できない（壊れた・暗号化された PDF、タイムアウト等）。"""


class FaxConvertLimitError(FaxConvertError):
    """ページ数などの上限を超えた。"""


def looks_like_pdf(head: bytes) -> bool:
    """先頭 1024 バイト以内に PDF ヘッダ（%PDF-）があるか（PDF 仕様の許容範囲）。"""
    return PDF_MAGIC in head[:1024]


def _page_image(page, max_lines: int):
    """1 ページを FAX 解像度の 1bit 画像（幅 1728 px）にする。"""
    from PIL import Image

    width_pt, height_pt = page.get_size()
    if width_pt <= 0 or height_pt <= 0:
        raise FaxConvertError("ページサイズが不正です")
    # 横長のページは 90 度回転して縦長で送る（FAX は主走査幅が固定のため）。
    rotation = 90 if width_pt > height_pt else 0
    if rotation:
        width_pt, height_pt = height_pt, width_pt
    # 幅を 1728 px に合わせる倍率。縦方向は 196/204 を掛けて縦横の dpi 差を吸収する。
    scale = FAX_WIDTH_PX / width_pt
    yfactor = FAX_YDPI / FAX_XDPI
    lines = height_pt * scale * yfactor
    if lines > max_lines:
        # 極端に縦長のページは全体を縮小して収める（画素数の上限）。
        scale *= max_lines / lines
    bitmap = page.render(scale=scale, rotation=rotation, grayscale=True, fill_color=(255, 255, 255, 255))
    try:
        image = bitmap.to_pil()
    finally:
        bitmap.close()
    width = min(FAX_WIDTH_PX, max(1, image.width))
    height = min(max_lines, max(1, round(image.height * yfactor)))
    image = image.convert("L").resize((width, height), Image.Resampling.LANCZOS)
    if width < FAX_WIDTH_PX:
        canvas = Image.new("L", (FAX_WIDTH_PX, height), 255)
        canvas.paste(image, ((FAX_WIDTH_PX - width) // 2, 0))
        image = canvas
    # 誤差拡散で 1bit 化（写真・網掛けも潰れにくい）。Pillow は PhotometricInterpretation=
    # MinIsBlack で保存するが、spandsp（txfax）は MinIsWhite 以外を読み込み時に反転して扱う。
    return image.convert("1")


def pdf_to_tiff(pdf_path: Path, tiff_path: Path, *, max_pages: int) -> int:
    """PDF を FAX 用の複数ページ TIFF（G4）に変換し、ページ数を返す（同一プロセス内で実行）。

    一時ファイルを tiff_path と同じディレクトリに作り、完成後に原子的に置き換える。
    FreeSWITCH（別コンテナ）から読めるよう 0644 にする。
    """
    import pypdfium2 as pdfium

    data_head = pdf_path.open("rb").read(1024)
    if not looks_like_pdf(data_head):
        raise FaxConvertError("PDF ファイルではありません")
    try:
        pdf = pdfium.PdfDocument(str(pdf_path))
    except pdfium.PdfiumError as exc:
        # パスワード付き・壊れた PDF はここで失敗する。
        raise FaxConvertError("PDF を開けません（壊れているか、パスワードで保護されています）") from exc
    try:
        n_pages = len(pdf)
        if n_pages < 1:
            raise FaxConvertError("PDF にページがありません")
        if n_pages > max_pages:
            raise FaxConvertLimitError(f"ページ数が上限（{max_pages} ページ）を超えています（{n_pages} ページ）")

        def frames():
            for i in range(n_pages):
                page = pdf[i]
                try:
                    yield _page_image(page, FAX_MAX_LINES)
                finally:
                    page.close()

        it = frames()
        first = next(it)
        fd, tmp_name = tempfile.mkstemp(dir=tiff_path.parent, prefix=".faxout-", suffix=".tif.tmp")
        tmp = Path(tmp_name)
        try:
            # 複数ページ TIFF の書き出しは書き戻し（seek + read）を伴うため w+b で開く。
            with os.fdopen(fd, "w+b") as fp:
                # append_images にジェネレータを渡し、ページを 1 枚ずつ書き出す（全ページを保持しない）。
                first.save(
                    fp,
                    format="TIFF",
                    save_all=True,
                    append_images=it,
                    compression="group4",
                    dpi=(FAX_XDPI, FAX_YDPI),
                )
            os.chmod(tmp, 0o644)
            tmp.replace(tiff_path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    finally:
        pdf.close()
    return n_pages


def _apply_rlimits(timeout: float) -> None:
    """子プロセスのメモリ・CPU 上限（Linux のみ有効。macOS などでは失敗しても続行）。"""
    try:
        import resource
    except ImportError:  # pragma: no cover - Windows
        return
    with contextlib.suppress(ValueError, OSError):
        # RLIMIT_AS ではなく RLIMIT_DATA: PDFium のアロケータは大きなアドレス空間を
        # 予約だけする（PROT_NONE）ため、AS で縛ると初期化に失敗しうる。DATA は実際に
        # 書き込み可能なメモリ（ヒープ・匿名 mmap）だけを数える（Linux 4.7 以降）。
        resource.setrlimit(
            resource.RLIMIT_DATA, (CONVERT_MEMORY_LIMIT_BYTES, CONVERT_MEMORY_LIMIT_BYTES)
        )
    cpu = int(timeout) + 5
    with contextlib.suppress(ValueError, OSError):
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))


def main(argv: list[str] | None = None) -> int:
    """``python -m millicall.fax.convert <pdf> <tiff> <max_pages> <timeout>``。

    成功時はページ数を標準出力に書いて 0。上限超過は 2、変換不能は 3（理由は標準エラー）。
    """
    args = sys.argv[1:] if argv is None else argv
    pdf_path, tiff_path, max_pages, timeout = Path(args[0]), Path(args[1]), int(args[2]), float(args[3])
    _apply_rlimits(timeout)
    try:
        pages = pdf_to_tiff(pdf_path, tiff_path, max_pages=max_pages)
    except FaxConvertLimitError as exc:
        sys.stderr.write(str(exc))
        return _EXIT_LIMIT
    except FaxConvertError as exc:
        sys.stderr.write(str(exc))
        return _EXIT_INVALID
    except MemoryError:
        sys.stderr.write("PDF の変換に必要なメモリが上限を超えました")
        return _EXIT_LIMIT
    sys.stdout.write(str(pages))
    return 0


async def convert_pdf_to_tiff(
    pdf_path: Path,
    tiff_path: Path,
    *,
    max_pages: int,
    timeout: float = CONVERT_TIMEOUT_SECONDS,
) -> int:
    """別プロセスで PDF → TIFF 変換し、ページ数を返す。失敗は FaxConvertError。"""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-m",
        "millicall.fax.convert",
        str(pdf_path),
        str(tiff_path),
        str(max_pages),
        str(timeout),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        tiff_path.unlink(missing_ok=True)
        raise FaxConvertError("PDF の変換がタイムアウトしました") from None
    except asyncio.CancelledError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        raise
    message = stderr.decode("utf-8", "replace").strip()[-500:]
    if proc.returncode == 0:
        try:
            return int(stdout.decode().strip())
        except ValueError:
            raise FaxConvertError("PDF の変換結果を読み取れません") from None
    tiff_path.unlink(missing_ok=True)
    if proc.returncode == _EXIT_LIMIT:
        raise FaxConvertLimitError(message or "PDF が上限を超えています")
    if proc.returncode == _EXIT_INVALID:
        raise FaxConvertError(message or "PDF を変換できません")
    # シグナル終了（PDFium のクラッシュ・CPU 上限）や想定外の例外。
    raise FaxConvertError("PDF を変換できません（変換プロセスが異常終了しました）")


if __name__ == "__main__":  # pragma: no cover - サブプロセスのエントリポイント
    raise SystemExit(main())
