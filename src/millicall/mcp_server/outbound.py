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

import asyncio
import contextlib
import json
import re
import uuid as _uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from millicall.ai import registry as ai_registry
from millicall.ai.llm.base import ChatMessage
from millicall.crypto import SecretBox
from millicall.mcp_server.ephemeral import EphemeralAgentSpec, EphemeralAgentStore
from millicall.media.service import AnswerRegistry, HangupRegistry, locked_bgapi
from millicall.models import AiAgent, Provider

# 外線とみなす番号プレフィクス（0 = 一般外線、184/186 = 発信者番号通知制御）。
_EXTERNAL_PREFIXES = ("0", "184", "186")

# 電話番号 / 発信者番号の許可文字: 数字・* # +（186/184 前置や内線番号を含む）。
# originate コマンドへの補間前に検証し、空白・改行・区切り文字による注入を防ぐ。
_VALID_NUMBER_RE = re.compile(r"^[0-9*#+]{1,32}$")

# converse システムプロンプト（[END_CALL] 版、旧 verbatim を [DONE]→[END_CALL] に置換）。
_CONVERSE_PROMPT_TEMPLATE = """あなたは電話で会話をしているAIアシスタントです。
相手は電話の向こうにいる人間です。自然な日本語の電話会話を行ってください。

## 会話の目的
{purpose}

{name_part}{points_part}

## 重要なルール
- 1回の発話は1〜2文に留めてください。電話では短く区切って話すのが自然です。
- 敬語を使ってください。
- 相手の発話に適切に反応してください（相槌、確認、質問への回答など）。
- 目的が達成できたら「ありがとうございました。失礼いたします。」のように締めの挨拶をして、\
[END_CALL] を発話の末尾に付けてください。
- 目的が達成できない場合（相手が断った等）も、丁寧に終了して [END_CALL] を付けてください。
- [END_CALL] は相手には読み上げられません。会話終了の合図としてだけ使います。
- 相手の発話が空だった場合は「もしもし、聞こえていますか？」と確認してください。
- わからないことを聞かれたら「確認して折り返します」と伝えてください。"""


def build_converse_system_prompt(purpose: str, key_points: str = "", your_name: str = "") -> str:
    """purpose/key_points/your_name を旧 verbatim（[END_CALL] 版）に差し込んで合成する。"""
    name_part = ""
    if your_name:
        name_part = f"あなたの名前は「{your_name}」です。最初に名乗ってください。"
    points_part = ""
    if key_points:
        points_part = f"\n\n## 伝えるべき要点\n{key_points}"
    return _CONVERSE_PROMPT_TEMPLATE.format(
        purpose=purpose, name_part=name_part, points_part=points_part
    )


_SUMMARY_SYSTEM = "あなたは電話会話の要約者です。以下のやり取りを1〜2文の日本語で要約してください。"


def build_summarizer(llm) -> "Callable[[str], Awaitable[str]]":
    """LLM で transcript を 1〜2 文に要約するコルーチンを返す（Task 6 が converse に渡す）。

    LLM は stream_chat のみ持つため、ストリームを連結して要約文にする。
    """

    async def _summarize(transcript_text: str) -> str:
        messages = [
            ChatMessage("system", _SUMMARY_SYSTEM),
            ChatMessage("user", transcript_text),
        ]
        parts: list[str] = []
        async for token in llm.stream_chat(messages):
            parts.append(token)
        return "".join(parts).strip()

    return _summarize


class _EslLike(Protocol):
    async def bgapi(self, command: str) -> str: ...

    async def api(self, command: str) -> str: ...


@dataclass(frozen=True)
class DialResult:
    call_uuid: str
    state: str  # 応答済みは "Up"


# 内部 role → 旧 speaker 語彙（§6 互換）。
_SPEAKER_MAP = {"assistant": "ai", "user": "human", "system": "system"}


@dataclass
class ConverseResult:
    """converse の結果（§6 JSON へ整形する素）。

    status="completed" のとき turns/summary/transcript が意味を持つ。
    status="error" のとき error を持ち transcript は []（§6 エラー形）。
    """

    status: str
    phone_number: str = ""
    purpose: str = ""
    turns: int = 0
    summary: str = ""
    transcript: list[dict] = field(default_factory=list)
    error: str = ""


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
                raise ValueError(f"既定 MCP エージェント (id={default_agent_id}) が見つかりません")
        else:
            agent = await db.scalar(
                select(AiAgent).where(AiAgent.enabled.is_(True)).order_by(AiAgent.id).limit(1)
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
        hangup_registry: HangupRegistry | None = None,
        ephemeral_store: EphemeralAgentStore | None = None,
        run_conversation: Callable[..., Awaitable[None]] | None = None,
        lock: asyncio.Lock | None = None,
        reconnect: Callable[[], Awaitable[_EslLike]] | None = None,
    ) -> None:
        self._esl = esl
        self._answer_registry = answer_registry
        self._sip_domain = sip_domain
        self._fetch_enabled_trunks = fetch_enabled_trunks
        self._uuid_factory = uuid_factory or (lambda: _uuid.uuid4().hex)
        # converse 用（dial のみ使うときは省略可）。
        self._hangup_registry = hangup_registry
        self._ephemeral_store = ephemeral_store
        # run_conversation は「セッションを WS ハンドラが駆動して transcript を積み、
        # 終話（[END_CALL]/相手切断）で hangup_registry が解決される」実運用経路の
        # テスト差し替え点。実運用では None（converse は hangup_registry を待つだけ）。
        self._run_conversation = run_conversation
        # 共有 ESL 接続の直列化（I6）: 未注入時は per-instance lock・再接続なしに
        # フォールバックする（後方互換。接続断は呼び出し元へ伝播）。
        self._lock = lock if lock is not None else asyncio.Lock()
        self._reconnect = reconnect

    async def _bgapi(self, command: str) -> None:
        """共有 ESL 接続を lock で直列化し、接続断時は reconnect で張り直して再送する。"""
        self._esl = await locked_bgapi(
            self._esl, command, lock=self._lock, reconnect=self._reconnect
        )

    async def _resolve_target(
        self, phone_number: str, caller_id: str, trunk: str
    ) -> tuple[str, str]:
        """(dest, caller_id) を解決する（旧 _resolve_endpoint 相当）。

        トランクが必要な外線で enabled トランクが無い場合は ValueError。

        セキュリティ: phone_number / caller_id / trunk は originate コマンド文字列に
        補間されるため、ここで一括して allowlist 検証する（ESL コマンドインジェクション
        対策）。空白・改行・`,{}'"` 等の区切り文字を含む値は fail-closed で拒否する。
        """
        if not phone_number or not _VALID_NUMBER_RE.match(phone_number):
            raise ValueError(f"invalid phone_number: {phone_number!r}")
        if caller_id and not _VALID_NUMBER_RE.match(caller_id):
            raise ValueError(f"invalid caller_id: {caller_id!r}")

        if trunk:
            # 指定トランクは enabled 一覧に実在するものだけ許可（未知値の補間を防ぐ）。
            trunks = await self._fetch_enabled_trunks()
            match = next((t for t in trunks if t.name == trunk), None)
            if match is None:
                raise ValueError(f"unknown trunk: {trunk!r}")
            resolved_cid = caller_id or getattr(match, "caller_id", "") or ""
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
        await self._bgapi(command)

        answered = await self._answer_registry.wait(call_uuid, timeout=timeout)
        if not answered:
            raise DialTimeout(call_uuid)
        return DialResult(call_uuid=call_uuid, state="Up")

    async def converse(
        self,
        phone_number: str,
        purpose: str,
        key_points: str = "",
        your_name: str = "",
        max_turns: int = 10,
        caller_id: str = "",
        trunk: str = "",
        *,
        providers: ResolvedProviders | None = None,
        summarizer: Callable[[str], Awaitable[str]] | None = None,
        answer_timeout: float = 30.0,
        max_conversation_seconds: float | None = None,
    ) -> ConverseResult:
        """発信 → 自律会話 → 終話 → transcript/summary 返却（§6）。

        - 既定 MCP エージェント（providers）の provider 構成を流用し、purpose/key_points/
          your_name から system_prompt を合成した一時エージェントを EphemeralAgentStore に登録。
        - `originate {millicall_ai_agent=ephemeral,...}<dest> &park` で発信（着信 AI と同じ
          audio_stream 自動起動経路に合流）。
        - CHANNEL_ANSWER を answer_timeout 秒待つ。応答なければ §6 エラー。
        - 会話は WS ハンドラ（ConversationSession）が駆動し transcript を積む。[END_CALL] または
          相手切断で HangupRegistry が解決される。max_conversation_seconds 超過時は
          uuid_kill でフォールバック終話する。
        - transcript を ai/human/system 語彙へマップし、summarizer で 1〜2 文要約して返す。
        """
        if self._ephemeral_store is None or self._hangup_registry is None:
            raise RuntimeError("converse には ephemeral_store と hangup_registry が必要です")

        # 番号解決（dial と同経路）。
        try:
            dest, resolved_cid = await self._resolve_target(phone_number, caller_id, trunk)
        except ValueError as exc:
            return ConverseResult(
                status="error", phone_number=phone_number, purpose=purpose, error=str(exc)
            )

        call_uuid = self._uuid_factory()
        transcript_raw: list = []

        # 一時エージェント spec を合成して store に登録（WS ハンドラが call_uuid で引く）。
        system_prompt = build_converse_system_prompt(purpose, key_points, your_name)
        greeting = ""
        if providers is not None:
            greeting = getattr(providers.agent, "greeting", "") or ""
            spec = EphemeralAgentSpec(
                system_prompt=system_prompt,
                greeting=greeting,
                llm_provider_id=providers.agent.llm_provider_id,
                tts_provider_id=providers.agent.tts_provider_id,
                stt_provider_id=providers.agent.stt_provider_id,
                max_history=getattr(providers.agent, "max_history", 10),
                silence_end_ms=getattr(providers.agent, "silence_end_ms", 600),
            )
            entry = self._ephemeral_store.register(
                call_uuid, spec, llm=providers.llm, tts=providers.tts, stt=providers.stt
            )
        else:
            # テスト経路: provider 未解決。spec のみ登録する。
            spec = EphemeralAgentSpec(
                system_prompt=system_prompt,
                greeting=greeting,
                llm_provider_id=0,
                tts_provider_id=0,
                stt_provider_id=0,
            )
            entry = self._ephemeral_store.register(call_uuid, spec)
        # 実運用では WS ハンドラが entry.transcript を積む。テストは run_conversation に渡す。
        transcript_raw = entry.transcript

        # 発信（ephemeral マーカー付き → audio_stream 自動起動に合流）。
        variables = [
            f"origination_uuid={call_uuid}",
            "millicall_ai_agent=ephemeral",
            "verbose_events=true",
        ]
        if resolved_cid:
            variables.append(f"origination_caller_id_number={resolved_cid}")
        var_str = ",".join(variables)
        self._answer_registry.register(call_uuid)
        self._hangup_registry.register(call_uuid)
        try:
            await self._bgapi(f"originate {{{var_str}}}{dest} &park")

            answered = await self._answer_registry.wait(call_uuid, timeout=answer_timeout)
            if not answered:
                return ConverseResult(
                    status="error",
                    phone_number=phone_number,
                    purpose=purpose,
                    error="30秒以内に応答がありませんでした",
                )

            # 会話駆動。テストは run_conversation を差し替え、実運用では WS ハンドラが担う。
            conv_task = None
            if self._run_conversation is not None:
                conv_task = asyncio.ensure_future(
                    self._run_conversation(call_uuid=call_uuid, transcript=transcript_raw)
                )

            # 上限時間: max_turns から概算（1 ターン ~30s）か明示指定。
            timeout = (
                max_conversation_seconds
                if max_conversation_seconds is not None
                else float(max_turns) * 30.0
            )
            hung_up = await self._hangup_registry.wait(call_uuid, timeout=timeout)
            if not hung_up:
                # フォールバック終話（future 取り逃し/END_CALL 無し）。
                await self._bgapi(f"uuid_kill {call_uuid}")
            if conv_task is not None and not conv_task.done():
                conv_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await conv_task
        finally:
            self._answer_registry.pop(call_uuid)
            self._hangup_registry.pop(call_uuid)
            self._ephemeral_store.pop(call_uuid)

        # transcript を §6 形へ整形（speaker マップ + turn 連番）。
        transcript = [
            {"turn": i, "speaker": _SPEAKER_MAP.get(role, role), "text": text}
            for i, (role, text, _latency) in enumerate(transcript_raw)
        ]
        turns = sum(1 for t in transcript if t["speaker"] == "human")
        summary = ""
        if summarizer is not None and transcript:
            joined = "\n".join(f"{t['speaker']}: {t['text']}" for t in transcript)
            summary = await summarizer(joined)
        return ConverseResult(
            status="completed",
            phone_number=phone_number,
            purpose=purpose,
            turns=turns,
            summary=summary,
            transcript=transcript,
        )
