"""Google Cloud Speech-to-Text v2 の真のストリーミング STT プロバイダ（gRPC, 任意依存）。

Task 9 で確定した STTProvider / STTSession 契約に載る実装。`feed()` で受けた PCM チャンクを
スレッド安全キュー経由でバックグラウンドの gRPC `StreamingRecognize` へ**逐次**流し込み、
`finish()` で確定 transcript を受け取る（録音後の一括送信＝バッチ化はしない）。

google-cloud-speech は任意依存であり、`import` 時ではなく実際に利用するタイミングで
明快なエラーを出す（deferred import）。認証はサービスアカウント/ADC 依存で本クラスは
資格情報を保持せず、ログ・例外・repr にも漏らさない。
"""

import asyncio
import queue
import threading
from typing import Any

_SENTINEL = object()

_MISSING_MESSAGE = (
    "google-cloud-speech が未インストールです。"
    "`uv sync --extra stt-google` を実行してください。"
)


class _FallbackRequest:
    """google-cloud-speech 未導入環境（＝フェイク client 注入テスト）専用の最小リクエスト。

    実 `StreamingRecognizeRequest` と同じ `recognizer` / `streaming_config` / `audio` 属性を持つ。
    実クライアントには到達しない（実クライアント生成にも同パッケージが要るため）ので、
    これで実クライアントを欺くことはない。実導入時は必ず実 gRPC 型を構築する。
    """

    __slots__ = ("recognizer", "streaming_config", "audio")

    def __init__(
        self,
        recognizer: str | None = None,
        streaming_config: Any | None = None,
        audio: bytes | None = None,
    ) -> None:
        self.recognizer = recognizer
        self.streaming_config = streaming_config
        self.audio = audio


class GoogleStreamingSTT:
    """Google Cloud Speech-to-Text v2 StreamingRecognize（gRPC）プロバイダ。

    音声チャンクは L16 モノ 8k PCM（正準形）をそのまま送る。`client` を注入しなければ、
    利用時に google-cloud-speech の SpeechClient を遅延生成する。
    """

    def __init__(
        self,
        project: str,
        location: str = "global",
        language: str = "ja-JP",
        model: str = "chirp_2",
        client: Any | None = None,
        api_key: str | None = None,
    ) -> None:
        self._project = project
        self._location = location
        self._language = language
        self._model = model
        self._client = client
        # api_key はサービスアカウント JSON 文字列（GUI 登録用）。未設定なら ADC。
        self._api_key = api_key

    def __repr__(self) -> str:
        # client オブジェクト（資格情報を保持しうる）を露出しない。
        return (
            f"GoogleStreamingSTT(project={self._project!r}, location={self._location!r}, "
            f"language={self._language!r}, model={self._model!r})"
        )

    @property
    def recognizer(self) -> str:
        return f"projects/{self._project}/locations/{self._location}/recognizers/_"

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from google.cloud.speech_v2 import SpeechClient
        except ImportError as exc:
            raise RuntimeError(_MISSING_MESSAGE) from exc
        credentials = self._build_credentials()
        if credentials is not None:
            self._client = SpeechClient(credentials=credentials)
        else:
            self._client = SpeechClient()
        return self._client

    def _build_credentials(self) -> Any | None:
        """SA JSON（api_key）が設定されていれば資格情報を作る。無ければ None（ADC）。"""
        if not self._api_key:
            return None
        import json

        from google.oauth2.service_account import Credentials

        return Credentials.from_service_account_info(json.loads(self._api_key))

    def _request_builders(self):
        """(make_config, make_audio) を返す。実型があれば実 gRPC リクエストを構築する。"""
        try:
            from google.cloud.speech_v2.types import cloud_speech as cs
        except ImportError:
            cs = None

        if cs is not None:
            config = cs.RecognitionConfig(
                explicit_decoding_config=cs.ExplicitDecodingConfig(
                    encoding=cs.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
                    sample_rate_hertz=8000,
                    audio_channel_count=1,
                ),
                language_codes=[self._language],
                model=self._model,
            )
            streaming_config = cs.StreamingRecognitionConfig(config=config)
            recognizer = self.recognizer

            def make_config() -> Any:
                return cs.StreamingRecognizeRequest(
                    recognizer=recognizer, streaming_config=streaming_config
                )

            def make_audio(chunk: bytes) -> Any:
                return cs.StreamingRecognizeRequest(audio=chunk)

            return make_config, make_audio

        # パッケージ未導入: ここへは注入フェイク client 経由でしか来ない。
        recognizer = self.recognizer

        def make_config() -> Any:
            return _FallbackRequest(recognizer=recognizer, streaming_config="cfg")

        def make_audio(chunk: bytes) -> Any:
            return _FallbackRequest(audio=chunk)

        return make_config, make_audio

    def open_session(self) -> "_GoogleSession":
        return _GoogleSession(self)


class _GoogleSession:
    """1 発話 = 1 gRPC ストリーム。feed でキューへ、finish で終端・確定取得。"""

    def __init__(self, provider: GoogleStreamingSTT) -> None:
        self._p = provider
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._started = False
        self._final = ""
        self._error: BaseException | None = None

    def _start(self) -> None:
        if self._started:
            return
        client = self._p._ensure_client()  # 未導入なら明快な RuntimeError
        make_config, make_audio = self._p._request_builders()
        self._thread = threading.Thread(
            target=self._run,
            args=(client, make_config, make_audio),
            daemon=True,
        )
        self._started = True
        self._thread.start()

    def _requests(self, make_config, make_audio):
        yield make_config()
        while True:
            item = self._queue.get()
            if item is _SENTINEL:
                return
            yield make_audio(item)

    def _run(self, client: Any, make_config, make_audio) -> None:
        try:
            responses = client.streaming_recognize(
                requests=self._requests(make_config, make_audio)
            )
            for response in responses:
                for result in getattr(response, "results", []):
                    if getattr(result, "is_final", False) and result.alternatives:
                        self._final = result.alternatives[0].transcript
        except BaseException as exc:  # noqa: BLE001  # スレッド境界越えに再送出用へ退避
            self._error = exc

    async def feed(self, pcm: bytes) -> None:
        # 最初の feed でストリームを開始し、以降チャンクを逐次流す（真のストリーミング）。
        self._start()
        self._queue.put(pcm)

    async def finish(self) -> str:
        if not self._started:
            # 一度も feed されていない発話は API を呼ばず空文字を返す。
            return ""
        self._queue.put(_SENTINEL)
        assert self._thread is not None
        await asyncio.to_thread(self._thread.join)
        if self._error is not None:
            raise self._error
        return self._final
