"""STT→LLM(ストリーミング)→文分割→TTS先読み合成→再生 を駆動する会話オーケストレータ。

設計要点:
    - LLM のトークンストリームを `。！？\n` 境界で文へ分割し、単一の合成ワーカーで
      逐次 TTS 合成する（無制限 create_task を避け、`prefetch` 分だけ先読み）。
    - 文単位の PCM を順番に CallControl で再生する。
    - バージイン（`on_barge_in`）は再生ループを即中断し `stop_playback` を送る。
      検知時は LLM pump タスクを cancel し、**完走させない**（未再生分で会話文脈を
      汚さないため、`history` には再生済みの文のみ記録する）。
    - 再生開始直後 `barge_in_grace_ms` はバージイン無効（誤検知抑制）。
    - 遅延計測: 「発話終端確定（on_utterance 入口）→ 最初の再生直前」の ms をログ出力し
      `on_turn` にも渡す。
    - LLM 応答に終了タグ `<end_talk/>`（後方互換 `[END_CALL]` も可）があれば発話後に
      （未中断時のみ）`hangup`。検出・除去は millicall.ai.end_talk に一本化。
      タグは文境界文字を含まないため、確定した文単位で検出すればチャンク分割の影響を
      受けない（end_talk モジュールの docstring 参照）。
    - STT 結果は `sanitize_user_input` で制御トークンを除去してから履歴へ積む
      （通話相手の発話による強制終了・乗っ取りの防止）。
    - システムプロンプトには終了タグ案内とインジェクション対策指示を自動追記する
      （`build_guarded_system_prompt`）。
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from millicall.ai.audio import pcm8k_to_wav
from millicall.ai.end_talk import (
    build_guarded_system_prompt,
    sanitize_user_input,
    split_end_talk,
)
from millicall.ai.llm.base import ChatMessage

logger = logging.getLogger("millicall.media.conversation")

_BOUNDARIES = "。！？\n"
_INTERRUPTED = object()

# on_turn は (role, text, latency_ms) のタプルを 1 引数で受ける。
# （テスト `on_turn=turns.append` が単一引数を要求するため。Task 15 の DB 永続化も同契約。）
OnTurn = Callable[[tuple[str, str, int]], Awaitable[None] | None]


class ConversationSession:
    def __init__(
        self,
        agent,
        stt,
        llm,
        tts,
        call_control,
        tts_dir: Path,
        call_uuid: str,
        on_turn: OnTurn | None = None,
        clock: Callable[[], float] | None = None,
        *,
        barge_in_grace_ms: int = 300,
        prefetch: int = 2,
    ) -> None:
        self._agent = agent
        self._stt = stt
        self._llm = llm
        self._tts = tts
        self._call_control = call_control
        self._tts_dir = Path(tts_dir)
        self._tts_dir.mkdir(parents=True, exist_ok=True)
        self._call_uuid = call_uuid
        self._on_turn = on_turn
        # 終了タグ案内 + インジェクション対策指示を自動追記したシステムプロンプト
        # （agent.system_prompt はセッション中不変なので初期化時に一度だけ組み立てる）。
        self._system_prompt = build_guarded_system_prompt(agent.system_prompt)
        self._clock = clock or time.perf_counter
        self._barge_in_grace_ms = barge_in_grace_ms
        self._prefetch = max(1, prefetch)
        self._history: list[ChatMessage] = []
        self._interrupt = asyncio.Event()
        self._speaking = False
        self._playback_start: float | None = None
        self._seq = 0
        # ストリーミング STT 用の開いているセッション（speech_start で開き speech_end で確定）。
        # None = 発話中でない。発話中フレームを逐次 feed することで STT レイテンシを
        # クリティカルパス（発話終端→応答）から外す。
        self._stt_stream = None
        # ターン毎に書き出した TTS WAV のパスを追跡し、通話終了時にまとめて削除する。
        # （prompts/ 配下の定型文キャッシュは _play_pcm が書かないため決して含まれない。）
        self._written_wavs: set[Path] = set()

    @property
    def speaking(self) -> bool:
        return self._speaking

    def cleanup(self) -> None:
        """このセッションが書き出したターン毎 TTS WAV を削除する（通話終了時のみ）。

        再生中のファイルを消すレースを避けるため、WS 切断/セッション終了時にのみ呼ぶ。
        prompts/ 配下（内容アドレス型の永続キャッシュ）は追跡対象外なので消えない。
        unlink は missing_ok=True で冪等。
        """
        for path in self._written_wavs:
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)
        self._written_wavs.clear()

    async def greet(self) -> None:
        if not self._agent.greeting:
            return
        self._interrupt.clear()
        self._speaking = True
        self._playback_start = None
        try:
            pcm = await self._tts.synthesize(self._agent.greeting)
            if self._interrupt.is_set():
                return
            self._playback_start = self._clock()
            await self._play_pcm(pcm)
        finally:
            self._speaking = False
            self._playback_start = None

    async def on_barge_in(self) -> None:
        if not self._speaking:
            return
        # 再生開始直後 barge_in_grace_ms はバージイン無効（I2）。
        if self._playback_start is not None:
            elapsed_ms = (self._clock() - self._playback_start) * 1000
            if elapsed_ms < self._barge_in_grace_ms:
                return
        self._interrupt.set()
        await self._call_control.stop_playback()

    async def on_utterance(self, pcm: bytes) -> None:
        """発話全体を一括で STT する経路（バッチ）。後方互換のため残す。

        通常のライブ経路は open/feed/finalize のストリーミング（下記）を使う。
        """
        # 空 audio の speech_end（VadSegmenter の対称性契約）は STT せずスキップ。
        if not pcm:
            return
        utterance_end = self._clock()
        text = (await self._transcribe(pcm)).strip()
        await self._handle_user_text(text, utterance_end, pcm_bytes=len(pcm))

    # --- ストリーミング STT ---
    # speech_start で open、発話中の各フレームを feed、speech_end で finalize。
    # 発話中に文字化を進めるため発話終端の finish() はほぼ即座に確定 transcript を返し、
    # STT レイテンシが「発話終端→応答」のクリティカルパスから外れる。
    def open_stt_stream(self) -> None:
        """speech_start で呼ぶ。発話用の STT ストリームセッションを開く。"""
        self._stt_stream = self._stt.open_session()

    async def feed_stt_stream(self, pcm: bytes) -> None:
        """発話中の各フレームを開いている STT ストリームへ逐次投入する。"""
        stream = self._stt_stream
        if stream is not None and pcm:
            await stream.feed(pcm)

    async def finalize_stt_stream(self) -> None:
        """speech_end（十分な長さ）で呼ぶ。STT を確定して応答生成へ進む。"""
        stream = self._stt_stream
        self._stt_stream = None
        if stream is None:
            return
        utterance_end = self._clock()
        text = (await stream.finish()).strip()
        await self._handle_user_text(text, utterance_end)

    async def abort_stt_stream(self) -> None:
        """短すぎる発話（VAD が audio="" を返す）や切断時に呼ぶ。STT を破棄し応答しない。"""
        stream = self._stt_stream
        self._stt_stream = None
        if stream is not None:
            with contextlib.suppress(Exception):
                await stream.finish()

    async def _handle_user_text(
        self, text: str, utterance_end: float, *, pcm_bytes: int | None = None
    ) -> None:
        """確定した発話テキストを履歴へ積み、LLM→TTS で応答する（バッチ/ストリーミング共通）。

        STT 結果は信頼できない入力なので、履歴へ積む前に制御トークン
        （<end_talk> 系タグ・[END_CALL]）を除去する。除去後に空なら応答しない。
        """
        text = sanitize_user_input(text)
        logger.info(
            "STT 結果 (uuid=%s, pcm_bytes=%s, text_len=%d)%s",
            self._call_uuid,
            pcm_bytes if pcm_bytes is not None else "stream",
            len(text),
            "" if text else " ← 空文字（無音/STT未認識）",
        )
        if not text:
            return
        self._history.append(ChatMessage("user", text))
        await self._emit_turn("user", text, 0)
        self._trim_history()
        messages = [ChatMessage("system", self._system_prompt), *self._history]
        await self._speak(messages, utterance_end)

    async def _transcribe(self, pcm: bytes) -> str:
        # STT セッションは必ず finally で finish()（またはキャンセル経路）を通す。
        sess = self._stt.open_session()
        finished = False
        try:
            await sess.feed(pcm)
            text = await sess.finish()
            finished = True
            return text
        finally:
            if not finished:
                with contextlib.suppress(Exception):
                    await sess.finish()

    def _trim_history(self) -> None:
        keep = self._agent.max_history
        if len(self._history) > keep:
            self._history = self._history[-keep:]

    async def _speak(self, messages: list[ChatMessage], utterance_end: float) -> None:
        self._interrupt.clear()
        self._speaking = True
        self._playback_start = None

        sentence_q: asyncio.Queue = asyncio.Queue()
        pcm_q: asyncio.Queue = asyncio.Queue(maxsize=self._prefetch)
        state = {"end_call": False}

        async def _pump() -> None:
            buffer = ""
            try:
                async for token in self._llm.stream_chat(messages):
                    buffer += token
                    while (idx := self._first_boundary(buffer)) != -1:
                        sentence = buffer[: idx + 1]
                        buffer = buffer[idx + 1 :]
                        await self._enqueue_sentence(sentence, sentence_q, state)
                await self._enqueue_sentence(buffer, sentence_q, state)
                await sentence_q.put(None)
            except asyncio.CancelledError:
                # I1: cancel 時は下流も破棄されるため sentinel 不要（full queue 座礁も回避）。
                raise
            except BaseException:
                # プロバイダ例外時も終端 sentinel を必ず下流へ（_synth 永久ブロック防止）。
                await sentence_q.put(None)
                raise

        async def _synth() -> None:
            try:
                while True:
                    sentence = await sentence_q.get()
                    if sentence is None:
                        await pcm_q.put(None)
                        return
                    pcm = await self._tts.synthesize(sentence)
                    await pcm_q.put((sentence, pcm))
            except asyncio.CancelledError:
                raise
            except BaseException:
                # プロバイダ例外時も終端 sentinel を必ず下流へ（消費ループ座礁防止）。
                await pcm_q.put(None)
                raise

        pump = asyncio.create_task(_pump())
        synth = asyncio.create_task(_synth())
        interrupt_wait = asyncio.create_task(self._interrupt.wait())

        played_sentences: list[str] = []
        latency_ms = 0
        first = True
        try:
            while True:
                item = await self._next_pcm(pcm_q, interrupt_wait)
                if item is _INTERRUPTED or item is None:
                    break
                sentence, pcm = item
                if self._interrupt.is_set():
                    break
                if first:
                    latency_ms = int((self._clock() - utterance_end) * 1000)
                    logger.info(
                        "AI latency: utterance_end -> first playback = %d ms (uuid=%s)",
                        latency_ms,
                        self._call_uuid,
                    )
                    first = False
                if self._playback_start is None:
                    self._playback_start = self._clock()
                await self._play_pcm(pcm)
                played_sentences.append(sentence)
                if self._interrupt.is_set():
                    break
        finally:
            # I1: pump を cancel し、finally で await して完走させない。
            pump.cancel()
            synth.cancel()
            for t in (pump, synth):
                with contextlib.suppress(BaseException):
                    await t
            # プロバイダ例外はターンを graceful に終了させたうえでログ（通話は継続）。
            for name, t in (("pump(LLM)", pump), ("synth(TTS)", synth)):
                if not t.cancelled() and t.exception() is not None:
                    logger.error(
                        "conversation %s task failed (uuid=%s)",
                        name,
                        self._call_uuid,
                        exc_info=t.exception(),
                    )
            if not interrupt_wait.done():
                interrupt_wait.cancel()
            with contextlib.suppress(BaseException):
                await interrupt_wait
            self._speaking = False
            self._playback_start = None

        assistant_text = "".join(played_sentences)
        if assistant_text:
            self._history.append(ChatMessage("assistant", assistant_text))
            await self._emit_turn("assistant", assistant_text, latency_ms)
        if state["end_call"] and not self._interrupt.is_set():
            # hangup 前に stop_playback（play_file 待ちの座礁防止）。
            await self._call_control.stop_playback()
            await self._call_control.hangup()

    async def _enqueue_sentence(self, sentence: str, sentence_q: asyncio.Queue, state) -> None:
        # 終了タグ判定は境界分割で確定した文単位で行う（タグは境界文字を含まないため
        # チャンク分割で泣き別れない — end_talk モジュール参照）。
        clean, end = split_end_talk(sentence)
        if end:
            state["end_call"] = True
        if clean:
            await sentence_q.put(clean)

    async def _next_pcm(self, pcm_q: asyncio.Queue, interrupt_wait: asyncio.Task):
        get_task = asyncio.ensure_future(pcm_q.get())
        done, _ = await asyncio.wait(
            {get_task, interrupt_wait}, return_when=asyncio.FIRST_COMPLETED
        )
        if get_task in done:
            return get_task.result()
        get_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await get_task
        return _INTERRUPTED

    @staticmethod
    def _first_boundary(buffer: str) -> int:
        idxs = [i for i in (buffer.find(b) for b in _BOUNDARIES) if i != -1]
        return min(idxs) if idxs else -1

    async def _play_pcm(self, pcm: bytes) -> None:
        self._seq += 1
        path = self._tts_dir / f"{self._call_uuid}_{self._seq}.wav"
        path.write_bytes(pcm8k_to_wav(pcm))
        self._written_wavs.add(path)
        await self._call_control.play_file(str(path))

    async def _emit_turn(self, role: str, text: str, latency_ms: int) -> None:
        if self._on_turn is not None:
            await _maybe_await(self._on_turn((role, text, latency_ms)))


async def _maybe_await(value):
    if value is not None and hasattr(value, "__await__"):
        return await value
    return value
