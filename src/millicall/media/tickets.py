"""Single-use credentials for FreeSWITCH's media WebSocket handshake."""

import re
import secrets
import time


class MediaStreamTickets:
    def __init__(self) -> None:
        self._pending: dict[str, tuple[str, str, float]] = {}

    def issue(self, call_uuid: str, agent: str) -> str:
        # Both values also enter an ESL command and a filesystem-derived call path.
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", call_uuid):
            raise ValueError("Invalid call identifier")
        if agent != "ephemeral" and not re.fullmatch(r"[0-9]{1,10}", agent):
            raise ValueError("Invalid media agent")
        now = time.monotonic()
        self._pending = {k: v for k, v in self._pending.items() if v[2] > now}
        if len(self._pending) >= 1024:
            raise ValueError("Too many pending media streams")
        token = secrets.token_urlsafe(32)
        self._pending[token] = (call_uuid, agent, now + 60)
        return token

    def consume(self, token: str, call_uuid: str, agent: str) -> bool:
        expected = self._pending.get(token)
        if expected is None or expected[:2] != (call_uuid, agent):
            return False
        self._pending.pop(token)
        return expected[2] > time.monotonic()

    def revoke(self, call_uuid: str) -> None:
        self._pending = {k: v for k, v in self._pending.items() if v[0] != call_uuid}
