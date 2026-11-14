"""FAX 送信（issue #141）: PDF→TIFF 変換・originate 文字列・結果反映・再試行・API・Webhook。"""

import asyncio
import io
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from PIL import Image, ImageDraw
from sqlalchemy import select

from millicall.crypto import SecretBox
from millicall.fax import convert as convert_mod
from millicall.fax.convert import (
    FAX_WIDTH_PX,
    FaxConvertError,
    FaxConvertLimitError,
    convert_pdf_to_tiff,
    pdf_to_tiff,
)
from millicall.fax.outbox import (
    FaxOutboxService,
    FaxSendError,
    build_originate,
    is_domestic_fax_number,
    issue_outbox_pdf_token,
    verify_outbox_pdf_token,
)
from millicall.models import AuditLog, FaxOutbox, Trunk
from millicall.webhooks.dispatcher import WEBHOOK_EVENTS
from millicall.webhooks.formats import describe_event


def _pdf_bytes(sizes=((595, 842),)) -> bytes:
    """ページサイズ（pt）ごとに 1 ページの PDF を作る（左上に黒い四角）。"""
    pages = []
    for w, h in sizes:
        im = Image.new("RGB", (w, h), "white")
        ImageDraw.Draw(im).rectangle((0, 0, 100, 100), fill="black")
        pages.append(im)
    buf = io.BytesIO()
    pages[0].save(buf, format="PDF", save_all=True, append_images=pages[1:], resolution=72)
    return buf.getvalue()


# --------------------------------------------------------------------------- 変換


def test_pdf_to_tiff_fax_format(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(_pdf_bytes([(595, 842), (842, 595), (300, 6000)]))
    tif = tmp_path / "a.tif"
    assert pdf_to_tiff(pdf, tif, max_pages=100) == 3
    with Image.open(tif) as im:
        assert im.n_frames == 3
        for i in range(3):
            im.seek(i)
            assert im.width == FAX_WIDTH_PX
            assert im.mode == "1"
            assert im.info["compression"] == "group4"
            assert tuple(round(v) for v in im.info["dpi"]) == (204, 196)
            # A4 縦はファイン（196 lpi）で約 2340 ライン。極端に縦長のページは上限で縮小。
            assert im.height <= convert_mod.FAX_MAX_LINES
            # TIFF Class F: 全ページ MinIsWhite（1 = 黒）
            assert im.tag_v2[262] == 0
            # 白地に黒（反転していない）: 大半の画素が白
            hist = im.histogram()
            assert hist[255] > hist[0]
        im.seek(0)
        assert 2300 < im.height < 2400
        assert im.getpixel((5, 5)) == 0  # 黒（四角）
        assert im.getpixel((1000, 1500)) == 255  # 白
    assert tif.stat().st_mode & 0o777 == 0o644
    # 一時ファイルは残らない
    assert {p.name for p in tmp_path.iterdir()} == {"a.pdf", "a.tif"}


def test_tiff_raw_bits_are_min_is_white(tmp_path):
    """Pillow のデコードを介さず、非圧縮で書いた TIFF の生ビットで「1 = 黒」を確認する。"""
    import struct

    from PIL import ImageChops

    im = Image.new("1", (16, 1), 255)
    ImageDraw.Draw(im).rectangle((0, 0, 3, 0), fill=0)  # 左 4 画素が黒
    path = tmp_path / "x.tif"
    ImageChops.invert(im).save(path, format="TIFF")
    convert_mod._set_min_is_white(path)
    buf = path.read_bytes()
    (off,) = struct.unpack_from("<I", buf, 4)
    (n,) = struct.unpack_from("<H", buf, off)
    tags = {}
    for i in range(n):
        tag, typ, _cnt, val = struct.unpack_from("<HHII", buf, off + 2 + i * 12)
        tags[tag] = val & 0xFFFF if typ == 3 else val
    assert tags[262] == 0
    assert buf[tags[273] : tags[273] + 2] == bytes([0b11110000, 0b00000000])
    with Image.open(path) as back:
        assert [back.getpixel((x, 0)) for x in (0, 3, 4, 15)] == [0, 0, 255, 255]


def test_set_min_is_white_rejects_non_tiff(tmp_path):
    bad = tmp_path / "x.tif"
    bad.write_bytes(b"MM\x00*" + b"\x00" * 8)
    with pytest.raises(FaxConvertError):
        convert_mod._set_min_is_white(bad)


def test_pdf_to_tiff_limits_and_invalid(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(_pdf_bytes([(595, 842)] * 3))
    with pytest.raises(FaxConvertLimitError, match="ページ数"):
        pdf_to_tiff(pdf, tmp_path / "a.tif", max_pages=2)
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4\n garbage")
    with pytest.raises(FaxConvertError):
        pdf_to_tiff(bad, tmp_path / "b.tif", max_pages=100)
    notpdf = tmp_path / "x.pdf"
    notpdf.write_bytes(b"hello")
    with pytest.raises(FaxConvertError, match="PDF ファイルではありません"):
        pdf_to_tiff(notpdf, tmp_path / "c.tif", max_pages=100)
    assert not any(p.suffix == ".tif" for p in tmp_path.iterdir())


async def test_convert_runs_in_subprocess_with_errors_mapped(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(_pdf_bytes([(595, 842), (595, 842)]))
    assert await convert_pdf_to_tiff(pdf, tmp_path / "a.tif", max_pages=10) == 2
    with pytest.raises(FaxConvertLimitError):
        await convert_pdf_to_tiff(pdf, tmp_path / "b.tif", max_pages=1)
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4\n garbage")
    with pytest.raises(FaxConvertError) as exc:
        await convert_pdf_to_tiff(bad, tmp_path / "c.tif", max_pages=10)
    assert not isinstance(exc.value, FaxConvertLimitError)
    assert not (tmp_path / "b.tif").exists() and not (tmp_path / "c.tif").exists()


async def test_convert_timeout_kills_process(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(_pdf_bytes())
    with pytest.raises(FaxConvertError, match="タイムアウト"):
        await convert_pdf_to_tiff(pdf, tmp_path / "a.tif", max_pages=10, timeout=0.001)


# --------------------------------------------------------------------------- originate


def test_build_originate_string():
    cmd = build_originate(
        call_uuid="0b9c7a4e-1111-4222-8333-444455556666",
        trunk_name="Flets02",
        to_number="0312345678",
        caller_id="0445895784",
        tiff_path="/app/data/fax/out/7.tif",
        job_id=7,
    )
    assert cmd == (
        "originate {origination_uuid=0b9c7a4e-1111-4222-8333-444455556666,"
        "origination_caller_id_number=0445895784,origination_caller_id_name=0445895784,"
        "absolute_codec_string=PCMU,fax_enable_t38=false,fax_verbose=false,"
        "ignore_early_media=true,originate_timeout=60,millicall_fax_outbox_id=7}"
        "sofia/gateway/Flets02/0312345678 &txfax(/app/data/fax/out/7.tif)"
    )
    # 発信者番号が無いトランクでは caller id 変数を付けない
    no_cid = build_originate(
        call_uuid="abcdefgh-1", trunk_name="t", to_number="0312345678", caller_id="",
        tiff_path="/x/1.tif", job_id=1,
    )
    assert "origination_caller_id" not in no_cid


@pytest.mark.parametrize(
    "field,value",
    [
        ("to_number", "0312345678,x=y"),
        ("to_number", "010123456789"),  # 国際
        ("to_number", "1840312345678"),
        ("trunk_name", "a b"),
        ("caller_id", "03}{"),
        ("tiff_path", "/tmp/a b.tif"),
        ("tiff_path", "/tmp/a);hangup(.tif"),
        ("call_uuid", "x y"),
    ],
)
def test_build_originate_rejects_injection(field, value):
    kw = dict(
        call_uuid="abcdefgh-1", trunk_name="t", to_number="0312345678", caller_id="03",
        tiff_path="/x/1.tif", job_id=1,
    )
    kw[field] = value
    with pytest.raises(ValueError):
        build_originate(**kw)


# --------------------------------------------------------------------------- サービス


class _FakeEsl:
    def __init__(self) -> None:
        self.bgapi_calls: list[str] = []
        self.api_calls: list[str] = []
        self.exists: dict[str, bool] = {}
        self.fail_bgapi = False

    async def bgapi(self, command: str) -> str:
        self.bgapi_calls.append(command)
        if self.fail_bgapi:
            raise ConnectionError("down")
        return "job"

    async def api(self, command: str) -> str:
        self.api_calls.append(command)
        if command.startswith("uuid_exists "):
            return "true" if self.exists.get(command.split()[1]) else "false"
        return "+OK"


class _FakeDispatcher:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict]] = []

    def emit(self, name: str, data: dict) -> None:
        self.emitted.append((name, data))


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 14, 0, 0, 0)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


async def _add_trunks(app, *names: str, enabled: bool = True) -> None:
    async with app.state.sessionmaker() as s:
        for name in names:
            s.add(
                Trunk(
                    name=name,
                    display_name=name,
                    host="192.168.1.1",
                    username="4",
                    did_number="0445895784",
                    caller_id="0445895784",
                    enabled=enabled,
                )
            )
        await s.commit()


@pytest.fixture
async def outbox(app):
    # アプリ本体のワーカーは止め、テスト用のサービスを直接駆動する。
    await app.state.fax_outbox_service.stop()
    esl = _FakeEsl()
    disp = _FakeDispatcher()
    clock = _Clock()
    svc = FaxOutboxService(
        app.state.sessionmaker,
        app.state.settings.fax_dir,
        esl_bgapi=esl.bgapi,
        esl_api=esl.api,
        dispatcher=disp,
        public_base_url="https://pbx.example",
        secret_box=SecretBox(app.state.secrets.master_key),
        clock=clock,
        free_bytes=lambda _p: 10**12,
    )
    await _add_trunks(app, "Flets02", "Brastel")
    return svc, esl, disp, clock


async def _job(app, job_id: int) -> FaxOutbox:
    async with app.state.sessionmaker() as s:
        return await s.get(FaxOutbox, job_id)


async def _submit(app, svc, **kw) -> FaxOutbox:
    """登録して、バックグラウンドの変換が終わった状態のジョブを返す。"""
    job = await svc.submit(**kw)
    assert job.status == "converting"
    await svc.drain()
    return await _job(app, job.id)


def _hangup(call_uuid: str, **kw) -> dict[str, str]:
    ev = {"Event-Name": "CHANNEL_HANGUP_COMPLETE", "Unique-ID": call_uuid}
    ev.update(kw)
    return ev


async def test_submit_converts_and_stores_with_server_names(app, outbox):
    svc, _esl, _disp, _clock = outbox
    job = await _submit(app, svc,
        data=_pdf_bytes([(595, 842), (595, 842)]), to_number="0312345678", trunk_name=None,
        user_id=None,
    )
    assert job.status == "queued" and job.pages == 2
    # 既定の発信トランク = 有効なトランクの名前順先頭
    assert job.trunk_name == "Brastel"
    out = app.state.settings.fax_dir / "out"
    assert Path(job.pdf_path) == (out / f"{job.id}.pdf").resolve()
    assert Path(job.tiff_path) == (out / f"{job.id}.tif").resolve()
    assert Path(job.pdf_path).read_bytes().startswith(b"%PDF")
    assert sorted(p.name for p in out.iterdir()) == [f"{job.id}.pdf", f"{job.id}.tif"]


async def test_submit_validation(app, outbox):
    svc, *_ = outbox
    pdf = _pdf_bytes()
    with pytest.raises(FaxSendError, match="国内番号"):
        await svc.submit(data=pdf, to_number="0101234567890", trunk_name=None, user_id=None)
    with pytest.raises(FaxSendError) as exc:
        await svc.submit(data=b"GIF89a", to_number="0312345678", trunk_name=None, user_id=None)
    assert exc.value.http_status == 415
    with pytest.raises(FaxSendError, match="トランク"):
        await svc.submit(data=pdf, to_number="0312345678", trunk_name="nope", user_id=None)


async def test_submit_rejects_disabled_trunk_and_low_disk(app, outbox):
    svc, *_ = outbox
    await _add_trunks(app, "Off", enabled=False)
    with pytest.raises(FaxSendError, match="トランク"):
        await svc.submit(data=_pdf_bytes(), to_number="0312345678", trunk_name="Off", user_id=None)
    svc._free_bytes = lambda _p: 0
    with pytest.raises(FaxSendError) as exc:
        await svc.submit(data=_pdf_bytes(), to_number="0312345678", trunk_name=None, user_id=None)
    assert exc.value.http_status == 507


async def test_send_success_updates_job_and_fires_webhook(app, outbox):
    svc, esl, disp, _clock = outbox
    job = await _submit(app, svc,
        data=_pdf_bytes(), to_number="0312345678", trunk_name="Flets02", user_id=None
    )
    await svc.tick()
    assert len(esl.bgapi_calls) == 1
    cmd = esl.bgapi_calls[0]
    sent = await _job(app, job.id)
    assert sent.status == "sending" and sent.attempts == 1
    assert cmd.startswith(f"originate {{origination_uuid={sent.call_uuid},")
    assert "absolute_codec_string=PCMU" in cmd and "fax_enable_t38=false" in cmd
    assert cmd.endswith(f"sofia/gateway/Flets02/0312345678 &txfax({sent.tiff_path})")

    # 関係ない通話の終了は無視
    await svc.handle(_hangup("other-uuid-0000"))
    await svc.handle(
        _hangup(
            sent.call_uuid,
            **{
                "Hangup-Cause": "NORMAL_CLEARING",
                "variable_fax_success": "1",
                "variable_fax_result_code": "0",
                "variable_fax_result_text": "OK",
                "variable_fax_document_transferred_pages": "1",
                "variable_fax_remote_station_id": "+81 3 1234 5678",
            },
        )
    )
    await svc.drain()
    done = await _job(app, job.id)
    assert done.status == "sent" and done.finished_at is not None
    assert done.remote_station_id == "+81 3 1234 5678" and done.result_text == "OK"
    assert len(disp.emitted) == 1
    name, data = disp.emitted[0]
    assert name == "fax.sent"
    fax = data["fax"]
    assert fax["direction"] == "outbound" and fax["success"] is True
    assert fax["to"] == "0312345678" and fax["trunk"] == "Flets02"
    assert fax["pages"] == 1 and fax["attempts"] == 1 and fax["finished_at"].endswith("Z")
    assert set(fax) >= {
        "id", "direction", "to", "trunk", "pages", "success", "result_text",
        "remote_station_id", "attempts", "finished_at", "pdf_url",
    }
    url = urlsplit(fax["pdf_url"])
    assert url.path == f"/api/faxes/outbox/{job.id}/pdf"
    token = parse_qs(url.query)["token"][0]
    assert verify_outbox_pdf_token(SecretBox(app.state.secrets.master_key), token, done)


async def test_retry_schedule_then_final_failure(app, outbox):
    svc, esl, disp, clock = outbox
    job = await _submit(app, svc,
        data=_pdf_bytes(), to_number="0312345678", trunk_name="Flets02", user_id=None
    )
    # 1 回目: 話中 → 1 分後に再試行
    await svc.tick()
    j = await _job(app, job.id)
    await svc.process_result(_hangup(j.call_uuid, **{"Hangup-Cause": "USER_BUSY"}))
    j = await _job(app, job.id)
    assert j.status == "queued" and j.attempts == 1
    assert j.next_attempt_at == clock.now + timedelta(seconds=60)
    assert "話中" in j.last_error
    await svc.tick()  # まだ待ち時間中
    assert len(esl.bgapi_calls) == 1
    clock.advance(60)
    await svc.tick()
    assert len(esl.bgapi_calls) == 2
    # 2 回目: 送信中のエラー → 5 分後
    j = await _job(app, job.id)
    await svc.process_result(
        _hangup(
            j.call_uuid,
            **{
                "Hangup-Cause": "NORMAL_CLEARING",
                "variable_fax_success": "0",
                "variable_fax_result_code": "49",
                "variable_fax_result_text": "The call dropped prematurely",
            },
        )
    )
    j = await _job(app, job.id)
    assert j.status == "queued" and j.attempts == 2
    assert j.next_attempt_at == clock.now + timedelta(seconds=300)
    assert j.last_error == "送信エラー: The call dropped prematurely"
    clock.advance(300)
    await svc.tick()
    # 3 回目: 無応答 → 上限で失敗確定
    j = await _job(app, job.id)
    await svc.process_result(_hangup(j.call_uuid, **{"Hangup-Cause": "NO_ANSWER"}))
    j = await _job(app, job.id)
    assert j.status == "failed" and j.attempts == 3 and j.finished_at is not None
    assert [n for n, _ in disp.emitted] == ["fax.failed"]
    payload = disp.emitted[0][1]["fax"]
    assert payload["success"] is False and payload["attempts"] == 3
    assert "応答なし" in payload["error"]


async def test_non_retriable_cause_fails_immediately(app, outbox):
    svc, _esl, disp, _clock = outbox
    job = await _submit(app, svc,
        data=_pdf_bytes(), to_number="0312345678", trunk_name="Flets02", user_id=None
    )
    await svc.tick()
    j = await _job(app, job.id)
    await svc.process_result(_hangup(j.call_uuid, **{"Hangup-Cause": "UNALLOCATED_NUMBER"}))
    j = await _job(app, job.id)
    assert j.status == "failed" and j.attempts == 1
    assert "使われていません" in j.last_error
    assert [n for n, _ in disp.emitted] == ["fax.failed"]


async def test_bgapi_failure_is_retried(app, outbox):
    svc, esl, _disp, clock = outbox
    esl.fail_bgapi = True
    job = await _submit(app, svc,
        data=_pdf_bytes(), to_number="0312345678", trunk_name="Flets02", user_id=None
    )
    await svc.tick()
    j = await _job(app, job.id)
    assert j.status == "queued" and j.attempts == 1 and "FreeSWITCH" in j.last_error
    assert j.next_attempt_at == clock.now + timedelta(seconds=60)


async def test_watchdog_fails_attempt_when_call_vanishes(app, outbox):
    svc, esl, _disp, clock = outbox
    job = await _submit(app, svc,
        data=_pdf_bytes(), to_number="0312345678", trunk_name="Flets02", user_id=None
    )
    await svc.tick()
    j = await _job(app, job.id)
    esl.exists[j.call_uuid] = True
    clock.advance(95)
    await svc.tick()
    assert (await _job(app, job.id)).status == "sending"  # 通話中はそのまま
    esl.exists[j.call_uuid] = False
    await svc.tick()
    assert (await _job(app, job.id)).status == "sending"  # 結果イベントの遅延到着を待つ
    clock.advance(10)
    await svc.tick()
    j2 = await _job(app, job.id)
    # 結果不明は二重送信を避けるため自動では再送しない
    assert j2.status == "failed" and "結果を受け取れません" in j2.last_error
    assert j2.attempts == 1
    # 反映済みの試行に遅れて届いた結果は無視される
    assert await svc.process_result(_hangup(j.call_uuid, variable_fax_success="1")) is None


async def test_watchdog_kills_overlong_send(app, outbox):
    svc, esl, _disp, clock = outbox
    job = await _submit(app, svc,
        data=_pdf_bytes(), to_number="0312345678", trunk_name="Flets02", user_id=None
    )
    await svc.tick()
    j = await _job(app, job.id)
    esl.exists[j.call_uuid] = True
    clock.advance(60 + 30 + 1800 + 60)
    await svc.tick()
    assert f"uuid_kill {j.call_uuid}" in esl.api_calls
    assert (await _job(app, job.id)).status == "failed"


async def test_concurrency_limit(app, outbox):
    svc, esl, _disp, _clock = outbox
    for _ in range(2):
        await _submit(
            app, svc, data=_pdf_bytes(), to_number="0312345678", trunk_name="Flets02", user_id=None
        )
    await svc.tick()
    await svc.tick()
    assert len(esl.bgapi_calls) == 1  # 既定は同時 1 本


async def test_start_fails_when_trunk_disabled(app, outbox):
    svc, esl, disp, _clock = outbox
    job = await _submit(app, svc,
        data=_pdf_bytes(), to_number="0312345678", trunk_name="Flets02", user_id=None
    )
    async with app.state.sessionmaker() as s:
        t = await s.scalar(select(Trunk).where(Trunk.name == "Flets02"))
        t.enabled = False
        await s.commit()
    await svc.tick()
    assert esl.bgapi_calls == []
    assert (await _job(app, job.id)).status == "failed"
    assert [n for n, _ in disp.emitted] == ["fax.failed"]


async def test_recover_on_startup(app, outbox):
    svc, esl, disp, _clock = outbox
    async with app.state.sessionmaker() as s:
        alive = FaxOutbox(
            to_number="0312345678", trunk_name="Flets02", status="sending", attempts=1,
            call_uuid="aaaaaaaa-0001",
        )
        gone = FaxOutbox(
            to_number="0312345678", trunk_name="Flets02", status="sending", attempts=1,
            call_uuid="bbbbbbbb-0002",
        )
        s.add_all([alive, gone])
        await s.commit()
        alive_id, gone_id = alive.id, gone.id
    esl.exists["aaaaaaaa-0001"] = True
    await svc.recover()
    assert (await _job(app, alive_id)).status == "sending"
    g = await _job(app, gone_id)
    assert g.status == "failed" and "再起動" in g.last_error
    assert [n for n, _ in disp.emitted] == ["fax.failed"]
    # 生きていた通話の結果は引き続き反映される
    await svc.process_result(_hangup("aaaaaaaa-0001", variable_fax_success="1"))
    assert (await _job(app, alive_id)).status == "sent"


async def test_cleanup_removes_old_finished_jobs(app, outbox):
    svc, *_ = outbox
    job = await _submit(app, svc,
        data=_pdf_bytes(), to_number="0312345678", trunk_name="Flets02", user_id=None
    )
    queued = await _submit(app, svc,
        data=_pdf_bytes(), to_number="0312345678", trunk_name="Flets02", user_id=None
    )
    async with app.state.sessionmaker() as s:
        j = await s.get(FaxOutbox, job.id)
        j.status = "sent"
        await s.commit()
    future = datetime.now() + timedelta(days=31)
    assert await svc.cleanup(30, now=future) == 1
    assert await _job(app, job.id) is None
    assert not Path(job.pdf_path).exists() and not Path(job.tiff_path).exists()
    # 送信待ちのジョブは消さない
    assert await _job(app, queued.id) is not None
    assert await svc.cleanup(0) == 0


def test_outbox_pdf_token_is_bound_to_job():
    box = SecretBox("test-master-key")
    a = FaxOutbox(id=1, to_number="0", trunk_name="t", created_at=datetime(2026, 1, 1))
    b = FaxOutbox(id=1, to_number="0", trunk_name="t", created_at=datetime(2026, 1, 2))
    token = issue_outbox_pdf_token(box, a)
    assert verify_outbox_pdf_token(box, token, a)
    assert not verify_outbox_pdf_token(box, token, b)
    assert not verify_outbox_pdf_token(box, "garbage", a)


# --------------------------------------------------------------------------- Webhook


def test_webhook_events_include_fax_send():
    assert "fax.sent" in WEBHOOK_EVENTS and "fax.failed" in WEBHOOK_EVENTS


def test_chat_format_for_fax_sent_and_failed():
    base = {
        "id": 3, "direction": "outbound", "to": "0312345678", "trunk": "Flets02", "pages": 2,
        "result_text": "OK", "remote_station_id": "", "attempts": 1,
        "finished_at": "2026-11-14T00:00:00Z", "pdf_url": "https://pbx.example/x.pdf",
    }
    ok = describe_event("fax.sent", {"fax": {**base, "success": True}})
    assert ok.title == "📠 FAX 送信完了"
    assert "0312345678" in ok.summary and "2 ページ" in ok.summary
    assert ok.link is not None and ok.link.url == "https://pbx.example/x.pdf"
    ng = describe_event(
        "fax.failed", {"fax": {**base, "success": False, "attempts": 3, "error": "話中（USER_BUSY）"}}
    )
    assert ng.title == "📠 FAX 送信失敗"
    values = {f.name: f.value for f in ng.fields}
    assert values["理由"] == "話中（USER_BUSY）"
    assert values["試行回数"] == "3 回"


# --------------------------------------------------------------------------- API


@pytest.fixture
async def api_ready(app):
    # API テストではワーカーを止め、登録直後の状態（queued）を検証できるようにする。
    await app.state.fax_outbox_service.stop()
    app.state.fax_outbox_service._free_bytes = lambda _p: 10**12
    await _add_trunks(app, "Flets02")
    return app


def _upload(data: bytes | None = None, name: str = "doc.pdf"):
    return {"file": (name, data if data is not None else _pdf_bytes(), "application/pdf")}


async def test_api_send_list_get_cancel_retry(auth_client, api_ready):
    app = api_ready
    r = await auth_client.post(
        "/api/faxes/outbox", files=_upload(name="../../evil.pdf"), data={"to_number": "0312345678"}
    )
    assert r.status_code == 202, r.text
    job = r.json()
    # 変換はバックグラウンド（アップロード要求は変換を待たない）
    assert job["status"] == "converting" and job["trunk_name"] == "Flets02"
    assert job["has_pdf"] is True
    await app.state.fax_outbox_service.drain()
    job = (await auth_client.get(f"/api/faxes/outbox/{job['id']}")).json()
    assert job["status"] == "queued" and job["pages"] == 1
    # ファイル名はサーバー採番（アップロード名は使わない）。元 PDF は 0600、TIFF は 0644
    out = app.state.settings.fax_dir / "out"
    assert sorted(p.name for p in out.iterdir()) == [f"{job['id']}.pdf", f"{job['id']}.tif"]
    assert (out / f"{job['id']}.pdf").stat().st_mode & 0o777 == 0o600
    assert (out / f"{job['id']}.tif").stat().st_mode & 0o777 == 0o644

    lst = (await auth_client.get("/api/faxes/outbox")).json()
    assert lst["total"] == 1 and lst["items"][0]["id"] == job["id"]
    assert (await auth_client.get(f"/api/faxes/outbox/{job['id']}")).json()["to_number"] == (
        "0312345678"
    )
    # 受信 FAX の API は従来どおり
    assert (await auth_client.get("/api/faxes")).json() == {"items": [], "total": 0}

    pdf = await auth_client.get(f"/api/faxes/outbox/{job['id']}/pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")

    # queued 以外は再送のみ・取消不可
    assert (await auth_client.post(f"/api/faxes/outbox/{job['id']}/retry")).status_code == 409
    r = await auth_client.post(f"/api/faxes/outbox/{job['id']}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert (await auth_client.post(f"/api/faxes/outbox/{job['id']}/cancel")).status_code == 409
    assert (await auth_client.get(f"/api/faxes/outbox/{job['id']}")).json()["can_retry"] is True
    r = await auth_client.post(f"/api/faxes/outbox/{job['id']}/retry")
    assert r.status_code == 200 and r.json()["status"] == "queued" and r.json()["attempts"] == 0
    assert r.json()["retry_count"] == 1

    async with app.state.sessionmaker() as s:
        logs = (
            await s.scalars(select(AuditLog).where(AuditLog.target_type == "fax_outbox"))
        ).all()
    actions = [log.action for log in logs]
    assert actions == ["fax.send", "fax.cancel", "fax.retry"]
    detail = json.loads(logs[0].detail)
    # 登録時点のページ数は 0（変換は非同期）。番号・トランクを記録する
    assert detail["to"] == "0312345678" and detail["trunk"] == "Flets02"


async def test_api_validation(auth_client, api_ready, monkeypatch):
    post = auth_client.post
    # 国際番号（010 / 00x）は国内番号の形式でも拒否する
    for number in ("010441234567", "01068212345", "00168212345", "0033123456"):
        r = await post("/api/faxes/outbox", files=_upload(), data={"to_number": number})
        assert r.status_code == 422, number
    r = await post("/api/faxes/outbox", files=_upload(b"PK\x03\x04"), data={"to_number": "0312345678"})
    assert r.status_code == 415
    r = await post(
        "/api/faxes/outbox", files=_upload(), data={"to_number": "0312345678", "trunk_name": "x"}
    )
    assert r.status_code == 422
    r = await post("/api/faxes/outbox", json={"to_number": "0312345678"})
    assert r.status_code == 415
    monkeypatch.setattr("millicall.fax.outbox_router.FAX_MAX_UPLOAD_BYTES", 100)
    r = await post("/api/faxes/outbox", files=_upload(), data={"to_number": "0312345678"})
    assert r.status_code == 413
    # 宣言サイズ（Content-Length）が上限を超えていれば本文を解析せずに 413
    monkeypatch.setattr("millicall.fax.outbox_router._MULTIPART_OVERHEAD", 0)

    async def _no_parse(*_a, **_kw):
        raise AssertionError("form must not be parsed")

    monkeypatch.setattr("starlette.requests.Request.form", _no_parse)
    r = await post("/api/faxes/outbox", files=_upload(), data={"to_number": "0312345678"})
    assert r.status_code == 413
    monkeypatch.undo()
    assert (await auth_client.get("/api/faxes/outbox")).json()["total"] == 0
    # ページ数の上限超過は変換時に判明する → ジョブは failed（理由つき）
    monkeypatch.setattr("millicall.fax.outbox.FAX_MAX_PAGES", 1)
    r = await post(
        "/api/faxes/outbox",
        files=_upload(_pdf_bytes([(595, 842)] * 2)),
        data={"to_number": "0312345678"},
    )
    assert r.status_code == 202
    await api_ready.state.fax_outbox_service.drain()
    job = (await auth_client.get(f"/api/faxes/outbox/{r.json()['id']}")).json()
    assert job["status"] == "failed" and "ページ数" in job["last_error"]
    assert job["can_retry"] is False  # TIFF が無いので再送不可

    # 拒否した登録は監査ログに残る（番号と理由。ファイル内容は残さない）
    async with api_ready.state.sessionmaker() as s:
        rejected = (
            await s.scalars(select(AuditLog).where(AuditLog.action == "fax.send_rejected"))
        ).all()
    reasons = [json.loads(log.detail) for log in rejected]
    assert {d["status"] for d in reasons} >= {413, 415, 422}
    assert any(d["to"] == "01068212345" and d["status"] == 422 for d in reasons)


async def test_api_permissions(client, user_factory, api_ready):
    app = api_ready
    assert (await client.get("/api/faxes/outbox")).status_code == 401
    await user_factory(username="plain", role="user")
    await client.post("/api/auth/login", json={"username": "plain", "password": "Passw0rd1"})
    assert (await client.get("/api/faxes/outbox")).status_code == 403
    r = await client.post("/api/faxes/outbox", files=_upload(), data={"to_number": "0312345678"})
    assert r.status_code == 403

    # 設定で一般ユーザーにも許可すると、自分のジョブだけ見える
    app.state.settings.fax_send_allow_users = True
    async with app.state.sessionmaker() as s:
        other = FaxOutbox(to_number="0311111111", trunk_name="Flets02", status="failed")
        s.add(other)
        await s.commit()
        other_id = other.id
    r = await client.post("/api/faxes/outbox", files=_upload(), data={"to_number": "0312345678"})
    assert r.status_code == 202
    mine = r.json()["id"]
    lst = (await client.get("/api/faxes/outbox")).json()
    assert [j["id"] for j in lst["items"]] == [mine] and lst["total"] == 1
    assert (await client.get(f"/api/faxes/outbox/{other_id}")).status_code == 404
    assert (await client.post(f"/api/faxes/outbox/{other_id}/retry")).status_code == 404
    assert (await client.get(f"/api/faxes/outbox/{other_id}/pdf")).status_code == 404
    assert (await client.get(f"/api/faxes/outbox/{mine}/pdf")).status_code == 200
    # 一般ユーザーは /api/trunks を使えないため、送信用の軽いトランク一覧を使う
    assert (await client.get("/api/trunks")).status_code == 403
    trunks = (await client.get("/api/faxes/outbox/trunks")).json()
    assert trunks == [
        {"name": "Flets02", "display_name": "Flets02", "trunk_type": "hgw", "is_default": True}
    ]


async def test_api_pdf_with_signed_token(client, user_factory, api_ready):
    app = api_ready
    username, password = await user_factory(username="admin1")
    await client.post("/api/auth/login", json={"username": username, "password": password})
    job = (
        await client.post("/api/faxes/outbox", files=_upload(), data={"to_number": "0312345678"})
    ).json()
    await client.post("/api/auth/logout")
    client.cookies.clear()
    box = SecretBox(app.state.secrets.master_key)
    async with app.state.sessionmaker() as s:
        row = await s.get(FaxOutbox, job["id"])
    url = urlsplit(app.state.fax_outbox_service.pdf_url(row))
    token = parse_qs(url.query)["token"][0]
    assert verify_outbox_pdf_token(box, token, row)
    ok = await client.get(f"/api/faxes/outbox/{job['id']}/pdf", params={"token": token})
    assert ok.status_code == 200 and ok.content.startswith(b"%PDF")
    bad = await client.get(f"/api/faxes/outbox/{job['id']}/pdf", params={"token": "x"})
    assert bad.status_code == 403
    missing = await client.get("/api/faxes/outbox/999/pdf", params={"token": token})
    assert missing.status_code == 403
    assert (await client.get(f"/api/faxes/outbox/{job['id']}/pdf")).status_code == 401
    # トークンでの取得は監査ログに残る
    async with app.state.sessionmaker() as s:
        logs = (
            await s.scalars(select(AuditLog).where(AuditLog.action == "fax.pdf_download"))
        ).all()
    assert len(logs) == 1 and logs[0].actor_label == "token"
    assert logs[0].target_id == str(job["id"])


async def test_api_retry_is_limited(auth_client, api_ready):
    app = api_ready
    job = (
        await auth_client.post(
            "/api/faxes/outbox", files=_upload(), data={"to_number": "0312345678"}
        )
    ).json()
    await app.state.fax_outbox_service.drain()
    for _ in range(3):
        async with app.state.sessionmaker() as s:
            row = await s.get(FaxOutbox, job["id"])
            row.status = "failed"
            await s.commit()
        assert (await auth_client.post(f"/api/faxes/outbox/{job['id']}/retry")).status_code == 200
    async with app.state.sessionmaker() as s:
        row = await s.get(FaxOutbox, job["id"])
        row.status = "failed"
        await s.commit()
    one = (await auth_client.get(f"/api/faxes/outbox/{job['id']}")).json()
    assert one["retry_count"] == 3 and one["can_retry"] is False
    r = await auth_client.post(f"/api/faxes/outbox/{job['id']}/retry")
    assert r.status_code == 409 and "3 回まで" in r.json()["detail"]


async def test_api_cancel_while_converting(auth_client, api_ready):
    app = api_ready
    job = (
        await auth_client.post(
            "/api/faxes/outbox", files=_upload(), data={"to_number": "0312345678"}
        )
    ).json()
    r = await auth_client.post(f"/api/faxes/outbox/{job['id']}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    await app.state.fax_outbox_service.drain()
    # 変換完了後も取消のまま。TIFF は記録され、再送できる
    one = (await auth_client.get(f"/api/faxes/outbox/{job['id']}")).json()
    assert one["status"] == "cancelled" and one["pages"] == 1 and one["can_retry"] is True


async def test_upload_slot_limits_concurrency(app, outbox):
    svc, *_ = outbox
    async with svc.upload_slot(), svc.upload_slot():
        with pytest.raises(FaxSendError) as exc:
            async with svc.upload_slot():
                pass
        assert exc.value.http_status == 429
    async with svc.upload_slot():
        pass


@pytest.mark.parametrize(
    "number,ok",
    [
        ("0312345678", True),
        ("09012345678", True),
        ("0120123456", True),
        ("01068212345", False),  # 010 = 国際
        ("00168212345", False),  # 00x = 国際
        ("0033123456", False),
        ("1840312345678", False),
        ("031234567", False),
    ],
)
def test_domestic_number_policy(number, ok):
    assert is_domestic_fax_number(number) is ok
    kw = dict(
        call_uuid="abcdefgh-1", trunk_name="t", to_number=number, caller_id="",
        tiff_path="/x/1.tif", job_id=1,
    )
    if ok:
        assert build_originate(**kw).endswith(f"/{number} &txfax(/x/1.tif)")
    else:
        with pytest.raises(ValueError):
            build_originate(**kw)


async def test_submit_rejects_international_number(app, outbox):
    svc, *_ = outbox
    with pytest.raises(FaxSendError, match="国際番号"):
        await svc.submit(
            data=_pdf_bytes(), to_number="01068212345", trunk_name=None, user_id=None
        )


async def test_cancel_and_start_do_not_race(app, outbox):
    """取消と発信開始を同時に走らせても「取消なのに発信」「発信したのに取消」にならない。"""
    svc, esl, _disp, _clock = outbox
    for order in ("start_first", "cancel_first"):
        esl.bgapi_calls.clear()
        job = await _submit(
            app, svc, data=_pdf_bytes(), to_number="0312345678", trunk_name="Flets02",
            user_id=None,
        )
        if order == "start_first":
            _, cancelled = await asyncio.gather(svc._start(job.id), svc.cancel(job.id))
        else:
            cancelled, _ = await asyncio.gather(svc.cancel(job.id), svc._start(job.id))
        row = await _job(app, job.id)
        if cancelled:
            assert row.status == "cancelled" and esl.bgapi_calls == []
        else:
            assert row.status == "sending" and len(esl.bgapi_calls) == 1
        # 次のジョブのために送信中を片付ける
        async with app.state.sessionmaker() as s:
            r = await s.get(FaxOutbox, job.id)
            r.status = "failed"
            await s.commit()


async def test_start_after_cancel_is_noop(app, outbox):
    svc, esl, _disp, _clock = outbox
    job = await _submit(
        app, svc, data=_pdf_bytes(), to_number="0312345678", trunk_name="Flets02", user_id=None
    )
    assert await svc.cancel(job.id) is True
    await svc._start(job.id)
    assert esl.bgapi_calls == [] and (await _job(app, job.id)).status == "cancelled"
    assert await svc.cancel(job.id) is False


async def test_recover_restarts_conversion(app, outbox):
    svc, *_ = outbox
    out = app.state.settings.fax_dir / "out"
    out.mkdir(parents=True, exist_ok=True)
    async with app.state.sessionmaker() as s:
        job = FaxOutbox(to_number="0312345678", trunk_name="Flets02", status="converting")
        s.add(job)
        await s.flush()
        pdf = out / f"{job.id}.pdf"
        pdf.write_bytes(_pdf_bytes())
        job.pdf_path = str(pdf.resolve())
        await s.commit()
        job_id = job.id
    await svc.recover()
    await svc.drain()
    row = await _job(app, job_id)
    assert row.status == "queued" and row.pages == 1


async def test_sweep_work_files(app, outbox):
    svc, *_ = outbox
    out = app.state.settings.fax_dir / "out"
    out.mkdir(parents=True, exist_ok=True)
    for name in (".faxout-abc.tif.tmp", ".upload-x.pdf", "7.pdf"):
        (out / name).write_bytes(b"x")
    # 日次の掃除は新しい作業ファイル（変換中かもしれない）を消さない
    assert svc.sweep_work_files(older_than=timedelta(days=1)) == 0
    old = datetime.now().timestamp() - 2 * 24 * 3600
    os.utime(out / ".faxout-abc.tif.tmp", (old, old))
    assert svc.sweep_work_files(older_than=timedelta(days=1)) == 1
    # 起動時（start）はすべて消す
    await svc.start()
    await svc.stop()
    assert sorted(p.name for p in out.iterdir()) == ["7.pdf"]


async def test_convert_cancel_kills_child_and_cleans_up(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(_pdf_bytes([(595, 842)] * 5))
    task = asyncio.create_task(convert_pdf_to_tiff(pdf, tmp_path / "a.tif", max_pages=10))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.pdf"]


def test_child_env_drops_secrets(monkeypatch):
    monkeypatch.setenv("MILLICALL_MASTER_KEY", "secret")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = convert_mod._child_env()
    assert "MILLICALL_MASTER_KEY" not in env
    assert env["PATH"] == "/usr/bin" and env["HOME"] == "/nonexistent"
