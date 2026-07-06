"""発信オーケストレーション（MCP dial/converse の下回りサービス）。

`OutboundCallService.dial` はトランク経由で外線へ、内線宛は SIP ドメインへ
`bgapi originate {...}<dest> &park` を発行し、CHANNEL_ANSWER を最大 timeout 秒
待って通話 call_uuid を返す。応答後チャネルは park に入るため、手動音声プリミティブ
（say/listen）や converse（Task 4）がその上で制御を行う。

番号解決（`_resolve_target`、旧 `_resolve_endpoint` 相当）:
    - 明示トランク指定 or 0/184/186 始まり → 外線: `sofia/gateway/<trunk>/<番号>`
      （トランク未指定時は enabled 先頭を自動選択、caller_id は指定 > トランク既定）。
    - それ以外 → 内線: `user/<ext>@<sip_domain>`。

発信者番号通知（コントローラ裁定#6・非通知回線）: 呼び出し側が phone_number 先頭に
`186`/`184` を前置する（旧互換）。本サービスは受けた番号をそのまま dest に補間する。

手動系は AI 会話用の audio_stream 自動起動（MediaEventRouter._maybe_start_audio_stream
が variable_millicall_ai_agent を検知して発行）と衝突しないよう、originate 変数に
`millicall_ai_agent` を付けない。
"""

import json
import uuid as _uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from millicall.ai import registry as ai_registry
from millicall.crypto import SecretBox
from millicall.media.service import AnswerRegistry
from millicall.models import AiAgent, Provider

# 外線とみなす番号プレフィクス（0 = 一般外線、184/186 = 発信者番号通知制御）。
_EXTERNAL_PREFIXES = ("0", "184", "186")


class _EslLike(Protocol):
    async def bgapi(self, command: str) -> str:
        ...

    async def api(self, command: str) -> str:
        ...


@dataclass(frozen=True)
class DialResult:
    call_uuid: str
    state: str  # 応答済みは "Up"


@dataclass
class ResolvedProviders:
    """既定 MCP エージェントとその provider 構成から組んだ LLM/TTS/STT 実体。

    converse（Task 4）は agent.system_prompt/greeting を差し替えて流用し、
    say/listen（手動系）は tts/stt のみ使う。
    """

    agent: AiAgent
    llm: object
    tts: object
    stt: object


async def _load_provider(
    db: AsyncSession, box: SecretBox, pid: int
) -> tuple[str, dict, str | None]:
    p = await db.get(Provider, pid)
    if p is None:
        raise ValueError(f"provider {pid} not found")
    config = json.loads(p.config_json or "{}")
    key = box.decrypt(p.api_key_encrypted) if p.api_key_encrypted else None
    return p.kind, config, key


async def resolve_default_providers(
    sessionmaker: async_sessionmaker[AsyncSession],
    secrets,
    default_agent_id: int | None,
) -> ResolvedProviders:
    """既定 MCP エージェントを解決し（裁定#1）、その provider 構成で LLM/TTS/STT を組む。

    default_agent_id が None のときは enabled な ai_agents の最小 id を採用する。
    該当エージェントが無い場合は ValueError。API キーは SecretBox 経由でのみ復号する。
    """
    box = SecretBox(secrets.master_key)
    async with sessionmaker() as db:
        if default_agent_id is not None:
            agent = await db.get(AiAgent, default_agent_id)
            if agent is None:
                raise ValueError(
                    f"既定 MCP エージェント (id={default_agent_id}) が見つかりません"
                )
        else:
            agent = await db.scalar(
                select(AiAgent)
                .where(AiAgent.enabled.is_(True))
                .order_by(AiAgent.id)
                .limit(1)
            )
            if agent is None:
                raise ValueError("利用可能な AI エージェントがありません")
        llm_kind, llm_cfg, llm_key = await _load_provider(db, box, agent.llm_provider_id)
        tts_kind, tts_cfg, tts_key = await _load_provider(db, box, agent.tts_provider_id)
        stt_kind, stt_cfg, stt_key = await _load_provider(db, box, agent.stt_provider_id)

    return ResolvedProviders(
        agent=agent,
        llm=ai_registry.build_llm(llm_kind, llm_cfg, llm_key),
        tts=ai_registry.build_tts(tts_kind, tts_cfg, tts_key),
        stt=ai_registry.build_stt(stt_kind, stt_cfg, stt_key),
    )


class DialTimeout(Exception):  # noqa: N818  # 公開 IF 名（Task 4/6 が依存）
    """CHANNEL_ANSWER が timeout 秒以内に来なかった。call_uuid を保持する。"""

    def __init__(self, call_uuid: str) -> None:
        super().__init__(f"no answer within timeout for {call_uuid}")
        self.call_uuid = call_uuid


class OutboundCallService:
    def __init__(
        self,
        *,
        esl: _EslLike,
        answer_registry: AnswerRegistry,
        sip_domain: str,
        fetch_enabled_trunks: Callable[[], Awaitable[list]],
        uuid_factory: Callable[[], str] | None = None,
    ) -> None:
        self._esl = esl
        self._answer_registry = answer_registry
        self._sip_domain = sip_domain
        self._fetch_enabled_trunks = fetch_enabled_trunks
        self._uuid_factory = uuid_factory or (lambda: _uuid.uuid4().hex)

    async def _resolve_target(
        self, phone_number: str, caller_id: str, trunk: str
    ) -> tuple[str, str]:
        """(dest, caller_id) を解決する（旧 _resolve_endpoint 相当）。

        トランクが必要な外線で enabled トランクが無い場合は ValueError。
        """
        if trunk:
            resolved_cid = caller_id
            if not resolved_cid:
                trunks = await self._fetch_enabled_trunks()
                match = next((t for t in trunks if t.name == trunk), None)
                if match is not None and getattr(match, "caller_id", ""):
                    resolved_cid = match.caller_id
            return f"sofia/gateway/{trunk}/{phone_number}", resolved_cid

        if phone_number.startswith(_EXTERNAL_PREFIXES):
            trunks = await self._fetch_enabled_trunks()
            if not trunks:
                raise ValueError("利用可能なトランクがありません")
            selected = trunks[0]
            resolved_cid = caller_id or getattr(selected, "caller_id", "") or ""
            return f"sofia/gateway/{selected.name}/{phone_number}", resolved_cid

        # 内線宛。
        return f"user/{phone_number}@{self._sip_domain}", caller_id

    async def dial(
        self,
        phone_number: str,
        caller_id: str = "",
        trunk: str = "",
        *,
        timeout: float = 30.0,
    ) -> DialResult:
        """発信して park、CHANNEL_ANSWER を待って call_uuid を返す。

        番号解決失敗（トランク無しなど）は ValueError を送出。
        timeout 秒以内に応答が無ければ DialTimeout(call_uuid) を送出。
        """
        dest, caller_id = await self._resolve_target(phone_number, caller_id, trunk)
        call_uuid = self._uuid_factory()

        variables = [
            f"origination_uuid={call_uuid}",
            "verbose_events=true",
        ]
        if caller_id:
            variables.append(f"origination_caller_id_number={caller_id}")
        var_str = ",".join(variables)

        self._answer_registry.register(call_uuid)
        command = f"originate {{{var_str}}}{dest} &park"
        await self._esl.bgapi(command)

        answered = await self._answer_registry.wait(call_uuid, timeout=timeout)
        if not answered:
            raise DialTimeout(call_uuid)
        return DialResult(call_uuid=call_uuid, state="Up")
