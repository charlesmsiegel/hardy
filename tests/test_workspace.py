from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from hardy.workspace import (
    ImportCycle,
    WorkspacePathError,
    assumptions,
    build_order,
    declarations,
    dependents,
    external_imports,
    internal_imports,
    module_name,
    module_path,
    name_aliases,
    parse_imports,
    safe_relative,
    statements,
    strip_comments,
    unreadable_assumptions,
)


def test_module_name_maps_directories_to_dots():
    assert module_name(PurePosixPath("Main.lean")) == "Main"
    assert module_name(PurePosixPath("Group/Sylow.lean")) == "Group.Sylow"


def test_module_path_is_the_inverse():
    assert module_path("Group.Sylow") == PurePosixPath("Group/Sylow.lean")


@pytest.mark.parametrize(
    "bad",
    [
        "/absolute/Main.lean",
        "../escape.lean",
        "Group/../../out.lean",
        "notes.txt",
        "",
        "9Bad.lean",
        "Group/.hidden.lean",
    ],
)
def test_safe_relative_rejects_paths_that_are_not_lean_modules(bad):
    with pytest.raises(WorkspacePathError):
        safe_relative(bad)


def test_safe_relative_normalises_separators():
    assert safe_relative("Group\\Sylow.lean") == PurePosixPath("Group/Sylow.lean")


def test_parse_imports_reads_only_the_header():
    source = (
        "-- a leading comment\n"
        "/- a block\n   comment -/\n"
        "import Mathlib\n"
        "import Group.Sylow\n"
        "\n"
        "theorem t : True := by\n"
        '  have s := "import NotReally"\n'
        "  trivial\n"
    )
    assert parse_imports(source) == ("Mathlib", "Group.Sylow")


def test_parse_imports_stops_at_the_first_declaration():
    assert parse_imports("import A\ntheorem t : True := trivial\nimport B\n") == ("A",)


TREE = {
    "Basic": "import Mathlib\ndef a := 1\n",
    "Group.Sylow": "import Basic\ndef b := a\n",
    "Main": "import Group.Sylow\nimport Mathlib\ndef c := b\n",
    "Scratch": "import Mathlib\ndef d := 2\n",
}


def test_internal_imports_ignores_external_ones():
    assert internal_imports(TREE["Main"], TREE) == ("Group.Sylow",)


def test_build_order_puts_dependencies_first_and_omits_the_unrelated():
    assert build_order(TREE, ["Main"]) == ("Basic", "Group.Sylow", "Main")


def test_build_order_is_deterministic_across_independent_modules():
    assert build_order(TREE, ["Main", "Scratch"]) == (
        "Basic",
        "Group.Sylow",
        "Main",
        "Scratch",
    )


def test_dependents_are_transitive_and_exclude_the_module_itself():
    assert dependents(TREE, "Basic") == frozenset({"Group.Sylow", "Main"})
    assert dependents(TREE, "Main") == frozenset()


def test_a_cycle_is_refused_by_name():
    cyclic = {"A": "import B\n", "B": "import A\n"}
    with pytest.raises(ImportCycle) as error:
        build_order(cyclic, ["A"])
    assert "A" in str(error.value) and "B" in str(error.value)


def test_a_missing_internal_import_is_simply_external():
    assert internal_imports("import Nowhere\n", TREE) == ()


def test_declarations_separate_theorems_from_lemmas():
    source = "theorem one : True := trivial\nlemma helper : True := trivial\ndef d := 1\n"
    found = declarations(source)
    assert found["theorem"] == ("one",)
    assert found["lemma"] == ("helper",)


def test_a_namespaced_declaration_is_reported_once_by_its_qualified_name():
    """One theorem must not read as two, or it would owe two writeups."""
    source = "namespace Hardy\ntheorem one : True := trivial\nend Hardy\n"
    assert declarations(source)["theorem"] == ("Hardy.one",)


def test_nested_namespaces_compose():
    source = "namespace A\nnamespace B\ntheorem one : True := trivial\nend B\nend A\n"
    assert declarations(source)["theorem"] == ("A.B.one",)


def test_a_namespace_that_ended_no_longer_qualifies():
    source = "namespace A\ntheorem one : True := trivial\nend A\ntheorem two : True := trivial\n"
    assert declarations(source)["theorem"] == ("A.one", "two")


def test_name_aliases_offer_the_bare_name_of_a_qualified_one():
    assert name_aliases("Hardy.one") == ("Hardy.one", "one")
    assert name_aliases("one") == ("one",)
    # Only the last component: `Group.one` is not resolvable from the root.
    assert name_aliases("Hardy.Group.one") == ("Hardy.Group.one", "one")


def test_declarations_see_through_attributes_and_modifiers():
    source = "@[simp]\nprivate theorem one : True := trivial\n"
    assert declarations(source)["theorem"] == ("one",)


def test_private_declarations_are_reported_as_a_subset_of_the_others():
    """A caller that has to *name* a declaration from another module cannot
    name a private one -- Lean mangles it out of reach."""
    source = (
        "@[simp]\nprivate theorem hidden : True := trivial\n"
        "private lemma step : True := trivial\n"
        "protected theorem shown : True := trivial\n"
        "theorem plain : True := trivial\n"
    )
    found = declarations(source)
    assert found["theorem"] == ("hidden", "shown", "plain")
    assert found["lemma"] == ("step",)
    # `protected` changes how a name is written, not who may write it.
    assert found["private"] == ("hidden", "step")


def test_a_private_declaration_inside_a_namespace_is_reported_qualified():
    source = "namespace Hardy\nprivate lemma step : True := trivial\nend Hardy\n"
    assert declarations(source)["private"] == ("Hardy.step",)


def test_a_declaration_named_private_is_not_a_private_declaration():
    """`theorem private_key` shares a prefix with the modifier and nothing else."""
    source = "theorem privateThing : True := trivial\nlemma private_step : True := trivial\n"
    assert declarations(source)["private"] == ()


def test_a_trailing_comment_does_not_hide_an_import():
    """`import Basic -- shared definitions` is a legal header line.

    Failing to read it would drop the dependency, and a dependency Hardy
    cannot see is one it will not rebuild when the imported file changes.
    """
    assert parse_imports("import Basic -- shared definitions\nimport Mathlib\n") == (
        "Basic",
        "Mathlib",
    )
    assert parse_imports("import Basic /- inline -/\n") == ("Basic",)


def test_a_bare_end_closes_the_innermost_scope():
    source = "namespace Hardy\ntheorem one : True := trivial\nend\ntheorem two : True := trivial\n"
    assert declarations(source)["theorem"] == ("Hardy.one", "two")


def test_a_section_does_not_qualify_a_name_and_can_be_ended_bare():
    source = (
        "namespace Hardy\n"
        "section\n"
        "theorem one : True := trivial\n"
        "end\n"
        "theorem two : True := trivial\n"
        "end Hardy\n"
        "theorem three : True := trivial\n"
    )
    assert declarations(source)["theorem"] == ("Hardy.one", "Hardy.two", "three")


def test_a_named_end_closes_what_was_left_open_inside_it():
    source = "namespace A\nsection\ntheorem one : True := trivial\nend A\ntheorem two : True := trivial\n"
    assert declarations(source)["theorem"] == ("A.one", "two")


def test_a_leading_prelude_does_not_end_the_header():
    """`prelude` opens a module before its imports, suppressing the implicit
    `import Init`. Reading it as the end of the header would drop every import
    after it, and a dependency Hardy cannot see is one it will not rebuild."""
    assert parse_imports("prelude\nimport Basic\nimport Mathlib\n") == ("Basic", "Mathlib")
    assert parse_imports("/- doc -/\nprelude\nimport Basic\n") == ("Basic",)


def test_prelude_after_an_import_is_still_the_end_of_the_header():
    assert parse_imports("import Basic\nprelude\nimport Other\n") == ("Basic",)


def test_a_declaration_named_like_prelude_still_ends_the_header():
    assert parse_imports("preludeThing\nimport Basic\n") == ()


def test_nested_block_comments_close_at_the_right_place():
    """`/- a /- b -/ c -/` is one comment, not one ending at the first `-/`.

    Closing early would leave the tail of the comment looking like code, stop
    the header scan, and drop the imports after it.
    """
    source = "/- outer /- inner -/ still outer -/\nimport Basic\nimport Mathlib\n"
    assert parse_imports(source) == ("Basic", "Mathlib")


def test_a_comment_marker_inside_a_string_is_not_a_comment():
    """The literal is blanked, so the `--` cannot start a comment and cannot
    swallow the rest of the line either. What matters is that nothing after it
    on the line is lost."""
    source = 'def s := "-- not a comment"\n'
    stripped = strip_comments(source)
    assert stripped.startswith('def s := ')
    assert len(stripped) == len(source)
    assert '-- not a comment' not in stripped


def test_a_declaration_inside_a_string_literal_is_not_a_declaration():
    """It is not one, and a caller that has to name every declaration would ask
    Lean about something that does not exist and refuse the file forever."""
    source = (
        'def blurb : String := "\ntheorem fake : True := trivial\n"\n\n'
        'theorem real : True := trivial\n'
    )
    assert declarations(source)["theorem"] == ("real",)


def test_blanking_a_string_keeps_the_lines_lined_up():
    source = 'def s := "one\ntwo\nthree"\ntheorem real : True := trivial\n'
    assert len(strip_comments(source).splitlines()) == len(source.splitlines())


def test_a_bare_quote_inside_a_hash_raw_string_does_not_end_it():
    """`r#"..."#` exists precisely so the body may contain a `"`."""
    source = (
        'def d := r#"a " b\ntheorem fake : True := trivial\n"#\n\n'
        'theorem real : True := trivial\n'
    )
    assert declarations(source)["theorem"] == ("real",)


def test_a_raw_string_ending_in_a_backslash_ends_there():
    """A backslash is an ordinary character in a raw string, so it does not
    escape the closing quote. Reading it as an escape ran the literal on and
    swallowed the code after it -- which is how a `sorry` slipped past the
    verifier's hole scan."""
    source = 'def d := r"a\\"\ntheorem real : True := trivial\n'
    assert declarations(source)["theorem"] == ("real",)


def test_an_r_ending_an_identifier_does_not_open_a_raw_string():
    source = 'def forr := "x"\ntheorem real : True := trivial\n'
    assert declarations(source)["theorem"] == ("real",)


def test_an_escaped_quote_does_not_end_a_string_early():
    """Otherwise the text after it would read as code again."""
    source = 'def s := "a \\" theorem sneaky : True"\ntheorem real : True := trivial\n'
    assert declarations(source)["theorem"] == ("real",)


def test_strip_comments_keeps_the_lines_lined_up():
    assert len(strip_comments("import A -- trailing\nimport B\n").splitlines()) == 2


def test_a_declaration_behind_a_leading_doc_comment_is_seen():
    """Lean reads the comment as whitespace. A theorem it hid would never be
    recorded, and so would never owe a writeup."""
    source = "/-- explanation -/ theorem result : True := by trivial\n"
    assert declarations(source)["theorem"] == ("result",)


def test_a_declaration_inside_a_block_comment_is_not_seen():
    source = "/-\ntheorem commented : True := trivial\n-/\ntheorem real : True := trivial\n"
    assert declarations(source)["theorem"] == ("real",)


def test_unicode_declaration_names_are_recognised():
    """Lean identifiers are Unicode; an ASCII pattern would not see these."""
    assert declarations("theorem α : True := trivial\n")["theorem"] == ("α",)
    assert declarations("lemma h₁ : True := trivial\n")["lemma"] == ("h₁",)
    assert declarations("namespace Γ\ntheorem δ : True := trivial\nend Γ\n")["theorem"] == ("Γ.δ",)


def test_a_name_on_the_line_after_its_keyword_is_found():
    assert declarations("theorem\n  result : True := trivial\n")["theorem"] == ("result",)


def test_a_split_declaration_is_attributed_to_its_keyword_scope():
    source = "namespace A\ntheorem\n  one : True := trivial\nend A\n"
    assert declarations(source)["theorem"] == ("A.one",)


def test_an_escaped_declaration_name_is_recognised():
    """`theorem «first result»` compiles -- verified against Lean 4.33.0-rc1 --
    so a pattern that could not see it would let that theorem escape the
    writeup gate entirely."""
    assert declarations("theorem «first result» : True := by trivial\n")["theorem"] == (
        "«first result»",
    )
    source = "namespace A\ntheorem «two words» : True := trivial\nend A\n"
    assert declarations(source)["theorem"] == ("A.«two words»",)


def test_a_module_header_and_public_import_carry_the_dependency():
    """Lean's module system: `module` opens the file and `public import` is an
    ordinary import. Both were ending the header scan, dropping the graph."""
    assert parse_imports("module\npublic import Basic\nimport Mathlib\n") == (
        "Basic",
        "Mathlib",
    )
    assert parse_imports("module\nmeta import Basic\n") == ("Basic",)
    assert parse_imports("public import Basic\n") == ("Basic",)


def test_import_all_is_still_a_dependency():
    assert parse_imports("module\npublic import all Basic\n") == ("Basic",)


def test_external_imports_are_what_is_left_over():
    assert external_imports(TREE["Main"], TREE) == ("Mathlib",)
    assert external_imports(TREE["Basic"], TREE) == ("Mathlib",)


def test_an_assumption_with_its_type_on_the_next_line_is_still_read():
    """`axiom trusted :` with the type below is ordinary Lean, and a
    line-anchored read returned nothing at all — so the one place Hardy compares
    a declared statement against the approved one was skipped, and the axiom
    passed on its name alone."""
    assert assumptions("axiom trusted :\n  False\n") == (("trusted", "False"),)


def test_an_assumption_wearing_binders_or_universes_is_refused_not_skipped():
    """`assumptions` skips a line it cannot match, which is the wrong direction
    for a gate: an axiom wearing binders or universe parameters was offered for
    approval by nobody and refused by nobody.

    Matching those shapes instead is worse, and was tried. Lean gives
    `axiom Sneaky (P : Prop) : P` the type `∀ P : Prop, P`, not the `P` after
    the colon, so comparing the tail accepts it against an approval of `P`.
    Nothing reconstructs a type here; the line is reported unreadable and the
    save is refused."""
    for source in (
        "axiom Sneaky (n : Nat) : False",
        "axiom Sneaky {α : Type} : False",
        "axiom Sneaky (P : Prop) : P",
        "axiom Sneaky (f : Nat → (Nat × Nat)) : False",
        "axiom Sneaky.{u} : Sort u",
    ):
        assert assumptions(source) == (), source
        assert unreadable_assumptions(source) == (source,), source


def test_a_readable_assumption_is_not_reported_unreadable():
    """The other half: what the approval flow does produce must pass cleanly,
    including a statement wrapped onto the next line, and a keyword that is
    part of a name or sits inside a string must not be read as a declaration
    at all."""
    for source in (
        "axiom Ok : True",
        "private axiom Ok : True",
        "axiom Ok :\n  ∀ n : Nat, n = n\n",
        "theorem axiom? : True := trivial",
        'def s := "axiom Foo : Bar"',
        "def «axiom» : Nat := 1",
    ):
        assert unreadable_assumptions(source) == (), source


def test_a_wrapped_assumption_is_gathered_rather_than_truncated():
    """Truncating at the newline failed a comparison that should have passed."""
    source = "axiom trusted : ∀ n : Nat,\n  n = n\n"
    assert assumptions(source) == (("trusted", "∀ n : Nat, n = n"),)


def test_gathering_an_assumption_stops_at_the_next_declaration():
    """Over-reading would append a theorem to the statement and refuse a save
    that should have passed."""
    assert assumptions("axiom a :\n  True\ntheorem T : True := trivial\n") == (("a", "True"),)
    assert assumptions("axiom a :\n  True\n@[simp] theorem T : True := trivial\n") == (("a", "True"),)


def test_a_root_qualified_declaration_escapes_its_namespace():
    """`_root_.bar` inside `namespace Foo` declares `bar`, not `Foo._root_.bar`.
    Keeping the marker made the audit ask about a name Lean never declared, and
    the module could never be saved."""
    source = "namespace Foo\n\ntheorem _root_.bar : True := trivial\n\nend Foo\n"
    assert declarations(source)["theorem"] == ("bar",)


def test_a_root_qualified_private_declaration_is_tracked_under_the_same_name():
    source = "namespace Foo\nprivate lemma _root_.step : True := trivial\nend Foo\n"
    found = declarations(source)
    assert found["lemma"] == ("step",)
    assert found["private"] == ("step",)


def test_a_wrapped_assumption_is_still_compared():
    """`set_option ... in axiom` is one command, and a scanner that stopped at
    the wrapper returned nothing — so the statement was never compared against
    the one a human approved, and the axiom passed on its name."""
    assert assumptions("set_option autoImplicit false in axiom trusted : False\n") == (
        ("trusted", "False"),
    )
    assert assumptions("open Nat in axiom trusted : False\n") == (("trusted", "False"),)
    assert assumptions("@[simp] axiom trusted : False\n") == (("trusted", "False"),)


def test_in_inside_a_statement_does_not_look_like_a_wrapper():
    assert assumptions("axiom h : ∀ x in s, P x\n") == (("h", "∀ x in s, P x"),)


def test_a_wrapped_declaration_is_still_audited():
    """An unseen theorem is one the audit never asks about."""
    assert declarations("set_option autoImplicit false in theorem T : True := trivial\n")[
        "theorem"
    ] == ("T",)
    assert declarations("open Nat in lemma L : True := trivial\n")["lemma"] == ("L",)


def test_an_indented_namespace_still_qualifies():
    """The assumption scan once defined its own `NAMESPACE`/`END`, which
    replaced these at import time and dropped the leading-whitespace tolerance —
    quietly, and for the declaration scan as well."""
    source = "namespace Foo\n  namespace Bar\n  theorem T : True := trivial\n  end Bar\nend Foo\n"
    assert declarations(source)["theorem"] == ("Foo.Bar.T",)


def test_an_escaped_namespace_still_qualifies():
    source = "namespace «my scope»\ntheorem T : True := trivial\nend «my scope»\n"
    assert declarations(source)["theorem"] == ("«my scope».T",)


def test_a_bare_end_closes_a_namespace_for_assumptions_too():
    """Not popping left every later axiom qualified by a namespace that had
    closed, and neither spelling could satisfy both the gate and the audit."""
    source = "namespace Foo\naxiom a : True\nend\naxiom b : False\n"
    assert assumptions(source) == (("Foo.a", "True"), ("b", "False"))


def test_a_section_inside_a_namespace_does_not_close_it_for_assumptions():
    source = "namespace Foo\nsection\naxiom a : True\nend\naxiom b : False\nend Foo\n"
    assert assumptions(source) == (("Foo.a", "True"), ("Foo.b", "False"))


def test_a_comment_marker_inside_an_escaped_name_is_part_of_the_name():
    """`theorem «result--unchecked»` is one token. Blanking from the `--` left
    no declaration at all, so the module recorded "not established" and saved
    anyway — an ordinary literal theorem past both the audit and the ratchet."""
    assert declarations("theorem «result--unchecked» : True := trivial\n")["theorem"] == (
        "«result--unchecked»",
    )
    assert declarations("theorem «a/-b» : True := trivial\n")["theorem"] == ("«a/-b»",)
    assert assumptions("axiom «trusted--x» : False\n") == (("«trusted--x»", "False"),)


def test_a_real_comment_after_an_escaped_name_is_still_a_comment():
    stripped = strip_comments("theorem «ok» : True := trivial -- gone\n")
    assert "«ok»" in stripped
    assert "gone" not in stripped


def test_an_unterminated_guillemet_does_not_swallow_the_file():
    assert declarations("theorem «oops : True\ntheorem after : True := trivial\n")[
        "theorem"
    ] == ("after",)


def test_statements_read_the_head_and_stop_at_the_proof():
    """What a reader has to be able to compare, and nothing else."""
    source = "@[simp] theorem one (n : Nat) : n = n := by rfl\n"
    assert statements(source) == {"one": "theorem one (n : Nat) : n = n"}


def test_a_statement_is_reported_under_the_name_lean_gives_it():
    source = "namespace Hardy\ntheorem one : True := trivial\nend Hardy\n"
    assert statements(source) == {"Hardy.one": "theorem one : True"}


def test_a_binder_default_is_not_the_proof():
    source = "theorem one (n : Nat := 3) : n = n := by rfl\n"
    assert statements(source)["one"] == "theorem one (n : Nat := 3) : n = n"


def test_a_let_in_the_proposition_is_not_the_proof():
    """`theorem t : let n := 1; n = 1 := by rfl` is ordinary Lean.

    Stopping at that first `:=` recorded the statement as `theorem t : let n`,
    which left the rest of the proposition outside what a writeup has to quote
    -- so a document could show a different one and still match.
    """
    source = "theorem one : let n := 1; n = 1 := by rfl\n"
    assert statements(source)["one"] == "theorem one : let n := 1; n = 1"


def test_a_string_literal_survives_into_the_statement():
    """`strip_comments` blanks literals so a `--` inside one cannot open a
    comment. Blanked here, `"a" = "a"` and `"b" = "b"` would be the same run of
    spaces, and a writeup quoting either would match a theorem about the other.
    """
    source = 'theorem one : "a" = "a" := by rfl' + "\n"
    assert statements(source)["one"] == 'theorem one : "a" = "a"'


def test_a_comment_inside_a_statement_is_dropped():
    source = "theorem one : True -- obviously\n  := trivial\n"
    assert statements(source)["one"] == "theorem one : True"


def test_a_theorem_written_inside_a_string_is_not_a_statement():
    """The scan still runs on text that cannot lie about declarations."""
    source = 'def s := "theorem fake : False := sorry"' + "\n"
    assert statements(source) == {}
