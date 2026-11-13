"""(kind, config, api_key) からプロバイダ実体を生成するファクトリ。

各プロバイダ実装タスク（Task 4/5/7/8/9/10）が対応 kind の分岐をここに追加する。

セキュリティ: SSRF ガード (M5)
  * openai_compatible: base_url はパブリック LLM エンドポイントを想定するため
    プライベート IP を含む内部アドレスへの接続を全て拒否する（_resolve_and_check_ssrf）。
    ビルド時に解決・検証し、_PinnedTransport を注入することで実際のリクエスト時の
    再解決（TOCTOU / DNS リバインディング）も防ぐ。follow_redirects=False。
  * voicevox: engine_url はローカル LAN 上の自ホスト型エンジン（AivisSpeech 等）を
    合法的に指す場合がある。RFC1918 プライベートアドレスは許可するが、
    loopback (127.x.x.x) / link-local (169.254.x.x) / multicast / reserved / unspecified
    は拒否する（_resolve_and_check_ssrf_lan_allowed）。
    _PinnedTransport + follow_redirects=False で TOCTOU 対策と redirect 追跡を抑制。
  * anthropic / gemini / vertex_ai / whisper / google_stt / coefont: エンドポイントは
    コードでハードコードされているため SSRF ガード不要（coefont の 302 リダイレクト先は
    CoeFont が署名発行する音声 URL で、ユーザー入力由来ではない）。

イベントループ非ブロック化 (issue #81)
  * SSRF ガードの DNS 解決（socket.getaddrinfo）は同期でブロックするため、async 文脈からは
    :func:`build_llm_async` / :func:`build_tts_async` を使う。DNS 解決を伴う kind のみ
    同期 build を既定 executor（asyncio.to_thread）で実行する。workflow の api_call
    （workflows/handlers/logic.py）が _resolve_and_check_ssrf を to_thread で呼ぶのと同じ流儀。
  * DNS を伴わない kind はスレッドに移さずループ上でそのまま構築する（コンストラクタは
    軽量で、スレッド上でのオブジェクト生成による挙動差を持ち込まないため）。
  * 同期版 build_llm / build_tts は互換のため残す（テスト・同期呼び出し元向け）。
    async 版は呼び出し時にモジュール属性の build_llm / build_tts を参照するため、
    既存テストの monkeypatch もそのまま効く。
"""

import asyncio
from urllib.parse import urlparse

from millicall.net_guard import (
    _PinnedTransport,
    _resolve_and_check_ssrf,
    _resolve_and_check_ssrf_lan_allowed,
)


class UnknownProviderKind(Exception):  # noqa: N818  # 後続タスクが依存する確定インターフェイス名
    pass


def build_llm(kind: str, config: dict, api_key: str | None):
    if kind == "openai_compatible":
        from millicall.ai.llm.openai_compat import OpenAICompatibleLLM

        base_url = config.get("base_url", "https://api.openai.com/v1")
        # SSRF ガード: ビルド時に解決・検証（プライベート IP を含む内部アドレスを拒否）
        parsed_host = urlparse(base_url).hostname or ""
        pinned_ip = _resolve_and_check_ssrf(base_url)
        transport = _PinnedTransport(parsed_host, pinned_ip)

        return OpenAICompatibleLLM(
            base_url=base_url,
            api_key=api_key,
            model=config.get("model", "gpt-4o-mini"),
            temperature=config.get("temperature", 0.7),
            max_tokens=config.get("max_tokens", 500),
            transport=transport,
        )
    if kind == "anthropic":
        from millicall.ai.llm.anthropic import AnthropicLLM

        return AnthropicLLM(
            api_key=api_key,
            model=config.get("model", "claude-sonnet-4-20250514"),
            max_tokens=config.get("max_tokens", 500),
        )
    if kind == "gemini":
        from millicall.ai.llm.gemini import GeminiLLM

        return GeminiLLM(
            api_key=api_key,
            model=config.get("model", "gemini-2.5-flash"),
            temperature=config.get("temperature", 0.7),
        )
    if kind == "vertex_ai":
        from millicall.ai.llm.vertex import VertexAILLM

        # auth_method="sa"(既定): api_key 欄 = SA JSON。"api_key": api_key 欄 = API キー
        # (express mode)。
        auth_method = config.get("auth_method", "sa")
        return VertexAILLM(
            sa_json=api_key if auth_method == "sa" else None,
            api_key=api_key if auth_method == "api_key" else None,
            auth_method=auth_method,
            project=config.get("project", ""),
            location=config.get("location", "us-central1"),
            model=config.get("model", "gemini-2.0-flash"),
            temperature=config.get("temperature", 0.7),
        )
    raise UnknownProviderKind(kind)


# SSRF ガードの DNS 解決を伴う kind（async 版はこれらだけスレッドで構築する）
_LLM_KINDS_WITH_DNS = frozenset({"openai_compatible"})
_TTS_KINDS_WITH_DNS = frozenset({"voicevox"})


async def build_llm_async(kind: str, config: dict, api_key: str | None):
    """:func:`build_llm` の async 版。DNS 解決がイベントループを止めない。"""
    if kind in _LLM_KINDS_WITH_DNS:
        return await asyncio.to_thread(build_llm, kind, config, api_key)
    return build_llm(kind, config, api_key)


async def build_tts_async(kind: str, config: dict, api_key: str | None):
    """:func:`build_tts` の async 版。DNS 解決がイベントループを止めない。"""
    if kind in _TTS_KINDS_WITH_DNS:
        return await asyncio.to_thread(build_tts, kind, config, api_key)
    return build_tts(kind, config, api_key)


def build_tts(kind: str, config: dict, api_key: str | None):
    # TODO(phase4): google_tts をプロバイダ抽象に追加
    if kind == "coefont":
        from millicall.ai.tts.coefont import CoefontTTS

        return CoefontTTS(
            access_key=config.get("access_key", ""),
            access_secret=api_key or "",
            coefont=config.get("coefont", ""),
            speed=config.get("speed", 1.0),
            pitch=config.get("pitch", 0.0),
        )
    if kind == "voicevox":
        from millicall.ai.tts.voicevox import VoicevoxTTS

        engine_url = config.get("engine_url", "http://127.0.0.1:50021")
        # SSRF ガード: LAN 許可モード（RFC1918 OK, loopback/link-local 拒否）
        # デフォルト 127.0.0.1 はループバックのため拒否される。
        # 本番では LAN IP（例: http://192.168.1.100:50021）を engine_url に設定すること。
        parsed_host = urlparse(engine_url).hostname or ""
        pinned_ip = _resolve_and_check_ssrf_lan_allowed(engine_url)
        transport = _PinnedTransport(parsed_host, pinned_ip)

        return VoicevoxTTS(
            engine_url=engine_url,
            speaker=config.get("speaker", 1),
            transport=transport,
        )
    if kind == "openjtalk":
        from millicall.ai.tts.openjtalk import OpenJTalkTTS

        return OpenJTalkTTS(
            dict_dir=config.get("dict_dir", "/var/lib/mecab/dic/open-jtalk/naist-jdic"),
            voice_path=config.get(
                "voice_path",
                "/usr/share/hts-voice/nitech-jp-atr503-m001/nitech_jp_atr503_m001.htsvoice",
            ),
        )
    raise UnknownProviderKind(kind)


def build_stt(kind: str, config: dict, api_key: str | None):
    if kind == "whisper":
        from millicall.ai.stt.whisper import WhisperSTT

        return WhisperSTT(
            api_key=api_key,
            model=config.get("model", "whisper-1"),
            language=config.get("language", "ja"),
        )
    if kind == "google_stt":
        from millicall.ai.stt.google import GoogleStreamingSTT

        return GoogleStreamingSTT(
            project=config.get("project", ""),
            location=config.get("location", "global"),
            language=config.get("language", "ja-JP"),
            model=config.get("model", "chirp_2"),
            api_key=api_key,
            # "sa"(既定) = SA JSON / ADC、"api_key" = client_options で API キー認証
            auth_method=config.get("auth_method", "sa"),
        )
    raise UnknownProviderKind(kind)
