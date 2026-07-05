from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@runtime_checkable
class LLMProvider(Protocol):
    def stream_chat(self, messages: list[ChatMessage]) -> AsyncIterator[str]:
        """トークン（文字列断片）を逐次 yield する非同期ジェネレータを返す。"""
        ...
