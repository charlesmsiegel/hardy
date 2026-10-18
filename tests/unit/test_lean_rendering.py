"""Every Lean file Hardy renders, pinned byte for byte.

Line numbers are counted from these files -- the axiom report is read off the
last line, `refute` and `inspect_declarations` read verdicts off fixed lines --
and the recorded-run readers rebuild them and compare bytes, so a renderer that
moved one newline would quietly fail every honest record. The fixture was
written by the renderers as they stood before they shared one layout function,
and each path below must still produce it exactly.

Regenerate only for a deliberate change of format, never to make this pass:
`python tests/unit/test_lean_rendering.py --write`.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from hardy.formal import refute
from hardy.formal.contracts import (
    DeclaredAssumption,
    EnvironmentIdentity,
    FormalizationProposal,
    Request,
    freeze_claim,
)
from hardy.formal.lean import LeanService, LeanTools, render_theorem, scratch_source
from hardy.formal.verifier import verification_source
from hardy.foundation.process import ProcessResult, ProcessSpec
from hardy.workflows.contracts import RunLimits

FIXTURE = Path(__file__).parents[1] / 'fixtures' / 'lean' / 'rendered-files.json'

IMPORTS = {
    'mathlib': ('Mathlib',),
    'two': ('Mathlib', 'Mathlib.Tactic.Ring'),
    'none': (),
}
BODIES = {
    'term': 'by rfl',
    'padded': '  by\n  simp\n\n',
    'empty': '',
    'crlf': 'by\r\n  rfl\r\n',
}
ASSUMED = {
    'none': (),
    'one': (DeclaredAssumption(name='Foo.ax', statement='  True  ', source='Hardy, 2026'),),
    'two': (
        DeclaredAssumption(name='«first axiom»', statement='∀ n : ℕ, n = n', source='a'),
        DeclaredAssumption(name='get!', statement='1 = 1', source='b'),
    ),
}
REQUESTS = {
    'theorem': Request('theorem t : True', 'x'),
    'decorated': Request(
        '@[simp] protected theorem Foo.t (n : Nat) : n = n', 'x', ('Mathlib', 'Mathlib.Data.Nat')
    ),
    'example': Request('example : True', 'x'),
    'root': Request('theorem _root_.bar : True', 'x'),
    'crlf': Request('theorem t\r\n  : True', 'x'),
}
PROOFS = {'term': 'by trivial', 'padded': '  by\r\n  trivial  \n', 'empty': ''}


def _claim(imports: tuple[str, ...], name: str, binders: str, proposition: str):
    proposal = FormalizationProposal(
        restatement='r',
        domains=(),
        quantifiers=(),
        assumptions=(),
        interpretation_choices=(),
        theorem_name=name,
        binders=binders,
        proposition=proposition,
    )
    environment = EnvironmentIdentity(
        lean_version='4.32.0',
        lean_commit='8c9756b',
        mathlib_revision='81a5d257',
        lake_manifest_sha256='b' * 64,
        imports=imports,
    )
    return freeze_claim('r', proposal, environment, datetime(2026, 7, 24, tzinfo=UTC))


def _capturing(sink: list[str]):
    """A runner that keeps the file Lean was handed and answers nothing."""

    def runner(spec: ProcessSpec) -> ProcessResult:
        sink.append(Path(spec.argv[-1]).read_bytes().decode('utf-8'))
        return ProcessResult(
            argv=spec.argv, cwd=spec.cwd, returncode=0, stdout='', stderr='',
            timed_out=False, output_overflow=False, duration_ms=1,
        )

    return runner


def _claim_files() -> dict[str, str]:
    files = {}
    shapes = {
        'plain': ('two_eq_two', '', '2 = 2'),
        'bound': ('«first result»', '  (n : ℕ) ', ' n + 0 = n  '),
    }
    for imports_key, imports in IMPORTS.items():
        for shape_key, shape in shapes.items():
            claim = _claim(imports, *shape)
            for body_key, body in BODIES.items():
                for assumed_key, assumed in ASSUMED.items():
                    key = f'{imports_key}/{shape_key}/{body_key}/{assumed_key}'
                    files[f'render_theorem/{key}'] = render_theorem(claim, body, assumed)
                    files[f'verification_source/{key}'] = verification_source(claim, body, assumed)
    return files


def _service_files(tmp: Path) -> dict[str, str]:
    seen: list[str] = []
    claim = _claim(('Mathlib',), 'two_eq_two', '', '2 = 2')
    service = LeanService(
        lake=tmp / 'lake', lean_project=tmp, environment=claim.environment,
        limits=RunLimits(), runner=_capturing(seen),
    )
    service.check_proof(claim, 'by rfl', ASSUMED['one'])
    service.check_scratch('#check Nat\n\n')
    service.inspect_declarations(('Nat.add_comm', 'IsCyclic'))
    keys = ('check_proof', 'check_scratch', 'inspect_declarations')
    return {f'LeanService.{key}': text for key, text in zip(keys, seen, strict=True)}


def _tools_files(tmp: Path) -> dict[str, str]:
    files = {}
    for request_key, request in REQUESTS.items():
        seen: list[str] = []
        tools = LeanTools(request, ('lean',), project=tmp, runner=_capturing(seen))
        for proof_key, proof in PROOFS.items():
            files[f'LeanTools.source/{request_key}/{proof_key}'] = tools.source(proof)
            files[f'LeanTools.source.audit/{request_key}/{proof_key}'] = tools.source(proof, audit=True)
        tools.inspect_goal('simp')
        tools.inspect_goal()
        tools.search_declaration('Nat.add_comm')
        tools.run_source('theorem x : True := trivial\n\n', audit=('axioms x', 'x'))
        keys = ('inspect_goal.tactic', 'inspect_goal.bare', 'search_declaration', 'run_source')
        files.update({f'LeanTools.{key}/{request_key}': text for key, text in zip(keys, seen, strict=True)})
    return files


def _probe_files() -> dict[str, str]:
    return {
        'scratch_source': scratch_source('  #check Nat  \n\n'),
        'refute.probe_source/imported': refute.probe_source('2 + 2 = 5')[0],
        'refute.probe_source/bare': refute.probe_source('∀ n : ℕ, n < 3', imported=False)[0],
    }


def rendered(tmp: Path) -> dict[str, str]:
    return {**_claim_files(), **_service_files(tmp), **_tools_files(tmp), **_probe_files()}


def test_every_rendered_lean_file_is_byte_identical_to_the_pinned_one(tmp_path) -> None:
    expected = json.loads(FIXTURE.read_text(encoding='utf-8'))
    actual = rendered(tmp_path)

    assert sorted(actual) == sorted(expected)
    changed = [key for key in expected if actual[key] != expected[key]]
    assert not changed, f'{len(changed)} rendered files changed, first: {changed[0]}'


def test_requests_keep_the_empty_import_header_used_by_existing_artifacts(tmp_path) -> None:
    """The public Request constructor accepts empty imports even though its JSON reader does not."""
    seen: list[str] = []
    tools = LeanTools(
        Request('theorem t : True', 'x', ()), ('lean',), project=tmp_path,
        runner=_capturing(seen),
    )

    assert tools.source('by trivial') == '\n\ntheorem t : True := by trivial\n'
    tools.search_declaration('Nat')
    assert seen == ['\n\n#check Nat\n#print Nat\n']


def test_the_probe_and_inspection_lines_are_counted_from_the_scratch_header() -> None:
    """`refute` and `inspect_declarations` read verdicts off line numbers; the
    first line after the header is where both expect their first command."""
    lines = scratch_source('first\nsecond').splitlines()

    assert lines[refute.FIRST_PROBE - 1] == 'first'
    assert refute.probe_source('True')[0].splitlines()[refute.FIRST_PROBE - 1].startswith('example')


if __name__ == '__main__' and sys.argv[1:] == ['--write']:
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        FIXTURE.write_text(
            json.dumps(rendered(Path(directory)), indent=1, ensure_ascii=False, sort_keys=True) + '\n',
            encoding='utf-8',
        )
