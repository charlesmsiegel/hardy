from __future__ import annotations

import pytest

from hardy.app import catalog


def test_the_claude_backend_lists_claude_models_only():
    """Hardy reaches Claude through the agent SDK, so that is the whole roster."""
    listed = catalog.available("claude")
    assert [entry.identifier for entry in listed][0] == "claude-opus-5"
    assert all(entry.backend == catalog.CLAUDE for entry in listed)


def test_the_api_backend_lists_the_same_claude_roster():
    """The Messages API is Anthropic's own front door: every identity the
    subscription backend can name, the key-authenticated one can too."""
    assert catalog.available("api") == catalog.available("claude")


def test_the_codex_backend_lists_no_claude_model():
    """Issue #28: a Codex session was offered every Claude identity and found out
    at the next request that none of them could be served."""
    assert catalog.available("codex") == []


def test_curated_entries_disclose_their_source_and_unknown_provider_capabilities():
    entry = catalog.describe("claude-opus-5")
    assert entry.source == "curated"
    assert entry.provenance == "Hardy bundled catalog"
    assert entry.capabilities is None
    assert "unknown" in entry.note
    assert "1M" not in entry.note


def test_an_unlisted_identity_is_still_accepted():
    """Typing one in is the escape hatch for a release the catalog has missed."""
    entry = catalog.describe("  claude-something-new ")
    assert entry.identifier == "claude-something-new"
    assert entry.backend is None
    assert entry.source == "configured"
    assert entry.capabilities is None


@pytest.mark.parametrize("backend", ["claude", "api"])
def test_a_claude_backend_is_not_refused_a_claude_model(backend):
    assert catalog.refusal("claude-opus-5", backend) is None


def test_the_codex_backend_is_refused_a_claude_model_with_the_reason():
    reason = catalog.refusal("claude-opus-5", "codex")
    assert reason is not None
    assert "claude-opus-5" in reason and "codex" in reason


@pytest.mark.parametrize("backend", ["claude", "api", "codex"])
def test_an_unlisted_identity_is_never_refused(backend):
    """The catalog is hand-maintained and the transport is the authority on a
    release it has not caught up with, so only a *listed* identity can be
    refused here. An unlisted one still fails at the provider, as it always
    has -- and that is the escape hatch working, not the defect."""
    assert catalog.refusal("something-the-catalog-lacks", backend) is None
