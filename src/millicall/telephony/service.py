import asyncio
import json
import logging
import re
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.config import Settings
from millicall.models import AiAgent, Extension, NetworkConfig, Trunk, Workflow
from millicall.numberplan import load_ring_groups_with_members
from millicall.secrets_store import Secrets
from millicall.telephony.esl import ESLClient, ESLError
from millicall.telephony.fsconfig import (
    AiAgentConfig,
    ExtensionConfig,
    FreeswitchConfigWriter,
    RingGroupConfig,
    TrunkConfig,
    WorkflowConfig,
    build_reload_commands,
)

logger = logging.getLogger("millicall.telephony.service")

# プレフィックスは 2〜8 桁の数字のみ許可（正規表現インジェクション対策）
_PREFIX_RE = re.compile(r"^[0-9]{2,8}$")


def build_config_writer(settings: Settings, secrets: Secrets) -> FreeswitchConfigWriter:
    raw_prefixes = [
        p.strip() for p in settings.outbound_international_allow.split(",") if p.strip()
    ]
    for p in raw_prefixes:
        if not _PREFIX_RE.match(p):
            raise ValueError(
                f"MILLICALL_OUTBOUND_INTERNATIONAL_ALLOW に無効なプレフィックスが含まれています: "
                f"'{p}' （2〜8桁の数字のみ許可）"
            )
    return FreeswitchConfigWriter(
        output_dir=settings.fs_config_dir,
        sip_domain=settings.sip_domain,
        esl_password=secrets.esl_password,
        sip_port=settings.sip_port,
        sip_ip=settings.sip_ip,
        rtp_ip=settings.rtp_ip,
        sip_bind_ip=settings.sip_bind_ip,
        event_socket_ip=settings.event_socket_ip,
        event_socket_port=settings.esl_port,
        external_sip_port=settings.external_sip_port,
        sip_external_ip=settings.sip_external_ip,
        international_allow_prefixes=raw_prefixes,
        # SIP多層防御 (Phase 6 Task 7)
        sip_trusted_cidrs=settings.sip_trusted_cidrs,
        sip_reject_anonymous=settings.sip_reject_anonymous,
    )


def build_esl_factory(settings: Settings, secrets: Secrets) -> Callable[[], ESLClient]:
    def factory() -> ESLClient:
        return ESLClient(settings.esl_host, settings.esl_port, secrets.esl_password)

    return factory


class TelephonyChangeListener:
    def __init__(
        self,
        writer: FreeswitchConfigWriter,
        esl_factory: Callable[[], ESLClient],
        esl_timeout: float = 5.0,
    ) -> None:
        self._writer = writer
        self._esl_factory = esl_factory
        self._esl_timeout = esl_timeout
        # 前回 regenerate 時の internal 実効バインド（sip-ip と domain）を保持する。
        # reloadxml では sofia プロファイルの sip-ip / bind は再バインドされないため、
        # この値が前回と変化した場合、または初回（None）の場合にのみ
        # `sofia profile internal restart` を送る（notify を参照）。初期値 None は
        # 「まだ一度も restart していない＝初回」を意味する。
        self._last_internal_bind: tuple[str | None, str | None] | None = None

    async def _load_configs(self, session: AsyncSession) -> list[ExtensionConfig]:
        result = await session.scalars(
            select(Extension).where(Extension.enabled.is_(True)).order_by(Extension.number)
        )
        return [
            ExtensionConfig(
                number=e.number,
                display_name=e.display_name,
                sip_password=e.sip_password,
                calling_permission=e.calling_permission,
            )
            for e in result
        ]

    async def _load_trunks(self, session: AsyncSession) -> list[TrunkConfig]:
        result = await session.scalars(
            select(Trunk).where(Trunk.enabled.is_(True)).order_by(Trunk.name)
        )
        return [
            TrunkConfig(
                name=t.name,
                display_name=t.display_name,
                host=t.host,
                username=t.username,
                password=t.password,
                did_number=t.did_number,
                caller_id=t.caller_id,
                inbound_extension=t.inbound_extension,
                source_port=t.source_port,
                trunk_type=t.trunk_type,
                inbound_cidrs=[c for c in t.inbound_cidrs.split(",") if c],
            )
            for t in result
        ]

    async def _load_ring_groups(self, session: AsyncSession) -> list[RingGroupConfig]:
        groups = await load_ring_groups_with_members(session)
        return [
            RingGroupConfig(
                number=g.number,
                name=g.name,
                member_numbers=[m.number for m in members],
            )
            for g, members in groups
        ]

    async def _load_ai_agents(self, session: AsyncSession) -> list[AiAgentConfig]:
        result = await session.scalars(
            select(AiAgent)
            .where(AiAgent.enabled.is_(True), AiAgent.number.is_not(None))
            .order_by(AiAgent.number)
        )
        return [AiAgentConfig(number=a.number or "", agent_id=a.id) for a in result]

    async def _load_child_lan_ip(self, session: AsyncSession) -> str | None:
        """子LAN（netd DHCP ネットワーク）が適用済みなら子LAN GW IP を返す。

        NetworkConfig（id=1 単一行）を読み、applied=True かつ lan_ip が非空の場合のみ
        その lan_ip を返す。未適用・未設定なら None（＝従来動作へフォールバック）。
        """
        cfg = await session.get(NetworkConfig, 1)
        if cfg is None:
            return None
        if cfg.applied and cfg.lan_ip:
            return cfg.lan_ip
        return None

    async def _load_workflows(self, session: AsyncSession) -> list[WorkflowConfig]:
        result = await session.scalars(
            select(Workflow).where(Workflow.enabled.is_(True)).order_by(Workflow.number)
        )
        workflows: list[WorkflowConfig] = []
        for wf in result:
            ring_count = 0
            try:
                defn = json.loads(wf.definition_json)
                start_nodes = [n for n in defn.get("nodes", []) if n.get("type") == "start"]
                if start_nodes:
                    ring_count = int(start_nodes[0].get("config", {}).get("ring_count", 0))
            except Exception:
                ring_count = 0  # any parse error -> default 0, never raise
            workflows.append(
                WorkflowConfig(number=wf.number, workflow_id=wf.id, ring_count=ring_count)
            )
        return workflows

    def update_outbound_policy(
        self, international_allow_prefixes: list[str], sip_reject_anonymous: bool
    ) -> None:
        """発信ポリシー（国際発信 allowlist / 匿名着信拒否）を差し替える。

        管理画面（PUT /api/settings）からの変更で使う。次回 regenerate/notify 時に
        新しい値でテンプレート展開される。
        """
        self._writer.update_outbound_policy(international_allow_prefixes, sip_reject_anonymous)

    async def regenerate(self, session: AsyncSession) -> None:
        configs = await self._load_configs(session)
        trunks = await self._load_trunks(session)
        ring_groups = await self._load_ring_groups(session)
        ai_agents = await self._load_ai_agents(session)
        workflows = await self._load_workflows(session)
        # 子LAN 適用時は internal プロファイル（sip-ip/rtp-ip/ドメイン）と
        # ディレクトリ・dialplan のドメインを子LAN GW IP へ切り替える。
        # 未適用時は None を渡して従来動作（sip_bind_ip / sip_domain）を維持する。
        child_lan_ip = await self._load_child_lan_ip(session)
        self._writer.set_internal_network(child_lan_ip, child_lan_ip)
        self._writer.write_all(
            configs,
            trunks,
            ring_groups=ring_groups,
            ai_agents=ai_agents,
            workflows=workflows,
        )

    def _current_internal_bind(self) -> tuple[str | None, str | None]:
        """今回生成に使う internal の実効バインド（sip-ip, domain）を返す。

        PR #57 で導入された internal_bind_ip / internal_domain のロジック（子LAN 適用時は
        lan_ip、そうでなければ sip_bind_ip / sip_domain）は set_internal_network で
        writer 側へ反映済みのため、その実効値を writer から取得して重複計算を避ける。
        """
        return (
            self._writer.internal_bind_ip,
            self._writer.internal_domain,
        )

    @staticmethod
    async def _esl_connect_and_reload(
        client: ESLClient,
        sync_gateway: str | None = None,
        current_trunk_names: list[str] | None = None,
        *,
        restart_internal: bool = False,
    ) -> None:
        await client.connect()
        await client.reloadxml()
        if restart_internal:
            # internal の実効バインド（sip-ip / domain）が前回生成時から変化した、または
            # 初回（core 起動相当）のケース。reloadxml だけでは sofia プロファイルの
            # sip-ip / bind は再バインドされないため、profile restart で確実に新しい IP へ
            # バインドし直す。internal は固定プロファイル名のため安全な固定文字列を使う。
            # 外線トランクの restart（build_reload_commands）とは独立に送る。
            await client.api("sofia profile internal restart")
        if sync_gateway is not None:
            # トランクごとに external_<name> プロファイルが分かれたため、
            # 対象トランクのプロファイルを操作する。current_trunk_names（現存する
            # トランク名）に対象があれば restart（新規ロード/変更反映）、無ければ
            # stop（削除済み。XML/ファイルは掃除済みのため restart では旧 in-memory
            # プロファイルが残りゴースト登録が続く。stop で明示破棄する）。
            # reloadxml だけでは sofia プロファイルは再ロードされない。register=true の
            # ゲートウェイは restart 後に直ちに REGISTER を試行するため、保存直後に
            # HGW 側で登録状態を確認できる。
            for cmd in build_reload_commands(current_trunk_names or [], changed=sync_gateway):
                await client.api(cmd)

    async def notify(self, session: AsyncSession, *, sync_gateway: str | None = None) -> None:
        await self.regenerate(session)
        # regenerate 後の DB 状態から現存トランク名を取得する。削除直後は対象が
        # ここに含まれないため、_esl_connect_and_reload が stop を選択する。
        current_trunk_names = [t.name for t in await self._load_trunks(session)]
        # internal の実効バインド（sip-ip / domain）が前回生成時と異なる、または初回
        # （前回値 None＝core 起動時の最初の notify）の場合に internal restart を送る。
        # sync_gateway とは独立に「internal バインドが変わったか / 初回か」で判定する。
        # 初回は稼働中 FreeSWITCH が古い設定の可能性があるため必ず restart する。
        current_internal_bind = self._current_internal_bind()
        restart_internal = (
            self._last_internal_bind is None or self._last_internal_bind != current_internal_bind
        )
        client = self._esl_factory()
        try:
            await asyncio.wait_for(
                self._esl_connect_and_reload(
                    client,
                    sync_gateway,
                    current_trunk_names,
                    restart_internal=restart_internal,
                ),
                timeout=self._esl_timeout,
            )
            # ESL 実行に成功した場合のみ保持値を更新する。失敗時に更新すると
            # 次回に restart を送れず古い IP のまま取り残される恐れがあるため。
            if restart_internal:
                self._last_internal_bind = current_internal_bind
        except TimeoutError:
            logger.warning(
                "reloadxml skipped (ESL connect timed out after %.1fs)", self._esl_timeout
            )
        except (OSError, ESLError) as exc:
            logger.warning("reloadxml skipped (FreeSWITCH ESL unreachable): %s", exc)
        finally:
            await client.close()
