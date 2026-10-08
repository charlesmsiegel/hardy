"""What the Lean lexer and the module scans produce, pinned over a fixed corpus.

`test_declaration_snapshot.py` pins what the declaration scans report. This
pins the layer under them, over the same sources: each view the lexer
renders, where it is uncertain or gave up, the token and numeral boundaries,
the quotations it can and cannot bound, whitespace normalisation, imports and
the structural refusals. Large values are pinned by digest, small ones as
they are. A change to the grammar regenerates this in the same commit, so
the diff explains itself in review:

    uv run python tests/unit/test_syntax_golden.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from hardy.formal import syntax

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ROOT / "tests" / "fixtures" / "lean" / "declaration-scan.json"
GOLDEN = ROOT / "tests" / "fixtures" / "lean" / "syntax-golden.json"

# Shapes the declaration snapshot does not carry: line endings, interpolation
# nested past the lexer's limit, too many readings at once, and headers.
EXTRA = {
    "crlf": "import Mathlib\r\nnamespace A\r\ntheorem t : True := trivial\r\nend A\r\n",
    "lone-cr": "theorem a : True := trivial -- note\rtheorem b : True := trivial\n",
    "deep-interpolation": "def s := " + 's!"{' * 20 + "x" + '}"' * 20 + "\ntheorem t : True := trivial\n",
    "many-readings": "def x := " + "+'a' " * 40 + "\ntheorem t : True := trivial\n",
    "module-header": "module\n\npublic import Mathlib.Data.Nat\nmeta import all Foo.Bar\nimport Baz\n",
    "prelude-header": "prelude\nimport Init.Core -- trailing\n/- block -/ import Init.Data\ntheorem t : True := trivial\n",
    "unicode-names": "theorem α₁ : True := trivial\ntheorem «first result» : True := trivial\n",
    "raw-strings": 'def s := r#"a " sorry "#\ntheorem t : True := sorry\n',
    "nested-comment": "/- /- -/ theorem hidden : False := sorry -/\ntheorem shown : True := trivial\n",
    "quotation": "macro \"m\" : tactic => `(tactic| sorry)\ntheorem t : True := by m\n",
}


def digest(value: object) -> str:
    if isinstance(value, bytes | bytearray):
        data = bytes(value)
    elif isinstance(value, str):
        data = value.encode("utf-8")
    else:
        data = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(data).hexdigest()[:16]


def characterize(source: str) -> dict[str, object]:
    lexed = syntax.lex(source)
    text = lexed.text
    tokens = syntax.identifier_tokens(text, lexed)
    blanked, unbounded = syntax.blank_bounded_quotations(lexed)
    try:
        imports: object = list(syntax.parse_imports(source))
    except Exception as error:  # noqa: BLE001 - pinned, not handled
        imports = f"raised {type(error).__name__}"
    return {
        "text": digest(text),
        "kept": digest(lexed.kept),
        "certain": digest(lexed.certain),
        "profiles": digest(lexed.profiles),
        "uncertain": lexed.uncertain(),
        "uncertain_at": digest([index for index in range(len(source)) if lexed.uncertain(index, index + 1)]),
        "uncertain_names": list(lexed.uncertain_names()),
        "overflow": lexed.overflow,
        "tokens": digest(sorted(tokens.items())),
        "tokens_unlexed": digest(sorted(syntax.identifier_tokens(text).items())),
        "numerals": digest(sorted(syntax.numeral_ends(text, lexed))),
        "quotations": [digest(blanked), [list(span) for span in unbounded]],
        "quotations_refusing_sorry": [list(span) for span in syntax.blank_bounded_quotations(lexed, "s")[1]],
        "declares_tokens": syntax.declares_tokens(lexed),
        "normalised": digest(syntax.normalise_lean(source)),
        "imports": imports,
        "unreadable_structure": list(syntax.unreadable_structure(source)),
    }


def corpus() -> dict[str, str]:
    entries = json.loads(SOURCES.read_text(encoding="utf-8"))
    found = {f"{index:04d}:{entry['origin']}": entry["source"] for index, entry in enumerate(entries)}
    found.update({f"extra:{name}": source for name, source in EXTRA.items()})
    return found


def load() -> dict[str, dict[str, object]]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_every_source_is_pinned():
    assert sorted(load()) == sorted(corpus())


@pytest.mark.parametrize("name", sorted(corpus()))
def test_the_lexer_reports_what_the_golden_file_recorded(name):
    assert characterize(corpus()[name]) == load()[name]


MODULES = {
    "Main": "import Mathlib\nimport Proj.B\nimport Proj.A\n",
    "Proj.A": "import Proj.C\n",
    "Proj.B": "import Proj.C\nimport Mathlib.Tactic\n",
    "Proj.C": "theorem c : True := trivial\n",
    "Proj.D": "import Proj.A\n",
}


def test_the_dependency_graph_is_unchanged():
    known = MODULES.keys()
    assert syntax.build_order(MODULES, ["Main", "Proj.D"]) == ("Proj.C", "Proj.A", "Proj.B", "Main", "Proj.D")
    assert syntax.dependents(MODULES, "Proj.C") == frozenset({"Proj.A", "Proj.B", "Main", "Proj.D"})
    assert syntax.internal_imports(MODULES["Main"], known) == ("Proj.B", "Proj.A")
    assert syntax.external_imports(MODULES["Proj.B"], known) == ("Mathlib.Tactic",)
    cyclic = {"A": "import B\n", "B": "import A\n"}
    with pytest.raises(syntax.ImportCycle, match="workspace modules import each other: A -> B -> A"):
        syntax.build_order(cyclic, ["A"])


def test_the_path_conventions_are_unchanged():
    assert syntax.safe_relative("Proj\\Sub\\M.lean").as_posix() == "Proj/Sub/M.lean"
    assert syntax.module_name(syntax.safe_relative("Proj/Sub/M.lean")) == "Proj.Sub.M"
    assert syntax.module_path("Proj.Sub.M").as_posix() == "Proj/Sub/M.lean"
    assert syntax._olean_relative("Proj.M").as_posix() == "Proj/M.olean"
    assert syntax._olean_module(syntax._olean_relative("Proj.Foo")) == "Proj.Foo"
    for bad, message in (("/abs/M.lean", "not a workspace Lean path"), ("A/../M.lean", "escapes"),
                         ("A/1x.lean", "not a Lean identifier"), ("M.txt", "not a workspace Lean path")):
        with pytest.raises(syntax.WorkspacePathError, match=message):
            syntax.safe_relative(bad)
    assert syntax.declared_name("_root_.x", ("A",)) == "x"
    assert syntax.declared_name("x", ("A", "B")) == "A.B.x"
    assert syntax.name_aliases("A.B.x") == ("A.B.x", "x")


def regenerate() -> None:
    golden = {name: characterize(source) for name, source in sorted(corpus().items())}
    GOLDEN.write_text(json.dumps(golden, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")


if __name__ == "__main__":
    regenerate()
    sys.exit(0)
