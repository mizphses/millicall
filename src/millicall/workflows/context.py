"""Per-call execution context for the workflow engine (Phase 4b Task 3).

``ChannelContext`` bundles the resources one workflow run needs on a single
parked channel: the call uuid, the variable store (with ``{{var}}`` template
expansion), the ESL call-control / voice primitives, and lightweight extension
points that later tasks fill in (DTMF collector — Task 6, agent/provider
resolvers — Task 5/7). It is intentionally dependency-light so the engine core
can be unit-tested with a bare context (all resource handles default to
``None``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

# Variable-name allowlist (also the only names ``{{...}}`` will expand).
_VAR_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_TEMPLATE_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def is_valid_variable_name(name: str) -> bool:
    """True if ``name`` matches the variable-name allowlist."""
    return bool(_VAR_NAME_RE.match(name))


def render_template(text: str, variables: dict[str, str]) -> str:
    """Expand ``{{var}}`` placeholders in ``text`` from ``variables``.

    Only well-formed variable names (``^[a-zA-Z_][a-zA-Z0-9_]*$``) are treated
    as references; anything else (e.g. ``{{1abc}}``) is left verbatim. Undefined
    variables expand to the empty string.
    """
    if not text:
        return text

    def _repl(match: re.Match[str]) -> str:
        return str(variables.get(match.group(1), ""))

    return _TEMPLATE_RE.sub(_repl, text)


@dataclass
class ChannelContext:
    """Execution context for one workflow run on one channel.

    Resource handles are optional so the engine core is unit-testable without a
    live channel; node handlers (Task 4–7) rely on the concrete resources being
    wired by the runner factory (Task 9).
    """

    uuid: str = ""
    variables: dict[str, str] = field(default_factory=dict)

    # Resource handles (wired by the runner factory in Task 9).
    call_control: Any = None  # EslCallControl: play_file/stop_playback/hangup/send_dtmf/transfer
    primitives: Any = None  # CallPrimitives: say/listen/say_and_listen
    tts_dir: Path | None = None
    sessionmaker: Any = None
    secrets: Any = None
    esl: Any = None

    # Extension points filled by later tasks.
    dtmf: Any = None  # DtmfCollector (Task 6)
    provider_resolver: Callable[[int], Awaitable[Any]] | None = None  # Task 5/7
    agent_resolver: Callable[[int], Awaitable[Any]] | None = None  # Task 7
    default_tts_provider_id: int | None = None
    smtp: Any = None  # SmtpEmailSender — Task 8, wired by the runner factory in Task 9

    # Lifecycle flag: set once the channel has been (or is being) hung up.
    hung_up: bool = False

    # --- variables ------------------------------------------------------- #

    def render(self, text: str) -> str:
        """Expand ``{{var}}`` in ``text`` using this context's variables."""
        return render_template(text, self.variables)

    def set_var(self, name: str, value: Any) -> None:
        """Store a variable (stringified). Rejects names outside the allowlist."""
        if not is_valid_variable_name(name):
            raise ValueError(f"invalid variable name: {name!r}")
        self.variables[name] = "" if value is None else str(value)

    def get_var(self, name: str, default: str = "") -> str:
        return self.variables.get(name, default)

    # --- lifecycle ------------------------------------------------------- #

    async def hangup(self) -> None:
        """Mark the channel hung up and, if wired, issue the ESL hangup."""
        self.hung_up = True
        if self.call_control is not None:
            await self.call_control.hangup()

    # --- resolution helpers (extension points) --------------------------- #

    async def resolve_provider(self, provider_id: int | None) -> Any:
        """Resolve a provider id to a built provider via the injected resolver.

        Falls back to ``default_tts_provider_id`` when ``provider_id`` is None.
        Returns None when no resolver is wired (engine-core / unit tests).
        """
        pid = provider_id if provider_id is not None else self.default_tts_provider_id
        if pid is None or self.provider_resolver is None:
            return None
        return await self.provider_resolver(pid)

    async def resolve_agent(self, agent_id: int) -> Any:
        """Resolve an agent id via the injected resolver (None if not wired)."""
        if self.agent_resolver is None:
            return None
        return await self.agent_resolver(agent_id)
