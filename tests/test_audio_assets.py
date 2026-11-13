import hashlib
from importlib import resources
from pathlib import Path

import pytest

from millicall.audio_assets import (
    FAX_RECEIVE_NOTICE_ID,
    builtin_audio_path,
    list_builtin_audio,
    sync_builtin_audio,
)
from millicall.media.call_control import is_safe_audio_path


def _asset_bytes(name: str) -> bytes:
    return resources.files("millicall.assets").joinpath("audio", name).read_bytes()


def test_manifest_lists_fax_receive_notice():
    items = {a.id: a for a in list_builtin_audio()}
    a = items[FAX_RECEIVE_NOTICE_ID]
    assert a.label == "FAX受信案内"
    assert a.file == "fax_receive_notice.wav"
    data = _asset_bytes(a.file)
    # 8kHz / 16bit / mono の PCM WAV（FreeSWITCH で直接再生できる形式）
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    assert int.from_bytes(data[22:24], "little") == 1  # channels
    assert int.from_bytes(data[24:28], "little") == 8000  # sample rate
    assert int.from_bytes(data[34:36], "little") == 16  # bits per sample


def test_every_manifest_file_is_bundled_and_ascii():
    for a in list_builtin_audio():
        assert a.file.isascii()
        assert _asset_bytes(a.file)


def test_sync_copies_and_is_idempotent(tmp_path: Path):
    copied = sync_builtin_audio(tmp_path)
    dst = tmp_path / "builtin" / "fax_receive_notice.wav"
    assert dst in copied
    src = _asset_bytes("fax_receive_notice.wav")
    assert hashlib.sha256(dst.read_bytes()).digest() == hashlib.sha256(src).digest()

    mtime = dst.stat().st_mtime_ns
    assert sync_builtin_audio(tmp_path) == []
    assert dst.stat().st_mtime_ns == mtime


def test_sync_recopies_when_destination_differs(tmp_path: Path):
    sync_builtin_audio(tmp_path)
    dst = tmp_path / "builtin" / "fax_receive_notice.wav"
    src = _asset_bytes("fax_receive_notice.wav")

    # サイズ違い
    dst.write_bytes(b"broken")
    assert sync_builtin_audio(tmp_path) == [dst]
    assert dst.read_bytes() == src

    # 同サイズで中身違い（ハッシュで検出）
    dst.write_bytes(b"\0" * len(src))
    assert sync_builtin_audio(tmp_path) == [dst]
    assert dst.read_bytes() == src


def test_builtin_audio_path_helper(tmp_path: Path):
    p = builtin_audio_path(FAX_RECEIVE_NOTICE_ID, tmp_path)
    assert p == tmp_path / "builtin" / "fax_receive_notice.wav"
    with pytest.raises(KeyError):
        builtin_audio_path("no_such_audio", tmp_path)


@pytest.mark.asyncio
async def test_builtin_api_returns_absolute_safe_path(app, auth_client, monkeypatch, tmp_path):
    tts_dir = (tmp_path / "tts").resolve()
    monkeypatch.setattr(app.state.settings, "tts_cache_dir", tts_dir)
    r = await auth_client.get("/api/audio/builtin")
    assert r.status_code == 200
    body = r.json()
    entry = next(e for e in body if e["id"] == FAX_RECEIVE_NOTICE_ID)
    assert entry["label"] == "FAX受信案内"
    path = Path(entry["path"])
    assert path.is_absolute()
    assert path == tts_dir / "builtin" / "fax_receive_notice.wav"
    assert is_safe_audio_path(entry["path"])


@pytest.mark.asyncio
async def test_builtin_api_allows_non_admin(client, user_factory):
    username, password = await user_factory(username="viewer1", password="Passw0rd1", role="user")
    await client.post("/api/auth/login", json={"username": username, "password": password})
    r = await client.get("/api/audio/builtin")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_builtin_api_requires_login(client):
    r = await client.get("/api/audio/builtin")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_startup_syncs_builtin_audio(app):
    dst = app.state.settings.tts_cache_dir / "builtin" / "fax_receive_notice.wav"
    assert dst.is_file()
