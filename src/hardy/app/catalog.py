"""Bundled model suggestions and their provenance, not an availability probe.

The menu filters suggestions by transport family (issue #28), but neither
catalog membership nor a configured identity proves account access or model
capabilities. This catalog performs no live discovery. Unlisted identities
remain usable as explicit input; the provider decides whether it can serve them.
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
    note: str = "capabilities unknown"
    backend: str | None = CLAUDE
    source: str = "curated"
    provenance: str = "Hardy bundled catalog"
    capabilities: tuple[str, ...] | None = None


# Preserve existing suggestions without asserting their current availability,
# performance, or context limits. Only the configured provider can establish those.
CATALOG: tuple[ModelInfo, ...] = (
    ModelInfo("claude-opus-5"),
    ModelInfo("claude-opus-4-8"),
    ModelInfo("claude-sonnet-5"),
    ModelInfo("claude-haiku-4-5"),
)


def family(backend: str) -> str:
    """The model family the transport `backend` can carry."""
    return SERVES.get(backend, backend)


def find(identifier: str) -> ModelInfo | None:
    target = identifier.strip().lower()
    return next((entry for entry in CATALOG if entry.identifier.lower() == target), None)


def describe(identifier: str) -> ModelInfo:
    """Describe a listed or configured identity without inferring its capabilities."""
    return find(identifier) or ModelInfo(identifier.strip(), "not in catalog; capabilities unknown",
                                        backend=None, source="configured", provenance="Explicit model identity")


def available(backend: str) -> list[ModelInfo]:
    """Family-compatible suggestions; account and endpoint availability are unverified."""
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
