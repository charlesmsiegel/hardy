"""What the chat prompt says about imports and file shape.

The failing run guessed granular module paths for fifteen calls and put 51
saves through one `Main.lean`; the succeeding run wrote `import Mathlib` in
five small files. The prompt now says to do the second.
"""

from __future__ import annotations

from hardy.prompts import CHAT_SYSTEM_PROMPT


def test_the_prompt_says_to_import_mathlib_and_nothing_narrower() -> None:
    """Scoped to Mathlib, not to every import: §6's decomposition
    instruction has a model write `import Helpers` for its own workspace
    module, and that must not read as forbidden by this sentence."""
    assert (
        "From Mathlib, write `import Mathlib` and nothing narrower; your own "
        "workspace modules are imported by their module name (`import Group.Sylow`)."
    ) in CHAT_SYSTEM_PROMPT


def test_the_prompt_says_to_build_a_proof_as_several_small_files() -> None:
    assert "never as one growing `Main.lean`" in CHAT_SYSTEM_PROMPT
    assert "checked with `check_lean` and saved once it is green" in CHAT_SYSTEM_PROMPT


def test_the_prompt_still_tells_the_model_to_look_modules_up() -> None:
    """The workspace-module case still needs `search_modules`."""
    assert "search_modules" in CHAT_SYSTEM_PROMPT
