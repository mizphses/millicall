"""AI 会話セッションのレジストリと、DB からの依存解決を行うセッションファクトリ。

`SessionRegistry` は call_uuid -> (ConversationSession, EslCallControl) を保持し、
WS ハンドラ・イベントルータ（PLAYBACK_STOP / CHANNEL_HANGUP_COMPLETE）が参照する。
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from millicall.ai import registry as ai_registry
from millicall.crypto import SecretBox
from millicall.media.call_control import EslCallControl
from millicall.media.conversation import ConversationSession
from millicall.models import AiAgent, CallMessage, Provider
from millicall.telephony.esl import ESLConnectionClosed


async def locked_bgapi(esl, command: str, *, lock: asyncio.Lock, reconnect=None):
    """共有 ESL 接続を lock で直列化して bgapi し、接続断時は reconnect で張り直して再送する。

    プランレビュー I6: 単一 ESL 接続を複数通話・複数 writer で共有する場合、並行する
    bgapi の書き込みが混線しないよう共有 lock で直列化する（EslCallControl._bgapi と同型）。
    reconnect で esl が差し替わった場合、呼び出し元が参照を更新できるよう「使用した esl」を
    返す（再接続時は新 esl、通常時は渡された esl）。reconnect 未注入時は
    ESLConnectionClosed をそのまま伝播する（後方互換）。
    """
    async with lock:
        try:
            await esl.bgapi(command)
            return esl
        except ESLConnectionClosed:
            if reconnect is None:
                raise
            esl = await reconnect()
            await esl.bgapi(command)
            return esl


class SessionRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, tuple] = {}

    def register(self, call_uuid: str, session, call_control) -> None:
        self._entries[call_uuid] = (session, call_control)

    def get(self, call_uuid: str):
        return self._entries.get(call_uuid)

    def pop(self, call_uuid: str):
        return self._entries.pop(call_uuid, None)

    def all_uuids(self) -> list[str]:
        """登録中のすべての call_uuid を返す（list_active_calls 用）。"""
        return list(self._entries.keys())


class AnswerRegistry:
    """call_uuid -> 応答完了 Future を保持する（発信オーケストレーション用）。

    OutboundCallService.dial/converse は originate 直前に `register(uuid)` して
    Future を得、`wait(uuid, timeout)` で CHANNEL_ANSWER を待つ。MediaEventRouter は
    CHANNEL_ANSWER 受信時に `resolve(uuid)` で Future を解決する。
    未登録 uuid の resolve は no-op（着信 AI など発信起点でない ANSWER を無視）。
    """

    def __init__(self) -> None:
        self._futures: dict[str, asyncio.Future[bool]] = {}

    def register(self, call_uuid: str) -> asyncio.Future[bool]:
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[bool] = loop.create_future()
        self._futures[call_uuid] = fut
        return fut

    def resolve(self, call_uuid: str) -> None:
        fut = self._futures.get(call_uuid)
        if fut is not None and not fut.done():
            fut.set_result(True)

    def pop(self, call_uuid: str) -> None:
        self._futures.pop(call_uuid, None)

    async def wait(self, call_uuid: str, timeout: float) -> bool:
        """CHANNEL_ANSWER を最大 timeout 秒待つ。応答なら True、タイムアウトで False。"""
        fut = self._futures.get(call_uuid)
        if fut is None:
            fut = self.register(call_uuid)
        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
        except TimeoutError:
            return False
        finally:
            self.pop(call_uuid)


class HangupRegistry:
    """call_uuid -> 通話終了完了 Future を保持する（converse オーケストレーション用）。

    AnswerRegistry と同型。converse は originate 直前に `register(uuid)` して Future を得、
    `wait(uuid, timeout)` で CHANNEL_HANGUP_COMPLETE を待つ。MediaEventRouter は
    CHANNEL_HANGUP_COMPLETE 受信時に `resolve(uuid)` で解決する。
    未登録 uuid の resolve は no-op（converse 起点でない切断を無視）。
    """

    def __init__(self) -> None:
        self._futures: dict[str, asyncio.Future[bool]] = {}

    def register(self, call_uuid: str) -> asyncio.Future[bool]:
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[bool] = loop.create_future()
        self._futures[call_uuid] = fut
        return fut

    def resolve(self, call_uuid: str) -> None:
        fut = self._futures.get(call_uuid)
        if fut is not None and not fut.done():
            fut.set_result(True)

    def pop(self, call_uuid: str) -> None:
        self._futures.pop(call_uuid, None)

    async def wait(self, call_uuid: str, timeout: float) -> bool:
        """CHANNEL_HANGUP_COMPLETE を最大 timeout 秒待つ。切断なら True、超過で False。"""
        fut = self._futures.get(call_uuid)
        if fut is None:
            fut = self.register(call_uuid)
        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
        except TimeoutError:
            return False
        finally:
            self.pop(call_uuid)


async def _load_provider(
    db: AsyncSession, box: SecretBox, pid: int
) -> tuple[str, dict, str | None]:
    p = await db.get(Provider, pid)
    if p is None:
        raise ValueError(f"provider {pid} not found")
    config = json.loads(p.config_json or "{}")
    key = box.decrypt(p.api_key_encrypted) if p.api_key_encrypted else None
    return p.kind, config, key


async def build_conversation_session(
    sessionmaker: async_sessionmaker[AsyncSession],
    secrets,
    esl,
    registry: SessionRegistry,
    call_uuid: str,
    agent_id: int,
    tts_dir: Path,
    *,
    lock: asyncio.Lock | None = None,
    reconnect: Callable[[], Awaitable[object]] | None = None,
):
    box = SecretBox(secrets.master_key)
    async with sessionmaker() as db:
        agent = await db.get(AiAgent, agent_id)
        if agent is None:
            raise ValueError(f"ai_agent {agent_id} not found")
        llm_kind, llm_cfg, llm_key = await _load_provider(db, box, agent.llm_provider_id)
        tts_kind, tts_cfg, tts_key = await _load_provider(db, box, agent.tts_provider_id)
        stt_kind, stt_cfg, stt_key = await _load_provider(db, box, agent.stt_provider_id)

    llm = ai_registry.build_llm(llm_kind, llm_cfg, llm_key)
    tts = ai_registry.build_tts(tts_kind, tts_cfg, tts_key)
    stt = ai_registry.build_stt(stt_kind, stt_cfg, stt_key)
    # lock/reconnect を注入することで、共有 ESL 接続の書き込み直列化（I6）と
    # 接続断時の自動張り直しを実現する。省略時は per-call 専用接続・再接続なし。
    # reconnect が返す新接続は app.state.esl_command にも反映されるため、
    # 既存セッションが古い接続を掴んだままになる問題は発生しない
    # （EslCallControl.reconnect 成功時に self._esl が更新される — call_control.py 参照）。
    call_control = EslCallControl(esl, call_uuid, lock=lock, reconnect=reconnect)

    async def _persist(turn: tuple[str, str, int]) -> None:
        role, text, latency_ms = turn
        async with sessionmaker() as db:
            db.add(
                CallMessage(
                    call_uuid=call_uuid,
                    agent_id=agent_id,
                    role=role,
                    text=text,
                    latency_ms=latency_ms if role == "assistant" else None,
                )
            )
            await db.commit()

    # agent は detached だが属性値は読み込み済み（expire_on_commit=False）
    session = ConversationSession(
        agent=agent,
        stt=stt,
        llm=llm,
        tts=tts,
        call_control=call_control,
        tts_dir=tts_dir,
        call_uuid=call_uuid,
        on_turn=_persist,
    )
    registry.register(call_uuid, session, call_control)
    return session, call_control


async def build_conversation_session_from_spec(
    sessionmaker: async_sessionmaker[AsyncSession],
    esl,
    registry: SessionRegistry,
    call_uuid: str,
    spec,
    llm,
    tts,
    stt,
    tts_dir: Path,
    *,
    transcript: list | None = None,
    call_control=None,
    lock: asyncio.Lock | None = None,
    reconnect: Callable[[], Awaitable[object]] | None = None,
):
    """DB の AiAgent ではなく一時 spec（EphemeralAgentSpec）から ConversationSession を組む。

    converse（Task 4）用。DB には保存しない一時ペルソナで会話するため、既定エージェントの
    provider 構成から作った llm/tts/stt を注入し、spec の system_prompt/greeting を使う。
    on_turn は transcript 収集（渡されたとき）と call_messages 永続化を**並行**で行う。
    spec は ConversationSession が読む属性（system_prompt/greeting/max_history/silence_end_ms）を
    duck-type で満たす。
    """
    if call_control is None:
        call_control = EslCallControl(esl, call_uuid, lock=lock, reconnect=reconnect)

    async def _on_turn(turn: tuple[str, str, int]) -> None:
        role, text, latency_ms = turn
        if transcript is not None:
            transcript.append(turn)
        async with sessionmaker() as db:
            db.add(
                CallMessage(
                    call_uuid=call_uuid,
                    agent_id=None,  # 一時エージェントは DB に無い
                    role=role,
                    text=text,
                    latency_ms=latency_ms if role == "assistant" else None,
                )
            )
            await db.commit()

    session = ConversationSession(
        agent=spec,
        stt=stt,
        llm=llm,
        tts=tts,
        call_control=call_control,
        tts_dir=tts_dir,
        call_uuid=call_uuid,
        on_turn=_on_turn,
    )
    registry.register(call_uuid, session, call_control)
    return session, call_control
