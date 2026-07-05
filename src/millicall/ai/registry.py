"""(kind, config, api_key) からプロバイダ実体を生成するファクトリ。

各プロバイダ実装タスク（Task 4/5/7/8/9/10）が対応 kind の分岐をここに追加する。
"""


class UnknownProviderKind(Exception):  # noqa: N818  # 後続タスクが依存する確定インターフェイス名
    pass


def build_llm(kind: str, config: dict, api_key: str | None):
    if kind == "openai_compatible":
        from millicall.ai.llm.openai_compat import OpenAICompatibleLLM

        return OpenAICompatibleLLM(
            base_url=config.get("base_url", "https://api.openai.com/v1"),
            api_key=api_key,
            model=config.get("model", "gpt-4o-mini"),
            temperature=config.get("temperature", 0.7),
            max_tokens=config.get("max_tokens", 500),
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
    raise UnknownProviderKind(kind)


def build_tts(kind: str, config: dict, api_key: str | None):
    # TODO(phase4): google_tts / coefont をプロバイダ抽象に追加
    if kind == "voicevox":
        from millicall.ai.tts.voicevox import VoicevoxTTS

        return VoicevoxTTS(
            engine_url=config.get("engine_url", "http://127.0.0.1:50021"),
            speaker=config.get("speaker", 1),
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
        )
    raise UnknownProviderKind(kind)
