import httpx

from millicall.ai.audio import pcm8k_to_wav
from millicall.ai.stt.base import BatchSTTSession

_API_URL = "https://api.openai.com/v1/audio/transcriptions"

# 旧実装 phase2/stt.py の HALLUCINATION_PHRASES を移植
_HALLUCINATION_PHRASES = {
    "ご視聴ありがとうございました",
    "チャンネル登録お願いします",
    "ご視聴ありがとうございます",
    "チャンネル登録よろしくお願いします",
    "おやすみなさい",
    "ありがとうございました",
    "thank you for watching",
    "thanks for watching",
    "subscribe",
}


def _normalize(text: str) -> str:
    return text.replace("。", "").replace("、", "").replace(" ", "").strip().lower()


def is_hallucination(text: str) -> bool:
    norm = _normalize(text)
    return any(_normalize(p) == norm for p in _HALLUCINATION_PHRASES)


class WhisperSTT:
    """OpenAI Whisper (audio/transcriptions) の一括 STT プロバイダ。

    api_key は Authorization ヘッダで送るため URL/例外/repr には漏らさない。
    """

    def __init__(
        self,
        api_key: str | None,
        model: str = "whisper-1",
        language: str = "ja",
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._language = language
        self._timeout = timeout
        self._transport = transport

    def __repr__(self) -> str:
        # api_key を平文で漏らさない
        return (
            f"WhisperSTT(model={self._model!r}, language={self._language!r}, "
            f"api_key={'***' if self._api_key else None})"
        )

    def open_session(self) -> BatchSTTSession:
        return BatchSTTSession(self._transcribe)

    async def _transcribe(self, pcm: bytes) -> str:
        wav = pcm8k_to_wav(pcm)
        headers = {"Authorization": f"Bearer {self._api_key or ''}"}
        files = {"file": ("audio.wav", wav, "audio/wav")}
        data = {
            "model": self._model,
            "language": self._language,
            "response_format": "text",
            "prompt": "電話での会話です。",
        }
        async with httpx.AsyncClient(
            timeout=self._timeout, transport=self._transport
        ) as client:
            resp = await client.post(_API_URL, headers=headers, files=files, data=data)
            resp.raise_for_status()
            text = resp.text.strip()
        if is_hallucination(text):
            return ""
        return text
