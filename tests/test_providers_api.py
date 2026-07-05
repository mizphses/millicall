import pytest

from millicall.providers.router import _redact  # noqa: PLC2701

# --------------------------------------------------------------------------- #
# _redact ユニットテスト（修正2）
# --------------------------------------------------------------------------- #


def test_redact_replaces_api_key_in_detail():
    """api_key が detail 文字列に含まれる場合は置換される。"""
    key = "sk-supersecret"
    detail = f"Connection error: api_key={key} is invalid"
    result = _redact(detail, key)
    assert key not in result
    assert "****" in result


def test_redact_no_key_returns_original():
    """api_key が None のとき detail はそのまま返る。"""
    detail = "some error message"
    assert _redact(detail, None) == detail


def test_redact_key_not_in_detail_returns_original():
    """api_key が detail に含まれないとき detail はそのまま返る。"""
    key = "sk-mykey"
    detail = "some unrelated error"
    assert _redact(detail, key) == detail


def test_redact_empty_key_returns_original():
    """api_key が空文字のとき detail はそのまま返る。"""
    detail = "some error message"
    assert _redact(detail, "") == detail


# --------------------------------------------------------------------------- #
# PATCH name 重複 → 409（修正1）
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_update_provider_name_conflict_returns_409(auth_client_with_telephony):
    """Provider A と B を作成後、B を PATCH で name=A に変更すると 409 が返る。"""
    c = auth_client_with_telephony
    # A を作成
    r_a = await c.post(
        "/api/providers",
        json={"name": "provider-a", "type": "llm", "kind": "openai_compatible", "config": {}},
    )
    assert r_a.status_code == 201, r_a.text
    # B を作成
    r_b = await c.post(
        "/api/providers",
        json={"name": "provider-b", "type": "llm", "kind": "openai_compatible", "config": {}},
    )
    assert r_b.status_code == 201, r_b.text
    id_b = r_b.json()["id"]
    # B を A の name に変更 → 409
    r_patch = await c.patch(f"/api/providers/{id_b}", json={"name": "provider-a"})
    assert r_patch.status_code == 409, r_patch.text


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
