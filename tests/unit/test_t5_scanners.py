"""Regressions for track T5's shared declaration and refusal grammars."""
import pytest

from hardy.formal import syntax
from hardy.formal.contracts import Request
from hardy.formal.declarations import DeclarationIndex
from hardy.formal.lean import DECLARATION_HEAD, LeanTools
from hardy.formal.verifier import FORBIDDEN_TOKEN, UNAUTHORIZED_SIGNATURE_TOKEN


@pytest.mark.parametrize('section', ['noncomputable section', 'public section', '@[expose] public section'])
def test_modified_section_uses_one_shared_scope_model(tmp_path, section):
    source = f'namespace Foo.Bar\n{section}\ntheorem a : True := trivial\nend\ntheorem c : True := trivial\naxiom ax : True\nend Bar\nend Foo\ntheorem b : True := trivial\n'
    names = ('Foo.Bar.a', 'Foo.Bar.c', 'b')
    assert syntax.declarations(source)['theorem'] == names
    assert syntax.assumptions(source) == (('Foo.Bar.ax', 'True'),)
    assert tuple(row[0] for row in DeclarationIndex(tmp_path)._file(source, 'Test') if row[0] != 'Foo.Bar.ax') == names


@pytest.mark.parametrize('modifier', ['public', 'meta', '@[simp] public'])
def test_visibility_modifier_is_part_of_the_frozen_declaration(modifier):
    declaration = f'{modifier} theorem p : True'
    assert syntax.named_declarations(declaration + ' := trivial\n') == ('p',)
    assert DECLARATION_HEAD.match(declaration).group(1) == 'p'
    assert Request.from_dict({'declaration': declaration, 'informal_claim': 'True'}).declaration == declaration


@pytest.mark.parametrize('body', ['by exact ⟨sorry, sorry⟩', 'by exact ·sorry', 'by exact ←sorry', 'by exact sorry⟩', 'by exact sorryAx _'])
def test_unicode_punctuation_cannot_hide_a_forbidden_hole(body):
    assert FORBIDDEN_TOKEN.search(body)
    assert LeanTools.has_holes(body)


@pytest.mark.parametrize('token', ['⟨axiom', '·opaque', '←theorem', 'lemma⟩'])
def test_signature_command_boundaries_follow_identifiers(token):
    assert UNAUTHORIZED_SIGNATURE_TOKEN.search(token)


@pytest.mark.parametrize('name', ['sorry_lemma', 'my_sorry', "Nat.sorry'", 'admitted', 'αsorry', 'sorryα'])
def test_forbidden_token_does_not_match_identifiers(name):
    assert FORBIDDEN_TOKEN.search(name) is None


def test_dependents_include_importers_of_a_deleted_module():
    assert syntax.dependents({'A': 'import B\n', 'C': 'import A\n'}, 'B') == frozenset({'A', 'C'})


def test_build_order_can_reuse_one_import_table():
    sources = {'A': 'import B\nimport Mathlib\n', 'B': ''}
    table = syntax.import_table(sources)
    assert table == {'A': ('B', 'Mathlib'), 'B': ()}
    assert syntax.build_order(sources, ['A'], imports=table) == ('B', 'A')


@pytest.mark.parametrize("modifier", ["public", "meta"])
def test_visibility_modifier_on_assumptions(modifier):
    assert syntax.assumptions(f"{modifier} axiom ax : True\n") == (("ax", "True"),)


@pytest.mark.parametrize('source, names', [
    ('namespace Foo.Bar\ntheorem a : True := trivial\nend Bar\nend Foo\ntheorem b : True := trivial\n', ('Foo.Bar.a', 'b')),
    ('namespace Foo\nnamespace Bar\ntheorem a : True := trivial\nend Foo.Bar\ntheorem b : True := trivial\n', ('Foo.Bar.a', 'b')),
    ('namespace Foo.Bar\nsection X.Y\ntheorem a : True := trivial\nend X.Y\ntheorem b : True := trivial\nend Foo.Bar\n', ('Foo.Bar.a', 'Foo.Bar.b')),
    ('namespace Foo\nmutual\ntheorem a : True := trivial\nend\ntheorem _root_.b : True := trivial\nend Foo\n', ('Foo.a', 'b')),
    ('noncomputable section\ntheorem a : True := trivial\n', ('a',)),
])
def test_scope_names_agree_between_audit_scan_and_declaration_index(tmp_path, source, names):
    assert syntax.declarations(source)['theorem'] == names
    assert tuple(row[0] for row in DeclarationIndex(tmp_path)._file(source, 'Test')) == names
