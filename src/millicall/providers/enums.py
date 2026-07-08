import enum


class ProviderType(enum.StrEnum):
    LLM = "llm"
    TTS = "tts"
    STT = "stt"


class ProviderKind(enum.StrEnum):
    OPENAI_COMPATIBLE = "openai_compatible"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    VERTEX_AI = "vertex_ai"
    VOICEVOX = "voicevox"
    OPENJTALK = "openjtalk"
    COEFONT = "coefont"
    WHISPER = "whisper"
    GOOGLE_STT = "google_stt"


# type ごとに許可される kind（保存時検証）
KIND_BY_TYPE: dict[ProviderType, set[ProviderKind]] = {
    ProviderType.LLM: {
        ProviderKind.OPENAI_COMPATIBLE,
        ProviderKind.ANTHROPIC,
        ProviderKind.GEMINI,
        ProviderKind.VERTEX_AI,
    },
    ProviderType.TTS: {ProviderKind.VOICEVOX, ProviderKind.OPENJTALK, ProviderKind.COEFONT},
    ProviderType.STT: {ProviderKind.WHISPER, ProviderKind.GOOGLE_STT},
}
