"""Which models Hardy knows about, and which backend each one needs.

The catalog is the offline answer to "what can I pick?". It is deliberately
short and hand-maintained: it names models Hardy has been pointed at, not every
model a provider sells. `discover` asks the provider for the authoritative list
when a key is available, so a stale entry here is a cosmetic problem rather than
a correctness one, and a local OpenAI-compatible server still shows up.
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from dataclasses import dataclass

ANTHROPIC = "anthropic"
OPENAI = "openai"
BACKENDS = (ANTHROPIC, OPENAI)

ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"


@dataclass(frozen=True)
class ModelInfo:
    identifier: str
    backend: str
    note: str = ""

    @property
    def provider(self) -> str:
        return "Claude (Anthropic Messages API)" if self.backend == ANTHROPIC else "GPT / OpenAI-compatible"


# Claude identifiers are exact and complete as written: never append a date suffix.
CATALOG: tuple[ModelInfo, ...] = (
    ModelInfo("claude-opus-5", ANTHROPIC, "strongest reasoning and long-horizon agentic work; 1M context"),
    ModelInfo("claude-opus-4-8", ANTHROPIC, "previous Opus; 1M context"),
    ModelInfo("claude-sonnet-5", ANTHROPIC, "near-Opus quality at lower cost; 1M context"),
    ModelInfo("claude-haiku-4-5", ANTHROPIC, "fastest and cheapest; 200K context"),
    ModelInfo("gpt-5.1", OPENAI, "OpenAI flagship"),
    ModelInfo("gpt-5", OPENAI, ""),
    ModelInfo("gpt-5-mini", OPENAI, "cheaper and faster"),
    ModelInfo("gpt-4.1", OPENAI, ""),
)


def backend_for(identifier: str | None) -> str:
    """The backend a model identity implies. Unknown identities are OpenAI-compatible.

    That default is what keeps `base_url` useful: anything served by a local
    llama.cpp, vLLM, or router endpoint speaks the OpenAI wire format.
    """
    if not identifier:
        return OPENAI
    known = find(identifier)
    if known:
        return known.backend
    return ANTHROPIC if identifier.strip().lower().startswith("claude-") else OPENAI


def find(identifier: str) -> ModelInfo | None:
    target = identifier.strip().lower()
    return next((entry for entry in CATALOG if entry.identifier.lower() == target), None)


def describe(identifier: str) -> ModelInfo:
    """The catalog entry for a model, inventing one for identities we do not list."""
    return find(identifier) or ModelInfo(identifier.strip(), backend_for(identifier), "not in the catalog")


def _get_json(url: str, headers: dict[str, str], timeout: float) -> dict:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def discover(backend: str, api_key: str, base_url: str, *, timeout: float = 10.0) -> list[str]:
    """Ask one provider which models it serves. Returns [] rather than raising.

    A missing key, an offline machine, or an endpoint without /models is an
    ordinary outcome here: the caller falls back to the static catalog.
    """
    if not api_key:
        return []
    if backend == ANTHROPIC:
        url, headers = base_url.rstrip("/") + "/models", {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION}
    else:
        url, headers = base_url.rstrip("/") + "/models", {"Authorization": f"Bearer {api_key}"}
    try:
        payload = _get_json(url, headers, timeout)
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError, KeyError):
        return []
    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return []
    return sorted({str(item["id"]) for item in entries if isinstance(item, dict) and item.get("id")})


def merge(discovered: dict[str, list[str]]) -> list[ModelInfo]:
    """The catalog, plus anything a provider reported that the catalog omits."""
    models = list(CATALOG)
    known = {entry.identifier.lower() for entry in models}
    for backend, identifiers in discovered.items():
        for identifier in identifiers:
            if identifier.lower() not in known:
                known.add(identifier.lower())
                models.append(ModelInfo(identifier, backend, "reported by the provider"))
    order = {backend: index for index, backend in enumerate(BACKENDS)}
    return sorted(models, key=lambda entry: (order.get(entry.backend, len(BACKENDS)), entry.identifier))
