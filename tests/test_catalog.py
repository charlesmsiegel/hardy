from __future__ import annotations

from hardy import catalog


def test_the_catalog_lists_claude_models_only():
    """Hardy reaches Claude through the agent SDK, so that is the whole roster
    until the Codex backend lands."""
    assert [entry.identifier for entry in catalog.available()][0] == "claude-opus-5"
    assert all(entry.backend == catalog.CLAUDE for entry in catalog.available())


def test_a_listed_model_keeps_its_note():
    assert "1M context" in catalog.describe("claude-opus-5").note


def test_an_unlisted_identity_is_still_accepted():
    """Typing one in is the escape hatch for a release the catalog has missed."""
    entry = catalog.describe("  claude-something-new ")
    assert entry.identifier == "claude-something-new"
    assert entry.backend == catalog.CLAUDE
