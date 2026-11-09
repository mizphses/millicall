import pytest

from millicall.ai import registry


class _ConfigEchoTTS:
    """config の speaker を PCM に埋め込む Fake。設定が音声に反映されたか判別できる。"""

    calls = 0

    def __init__(self, config):
        self._speaker = int(config.get("speaker", 1))

    async def synthesize(self, text):
        type(self).calls += 1
        return bytes([self._speaker, 0]) * 80


@pytest.mark.asyncio
async def test_synthesize_regenerates_after_voice_setting_change(
    app, auth_client_with_telephony, monkeypatch, tmp_path
):
    # 既定の tts_cache_dir はリポジトリ配下で実行間に残るため、テスト毎の一時ディレクトリへ
    monkeypatch.setattr(app.state.settings, "tts_cache_dir", tmp_path / "tts")
    _ConfigEchoTTS.calls = 0
    monkeypatch.setattr(registry, "build_tts", lambda kind, config, api_key: _ConfigEchoTTS(config))
    c = auth_client_with_telephony
    r = await c.post(
        "/api/providers",
        json={
            "name": "vv",
            "type": "tts",
            "kind": "voicevox",
            "config": {"engine_url": "http://192.168.1.10:50021", "speaker": 1},
        },
    )
    pid = r.json()["id"]

    first = await c.post("/api/tts-cache/synthesize", json={"provider_id": pid, "text": "案内"})
    assert first.status_code == 200
    assert first.json()["cached"] is False
    again = await c.post("/api/tts-cache/synthesize", json={"provider_id": pid, "text": "案内"})
    assert again.json() == {"path": first.json()["path"], "cached": True}
    assert _ConfigEchoTTS.calls == 1

    r = await c.patch(
        f"/api/providers/{pid}",
        json={"config": {"engine_url": "http://192.168.1.10:50021", "speaker": 3}},
    )
    assert r.status_code == 200
    changed = await c.post("/api/tts-cache/synthesize", json={"provider_id": pid, "text": "案内"})
    assert changed.json()["cached"] is False
    assert changed.json()["path"] != first.json()["path"]
    assert _ConfigEchoTTS.calls == 2
    with open(changed.json()["path"], "rb") as f:
        new_wav = f.read()
    with open(first.json()["path"], "rb") as f:
        old_wav = f.read()
    assert new_wav != old_wav  # 新しい設定（speaker=3）の音声になっている


@pytest.mark.asyncio
async def test_synthesize_404_for_missing_provider(auth_client_with_telephony):
    c = auth_client_with_telephony
    resp = await c.post("/api/tts-cache/synthesize", json={"provider_id": 999, "text": "案内文"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_synthesize_422_for_non_tts_provider(auth_client_with_telephony):
    c = auth_client_with_telephony
    r = await c.post(
        "/api/providers",
        json={"name": "l", "type": "llm", "kind": "openai_compatible", "config": {}},
    )
    pid = r.json()["id"]
    resp = await c.post("/api/tts-cache/synthesize", json={"provider_id": pid, "text": "x"})
    assert resp.status_code == 422
