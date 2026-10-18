"""Transport facts used when configuring and constructing application sessions.

These facts describe Hardy's adapters, not a provider's current availability or
model capabilities. Interactive sessions and staged SDK runs remain distinct;
the latter can use Codex without making it an interactive backend.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Transport(StrEnum):
    """The protocol and turn-loop owner an application adapter constructs."""

    AGENT_SDK = "agent-sdk"
    MESSAGES = "messages"
    CHAT_COMPLETIONS = "chat-completions"
    CODEX_SDK = "codex-sdk"


@dataclass(frozen=True)
class Backend:
    """A named transport, its model roster and credential description."""

    name: str
    model_family: str
    authentication: str
    transport: Transport

    @property
    def interactive(self) -> bool:
        """Whether the application offers this adapter for ordinary sessions."""
        return self.transport != Transport.CODEX_SDK

    @property
    def provider_budget(self) -> bool:
        """Whether Hardy owns the call boundary that enforces provider spend."""
        return self.transport in {Transport.MESSAGES, Transport.CHAT_COMPLETIONS}

    @property
    def requires_model(self) -> bool:
        """Whether the endpoint must supply an explicit, unguessed identity."""
        return self.transport == Transport.CHAT_COMPLETIONS


BACKENDS = (
    Backend("claude", "claude", "Claude Code subscription", Transport.AGENT_SDK),
    Backend("api", "claude", "Anthropic API key (metered)", Transport.MESSAGES),
    Backend("openai", "openai", "OpenAI-compatible API key (metered)", Transport.CHAT_COMPLETIONS),
    Backend("openrouter", "openrouter", "OpenRouter API key (metered)", Transport.CHAT_COMPLETIONS),
    Backend("codex", "codex", "codex", Transport.CODEX_SDK),
)


def find(name: str) -> Backend | None:
    """Resolve an adapter name without guessing how an unknown transport works."""
    return next((backend for backend in BACKENDS if backend.name == name), None)
