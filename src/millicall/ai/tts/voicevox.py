import httpx

from millicall.ai.audio import wav_to_pcm8k


class VoicevoxTTS:
    """VOICEVOX 互換エンジン（AivisSpeech 等含む）。engine_url は設定制。"""

    def __init__(
        self,
        engine_url: str,
        speaker: int = 1,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = engine_url.rstrip("/")
        self._speaker = speaker
        self._timeout = timeout
        self._transport = transport

    async def synthesize(self, text: str) -> bytes:
        params = {"text": text, "speaker": str(self._speaker)}
        async with httpx.AsyncClient(
            timeout=self._timeout,
            transport=self._transport,
            follow_redirects=False,
        ) as client:
            q = await client.post(f"{self._base}/audio_query", params=params)
            q.raise_for_status()
            syn = await client.post(
                f"{self._base}/synthesis",
                params={"speaker": str(self._speaker)},
                json=q.json(),
            )
            syn.raise_for_status()
            return wav_to_pcm8k(syn.content)
