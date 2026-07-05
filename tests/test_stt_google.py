"""Google Cloud Speech-to-Text v2 ストリーミング STT のユニットテスト。

google-cloud-speech は任意依存であり CI/dev には未導入。よって実 gRPC 型ではなく、
実型と同じ属性 (`audio` / `streaming_config` / `recognizer`) だけを読むフェイク client を
注入して結線を検証する（I4: SimpleNamespace で実クライアントを欺かない / 実型互換フェイク）。
フェイクの `streaming_recognize` はリクエスト generator を逐次 for で消費するため、
feed した各チャンクが個別の audio リクエストとしてストリームへ流れること (I3: 真のストリーミング)
を検証できる。
"""

import pytest

from millicall.ai.registry import UnknownProviderKind, build_stt
from millicall.ai.stt.base import STTProvider, STTSession
from millicall.ai.stt.google import GoogleStreamingSTT


class _Alt:
    def __init__(self, transcript: str) -> None:
        self.transcript = transcript


class _Result:
    def __init__(self, transcript: str, is_final: bool) -> None:
        self.alternatives = [_Alt(transcript)]
        self.is_final = is_final


class _Response:
    def __init__(self, results: list[_Result]) -> None:
        self.results = results


class _FakeClient:
    """実 SpeechClient.streaming_recognize(requests=...) を模した最小フェイク。

    実型と同じく `audio` / `streaming_config` 属性のみを参照し、generator を逐次消費する。
    """

    def __init__(self, responses: list[_Response]) -> None:
        self._responses = responses
        self.audio_requests: list[bytes] = []
        self.config_requests: list[object] = []

    def streaming_recognize(self, requests):
        for req in requests:
            if getattr(req, "audio", None):
                self.audio_requests.append(req.audio)
            elif getattr(req, "streaming_config", None) is not None:
                self.config_requests.append(req)
        return list(self._responses)


def _final_responses() -> list[_Response]:
    return [
        _Response([_Result("こんにちは", False)]),
        _Response([_Result("こんにちは世界", True)]),
    ]


def test_provider_satisfies_protocols():
    stt = GoogleStreamingSTT(project="p", client=_FakeClient([]))
    assert isinstance(stt, STTProvider)
    assert isinstance(stt.open_session(), STTSession)


@pytest.mark.asyncio
async def test_google_streaming_returns_final():
    stt = GoogleStreamingSTT(project="p", client=_FakeClient(_final_responses()))
    sess = stt.open_session()
    await sess.feed(b"\x01\x00" * 800)
    await sess.feed(b"\x02\x00" * 800)
    assert await sess.finish() == "こんにちは世界"


@pytest.mark.asyncio
async def test_google_streams_each_chunk_incrementally():
    """録音一括送信ではなく、feed した各チャンクが個別リクエストで流れる (I3)。"""
    fake = _FakeClient(_final_responses())
    stt = GoogleStreamingSTT(project="p", client=fake)
    sess = stt.open_session()
    c1 = b"\x01\x00" * 800
    c2 = b"\x02\x00" * 800
    await sess.feed(c1)
    await sess.feed(c2)
    await sess.finish()
    # バッチ結合 (b"".join) された 1 リクエストではなく、2 つの独立した audio リクエスト。
    assert fake.audio_requests == [c1, c2]
    # 先頭に streaming_config を含む設定リクエストが 1 度だけ送られる。
    assert len(fake.config_requests) == 1


@pytest.mark.asyncio
async def test_config_request_carries_recognizer_and_config():
    fake = _FakeClient(_final_responses())
    stt = GoogleStreamingSTT(
        project="proj-x", location="global", language="ja-JP", client=fake
    )
    sess = stt.open_session()
    await sess.feed(b"\x01\x00" * 800)
    await sess.finish()
    cfg = fake.config_requests[0]
    assert cfg.recognizer == "projects/proj-x/locations/global/recognizers/_"
    assert cfg.streaming_config is not None


@pytest.mark.asyncio
async def test_empty_session_returns_empty_without_calling_client():
    class _Boom:
        def streaming_recognize(self, requests):  # pragma: no cover
            raise AssertionError("空セッションで client を呼んではならない")

    stt = GoogleStreamingSTT(project="p", client=_Boom())
    sess = stt.open_session()
    assert await sess.finish() == ""


@pytest.mark.asyncio
async def test_missing_package_raises_clear_error_not_import_error():
    """client 未注入かつ google-cloud-speech 未導入なら、import 時ではなく使用時に明快な RuntimeError。"""
    stt = GoogleStreamingSTT(project="p")  # client 注入なし
    sess = stt.open_session()
    with pytest.raises(RuntimeError) as exc:
        await sess.feed(b"\x01\x00" * 800)
    msg = str(exc.value)
    assert "stt-google" in msg
    assert "google-cloud-speech" in msg


def test_repr_does_not_leak_credentials():
    stt = GoogleStreamingSTT(project="secret-project", client=_FakeClient([]))
    text = repr(stt)
    # サービスアカウント/認証は ADC 依存で本クラスは保持しないが、client オブジェクトを露出しない。
    assert "_FakeClient" not in text
    assert "GoogleStreamingSTT" in text


def test_registry_builds_google_stt_without_package():
    provider = build_stt(
        "google_stt",
        {"project": "p", "location": "global", "language": "ja-JP", "model": "chirp_2"},
        None,
    )
    assert isinstance(provider, GoogleStreamingSTT)


def test_registry_unknown_kind_still_raises():
    with pytest.raises(UnknownProviderKind):
        build_stt("nope", {}, None)
