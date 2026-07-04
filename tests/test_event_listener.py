import asyncio
from datetime import datetime

from millicall.models import Cdr
from millicall.telephony.esl import ESLClient
from millicall.telephony.events import CdrRecorder, EslEventListener, event_to_cdr


def _hangup_event(uuid="uuid-abc"):
    return {
        "Event-Name": "CHANNEL_HANGUP_COMPLETE",
        "Channel-Call-UUID": uuid,
        "Call-Direction": "outbound",
        "Caller-Caller-ID-Number": "1001",
        "Caller-Caller-ID-Name": "Alice",
        "Caller-Destination-Number": "0312345678",
        "Caller-Channel-Created-Time": "1751700000000000",
        "Caller-Channel-Answered-Time": "1751700005000000",
        "Caller-Channel-Hangup-Time": "1751700035000000",
        "variable_duration": "35",
        "variable_billsec": "30",
        "Hangup-Cause": "NORMAL_CLEARING",
    }


def test_event_to_cdr_maps_fields():
    cdr = event_to_cdr(_hangup_event())
    assert cdr is not None
    assert cdr.call_uuid == "uuid-abc"
    assert cdr.direction == "outbound"
    assert cdr.src_number == "1001"
    assert cdr.dst_number == "0312345678"
    assert cdr.duration_seconds == 35
    assert cdr.billsec_seconds == 30
    assert cdr.hangup_cause == "NORMAL_CLEARING"
    assert isinstance(cdr.started_at, datetime)


def test_event_to_cdr_ignores_other_events():
    assert event_to_cdr({"Event-Name": "CHANNEL_CREATE"}) is None


async def test_recorder_persists_and_dedupes(app):
    recorder = CdrRecorder(app.state.sessionmaker)
    await recorder.handle(_hangup_event("dup-uuid"))
    await recorder.handle(_hangup_event("dup-uuid"))  # 2レグ目 → UNIQUE で無視
    async with app.state.sessionmaker() as s:
        from sqlalchemy import select

        rows = list(await s.scalars(select(Cdr).where(Cdr.call_uuid == "dup-uuid")))
    assert len(rows) == 1


async def _fake_fs_emitting_hangup():
    async def handle(reader, writer):
        try:
            writer.write(b"Content-Type: auth/request\n\n")
            await writer.drain()
            await reader.readuntil(b"\n\n")
            writer.write(b"Content-Type: command/reply\nReply-Text: +OK accepted\n\n")
            await writer.drain()
            await reader.readuntil(b"\n\n")  # subscribe
            writer.write(b"Content-Type: command/reply\nReply-Text: +OK\n\n")
            await writer.drain()
            ev = (
                b"Event-Name: CHANNEL_HANGUP_COMPLETE\n"
                b"Channel-Call-UUID: live-uuid\n"
                b"Caller-Caller-ID-Number: 1001\n"
                b"Caller-Destination-Number: 0312345678\n"
                b"variable_duration: 12\n"
                b"variable_billsec: 8\n"
                b"Hangup-Cause: NORMAL_CLEARING\n\n"
            )
            writer.write(
                b"Content-Type: text/event-plain\nContent-Length: %d\n\n%s" % (len(ev), ev)
            )
            await writer.drain()
            with __import__("contextlib").suppress(Exception):
                await reader.read(1024)
        finally:
            if not writer.is_closing():
                writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def test_listener_records_cdr_from_live_event(app):
    server, port = await _fake_fs_emitting_hangup()
    recorder = CdrRecorder(app.state.sessionmaker)

    def make_client(handler):
        return ESLClient("127.0.0.1", port, "s3cret", on_event=handler)

    listener = EslEventListener(
        make_client, ["CHANNEL_HANGUP_COMPLETE"], recorder.handle, min_backoff=0.05
    )
    async with server:
        await listener.start()
        for _ in range(40):
            async with app.state.sessionmaker() as s:
                from sqlalchemy import select

                rows = list(await s.scalars(select(Cdr).where(Cdr.call_uuid == "live-uuid")))
            if rows:
                break
            await asyncio.sleep(0.05)
        await listener.stop()
    assert rows and rows[0].dst_number == "0312345678"
