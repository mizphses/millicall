import asyncio
import logging
import re
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.config import Settings
from millicall.models import Extension, Route, Trunk, Workflow
from millicall.secrets_store import Secrets
from millicall.telephony.esl import ESLClient, ESLError
from millicall.telephony.fsconfig import (
    ExtensionConfig,
    FreeswitchConfigWriter,
    RouteConfig,
    TrunkConfig,
)

logger = logging.getLogger("millicall.telephony.service")

# プレフィックスは 2〜8 桁の数字のみ許可（正規表現インジェクション対策）
_PREFIX_RE = re.compile(r"^[0-9]{2,8}$")


def build_config_writer(settings: Settings, secrets: Secrets) -> FreeswitchConfigWriter:
    raw_prefixes = [p.strip() for p in settings.outbound_international_allow.split(",") if p.strip()]
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
        international_allow_prefixes=raw_prefixes,
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

    async def _load_configs(self, session: AsyncSession) -> list[ExtensionConfig]:
        result = await session.scalars(
            select(Extension).where(Extension.enabled.is_(True)).order_by(Extension.number)
        )
        return [
            ExtensionConfig(
                number=e.number, display_name=e.display_name, sip_password=e.sip_password
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
            )
            for t in result
        ]

    async def _load_routes(self, session: AsyncSession) -> list[RouteConfig]:
        # FreeSWITCHは文書順で評価（first match wins）。決定性のためmatch_number昇順
        result = await session.scalars(
            select(Route).where(Route.enabled.is_(True)).order_by(Route.match_number)
        )
        routes: list[RouteConfig] = []
        for r in result:
            ring_count = 0
            if r.target_type == "workflow":
                try:
                    wf = await session.get(Workflow, int(r.target_value))
                    if wf is not None:
                        import json
                        defn = json.loads(wf.definition_json)
                        start_nodes = [n for n in defn.get("nodes", []) if n.get("type") == "start"]
                        if start_nodes:
                            ring_count = int(start_nodes[0].get("config", {}).get("ring_count", 0))
                except Exception:
                    ring_count = 0  # any parse error -> default 0, never raise
            routes.append(RouteConfig(
                match_number=r.match_number,
                target_type=r.target_type,
                target_value=r.target_value,
                ring_count=ring_count,
            ))
        return routes

    async def regenerate(self, session: AsyncSession) -> None:
        configs = await self._load_configs(session)
        trunks = await self._load_trunks(session)
        routes = await self._load_routes(session)
        self._writer.write_all(configs, trunks, routes)

    @staticmethod
    async def _esl_connect_and_reload(client: ESLClient) -> None:
        await client.connect()
        await client.reloadxml()

    async def notify(self, session: AsyncSession) -> None:
        await self.regenerate(session)
        client = self._esl_factory()
        try:
            await asyncio.wait_for(
                self._esl_connect_and_reload(client),
                timeout=self._esl_timeout,
            )
        except TimeoutError:
            logger.warning(
                "reloadxml skipped (ESL connect timed out after %.1fs)", self._esl_timeout
            )
        except (OSError, ESLError) as exc:
            logger.warning("reloadxml skipped (FreeSWITCH ESL unreachable): %s", exc)
        finally:
            await client.close()
