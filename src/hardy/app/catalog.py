"""Which Claude models Hardy knows about.

Hardy reaches Claude through the Claude Code agent SDK, so a model is usable
when the subscription behind that CLI can reach it. There is no key to probe a
provider with and no `/models` endpoint in play, which is why this list is
hand-maintained and why an identifier not on it is still accepted: typing one in
is the escape hatch for a release this file has not caught up with.
"""

from __future__ import annotations

from dataclasses import dataclass

CLAUDE = "claude"


@dataclass(frozen=True)
class ModelInfo:
    identifier: str
    note: str = ""
    backend: str = CLAUDE


# Claude identifiers are exact and complete as written: never append a date suffix.
CATALOG: tuple[ModelInfo, ...] = (
    ModelInfo("claude-opus-5", "strongest reasoning and long-horizon agentic work; 1M context"),
    ModelInfo("claude-opus-4-8", "previous Opus; 1M context"),
    ModelInfo("claude-sonnet-5", "near-Opus quality at lower cost; 1M context"),
    ModelInfo("claude-haiku-4-5", "fastest and cheapest; 200K context"),
)


def find(identifier: str) -> ModelInfo | None:
    target = identifier.strip().lower()
    return next((entry for entry in CATALOG if entry.identifier.lower() == target), None)


def describe(identifier: str) -> ModelInfo:
    """The catalog entry for a model, inventing one for identities we do not list."""
    return find(identifier) or ModelInfo(identifier.strip(), "not in the catalog")


def available() -> list[ModelInfo]:
    return list(CATALOG)
