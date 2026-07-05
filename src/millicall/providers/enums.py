import enum


class ProviderType(enum.StrEnum):
    LLM = "llm"
    TTS = "tts"
    STT = "stt"


class ProviderKind(enum.StrEnum):
    OPENAI_COMPATIBLE = "openai_compatible"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    VOICEVOX = "voicevox"
    OPENJTALK = "openjtalk"
    WHISPER = "whisper"
    GOOGLE_STT = "google_stt"


# type ごとに許可される kind（保存時検証）
KIND_BY_TYPE: dict[ProviderType, set[ProviderKind]] = {
    ProviderType.LLM: {ProviderKind.OPENAI_COMPATIBLE, ProviderKind.ANTHROPIC, ProviderKind.GEMINI},
    ProviderType.TTS: {ProviderKind.VOICEVOX, ProviderKind.OPENJTALK},
    ProviderType.STT: {ProviderKind.WHISPER, ProviderKind.GOOGLE_STT},
}
