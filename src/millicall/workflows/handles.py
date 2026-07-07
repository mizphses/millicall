"""Output-handle vocabulary — the backend single source of truth (Task 1).

``HANDLE_VOCAB`` maps each node ``type`` to its list of output-handle ids. For
the two nodes whose handles depend on config (``dtmf_input`` on ``max_digits``,
``intent_detection`` on ``intents``) the vocab entry is a *representative*
default; ``output_handles(node)`` computes the true, per-node handle list and
must be used for edge validation and transition resolution.
"""

from __future__ import annotations

from typing import Any

_DIGITS = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"]
_MENU_HANDLES = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "timeout"]

HANDLE_VOCAB: dict[str, list[str]] = {
    # terminal / goto -> no output handles
    "start": ["out"],
    "end": [],
    "hangup": [],
    "transfer": [],
    "voicemail": [],
    "human_escalation": [],
    "goto": [],
    # single-output pass-through
    "play_audio": ["out"],
    "set_variable": ["out"],
    "collect_info": ["out"],
    "ai_conversation": ["out"],
    "call_workflow": ["out"],
    # fixed branch sets
    "condition": ["true", "false"],
    "time_condition": ["match", "no_match"],
    "api_call": ["success", "error"],
    "email_notify": ["success", "error"],
    "menu": list(_MENU_HANDLES),
    # dynamic (representative defaults; use output_handles(node) for the real set)
    "dtmf_input": [*_DIGITS, "timeout"],
    "intent_detection": ["other"],
}


def output_handles(node: Any) -> list[str]:
    """Return the output-handle ids for a *specific* node instance.

    Dynamic per-config handling:
      * ``dtmf_input``: max_digits==1 -> ["0".."9","timeout"]; >1 -> ["done","timeout"].
      * ``intent_detection``: each ``intents`` key + ``fallback_intent`` (deduped).
    All other types come straight from :data:`HANDLE_VOCAB`.
    """
    node_type = node.type
    if node_type == "dtmf_input":
        if node.config.max_digits == 1:
            return [*_DIGITS, "timeout"]
        return ["done", "timeout"]
    if node_type == "intent_detection":
        handles = list(node.config.intents.keys())
        if node.config.fallback_intent not in handles:
            handles.append(node.config.fallback_intent)
        return handles
    return list(HANDLE_VOCAB[node_type])
