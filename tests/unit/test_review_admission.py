"""Admission requires actual source material, including when replaying older gates."""
from types import SimpleNamespace

import pytest
from test_semantic_operations import fixture

from hardy.foundation.values import ToolResult
from hardy.workflows.admission import ProbeOperations
from hardy.workflows.interactive.scoped_admission import ScopedAdmissionOperations
from hardy.workflows.interactive.semantic_records import ItemRequest, JudgmentRequest, SourceRegion


def admission(tmp_path, ranges):
    owners, ops, _, _, scope, _ = fixture(tmp_path)
    source = ops.source('admission-paper', '   \nMeaningful source.')
    regions = tuple(SourceRegion(source=source.ref, start=start, end=end) for start, end in ranges)
    item = ops.item(ItemRequest(id='background', kind='external_result', name='background',
        statement='∀ n : Nat, n = n', origin='background_paper', source=regions[0]))
    review = ops.judge(JudgmentRequest(subject=item.ref, scope=scope.ref, purpose='correspondence',
        verdict='approved', reviewer='operator', conditions='controlled proposal reading', reason='Same proposition'))
    calls = []

    def probe(source):
        calls.append(source)
        return SimpleNamespace(ok=False, output='Controlled Lean diagnostics', diagnostics=[
            SimpleNamespace(severity='error', line=index)
            for index, line in enumerate(source.splitlines(), 1)
            if line.startswith('example') and 'by sorry' not in line])

    ops.admissions.configure(ScopedAdmissionOperations(
        search=lambda _: ToolResult(True, 'Inspected declarations'),
        probes=ProbeOperations(elaborate=probe, refute=probe), environment=lambda: 'controlled'))
    request = ops.admissions.request(item.ref, scope.ref, sources=regions)
    gates = ops.admissions.run_gates(request.id, faithfulness=ops.judgments.reference(review))
    return owners, ops, item, scope, request, gates, calls


@pytest.mark.parametrize('ranges', [((0, 0),), ((0, 4),), ((4, 22), (0, 0))],
    ids=['empty', 'whitespace', 'mixed-with-empty'])
def test_empty_source_passages_do_not_pass_admission(tmp_path, ranges):
    _, ops, item, scope, request, gates, calls = admission(tmp_path, ranges)
    assert calls, 'The other controlled gates ran; source material must have its own gate.'
    assert gates.missing_gates == ('source',)
    with pytest.raises(ValueError, match='gates'):
        ops.admissions.decide(request.id, approved=True, reviewer='operator',
            conditions='read gate results', reason='Try admission')
    assert item.ref not in ops.store.read().head(scope.id).allowed_background


def test_older_empty_source_gate_does_not_authorize_admission(tmp_path):
    owners, ops, item, scope, request, gates, _ = admission(tmp_path, ((0, 0),))
    # Replay a gate produced before empty source passages were rejected. Its
    # exact inputs and probe results are retained; the current reader must
    # recheck the source requirement rather than trust the old passing flag.
    legacy = gates.model_copy(update={'id': 'legacy-empty-source-gate', 'missing_gates': ()})
    owners._append(legacy)
    with pytest.raises(ValueError, match='gates'):
        ops.admissions.decide(request.id, approved=True, reviewer='operator',
            conditions='read old gate results', reason='Try historical admission')
    assert item.ref not in ops.store.read().head(scope.id).allowed_background


def test_meaningful_source_passage_can_pass_admission(tmp_path):
    _, ops, item, scope, request, gates, calls = admission(tmp_path, ((4, 22),))
    assert calls and not gates.missing_gates
    ops.admissions.decide(request.id, approved=True, reviewer='operator',
        conditions='read exact source and probe results', reason='Admit background')
    assert item.ref in ops.store.read().head(scope.id).allowed_background
