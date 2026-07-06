"""converse 用の一時エージェント（DB 非保存のアドホックペルソナ）レジストリ。

converse は DB に無い一時ペルソナで会話する。`OutboundCallService.converse` が
purpose/key_points/your_name から `EphemeralAgentSpec` を合成し、既定 MCP エージェント由来の
provider（llm/tts/stt）とともに `EphemeralAgentStore` に put する。

`audio_fork_ws` は `?agent=ephemeral`（非数値マーカー）を受けたとき call_uuid で store を引き、
`build_conversation_session_from_spec` に spec + provider + transcript を渡してセッションを組む
（着信の DB エージェント経路と合流するが、DB には触れない）。
"""

from dataclasses import dataclass, field


@dataclass
class EphemeralAgentSpec:
    """ConversationSession が読む属性（system_prompt/greeting/max_history/silence_end_ms）を
    duck-type で満たす一時エージェント spec。DB の AiAgent 相当。"""

    system_prompt: str
    greeting: str
    llm_provider_id: int
    tts_provider_id: int
    stt_provider_id: int
    max_history: int = 10
    silence_end_ms: int = 600


@dataclass
class _EphemeralEntry:
    """store が call_uuid ごとに保持する: spec + 解決済み provider + transcript バッファ。"""

    spec: EphemeralAgentSpec
    llm: object
    tts: object
    stt: object
    transcript: list = field(default_factory=list)


class EphemeralAgentStore:
    """call_uuid -> 一時エージェント（spec + provider + transcript）を保持する。

    put/get はエントリ全体を扱う。互換のため `put(uuid, spec)`（spec のみ）も許容する
    （provider/transcript は後から set できる）。converse は provider 付きで登録する。
    """

    def __init__(self) -> None:
        self._entries: dict[str, _EphemeralEntry] = {}

    def register(
        self,
        call_uuid: str,
        spec: EphemeralAgentSpec,
        *,
        llm: object = None,
        tts: object = None,
        stt: object = None,
    ) -> _EphemeralEntry:
        entry = _EphemeralEntry(spec=spec, llm=llm, tts=tts, stt=stt)
        self._entries[call_uuid] = entry
        return entry

    def put(self, call_uuid: str, spec: EphemeralAgentSpec):
        """spec のみで登録する簡易版（テスト/後付け provider 用）。spec を返す。"""
        self.register(call_uuid, spec)
        return spec

    def get(self, call_uuid: str):
        """登録済みエントリを返す。put(spec のみ) 経由なら spec を返す（互換）。"""
        entry = self._entries.get(call_uuid)
        if entry is None:
            return None
        # provider 未設定（put(spec) 経由）のときは spec を返し、
        # provider 付き（register 経由）のときはエントリを返す。
        if entry.llm is None and entry.tts is None and entry.stt is None:
            return entry.spec
        return entry

    def get_entry(self, call_uuid: str) -> "_EphemeralEntry | None":
        return self._entries.get(call_uuid)

    def pop(self, call_uuid: str):
        entry = self._entries.pop(call_uuid, None)
        if entry is None:
            return None
        if entry.llm is None and entry.tts is None and entry.stt is None:
            return entry.spec
        return entry
