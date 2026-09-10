"""Which models Hardy knows about, and which backend can carry each one.

Hardy reaches Claude through the Claude Code agent SDK, so a model is usable
when the subscription behind that CLI can reach it. There is no key to probe a
provider with and no `/models` endpoint in play, which is why this list is
hand-maintained and why an identifier not on it is still accepted: typing one in
is the escape hatch for a release this file has not caught up with.

The list is read through the backend a session runs on, never whole. Every
entry names the family it belongs to and every transport names the family it
carries, and the `/model` menu shows only where the two agree. Issue #28 is
what the unfiltered list did: a Codex session was offered four Claude
identities, and the one it picked failed at the next provider request rather
than at selection.
"""

from __future__ import annotations

from dataclasses import dataclass

CLAUDE = "claude"
CODEX = "codex"

#: The model family each transport carries. `claude` and `api` both reach
#: Anthropic's models -- one through the agent SDK, one through the Messages
#: API -- so they read the same roster; `codex` reaches OpenAI's and none of
#: these. A transport not named here carries a family of its own name, which
#: lists nothing and refuses every catalogued entry: the safe answer for a
#: backend this file has never heard of.
SERVES: dict[str, str] = {"claude": CLAUDE, "api": CLAUDE, "codex": CODEX}


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


def family(backend: str) -> str:
    """The model family the transport `backend` can carry."""
    return SERVES.get(backend, backend)


def find(identifier: str) -> ModelInfo | None:
    target = identifier.strip().lower()
    return next((entry for entry in CATALOG if entry.identifier.lower() == target), None)


def describe(identifier: str) -> ModelInfo:
    """The catalog entry for a model, inventing one for identities we do not list."""
    return find(identifier) or ModelInfo(identifier.strip(), "not in the catalog")


def available(backend: str) -> list[ModelInfo]:
    """The catalogued models the transport `backend` can serve."""
    served = family(backend)
    return [entry for entry in CATALOG if entry.backend == served]


def refusal(identifier: str, backend: str) -> str | None:
    """Why `backend` cannot switch to `identifier`, or None when it can.

    Only a catalogued identity can be refused: an unlisted one is the escape
    hatch, and the transport is the authority on a release this file has not
    caught up with. So the answer is given at selection for everything this
    file knows, and left to the provider for everything it does not.
    """
    entry = find(identifier)
    if entry is None or entry.backend == family(backend):
        return None
    return (
        f"{entry.identifier} is a {entry.backend} model, and the {backend} backend cannot serve it."
    )
