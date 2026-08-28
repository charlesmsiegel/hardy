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


def test_the_tool_bullet_does_not_offer_search_modules_as_a_narrowing_step() -> None:
    """Brutal review pass 3, finding #3: the tool bullet used to say
    `search_modules` "finds the module to `import` for a name you have in
    mind", which reads as license to import the narrower path it returns --
    exactly what the "import `Mathlib` and nothing narrower" paragraph below
    it forbids, and what the tool's own description (`search_tools.py`)
    already disclaims. The bullet now names the same rule the rest of the
    prompt gives: `search_modules` confirms a path exists; from Mathlib,
    import `Mathlib` whole."""
    assert (
        "`search_modules` confirms a module path exists and finds a workspace or "
        "shared-library module's path — from Mathlib, import `Mathlib` whole;"
    ) in CHAT_SYSTEM_PROMPT
    assert "finds the module to `import` for a name you have in mind" not in CHAT_SYSTEM_PROMPT


def test_inspect_declarations_is_not_said_to_settle_mathlib_outright() -> None:
    """Brutal review pass 3, finding #3: the prompt used to say
    `inspect_declarations` "settles 'does Mathlib have this' outright", which
    contradicts `SPELLINGS_HINT`'s "that is evidence about the spellings, not
    about the result" for the very same answer. Softened to what is actually
    true: a finished batch settles which spellings exist, not whether the
    result is absent from Mathlib."""
    assert (
        "it asks Lean directly whether names exist and hands back their real "
        "signatures, so a batch that finishes settles those spellings."
    ) in CHAT_SYSTEM_PROMPT
    assert 'so it settles "does Mathlib have this" outright' not in CHAT_SYSTEM_PROMPT


def test_the_prompt_no_longer_tells_the_model_to_search_before_every_mathlib_import() -> None:
    """Finding #6 (second brutal review): under `import Mathlib` and nothing
    narrower, the only Mathlib import needs no lookup, so "call
    `search_modules` before you write a Mathlib import" is advice for a
    workflow the rest of this same prompt forbids. `search_modules` is for
    a failing import, not a gate in front of every one."""
    assert "Call `search_modules` before you write a Mathlib import" not in CHAT_SYSTEM_PROMPT
    assert (
        "If an import fails, look the module up with `search_modules` rather than "
        "concluding the toolchain is broken."
    ) in CHAT_SYSTEM_PROMPT
