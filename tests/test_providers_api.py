import pytest


@pytest.mark.asyncio
async def test_create_provider_masks_api_key(auth_client_with_telephony):
    c = auth_client_with_telephony
    resp = await c.post(
        "/api/providers",
        json={
            "name": "my-openai",
            "type": "llm",
            "kind": "openai_compatible",
            "config": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
            "api_key": "sk-abcdefgh1234",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["api_key_masked"] == "****1234"
    assert "api_key" not in body
    assert body["config"]["model"] == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_reject_kind_not_matching_type(auth_client_with_telephony):
    c = auth_client_with_telephony
    resp = await c.post(
        "/api/providers",
        json={"name": "bad", "type": "llm", "kind": "voicevox", "config": {}},
    )
    assert resp.status_code == 422


def test_unknown_kind_raises_in_registry():
    """registry.build_* は未実装 kind に UnknownProviderKind を送出する（ネットワーク不使用）。

    NOTE: 旧テストは kind=openai_compatible で 501 を確認していたが、
    Task 4 が openai_compatible を実装すると実ネットワーク呼び出しが発生し
    グリーンスイートを壊す。ここでは永遠に実装されないセンチネル kind で
    registry レベルの動作を検証する。HTTP 501 の配線は router の except 節が
    担うため、registry の UnknownProviderKind を確認すれば十分。
    """
    import pytest as _pytest

    from millicall.ai.registry import UnknownProviderKind, build_llm, build_stt, build_tts

    with _pytest.raises(UnknownProviderKind):
        build_llm("__unimplemented__", {}, None)
    with _pytest.raises(UnknownProviderKind):
        build_tts("__unimplemented__", {}, None)
    with _pytest.raises(UnknownProviderKind):
        build_stt("__unimplemented__", {}, None)
