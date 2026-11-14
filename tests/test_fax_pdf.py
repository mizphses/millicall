"""受信 FAX の TIFF → PDF 変換（issue #143）。

Pillow の PDF プラグインで 1bit 画像を書き出すと白黒反転・崩れが起きたため、変換本体を
img2pdf に置き換えた。ここでは Pillow で作った FAX 相当の TIFF（group3/group4、
WhiteIsZero/BlackIsZero、FillOrder=1/2、複数ページ、縦横で異なる dpi）を変換し、
pikepdf で PDF の構造（ページ数・MediaBox・CCITTFaxDecode・BlackIs1）と、埋め込まれた
画像の画素が元の TIFF と一致することを確かめる。
"""

import struct
from decimal import Decimal
from pathlib import Path

import pikepdf
import pytest
from PIL import Image, ImageChops

from millicall.fax import service as fax_service_mod
from millicall.fax.service import FaxConversionError, FaxConversionLimitError, tiff_to_pdf

WHITE_IS_ZERO = 0
BLACK_IS_ZERO = 1
SIZE = (1728, 1100)
# 左上の黒い矩形（その他は白）。反転すると左上が白・その他が黒になる。
BOX = (0, 0, 100, 50)


@pytest.fixture(autouse=True)
def _restore_pillow_pixel_limit(monkeypatch):
    # tiff_to_pdf は Image.MAX_IMAGE_PIXELS をプロセス全体に設定するため、上限を下げた
    # テストの値が後続のテスト（フィクスチャの TIFF 生成）に漏れないよう毎回戻す。
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", fax_service_mod.FAX_MAX_PIXELS)


def _page(index: int) -> Image.Image:
    im = Image.new("1", SIZE, color=1)
    im.paste(0, BOX)
    # ページごとに位置の違う黒線を入れ、ページの取り違えも検出できるようにする。
    im.paste(0, (200, 300 + index * 100, 1500, 310 + index * 100))
    return im


def _set_tag(path: Path, tag: int, value: int) -> None:
    """リトルエンディアン TIFF の全 IFD で SHORT 型タグの値を書き換える。"""
    data = bytearray(path.read_bytes())
    assert data[:2] == b"II"
    offset = struct.unpack_from("<I", data, 4)[0]
    found = False
    while offset:
        count = struct.unpack_from("<H", data, offset)[0]
        for i in range(count):
            entry = offset + 2 + i * 12
            t, typ, n = struct.unpack_from("<HHI", data, entry)
            if t == tag:
                assert typ == 3 and n == 1  # SHORT が 1 個
                struct.pack_into("<H", data, entry + 8, value)
                found = True
        offset = struct.unpack_from("<I", data, offset + 2 + count * 12)[0]
    assert found
    path.write_bytes(bytes(data))


def _make_fax_tiff(
    path: Path,
    *,
    pages: int = 1,
    compression: str = "group4",
    photometric: int = BLACK_IS_ZERO,
    fill_order: int = 1,
    dpi=(204, 196),
) -> list[Image.Image]:
    """FAX 相当の 1bit TIFF を作り、各ページの「見た目」（白地に黒）を返す。

    Pillow は mode "1" を BlackIsZero で保存し、tiffinfo={262: 0} を渡しても画素が
    正しく書けない。そこで WhiteIsZero は「反転した画像を BlackIsZero で保存してから
    PhotometricInterpretation タグを 0 に書き換える」ことで作る（spandsp の受信 TIFF と
    同じく、デコードすると白地に黒になる）。
    """
    expected = [_page(i) for i in range(pages)]
    if photometric == WHITE_IS_ZERO:
        frames = [ImageChops.invert(p.convert("L")).convert("1") for p in expected]
    else:
        frames = [p.copy() for p in expected]
    frames[0].save(
        path,
        format="TIFF",
        save_all=True,
        append_images=frames[1:],
        compression=compression,
        dpi=dpi,
        tiffinfo={266: fill_order},
    )
    if photometric == WHITE_IS_ZERO:
        _set_tag(path, 262, WHITE_IS_ZERO)
    # 前提: 作った TIFF を Pillow で開くと期待どおりの見た目になる。
    with Image.open(path) as im:
        assert im.tag_v2.get(262) == photometric
        assert im.tag_v2.get(266) == fill_order
        for i, want in enumerate(expected):
            im.seek(i)
            assert ImageChops.difference(im.convert("L"), want.convert("L")).getbbox() is None
    return expected


def _images(pdf: pikepdf.Pdf, page_index: int) -> list[pikepdf.Object]:
    page = pdf.pages[page_index]
    return [xobj for _, xobj in page.Resources.XObject.items()]


def _decode_params(xobj: pikepdf.Object) -> pikepdf.Dictionary:
    parms = xobj.DecodeParms
    if isinstance(parms, pikepdf.Array):
        assert len(parms) == 1
        parms = parms[0]
    return parms


def _filters(xobj: pikepdf.Object) -> list[str]:
    f = xobj.Filter
    return [str(x) for x in f] if isinstance(f, pikepdf.Array) else [str(f)]


@pytest.mark.parametrize("compression", ["group3", "group4"])
@pytest.mark.parametrize("photometric", [WHITE_IS_ZERO, BLACK_IS_ZERO])
@pytest.mark.parametrize("fill_order", [1, 2])
def test_pdf_pixels_match_tiff(tmp_path, compression, photometric, fill_order):
    tif = tmp_path / "x.tif"
    expected = _make_fax_tiff(
        tif, pages=2, compression=compression, photometric=photometric, fill_order=fill_order
    )
    pdf_path = tmp_path / "x.pdf"
    assert tiff_to_pdf(tif, pdf_path) == 2
    # 一時ファイルは残らない
    assert {p.name for p in tmp_path.iterdir()} == {"x.tif", "x.pdf"}

    with pikepdf.open(pdf_path) as pdf:
        assert len(pdf.pages) == 2
        for i, want in enumerate(expected):
            (xobj,) = _images(pdf, i)
            # CCITT のまま可逆に埋め込まれている（JPEG や非圧縮になっていない）
            assert _filters(xobj) == ["/CCITTFaxDecode"]
            assert int(xobj.BitsPerComponent) == 1
            assert (int(xobj.Width), int(xobj.Height)) == SIZE
            got = pikepdf.PdfImage(xobj).as_pil_image().convert("L")
            # 白黒反転・崩れがない（左上の黒矩形とページごとの黒線まで一致）
            assert ImageChops.difference(got, want.convert("L")).getbbox() is None


@pytest.mark.parametrize("photometric", [WHITE_IS_ZERO, BLACK_IS_ZERO])
def test_ccitt_params_are_consistent_with_photometric(tmp_path, photometric):
    """画素を取り出さずに、CCITTFaxDecode のパラメータが白地に黒を表すことを確かめる。

    img2pdf は TIFF の photometric を Pillow でデコードした結果（白地に黒）を group4 で
    埋め込み、/BlackIs1 true・/Decode なし（既定の [0 1]）で出力する。CCITT の符号は
    白/黒のランを表すため、BlackIs1 true で 1 = 黒、DeviceGray で 0 = 黒となり、元が
    WhiteIsZero でも BlackIsZero でも同じ見た目になる。反転した /Decode [1 0] や
    BlackIs1 false が付くと白黒が逆転する（issue #143 の症状）。
    """
    tif = tmp_path / "x.tif"
    _make_fax_tiff(tif, compression="group3", photometric=photometric, fill_order=2)
    pdf_path = tmp_path / "x.pdf"
    tiff_to_pdf(tif, pdf_path)
    with pikepdf.open(pdf_path) as pdf:
        (xobj,) = _images(pdf, 0)
        assert _filters(xobj) == ["/CCITTFaxDecode"]
        assert str(xobj.ColorSpace) == "/DeviceGray"
        assert "/Decode" not in xobj or [float(v) for v in xobj.Decode] == [0.0, 1.0]
        parms = _decode_params(xobj)
        assert bool(parms.get("/BlackIs1", False)) is True
        assert int(parms.K) == -1  # group4
        assert int(parms.Columns) == SIZE[0]
        assert int(parms.Rows) == SIZE[1]


@pytest.mark.parametrize(
    ("dpi", "height"),
    [
        # FAX のファイン（204x196）と標準（204x98）。A4 相当の幅 1728 画素 = 約 215 mm。
        ((204, 196), 2200),
        ((204, 98), 1100),
    ],
)
def test_mediabox_keeps_aspect_ratio_with_anisotropic_dpi(tmp_path, dpi, height):
    tif = tmp_path / "x.tif"
    Image.new("1", (1728, height), color=1).save(tif, format="TIFF", compression="group4", dpi=dpi)
    pdf_path = tmp_path / "x.pdf"
    assert tiff_to_pdf(tif, pdf_path) == 1
    with pikepdf.open(pdf_path) as pdf:
        box = [Decimal(str(v)) for v in pdf.pages[0].MediaBox]
    width_pt, height_pt = box[2] - box[0], box[3] - box[1]
    assert abs(width_pt - Decimal(1728 * 72) / dpi[0]) < Decimal("0.01")
    assert abs(height_pt - Decimal(height * 72) / dpi[1]) < Decimal("0.01")
    # A4（210x297 mm ≒ 595x842 pt）に近い縦長になる（縦が潰れない）
    assert 600 < width_pt < 620
    assert 800 < height_pt < 820


def test_limits_are_checked_before_conversion(tmp_path, monkeypatch):
    monkeypatch.setattr(fax_service_mod, "FAX_MAX_PIXELS", 100 * 100)
    tif = tmp_path / "x.tif"
    _make_fax_tiff(tif, compression="group3", photometric=WHITE_IS_ZERO, fill_order=2)
    with pytest.raises(FaxConversionLimitError, match="画素数"):
        tiff_to_pdf(tif, tmp_path / "x.pdf")
    assert not list(tmp_path.glob("*.pdf*"))
    assert not list(tmp_path.glob(".fax-*"))


def test_page_limit_with_group3(tmp_path, monkeypatch):
    monkeypatch.setattr(fax_service_mod, "FAX_MAX_PAGES", 2)
    tif = tmp_path / "x.tif"
    _make_fax_tiff(tif, pages=3, compression="group3")
    with pytest.raises(FaxConversionLimitError, match="ページ数"):
        tiff_to_pdf(tif, tmp_path / "x.pdf")
    assert not list(tmp_path.glob("*.pdf*"))


def test_broken_tiff_raises_conversion_error_without_content_in_log(tmp_path, caplog):
    tif = tmp_path / "broken.tif"
    secret = b"SECRET-FAX-CONTENT"
    tif.write_bytes(b"II*\x00" + secret * 10)
    with pytest.raises(FaxConversionError):
        tiff_to_pdf(tif, tmp_path / "x.pdf")
    assert "broken.tif" in caplog.text
    assert "SECRET" not in caplog.text
    assert not list(tmp_path.glob("*.pdf*"))
    assert not list(tmp_path.glob(".fax-*"))
