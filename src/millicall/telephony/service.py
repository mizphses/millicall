import asyncio
import logging
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.config import Settings
from millicall.models import Extension
from millicall.secrets_store import Secrets
from millicall.telephony.esl import ESLClient, ESLError
from millicall.telephony.fsconfig import ExtensionConfig, FreeswitchConfigWriter

logger = logging.getLogger("millicall.telephony.service")


def build_config_writer(settings: Settings, secrets: Secrets) -> FreeswitchConfigWriter:
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

    async def regenerate(self, session: AsyncSession) -> None:
        configs = await self._load_configs(session)
        self._writer.write_all(configs)

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
