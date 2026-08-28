"""What the declaration-name index reads, and what a hit from it means.

The index exists because of a measured failure: on the pinned toolchain
`#find` never returned, so a session asking whether Mathlib has a notion of a
simple group was told nothing matched and concluded it does not. The names are
in Mathlib's own sources, on disk, in plain text -- so this reads them the way
`modules.py` reads the module index, and a question like `IsSimpleGroup`
is answered without Lean running at all.

The honesty constraint runs the other way from most of Hardy's: a textual scan
can *miss* declarations (macro-generated names, grammar it does not model), so
a miss here is weaker evidence than `inspect_declarations` and nothing in these
tests lets an index miss present itself as Lean's word.
"""

from __future__ import annotations

import importlib


def _package(tmp_path, name='mathlib'):
    root = tmp_path / '.lake' / 'packages' / name
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write(root, relative, source):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding='utf-8')
    return path


def _index(tmp_path):
    declarations = importlib.import_module('hardy.declarations')
    return declarations.DeclarationIndex(tmp_path)


def test_declarations_carry_the_namespace_they_were_declared_under(tmp_path) -> None:
    """The motivating case: `IsSimpleGroup` is a `class` in Mathlib's sources.
    The graded session asked for it, `#find` timed out, and the model concluded
    Mathlib has no notion of a simple group. A name index answers from the
    text, without Lean running at all."""
    root = _package(tmp_path)
    _write(
        root,
        'Mathlib/GroupTheory/SimpleGroup.lean',
        'import Mathlib.GroupTheory.Subgroup\n'
        '\n'
        'class IsSimpleGroup (G : Type u) [Group G] : Prop where\n'
        '  eq_bot_or_eq_top : True\n'
        '\n'
        'namespace IsSimpleGroup\n'
        '\n'
        'theorem eq_bot_of_lt {G : Type u} : True := trivial\n'
        '\n'
        'end IsSimpleGroup\n',
    )

    found = _index(tmp_path).search('IsSimpleGroup', 10)

    names = [record.name for record in found]
    assert 'IsSimpleGroup' in names
    assert 'IsSimpleGroup.eq_bot_of_lt' in names
    top = next(record for record in found if record.name == 'IsSimpleGroup')
    assert top.signature.startswith('class IsSimpleGroup')
    assert top.source_file == 'Mathlib.GroupTheory.SimpleGroup'
    assert top.line == 3


def test_a_nested_namespace_prefixes_and_unprefixes_all_of_its_components(tmp_path) -> None:
    root = _package(tmp_path)
    _write(
        root,
        'Mathlib/A.lean',
        'namespace Foo.Bar\n'
        'theorem inside : True := trivial\n'
        'end Foo.Bar\n'
        'theorem outside : True := trivial\n',
    )

    names = {record.name for record in _index(tmp_path).search('side', 10)}

    assert names == {'Foo.Bar.inside', 'outside'}


def test_a_root_prefixed_declaration_escapes_the_namespace_it_sits_in(tmp_path) -> None:
    """`theorem _root_.IsPGroup.toSylow` inside `namespace Sylow` declares
    `IsPGroup.toSylow` -- that is what `_root_.` is for. Found in Mathlib's
    own `GroupTheory/Sylow.lean`, where the first scan produced
    `Sylow._root_.IsPGroup.toSylow`, a name nothing can elaborate."""
    root = _package(tmp_path)
    _write(
        root,
        'Mathlib/Root.lean',
        'namespace Sylow\n'
        'theorem _root_.IsPGroup.toSylow : True := trivial\n'
        'theorem inside_sylow : True := trivial\n'
        'end Sylow\n',
    )

    names = {record.name for record in _index(tmp_path).search('sylow', 10)}

    assert names == {'IsPGroup.toSylow', 'Sylow.inside_sylow'}


def test_an_end_naming_the_inner_component_closes_only_that_component(tmp_path) -> None:
    """Lean's own semantics, pinned because a reviewer proposed the opposite.

    `namespace Foo.Bar` opens two namespaces, and `end Bar` closes only the
    inner one, leaving `Foo` active -- which is why the scanner pushes one
    scope per component rather than one per command. A bare `end` cannot
    close a namespace in Lean 4 at all; it closes a `section` or `mutual`,
    and those push their own scope."""
    root = _package(tmp_path)
    _write(
        root,
        'Mathlib/Partial.lean',
        'namespace Foo.Bar\n'
        'theorem inner_one : True := trivial\n'
        'end Bar\n'
        'theorem still_in_foo : True := trivial\n'
        'end Foo\n'
        'theorem top_level_one : True := trivial\n',
    )

    index = _index(tmp_path)

    assert [record.name for record in index.search('inner_one', 5)] == ['Foo.Bar.inner_one']
    assert [record.name for record in index.search('still_in_foo', 5)] == ['Foo.still_in_foo']
    assert [record.name for record in index.search('top_level_one', 5)] == ['top_level_one']


def test_a_head_wrapped_onto_continuation_lines_is_recorded_whole(tmp_path) -> None:
    """Mathlib routinely wraps a head -- binders and result type on indented
    lines under the keyword. Recording only the first physical line handed
    the ranking `theorem foo` as a signature, and the index's rendering wins
    over Loogle's complete one, so the model read a name where a type should
    be. The body is still cut: what follows `:=` or `where` is the proof or
    the fields, not the head."""
    root = _package(tmp_path)
    _write(
        root,
        'Mathlib/Wrapped.lean',
        'theorem wrapped_head {G : Type u}\n'
        '    (h : True) :\n'
        '    1 + 1 = 2 := by\n'
        '  simp\n'
        '\n'
        'structure WrappedStructure (n : Nat)\n'
        '    extends Inhabited Nat where\n'
        '  field : Nat\n',
    )

    index = _index(tmp_path)

    (head,) = index.search('wrapped_head', 5)
    assert head.signature == 'theorem wrapped_head {G : Type u} (h : True) : 1 + 1 = 2'
    (shape,) = index.search('WrappedStructure', 5)
    assert shape.signature == 'structure WrappedStructure (n : Nat) extends Inhabited Nat'


def test_two_threads_arriving_cold_pay_for_one_scan_between_them(tmp_path, monkeypatch) -> None:
    """The MCP server can field `search_declarations` and `rank_premises`
    concurrently, and the retriever's admission lock only serializes
    rankings -- so both calls could see an unbuilt index and each walk the
    whole source tree, doubling a cost budgeted at up to two minutes."""
    import threading
    import time

    declarations = importlib.import_module('hardy.declarations')
    root = _package(tmp_path)
    _write(root, 'Mathlib/Once.lean', 'theorem only_one : True := trivial\n')
    index = declarations.DeclarationIndex(tmp_path)
    scans = []
    original = declarations.DeclarationIndex._scan

    def slow_scan(self):
        scans.append(threading.get_ident())
        time.sleep(0.05)
        return original(self)

    monkeypatch.setattr(declarations.DeclarationIndex, '_scan', slow_scan)
    started = threading.Barrier(2, timeout=5)

    def search():
        started.wait()
        index.search('only_one', 5)

    threads = [threading.Thread(target=search) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert len(scans) == 1
    assert index.count() == 1


def test_a_section_does_not_disturb_the_namespace_its_end_sits_inside(tmp_path) -> None:
    """`section`/`end` pairs nest freely inside a namespace, and an `end` that
    closes a section must not pop the namespace around it."""
    root = _package(tmp_path)
    _write(
        root,
        'Mathlib/B.lean',
        'namespace Nat\n'
        'section\n'
        'variable (n : Nat)\n'
        'theorem in_section : True := trivial\n'
        'end\n'
        'section Named\n'
        'theorem in_named_section : True := trivial\n'
        'end Named\n'
        'theorem after_sections : True := trivial\n'
        'end Nat\n',
    )

    names = {record.name for record in _index(tmp_path).search('section', 10)} | {
        record.name for record in _index(tmp_path).search('after', 10)
    }

    assert names == {'Nat.in_section', 'Nat.in_named_section', 'Nat.after_sections'}


def test_every_declaration_keyword_is_recognised_with_its_modifiers(tmp_path) -> None:
    root = _package(tmp_path)
    _write(
        root,
        'Mathlib/C.lean',
        'theorem a_theorem : True := trivial\n'
        'lemma a_lemma : True := trivial\n'
        'def a_def : Nat := 0\n'
        'abbrev an_abbrev : Nat := 0\n'
        'structure AStructure where\n'
        '  field : Nat\n'
        'class AClass (x : Nat) : Prop where\n'
        'inductive AnInductive where\n'
        '  | one\n'
        'opaque an_opaque : Nat\n'
        'axiom an_axiom : True\n'
        'instance a_named_instance : Inhabited Nat := ⟨0⟩\n'
        '@[simp] theorem with_attribute : True := trivial\n'
        'protected theorem with_modifier : True := trivial\n'
        'noncomputable def with_noncomputable : Nat := 0\n',
    )

    index = _index(tmp_path)
    for wanted in (
        'a_theorem',
        'a_lemma',
        'a_def',
        'an_abbrev',
        'AStructure',
        'AClass',
        'AnInductive',
        'an_opaque',
        'an_axiom',
        'a_named_instance',
        'with_attribute',
        'with_modifier',
        'with_noncomputable',
    ):
        assert [record.name for record in index.search(wanted, 5)] == [wanted], wanted


def test_an_anonymous_instance_contributes_no_name(tmp_path) -> None:
    root = _package(tmp_path)
    _write(
        root,
        'Mathlib/D.lean',
        'instance : Inhabited Nat := ⟨0⟩\n'
        'instance named_one : Inhabited Bool := ⟨true⟩\n',
    )

    assert [record.name for record in _index(tmp_path).search('Inhabited', 10)] == []
    assert [record.name for record in _index(tmp_path).search('named_one', 10)] == ['named_one']


def test_a_declaration_behind_a_same_line_command_wrapper_is_still_seen(tmp_path) -> None:
    """`set_option x y in theorem foo` is Lean's way of scoping one command,
    and the workspace scanner already reads through it (`workspace.WRAPPER`)
    for the same reason it matters here: an unseen declaration is an index
    miss for a name the sources really ship."""
    root = _package(tmp_path)
    _write(
        root,
        'Mathlib/Wrapper.lean',
        'set_option pp.universes true in theorem wrapped_by_option : True := trivial\n'
        'open Nat in theorem wrapped_by_open : True := trivial\n',
    )

    index = _index(tmp_path)

    assert [record.name for record in index.search('wrapped_by_option', 5)] == [
        'wrapped_by_option'
    ]
    assert [record.name for record in index.search('wrapped_by_open', 5)] == ['wrapped_by_open']


def test_the_miss_diagnostic_names_the_inspection_tool_this_surface_offers(tmp_path) -> None:
    """The staged and MCP surfaces register `lean_inspect_declarations`, not
    `inspect_declarations` -- a model following the recovery instruction there
    would make an unknown-tool call precisely when it needs help. Each surface
    hands in its own tool name."""
    declarations = importlib.import_module('hardy.declarations')

    missed = declarations.search_result(
        declarations.DeclarationIndex(tmp_path),
        'NoSuchName',
        inspect_tool='lean_inspect_declarations',
    )

    (note,) = missed.diagnostics
    assert 'lean_inspect_declarations' in note.message


def test_a_declaration_inside_a_comment_is_not_a_declaration(tmp_path) -> None:
    """Doc comments quote declaration heads all the time -- `/-- like
    `theorem foo` -/` -- and indexing those would answer name questions with
    names that do not exist."""
    root = _package(tmp_path)
    _write(
        root,
        'Mathlib/E.lean',
        '/-- A doc comment mentioning\n'
        'theorem commented_out : True\n'
        'across lines. -/\n'
        'theorem real_one : True := trivial\n'
        '-- theorem line_commented : True\n'
        '/- block\n'
        'theorem also_commented : True\n'
        '-/\n',
    )

    index = _index(tmp_path)
    assert [record.name for record in index.search('commented', 10)] == []
    assert [record.name for record in index.search('real_one', 10)] == ['real_one']


def test_search_matches_are_case_insensitive_and_leaf_first(tmp_path) -> None:
    """Someone searching `sylow` wants `Sylow` itself and `Nat.sylow_thing`
    ahead of a middle-component match, the same ordering `search_modules`
    settled on and for the same reason."""
    root = _package(tmp_path)
    _write(
        root,
        'Mathlib/F.lean',
        'theorem Sylow : True := trivial\n'
        'namespace Sylow\n'
        'theorem card_eq : True := trivial\n'
        'end Sylow\n'
        'theorem exists_sylow_subgroup : True := trivial\n',
    )

    found = [record.name for record in _index(tmp_path).search('sylow', 10)]

    assert set(found) == {'Sylow', 'Sylow.card_eq', 'exists_sylow_subgroup'}
    # `Sylow.card_eq` matches only on a middle component, so it orders last.
    assert found[-1] == 'Sylow.card_eq'


def test_a_multi_word_query_ranks_names_matching_more_of_its_words_first(tmp_path) -> None:
    """The graded session searched for concepts -- `Sylow simple group` -- and
    the module index could only refuse. Names fuse words (`IsSimpleGroup`
    contains both `simple` and `group`), so a name matching every word of the
    query is almost always the name that was meant."""
    root = _package(tmp_path)
    _write(
        root,
        'Mathlib/G.lean',
        'class IsSimpleGroup : Prop where\n'
        'theorem simple_iff : True := trivial\n'
        'theorem group_mul : True := trivial\n',
    )

    found = [record.name for record in _index(tmp_path).search('simple group', 10)]

    assert found[0] == 'IsSimpleGroup'
    assert set(found) == {'IsSimpleGroup', 'simple_iff', 'group_mul'}


def test_only_package_sources_are_read_and_build_trees_are_skipped(tmp_path) -> None:
    """The workspace's own files hold the model's work in progress, and a build
    tree can hold a stale copy of anything. Only what a package ships answers
    "does Mathlib have it"."""
    root = _package(tmp_path)
    _write(root, 'Mathlib/H.lean', 'theorem shipped : True := trivial\n')
    _write(root, '.lake/build/ir/Stale.lean', 'theorem stale_copy : True := trivial\n')
    _write(tmp_path / 'Group', 'Sylow.lean', 'theorem workspace_own : True := trivial\n')

    index = _index(tmp_path)

    assert [record.name for record in index.search('shipped', 5)] == ['shipped']
    assert index.search('stale_copy', 5) == ()
    assert index.search('workspace_own', 5) == ()


def test_a_package_the_manifest_does_not_name_is_not_scanned(tmp_path) -> None:
    """`.lake/packages` can hold a directory the current manifest no longer
    names -- Lake does not sweep removed packages -- and its declarations are
    not part of the environment the identity describes. When the manifest is
    readable it decides which packages count; when it is not, every package
    directory is read, because a project someone assembled by hand should
    degrade toward extra leads rather than toward none. The fixtures in the
    rest of this file exercise exactly that fallback: none of them write a
    manifest."""
    import json

    (tmp_path / 'lake-manifest.json').write_text(
        json.dumps({'packages': [{'name': 'mathlib'}]}), encoding='utf-8'
    )
    _write(_package(tmp_path, 'mathlib'), 'Mathlib/Named.lean',
           'theorem manifest_named : True := trivial\n')
    _write(_package(tmp_path, 'stale'), 'Stale/Left.lean',
           'theorem stale_left_behind : True := trivial\n')

    index = _index(tmp_path)

    assert [record.name for record in index.search('manifest_named', 5)] == ['manifest_named']
    assert index.search('stale_left_behind', 5) == ()


def test_only_modules_the_package_root_index_declares_are_read(tmp_path) -> None:
    """A package checkout ships more than its library: test trees, scripts,
    and modules its umbrella deliberately does not import, none of which
    `import Mathlib` reaches. The root index is the list of what a package
    ships -- the same reading `modules.py` settled on -- so when one exists it
    decides which files this index reads. A package shipping no root index is
    scanned whole, the direction that costs precision rather than hiding a
    lead."""
    root = _package(tmp_path)
    _write(root, 'Mathlib.lean', 'import Mathlib.A\n')
    _write(root, 'Mathlib/A.lean', 'theorem declared_module_decl : True := trivial\n')
    _write(root, 'Mathlib/B.lean', 'theorem undeclared_module_decl : True := trivial\n')
    _write(root, 'test/T.lean', 'theorem test_tree_decl : True := trivial\n')

    index = _index(tmp_path)

    assert [record.name for record in index.search('declared_module_decl', 5)] == [
        'declared_module_decl'
    ]
    assert index.search('undeclared_module_decl', 5) == ()
    assert index.search('test_tree_decl', 5) == ()


def test_an_unreadable_file_costs_its_declarations_and_nothing_else(tmp_path, monkeypatch) -> None:
    """Simulated rather than `chmod(0)`, which a root test runner reads anyway."""
    from pathlib import Path

    root = _package(tmp_path)
    _write(root, 'Mathlib/I.lean', 'theorem readable : True := trivial\n')
    unreadable = _write(root, 'Mathlib/J.lean', 'theorem unreadable_one : True := trivial\n')
    original = Path.read_text

    def refusing(self, *args, **kwargs):
        if self == unreadable:
            raise OSError('permission denied')
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', refusing)

    assert [record.name for record in _index(tmp_path).search('readable', 5)] == ['readable']


def test_the_index_is_read_once_and_says_how_much_it_read(tmp_path) -> None:
    """A session holds one index for its lifetime, exactly like the module
    index; and the count is what a refusal message cites so an empty answer
    names the corpus it is an answer about."""
    root = _package(tmp_path)
    _write(root, 'Mathlib/K.lean', 'theorem first_one : True := trivial\n')
    index = _index(tmp_path)

    assert index.count() == 1
    _write(root, 'Mathlib/L.lean', 'theorem second_one : True := trivial\n')
    assert index.count() == 1
    assert index.search('second_one', 5) == ()


def test_a_project_with_no_packages_is_an_empty_index(tmp_path) -> None:
    index = _index(tmp_path)

    assert index.count() == 0
    assert index.search('anything', 5) == ()

    declarations = importlib.import_module('hardy.declarations')
    assert declarations.DeclarationIndex(None).search('anything', 5) == ()


def test_a_blank_query_matches_nothing_rather_than_everything(tmp_path) -> None:
    root = _package(tmp_path)
    _write(root, 'Mathlib/M.lean', 'theorem something : True := trivial\n')

    index = _index(tmp_path)

    assert index.search('', 5) == ()
    assert index.search('   ', 5) == ()


def test_results_are_bounded_by_the_limit_and_deterministic(tmp_path) -> None:
    root = _package(tmp_path)
    _write(
        root,
        'Mathlib/N.lean',
        ''.join(f'theorem result_{index} : True := trivial\n' for index in range(30)),
    )

    index = _index(tmp_path)
    found = index.search('result_', 5)

    assert len(found) == 5
    assert [record.name for record in found] == sorted(record.name for record in found)


def test_search_result_keeps_the_bounds_the_find_backend_enforced(tmp_path) -> None:
    """The backend moved; the tool contract must not. The same query and limit
    bounds, refused with the same sentences, so every surface still shows the
    model one error either way."""
    import pytest

    declarations = importlib.import_module('hardy.declarations')
    index = declarations.DeclarationIndex(tmp_path)

    for query in ('', 'x' * 513, 'two\nlines', 'carriage\rreturn'):
        with pytest.raises(ValueError, match='one bounded line'):
            declarations.search_result(index, query)
    for limit in (0, 21):
        with pytest.raises(ValueError, match='between 1 and 20'):
            declarations.search_result(index, 'x', limit)


def test_search_result_reports_truncation_and_never_a_timeout(tmp_path) -> None:
    root = _package(tmp_path)
    _write(
        root,
        'Mathlib/O.lean',
        ''.join(f'theorem match_{index} : True := trivial\n' for index in range(5)),
    )
    declarations = importlib.import_module('hardy.declarations')

    search = declarations.search_result(declarations.DeclarationIndex(tmp_path), 'match_', 3)

    assert search.success and not search.timed_out
    assert len(search.results) == 3
    assert search.truncated
    assert not declarations.search_result(
        declarations.DeclarationIndex(tmp_path), 'match_', 20
    ).truncated


def test_an_empty_search_result_says_what_a_miss_is_evidence_of(tmp_path) -> None:
    """A finished name search that matched nothing is evidence about the
    *index* -- a macro-built name is invisible to a textual scan -- and the
    sentence travels inside the answer, so no surface can present the miss as
    Lean's word on Mathlib. This is the other half of the `IsSimpleGroup`
    failure: the first half was a timeout dressed as an empty answer, and this
    keeps an honest empty answer from overclaiming in the same direction."""
    root = _package(tmp_path)
    _write(root, 'Mathlib/P.lean', 'theorem unrelated : True := trivial\n')
    declarations = importlib.import_module('hardy.declarations')

    search = declarations.search_result(declarations.DeclarationIndex(tmp_path), 'IsSympleGroup')

    assert search.success
    assert search.results == ()
    (note,) = search.diagnostics
    assert note.severity == 'information'
    assert 'IsSympleGroup' in note.message
    assert '1 names were read' in note.message
    assert 'inspect_declarations' in note.message
    # And a search that found something carries no such note.
    found = declarations.search_result(declarations.DeclarationIndex(tmp_path), 'unrelated')
    assert found.diagnostics == ()
