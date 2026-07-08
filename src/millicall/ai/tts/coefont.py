import hashlib
import hmac
import json
import time

import httpx

from millicall.ai.audio import wav_to_pcm8k

_API_URL = "https://api.coefont.cloud/v2/text2speech"


class CoefontTTS:
    """CoeFont CLOUD text2speech。

    認証は access key(Authorization ヘッダー)+ access secret(HMAC-SHA256 署名)。
    署名対象は「X-Coefont-Date + リクエストボディ」のバイト列のため、
    JSON は一度だけ直列化し、署名と送信で同一バイトを使う。
    レスポンスは 302 で署名付き音声 URL へリダイレクトされる。追跡は
    明示的に 1 段のみ行う(follow_redirects=False を維持)。
    """

    def __init__(
        self,
        access_key: str,
        access_secret: str,
        coefont: str,
        speed: float = 1.0,
        pitch: float = 0.0,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._access_key = access_key
        self._access_secret = access_secret
        self._coefont = coefont
        self._speed = speed
        self._pitch = pitch
        self._timeout = timeout
        self._transport = transport

    async def synthesize(self, text: str) -> bytes:
        body = json.dumps(
            {
                "coefont": self._coefont,
                "text": text,
                "speed": self._speed,
                "pitch": self._pitch,
                "format": "wav",
            },
            ensure_ascii=False,
        ).encode("utf-8")
        date = str(int(time.time()))
        signature = hmac.new(
            self._access_secret.encode("utf-8"),
            date.encode("ascii") + body,
            hashlib.sha256,
        ).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "Authorization": self._access_key,
            "X-Coefont-Date": date,
            "X-Coefont-Content": signature,
        }
        async with httpx.AsyncClient(
            timeout=self._timeout,
            transport=self._transport,
            follow_redirects=False,
        ) as client:
            res = await client.post(_API_URL, content=body, headers=headers)
            if res.status_code in (301, 302, 303, 307, 308):
                location = res.headers.get("location", "")
                audio = await client.get(location)
                audio.raise_for_status()
                return wav_to_pcm8k(audio.content)
            res.raise_for_status()
            return wav_to_pcm8k(res.content)
