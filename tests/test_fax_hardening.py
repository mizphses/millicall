"""FAX 受信の堅牢化（PR #127 レビュー対応）。

変換上限・空き容量・保持期間・署名付き PDF URL・受信番号の監査とプリフライト。
"""

import uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from PIL import Image
from sqlalchemy import select

from millicall.crypto import SecretBox
from millicall.fax import service as fax_service_mod
from millicall.fax.service import (
    FaxConversionLimitError,
    FaxService,
    issue_pdf_token,
    tiff_to_pdf,
    verify_pdf_token,
)
from millicall.models import AuditLog, Fax


def _make_tiff(path: Path, pages: int = 2, size=(1728, 1100), dpi=(204, 98)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = [Image.new("1", size, color=1) for _ in range(pages)]
    frames[0].save(
        path, format="TIFF", save_all=True, append_images=frames[1:], compression="group4", dpi=dpi
    )


def _event(call_uuid: str) -> dict[str, str]:
    return {
        "Event-Name": "CUSTOM",
        "Event-Subclass": "spandsp::rxfaxresult",
        "Unique-ID": call_uuid,
        "Caller-Caller-ID-Number": "0312345678",
        "Caller-Destination-Number": "300",
        "fax-success": "1",
        "fax-result-text": "OK",
        "fax-document-total-pages": "2",
    }


# --------------------------------------------------------------------------- 変換上限


def test_pdf_aspect_is_corrected_via_dpi(tmp_path):
    tif = tmp_path / "x.tif"
    _make_tiff(tif, pages=1, size=(1728, 1100), dpi=(204, 98))
    pdf = tmp_path / "x.pdf"
    assert tiff_to_pdf(tif, pdf) == 1
    data = pdf.read_bytes()
    # MediaBox 高さ = 1100 * 72 / 98 ≒ 808.16（縦が潰れない）
    assert b"808.16" in data
    # 一時ファイルは残らない
    assert {p.name for p in tmp_path.iterdir()} == {"x.tif", "x.pdf"}


def test_page_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(fax_service_mod, "FAX_MAX_PAGES", 2)
    tif = tmp_path / "x.tif"
    _make_tiff(tif, pages=3, size=(100, 100))
    with pytest.raises(FaxConversionLimitError, match="ページ数"):
        tiff_to_pdf(tif, tmp_path / "x.pdf")
    assert not (tmp_path / "x.pdf").exists()


def test_file_size_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(fax_service_mod, "FAX_MAX_TIFF_BYTES", 10)
    tif = tmp_path / "x.tif"
    _make_tiff(tif, pages=1, size=(100, 100))
    with pytest.raises(FaxConversionLimitError, match="大きすぎ"):
        tiff_to_pdf(tif, tmp_path / "x.pdf")


def test_pixel_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(fax_service_mod, "FAX_MAX_PIXELS", 100 * 100)
    tif = tmp_path / "x.tif"
    _make_tiff(tif, pages=1, size=(200, 200))
    with pytest.raises(FaxConversionLimitError, match="画素数"):
        tiff_to_pdf(tif, tmp_path / "x.pdf")
    assert not list(tmp_path.glob("*.pdf*"))


async def test_limit_marks_fax_failed_and_keeps_tiff(app, monkeypatch):
    monkeypatch.setattr(fax_service_mod, "FAX_MAX_PAGES", 1)
    fax_dir = app.state.settings.fax_dir
    call_uuid = str(uuid.uuid4())
    _make_tiff(fax_dir / f"{call_uuid}.tif", pages=2, size=(100, 100))
    svc = FaxService(app.state.sessionmaker, fax_dir)
    fax = await svc.process(_event(call_uuid))
    assert fax.success is False
    assert "PDF 変換を中止" in fax.result_text
    assert fax.pdf_path is None
    assert Path(fax.tiff_path).exists()


async def test_low_disk_space_skips_pdf(app, caplog):
    fax_dir = app.state.settings.fax_dir
    call_uuid = str(uuid.uuid4())
    _make_tiff(fax_dir / f"{call_uuid}.tif", pages=1, size=(100, 100))
    svc = FaxService(app.state.sessionmaker, fax_dir, free_bytes=lambda _p: 10 * 1024 * 1024)
    fax = await svc.process(_event(call_uuid))
    assert fax.pdf_path is None
    assert fax.success is True  # 受信自体は成功
    assert "空き容量" in caplog.text


# --------------------------------------------------------------------------- 保持期間


async def test_retention_cleanup_deletes_old_rows_and_files(app):
    fax_dir = app.state.settings.fax_dir
    fax_dir.mkdir(parents=True, exist_ok=True)
    old_tif = fax_dir / "old.tif"
    old_tif.write_bytes(b"x")
    new_tif = fax_dir / "new.tif"
    new_tif.write_bytes(b"x")
    now = datetime(2026, 11, 14, 12, 0, 0)
    async with app.state.sessionmaker() as s:
        s.add(Fax(call_uuid="old", tiff_path=str(old_tif), received_at=now - timedelta(days=31)))
        s.add(Fax(call_uuid="new", tiff_path=str(new_tif), received_at=now - timedelta(days=1)))
        await s.commit()
    svc = FaxService(app.state.sessionmaker, fax_dir)
    assert await svc.cleanup(0, now=now) == 0
    assert await svc.cleanup(30, now=now) == 1
    assert not old_tif.exists() and new_tif.exists()
    async with app.state.sessionmaker() as s:
        assert [f.call_uuid for f in (await s.scalars(select(Fax))).all()] == ["new"]


# --------------------------------------------------------------------------- 署名付き URL


def _fax(fax_id=12, call_uuid="u-a", received_at=datetime(2026, 11, 14, 1, 2, 3)) -> Fax:
    return Fax(id=fax_id, call_uuid=call_uuid, received_at=received_at)


def test_pdf_token_binds_fax_identity_and_expires(monkeypatch):
    box = SecretBox("k" * 32)
    fax = _fax()
    token = issue_pdf_token(box, fax)
    assert verify_pdf_token(box, token, fax)
    assert not verify_pdf_token(box, token, _fax(fax_id=13))
    # 同じ id でも別の FAX（id 再利用）は開けない
    assert not verify_pdf_token(box, token, _fax(call_uuid="u-b"))
    assert not verify_pdf_token(box, token, _fax(received_at=datetime(2026, 11, 15)))
    assert not verify_pdf_token(SecretBox("other" * 8), token, fax)
    tampered = token[:20] + ("A" if token[20] != "A" else "B") + token[21:]
    assert not verify_pdf_token(box, tampered, fax)
    monkeypatch.setattr(fax_service_mod, "PDF_TOKEN_TTL_SECONDS", -10)
    assert not verify_pdf_token(box, token, fax)


async def test_token_for_deleted_fax_does_not_open_new_fax_with_same_id(app, client):
    fax_dir = app.state.settings.fax_dir
    fax_dir.mkdir(parents=True, exist_ok=True)
    pdf_a = fax_dir / "a.pdf"
    pdf_a.write_bytes(b"%PDF-1.4 A")
    pdf_b = fax_dir / "b.pdf"
    pdf_b.write_bytes(b"%PDF-1.4 B")
    box = SecretBox(app.state.secrets.master_key)
    async with app.state.sessionmaker() as s:
        fax_a = Fax(call_uuid="uuid-a", pdf_path=str(pdf_a))
        s.add(fax_a)
        await s.commit()
        await s.refresh(fax_a)
        token = issue_pdf_token(box, fax_a)
        fax_id = fax_a.id
        await s.delete(fax_a)
        await s.commit()
        # 同じ id を明示して別の FAX を作る（rowid 再利用の再現）
        s.add(Fax(id=fax_id, call_uuid="uuid-b", pdf_path=str(pdf_b)))
        await s.commit()
    r = await client.get(f"/api/faxes/{fax_id}/pdf", params={"token": token})
    assert r.status_code == 403


async def test_faxes_ids_are_not_reused(app):
    async with app.state.sessionmaker() as s:
        first = Fax(call_uuid="x1")
        s.add(first)
        await s.commit()
        first_id = first.id
        await s.delete(first)
        await s.commit()
        second = Fax(call_uuid="x2")
        s.add(second)
        await s.commit()
        assert second.id > first_id


async def test_pdf_url_token_allows_download_without_session(app, client):
    fax_dir = app.state.settings.fax_dir
    call_uuid = str(uuid.uuid4())
    _make_tiff(fax_dir / f"{call_uuid}.tif", pages=1, size=(100, 100))
    box = SecretBox(app.state.secrets.master_key)
    svc = FaxService(
        app.state.sessionmaker, fax_dir, public_base_url="https://pbx.example", secret_box=box
    )
    fax = await svc.process(_event(call_uuid))
    url = svc.payload(fax)["pdf_url"]
    parts = urlsplit(url)
    assert parts.path == f"/api/faxes/{fax.id}/pdf"
    token = parse_qs(parts.query)["token"][0]

    ok = await client.get(f"/api/faxes/{fax.id}/pdf", params={"token": token})
    assert ok.status_code == 200
    assert ok.content.startswith(b"%PDF")
    # 改ざん・別 ID のトークン・トークン無し（未ログイン）は拒否
    tampered = token[:20] + ("A" if token[20] != "A" else "B") + token[21:]
    bad = await client.get(f"/api/faxes/{fax.id}/pdf", params={"token": tampered})
    assert bad.status_code == 403
    other = await client.get(f"/api/faxes/{fax.id + 1}/pdf", params={"token": token})
    assert other.status_code == 403
    assert (await client.get(f"/api/faxes/{fax.id}/pdf")).status_code == 401
    # トークンでは他の API は使えない
    assert (await client.get(f"/api/faxes/{fax.id}", params={"token": token})).status_code == 401


# --------------------------------------------------------------------------- 受信番号の監査


async def test_fax_receiver_changes_are_audited(auth_client, app):
    rid = (
        await auth_client.post("/api/fax-receivers", json={"number": "300", "name": "FAX"})
    ).json()["id"]
    await auth_client.patch(
        f"/api/fax-receivers/{rid}", json={"number": "301", "name": "FAX", "enabled": True}
    )
    await auth_client.delete(f"/api/fax-receivers/{rid}")
    async with app.state.sessionmaker() as s:
        actions = [
            (a.action, a.target_id)
            for a in (
                await s.scalars(
                    select(AuditLog)
                    .where(AuditLog.target_type == "fax_receiver")
                    .order_by(AuditLog.id)
                )
            ).all()
        ]
    assert actions == [
        ("fax_receiver.create", str(rid)),
        ("fax_receiver.update", str(rid)),
        ("fax_receiver.delete", str(rid)),
    ]


async def test_fax_receiver_preflight_failure_rolls_back(auth_client, app):
    listener = app.state.change_listener

    async def failing_validate(session):
        raise ValueError("render failed")

    original = listener.validate
    listener.validate = failing_validate
    try:
        r = await auth_client.post("/api/fax-receivers", json={"number": "300", "name": "FAX"})
    finally:
        listener.validate = original
    assert r.status_code == 422
    assert (await auth_client.get("/api/fax-receivers")).json() == []
