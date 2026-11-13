"""FAX 受信（issue #121）: dialplan 生成・FAX 自動検知・TIFF→PDF・API。"""

import uuid
from pathlib import Path

import defusedxml.ElementTree as ET  # noqa: N817
import pytest
from PIL import Image
from sqlalchemy import select

from millicall.fax.service import FaxService, tiff_to_pdf
from millicall.models import Fax, FaxReceiver, Trunk
from millicall.telephony.fsconfig import (
    FaxReceiverConfig,
    FreeswitchConfigWriter,
    TrunkConfig,
)

# --------------------------------------------------------------------------- dialplan


def _writer(tmp_path: Path) -> FreeswitchConfigWriter:
    return FreeswitchConfigWriter(
        output_dir=tmp_path / "fs",
        sip_domain="192.168.1.10",
        esl_password="esl",
        fax_dir=tmp_path / "fax",
    )


def _trunk(**kw) -> TrunkConfig:
    base = dict(
        name="Flets02",
        display_name="Flets02",
        host="192.168.1.1",
        username="4",
        password="pw",
        did_number="0445895784",
        inbound_extension="200",
    )
    base.update(kw)
    return TrunkConfig(**base)


def test_dialplan_renders_fax_extension_with_absolute_path(tmp_path):
    out = _writer(tmp_path).render_all(
        [], fax_receivers=[FaxReceiverConfig(number="300", name="FAX")]
    )
    xml = out["dialplan/default.xml"]
    ET.fromstring(xml)
    assert 'extension name="fax_300"' in xml
    assert 'expression="^300$"' in xml
    fax_dir = str((tmp_path / "fax").resolve())
    assert f'<action application="rxfax" data="{fax_dir}/${{uuid}}.tif"/>' in xml
    assert '<action application="set" data="fax_enable_t38=false"/>' in xml
    assert '<action application="answer"/>' in xml
    # fax extension は汎用 internal_extensions より前に評価される
    assert xml.index("fax_300") < xml.index("internal_extensions")


def test_dialplan_without_fax_receivers_has_no_rxfax(tmp_path):
    xml = _writer(tmp_path).render_all([])["dialplan/default.xml"]
    assert "rxfax" not in xml


def test_public_renders_fax_detect_only_when_enabled(tmp_path):
    w = _writer(tmp_path)
    fax = [FaxReceiverConfig(number="300")]
    off = w.render_all([], trunks=[_trunk()], fax_receivers=fax)["dialplan/public.xml"]
    assert "spandsp_start_fax_detect" not in off
    assert '<action application="set" data="millicall_inbound_trunk=Flets02"/>' in off

    on = w.render_all(
        [],
        trunks=[_trunk(fax_detect_enabled=True, fax_route_number="300", fax_detect_timeout_sec=25)],
        fax_receivers=fax,
    )["dialplan/public.xml"]
    ET.fromstring(on)
    assert (
        '<action application="spandsp_start_fax_detect" '
        "data=\"transfer '300 XML default' 25 cng\"/>" in on
    )
    # 検知の仕掛けは通常の transfer より前
    assert on.index("spandsp_start_fax_detect") < on.index('application="transfer"')


def test_public_skips_fax_detect_for_dangling_route_or_fax_inbound(tmp_path):
    w = _writer(tmp_path)
    # 切替先が FAX 受信番号として存在しない
    dangling = w.render_all(
        [], trunks=[_trunk(fax_detect_enabled=True, fax_route_number="301")],
        fax_receivers=[FaxReceiverConfig(number="300")],
    )["dialplan/public.xml"]
    assert "spandsp_start_fax_detect" not in dangling
    # 着信先がそもそも FAX 受信番号（専用回線）
    dedicated = w.render_all(
        [],
        trunks=[_trunk(inbound_extension="300", fax_detect_enabled=True, fax_route_number="300")],
        fax_receivers=[FaxReceiverConfig(number="300")],
    )["dialplan/public.xml"]
    assert "spandsp_start_fax_detect" not in dedicated


def test_fax_detect_timeout_is_clamped(tmp_path):
    out = _writer(tmp_path).render_all(
        [],
        trunks=[_trunk(fax_detect_enabled=True, fax_route_number="300", fax_detect_timeout_sec=999)],
        fax_receivers=[FaxReceiverConfig(number="300")],
    )["dialplan/public.xml"]
    assert "XML default' 120 cng" in out


def test_writer_rejects_unsafe_fax_dir(tmp_path):
    with pytest.raises(ValueError):
        FreeswitchConfigWriter(
            output_dir=tmp_path, sip_domain="x", esl_password="e", fax_dir=tmp_path / "a b'"
        )


# --------------------------------------------------------------------------- TIFF→PDF


def _make_tiff(path: Path, pages: int = 2, dpi=(204, 98)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = [Image.new("1", (1728, 1100), color=1) for _ in range(pages)]
    frames[0].save(
        path, format="TIFF", save_all=True, append_images=frames[1:], compression="group4", dpi=dpi
    )


def test_tiff_to_pdf_multi_page(tmp_path):
    tif = tmp_path / "x.tif"
    _make_tiff(tif, pages=2)
    pdf = tmp_path / "x.pdf"
    assert tiff_to_pdf(tif, pdf) == 2
    data = pdf.read_bytes()
    assert data.startswith(b"%PDF")
    assert data.count(b"/Type /Page\n") + data.count(b"/Type /Page ") + data.count(
        b"/Type /Page>"
    ) >= 2 or b"/Count 2" in data


class _FakeDispatcher:
    def __init__(self):
        self.emitted: list[tuple[str, dict]] = []

    def emit(self, name, data):
        self.emitted.append((name, data))


def _rxfax_event(call_uuid: str, **kw) -> dict[str, str]:
    ev = {
        "Event-Name": "CUSTOM",
        "Event-Subclass": "spandsp::rxfaxresult",
        "Unique-ID": call_uuid,
        "Caller-Caller-ID-Number": "0312345678",
        "Caller-Destination-Number": "300",
        "fax-success": "1",
        "fax-result-text": "OK",
        "fax-document-total-pages": "2",
        "fax-remote-station-id": "+81 3 1234 5678",
        "variable_millicall_inbound_trunk": "Flets02",
    }
    ev.update(kw)
    return ev


async def test_fax_service_converts_records_and_fires_webhook(app):
    fax_dir = app.state.settings.fax_dir
    async with app.state.sessionmaker() as s:
        s.add(
            Trunk(
                name="Flets02",
                display_name="Flets02",
                host="192.168.1.1",
                username="4",
                did_number="0445895784",
            )
        )
        await s.commit()
    call_uuid = str(uuid.uuid4())
    _make_tiff(fax_dir / f"{call_uuid}.tif", pages=2)
    disp = _FakeDispatcher()
    svc = FaxService(
        app.state.sessionmaker, fax_dir, dispatcher=disp, public_base_url="https://pbx.example"
    )

    fax = await svc.process(_rxfax_event(call_uuid))

    assert fax is not None
    assert fax.pages == 2 and fax.success is True
    assert fax.to_number == "0445895784"  # トランクの DID
    assert fax.from_number == "0312345678"
    assert Path(fax.pdf_path).read_bytes().startswith(b"%PDF")
    assert Path(fax.tiff_path).exists()  # 原本 TIFF は残す
    async with app.state.sessionmaker() as s:
        assert len((await s.scalars(select(Fax))).all()) == 1
    assert len(disp.emitted) == 1
    name, data = disp.emitted[0]
    assert name == "fax.received"
    assert data["fax"]["pdf_url"] == f"https://pbx.example/api/faxes/{fax.id}/pdf"
    assert data["fax"]["pages"] == 2
    assert data["fax"]["to"] == "0445895784"


async def test_fax_service_handle_ignores_other_events_and_runs_in_background(app):
    svc = FaxService(app.state.sessionmaker, app.state.settings.fax_dir)
    await svc.handle({"Event-Name": "CHANNEL_CREATE"})
    await svc.handle(_rxfax_event(str(uuid.uuid4()), **{"fax-success": "0"}))
    await svc.drain()
    async with app.state.sessionmaker() as s:
        rows = (await s.scalars(select(Fax))).all()
    assert len(rows) == 1
    assert rows[0].success is False and rows[0].pdf_path is None


async def test_fax_service_rejects_traversal_uuid(app):
    svc = FaxService(app.state.sessionmaker, app.state.settings.fax_dir)
    assert await svc.process(_rxfax_event("../../etc/passwd")) is None


# --------------------------------------------------------------------------- API


async def test_fax_receiver_crud_and_number_plan(auth_client):
    r = await auth_client.post("/api/fax-receivers", json={"number": "300", "name": "代表 FAX"})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    plan = (await auth_client.get("/api/number-plan")).json()
    assert {"number": "300", "kind": "fax"}.items() <= next(
        e for e in plan if e["number"] == "300"
    ).items()

    # 番号プラン内で重複不可
    r = await auth_client.post("/api/ring-groups", json={"number": "300", "name": "g"})
    assert r.status_code == 409
    r = await auth_client.post("/api/fax-receivers", json={"number": "300", "name": "dup"})
    assert r.status_code == 409

    r = await auth_client.patch(
        f"/api/fax-receivers/{rid}", json={"number": "301", "name": "FAX", "enabled": True}
    )
    assert r.status_code == 200 and r.json()["number"] == "301"
    assert (await auth_client.delete(f"/api/fax-receivers/{rid}")).status_code == 204


async def test_trunk_inbound_can_target_fax_receiver(auth_client):
    await auth_client.post("/api/fax-receivers", json={"number": "300", "name": "FAX"})
    r = await auth_client.post(
        "/api/trunks",
        json={
            "name": "Flets02",
            "display_name": "Flets02",
            "host": "192.168.1.1",
            "username": "4",
            "password": "p",
            "inbound_extension": "300",
        },
    )
    assert r.status_code == 201, r.text


async def _insert_fax(app, **kw) -> int:
    async with app.state.sessionmaker() as s:
        fax = Fax(call_uuid=kw.pop("call_uuid", "u-1"), **kw)
        s.add(fax)
        await s.commit()
        return fax.id


async def test_fax_list_get_pdf_delete(auth_client, app):
    fax_dir = app.state.settings.fax_dir
    tif = fax_dir / "u-1.tif"
    _make_tiff(tif)
    pdf = fax_dir / "u-1.pdf"
    tiff_to_pdf(tif, pdf)
    fid = await _insert_fax(
        app,
        from_number="0312345678",
        to_number="0445895784",
        pages=2,
        success=True,
        result_text="OK",
        tiff_path=str(tif),
        pdf_path=str(pdf),
    )
    await _insert_fax(app, call_uuid="u-2", pages=0, success=False)

    lst = await auth_client.get("/api/faxes", params={"limit": 1})
    assert lst.status_code == 200
    assert lst.json()["total"] == 2 and len(lst.json()["items"]) == 1

    one = await auth_client.get(f"/api/faxes/{fid}")
    assert one.status_code == 200
    body = one.json()
    assert body["has_pdf"] is True
    assert "pdf_path" not in body and "tiff_path" not in body

    dl = await auth_client.get(f"/api/faxes/{fid}/pdf")
    assert dl.status_code == 200
    assert dl.headers["content-type"] == "application/pdf"
    assert dl.content.startswith(b"%PDF")

    r = await auth_client.delete(f"/api/faxes/{fid}")
    assert r.status_code == 204
    assert not pdf.exists() and not tif.exists()
    assert (await auth_client.get(f"/api/faxes/{fid}")).status_code == 404


async def test_fax_missing_returns_404(auth_client, app):
    assert (await auth_client.get("/api/faxes/999")).status_code == 404
    assert (await auth_client.get("/api/faxes/999/pdf")).status_code == 404
    assert (await auth_client.delete("/api/faxes/999")).status_code == 404
    fid = await _insert_fax(app, success=False)
    assert (await auth_client.get(f"/api/faxes/{fid}/pdf")).status_code == 404


async def test_fax_pdf_outside_fax_dir_is_rejected(auth_client, app, tmp_path):
    outside = tmp_path / "secret.pdf"
    outside.write_bytes(b"%PDF-1.4 secret")
    fid = await _insert_fax(app, pdf_path=str(outside), tiff_path=str(outside))
    traversal = await _insert_fax(
        app, call_uuid="u-3", pdf_path=str(app.state.settings.fax_dir / ".." / "secret.pdf")
    )
    assert (await auth_client.get(f"/api/faxes/{fid}/pdf")).status_code == 404
    assert (await auth_client.get(f"/api/faxes/{traversal}/pdf")).status_code == 404
    # 削除しても fax_dir 外のファイルは消さない
    assert (await auth_client.delete(f"/api/faxes/{fid}")).status_code == 204
    assert outside.exists()


async def test_fax_api_requires_admin(client, user_factory):
    assert (await client.get("/api/faxes")).status_code == 401
    await user_factory(username="plain", password="Passw0rd1", role="user")
    await client.post("/api/auth/login", json={"username": "plain", "password": "Passw0rd1"})
    assert (await client.get("/api/faxes")).status_code == 403
    assert (await client.get("/api/faxes/1/pdf")).status_code == 403
    assert (await client.get("/api/fax-receivers")).status_code == 403


# --------------------------------------------------------------------------- trunk API


_TRUNK = {
    "name": "Flets02",
    "display_name": "Flets02",
    "host": "192.168.1.1",
    "username": "4",
    "password": "p",
}


async def test_trunk_fax_detect_defaults(auth_client):
    r = await auth_client.post("/api/trunks", json=_TRUNK)
    assert r.status_code == 201
    body = r.json()
    assert body["fax_detect_enabled"] is False
    assert body["fax_detect_timeout_sec"] == 20
    assert body["fax_route_number"] is None


async def test_trunk_fax_detect_validation(auth_client):
    # 有効化には切替先が必須
    r = await auth_client.post("/api/trunks", json={**_TRUNK, "fax_detect_enabled": True})
    assert r.status_code == 422
    # 切替先は FAX 受信番号でなければならない（内線番号・未登録番号は不可）
    await auth_client.post("/api/ring-groups", json={"number": "200", "name": "g"})
    r = await auth_client.post(
        "/api/trunks", json={**_TRUNK, "fax_detect_enabled": True, "fax_route_number": "200"}
    )
    assert r.status_code == 422
    r = await auth_client.post(
        "/api/trunks", json={**_TRUNK, "fax_detect_enabled": True, "fax_route_number": "1x"}
    )
    assert r.status_code == 422
    r = await auth_client.post("/api/trunks", json={**_TRUNK, "fax_detect_timeout_sec": 4})
    assert r.status_code == 422
    r = await auth_client.post("/api/trunks", json={**_TRUNK, "fax_detect_timeout_sec": 121})
    assert r.status_code == 422


async def test_trunk_fax_detect_patch(auth_client, app):
    await auth_client.post("/api/fax-receivers", json={"number": "300", "name": "FAX"})
    tid = (await auth_client.post("/api/trunks", json=_TRUNK)).json()["id"]
    r = await auth_client.patch(
        f"/api/trunks/{tid}",
        json={"fax_detect_enabled": True, "fax_route_number": "300", "fax_detect_timeout_sec": 30},
    )
    assert r.status_code == 200, r.text
    assert r.json()["fax_detect_enabled"] is True
    assert r.json()["fax_route_number"] == "300"
    assert r.json()["fax_detect_timeout_sec"] == 30

    # 有効なまま切替先だけ消すのは不可
    r = await auth_client.patch(f"/api/trunks/{tid}", json={"fax_route_number": None})
    assert r.status_code == 422
    # 無効化と同時に消すのは可
    r = await auth_client.patch(
        f"/api/trunks/{tid}", json={"fax_detect_enabled": False, "fax_route_number": ""}
    )
    assert r.status_code == 200
    assert r.json()["fax_route_number"] is None

    # 切替先に使われている FAX 受信番号は削除不可・番号変更は追従
    await auth_client.patch(
        f"/api/trunks/{tid}", json={"fax_detect_enabled": True, "fax_route_number": "300"}
    )
    async with app.state.sessionmaker() as s:
        rid = (await s.scalars(select(FaxReceiver.id))).one()
    assert (await auth_client.delete(f"/api/fax-receivers/{rid}")).status_code == 409
    await auth_client.patch(
        f"/api/fax-receivers/{rid}", json={"number": "310", "name": "FAX", "enabled": True}
    )
    assert (await auth_client.get(f"/api/trunks/{tid}")).json()["fax_route_number"] == "310"


# --------------------------------------------------------------------------- 案内音声（#123 連携）


def _announce_writer(tmp_path: Path) -> FreeswitchConfigWriter:
    return FreeswitchConfigWriter(
        output_dir=tmp_path / "fs",
        sip_domain="192.168.1.10",
        esl_password="esl",
        fax_dir=tmp_path / "fax",
        fax_announce_path=tmp_path / "tts" / "builtin" / "fax_receive_notice.wav",
    )


def _actions(xml: str, trunk: str = "Flets02") -> list[tuple[str, str]]:
    root = ET.fromstring(xml)
    ext = next(e for e in root.iter("extension") if e.get("name") == f"inbound_trunk_{trunk}")
    return [
        (a.get("application"), a.get("data") or "")
        for a in ext.iter("action")
        if a.get("application") != "log"
    ]


def test_fax_detect_with_announce_answers_detects_plays_then_transfers(tmp_path):
    out = _announce_writer(tmp_path).render_all(
        [],
        trunks=[
            _trunk(fax_detect_enabled=True, fax_route_number="300", fax_detect_announce=True)
        ],
        fax_receivers=[FaxReceiverConfig(number="300")],
    )["dialplan/public.xml"]
    wav = str((tmp_path / "tts" / "builtin" / "fax_receive_notice.wav").resolve())
    actions = _actions(out)
    assert [app for app, _ in actions] == [
        "set",
        "answer",
        "spandsp_start_fax_detect",
        "playback",
        "transfer",
    ]
    assert ("playback", wav) in actions
    assert ("transfer", "200 XML default") in actions


def test_fax_detect_without_announce_has_no_answer_or_playback(tmp_path):
    out = _announce_writer(tmp_path).render_all(
        [],
        trunks=[_trunk(fax_detect_enabled=True, fax_route_number="300")],
        fax_receivers=[FaxReceiverConfig(number="300")],
    )["dialplan/public.xml"]
    assert [app for app, _ in _actions(out)] == ["set", "spandsp_start_fax_detect", "transfer"]


def test_announce_flag_alone_does_nothing_without_fax_detect(tmp_path):
    out = _announce_writer(tmp_path).render_all(
        [],
        trunks=[_trunk(fax_detect_announce=True)],
        fax_receivers=[FaxReceiverConfig(number="300")],
    )["dialplan/public.xml"]
    assert "playback" not in out and 'application="answer"' not in out


async def test_trunk_fax_detect_announce_api(auth_client):
    await auth_client.post("/api/fax-receivers", json={"number": "300", "name": "FAX"})
    r = await auth_client.post("/api/trunks", json=_TRUNK)
    assert r.json()["fax_detect_announce"] is False
    r = await auth_client.patch(
        f"/api/trunks/{r.json()['id']}",
        json={"fax_detect_enabled": True, "fax_route_number": "300", "fax_detect_announce": True},
    )
    assert r.status_code == 200
    assert r.json()["fax_detect_announce"] is True
