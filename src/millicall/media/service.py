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
