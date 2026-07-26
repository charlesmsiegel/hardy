from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from hardy.workspace import (
    ImportCycle,
    WorkspacePathError,
    build_order,
    declarations,
    dependents,
    internal_imports,
    module_name,
    module_path,
    name_aliases,
    parse_imports,
    safe_relative,
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
