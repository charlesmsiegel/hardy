"""What the chat prompt says about imports and file shape.

The failing run guessed granular module paths for fifteen calls and put 51
saves through one `Main.lean`; the succeeding run wrote `import Mathlib` in
five small files. The prompt now says to do the second.
"""

from __future__ import annotations

from hardy.prompts import CHAT_SYSTEM_PROMPT


def test_the_prompt_says_to_import_mathlib_and_nothing_narrower() -> None:
    assert "Write `import Mathlib` and nothing narrower." in CHAT_SYSTEM_PROMPT


def test_the_prompt_says_to_build_a_proof_as_several_small_files() -> None:
    assert "never as one growing `Main.lean`" in CHAT_SYSTEM_PROMPT
    assert "checked with `check_lean` and saved once it is green" in CHAT_SYSTEM_PROMPT


def test_the_prompt_still_tells_the_model_to_look_modules_up() -> None:
    """The workspace-module case still needs `search_modules`."""
    assert "search_modules" in CHAT_SYSTEM_PROMPT
