from __future__ import annotations

import io
import json

import pytest

from hardy import catalog


def test_claude_identities_select_the_anthropic_backend():
    assert catalog.backend_for("claude-opus-5") == catalog.ANTHROPIC
    assert catalog.backend_for("claude-sonnet-5") == catalog.ANTHROPIC
    # Unlisted Claude releases still route correctly rather than falling through.
    assert catalog.backend_for("claude-something-new") == catalog.ANTHROPIC


def test_everything_else_stays_openai_compatible():
    assert catalog.backend_for("gpt-5.1") == catalog.OPENAI
    assert catalog.backend_for("meta-llama/Llama-3.3-70B") == catalog.OPENAI
    assert catalog.backend_for(None) == catalog.OPENAI


def test_describing_an_unlisted_model_still_yields_a_usable_entry():
    entry = catalog.describe("  Local-Model-7B ")
    assert entry.identifier == "Local-Model-7B"
    assert entry.backend == catalog.OPENAI


def test_discovery_needs_a_key_and_never_raises(monkeypatch: pytest.MonkeyPatch):
    def explode(*args, **kwargs):
        raise OSError("no network")

    monkeypatch.setattr(catalog.urllib.request, "urlopen", explode)
    assert catalog.discover(catalog.OPENAI, "", "https://example.invalid/v1") == []
    assert catalog.discover(catalog.OPENAI, "key", "https://example.invalid/v1") == []


def test_discovery_reads_the_provider_model_list(monkeypatch: pytest.MonkeyPatch):
    seen: dict[str, object] = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["headers"] = dict(request.headers)
        return io.BytesIO(json.dumps({"data": [{"id": "local-b"}, {"id": "local-a"}]}).encode())

    monkeypatch.setattr(catalog.urllib.request, "urlopen", fake_urlopen)
    assert catalog.discover(catalog.ANTHROPIC, "secret", catalog.ANTHROPIC_BASE_URL) == ["local-a", "local-b"]
    assert seen["url"] == "https://api.anthropic.com/v1/models"
    assert seen["headers"]["X-api-key"] == "secret"


def test_merge_adds_unlisted_models_without_duplicating_catalog_entries():
    merged = catalog.merge({catalog.OPENAI: ["gpt-5.1", "local-7b"], catalog.ANTHROPIC: ["claude-opus-5"]})
    identifiers = [entry.identifier for entry in merged]
    assert identifiers.count("gpt-5.1") == 1
    assert identifiers.count("claude-opus-5") == 1
    assert "local-7b" in identifiers
    # Claude first, so the two providers are visually separable in the listing.
    backends = [entry.backend for entry in merged]
    assert backends == sorted(backends, key=lambda name: catalog.BACKENDS.index(name))


def test_a_local_server_is_probed_without_credentials(monkeypatch: pytest.MonkeyPatch):
    """A keyless local endpoint is the one case where its catalog is otherwise
    unknowable, and it is exactly the case that needs no Authorization header."""
    seen: dict[str, object] = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["auth"] = request.headers.get("Authorization")
        return io.BytesIO(json.dumps({"data": [{"id": "local-7b"}]}).encode())

    monkeypatch.setattr(catalog.urllib.request, "urlopen", fake_urlopen)
    assert catalog.discover(catalog.OPENAI, "", "http://localhost:8000/v1") == ["local-7b"]
    assert seen["url"] == "http://localhost:8000/v1/models"
    assert seen["auth"] is None


def test_hosted_and_anthropic_discovery_still_need_a_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(catalog.urllib.request, "urlopen", lambda *a, **k: pytest.fail("probed without a key"))
    assert catalog.discover(catalog.OPENAI, "", "https://api.openai.com/v1") == []
    assert catalog.discover(catalog.ANTHROPIC, "", "http://localhost:8000/v1") == []
