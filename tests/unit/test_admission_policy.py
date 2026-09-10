"""Admission decisions without a session, provider, filesystem or real Lean."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from hardy.formal import refute
from hardy.formal.lean import LeanDiagnostic, LeanToolResult
from hardy.workflows import admission
from hardy.workflows.ledger.contracts import ArtifactRef, Scope, VersionRef


@pytest.fixture
def policy_module():
    return admission


def result_for(source, *, closes=None, bad_line=None):
    diagnostics = tuple(
        LeanDiagnostic(severity="error", message="unsolved goals", line=index, column=0)
        for index, line in enumerate(source.splitlines(), 1)
        if line.startswith("example") and not line.endswith((f"by {closes}", "by sorry"))
    )
    if bad_line is not None:
        diagnostics += (LeanDiagnostic(severity="error", message="bad declaration", line=bad_line, column=0),)
    return LeanToolResult(False, "bad declaration" if bad_line else "", source, diagnostics=diagnostics)


@pytest.mark.parametrize("kind", ["LOCAL_BINDER", "LOCAL_HYPOTHESIS", "CONJECTURE"])
def test_context_and_conjectures_cannot_request_global_trust(policy_module, kind):
    p = policy_module
    request = p.AdmissionRequest(getattr(p.TrustRequestKind, kind))
    assert "global" in p.AdmissionPolicy().request_refusal(request)


@pytest.mark.parametrize("digest,expected", [("a" * 64, "must prove"), ("b" * 64, "stale")])
def test_target_and_revision_drift_refuse_self_assumption(policy_module, digest, expected):
    p = policy_module
    scope = Scope(id="project", must_prove=(VersionRef(id="goal", digest="a" * 64),))
    request = p.AdmissionRequest(p.TrustRequestKind.GLOBAL_ASSUMPTION, subject=VersionRef(id="goal", digest=digest), scope=scope)
    assert expected in p.AdmissionPolicy().request_refusal(request)


@pytest.mark.parametrize("subject,scope", [(VersionRef(id="goal", digest="a" * 64), None), (None, Scope(id="project"))])
def test_scope_and_subject_are_paired(policy_module, subject, scope):
    p = policy_module
    request = p.AdmissionRequest(p.TrustRequestKind.GLOBAL_ASSUMPTION, subject=subject, scope=scope)
    assert "together" in p.AdmissionPolicy().request_refusal(request)


def test_distinct_subject_and_no_ledger_identity_are_supported(policy_module):
    p = policy_module
    policy = p.AdmissionPolicy()
    assert policy.request_refusal(p.AdmissionRequest(p.TrustRequestKind.GLOBAL_ASSUMPTION)) is None
    assert policy.request_refusal(p.AdmissionRequest(p.TrustRequestKind.GLOBAL_ASSUMPTION, subject=VersionRef(id="lemma", digest="a" * 64), scope=Scope(id="project", must_prove=(VersionRef(id="goal", digest="a" * 64),)))) is None


def test_search_is_exhausted_after_consume_and_unfinished_attempt_is_honest(policy_module):
    p = policy_module
    search = p.SearchEvidence()
    assert search.refusal(available=True) is not None
    assert search.refusal(available=False) is None
    search.attempted_inspection()
    assert search.refusal(available=True) is None
    assert search.description() == ["1 inspection(s) attempted since the last request, none finished"]
    search.consume()
    assert search.refusal(available=True) is not None


def test_completed_search_records_resolved_names_and_caps_prompt(policy_module):
    search = policy_module.SearchEvidence()
    search.attempted_inspection()
    search.note_inspected([f"n{i}" for i in range(22)], '{"resolved": [{"name": "n21"}]}')
    description = search.description()
    assert description[0] == "22 names inspected; last 20:"
    assert description[1] == "n2 ✗"
    assert description[-1] == "n21 ✓"
    search.consume()
    assert search.description() == []


@pytest.mark.parametrize("statement", ["axiom bad : True", "True\naxiom bad : False"])
def test_shape_refusal_precedes_any_lean_execution(policy_module, statement):
    p = policy_module
    def unexpected(source):
        pytest.fail("malformed statement must not run Lean")
    decision = p.AdmissionPolicy().check_global("f", statement, p.ProbeOperations(unexpected, unexpected))
    assert decision.refusal


def test_assumption_probe_keeps_axiom_last_and_returns_actual_proof(policy_module):
    p = policy_module
    captured = []
    def run(source):
        captured.append(source)
        return result_for(source, closes="exact?")
    refusal, caveat = p.assumption_probe("axiom difficult : True", run_source=run)
    assert "theorem, not an assumption" in refusal
    assert "by exact?" in refusal
    assert caveat == ""
    lines = captured[0].splitlines()
    assert lines[0] == "import Mathlib"
    assert lines[2].startswith("example")
    assert lines[-1] == "axiom difficult : True"
    assert all(not line.startswith("axiom") for line in lines[:-1])


def test_failed_declaration_is_not_mistaken_for_a_valid_assumption(policy_module):
    refusal, caveat = policy_module.assumption_probe("axiom bad : Missing", run_source=lambda source: result_for(source, bad_line=9))
    assert "does not accept" in refusal
    assert "bad declaration" in refusal
    assert not caveat


def test_unavailable_probe_skips_vacuity_and_carries_caveat(policy_module):
    p = policy_module
    calls = []
    def run(source):
        calls.append(source)
        raise OSError("offline")
    decision = p.AdmissionPolicy().check_global("f", "∀ (n : Nat), n = n", p.ProbeOperations(run, run))
    assert decision.refusal is None
    assert "could not be checked (offline)" in decision.checked
    assert len(calls) == 1


def test_vacuity_probe_runs_on_stripped_hypotheses(policy_module):
    captured = []
    def run(source):
        captured.append(source)
        return result_for(source, closes="simp")
    warning = policy_module.vacuity_probe("∀ (n : Nat) (h : n > 0), n = n", run_source=run)
    assert "every hypothesis removed" in warning
    assert "h :" not in captured[0]
    assert "(n : Nat)" in captured[0]


@pytest.mark.parametrize("imported", [True, False])
def test_refutation_header_ownership_and_refuted_disposition(policy_module, imported):
    p = policy_module
    captured = []
    def run(source):
        captured.append(source)
        complete = source if imported else "import Mathlib\n\n" + source
        return result_for(complete, closes="decide")
    verdict = p.refutation_probe("False", run_source=run, imported=imported)
    decision = p.AdmissionPolicy().refutation(verdict)
    assert decision.refusal
    assert verdict.tactic == "decide"
    assert captured[0].startswith("import Mathlib") is imported


def test_inconclusive_refutation_is_a_gap_not_a_refusal(policy_module):
    decision = policy_module.AdmissionPolicy().refutation(refute.Verdict(False, caveat="timed out"))
    assert decision.refusal is None
    assert decision.checked == "timed out"


def test_paper_probe_elaborates_then_refutes_before_admission(policy_module):
    p = policy_module
    calls = []
    def elaborate(source):
        calls.append("elaborate")
        return result_for(source)
    def refutation(source):
        calls.append("refute")
        return result_for(source, closes="decide")
    decision = p.AdmissionPolicy().check_paper("f", "Papers.P.f", "False", "statement", p.ProbeOperations(elaborate, refutation))
    assert "NEGATION" in decision.refusal
    assert "decide" in decision.refusal
    assert calls == ["elaborate", "refute"]


def test_opaque_constant_checks_shape_but_runs_no_proposition_probes(policy_module):
    p = policy_module
    def unexpected(source):
        pytest.fail("an opaque type is not a proposition to prove or refute")
    operations = p.ProbeOperations(unexpected, unexpected)
    policy = p.AdmissionPolicy()
    malformed = policy.check_paper("f", "Papers.P.f", "axiom other : Nat", "constant", operations)
    assert malformed.refusal
    valid = policy.check_paper("f", "Papers.P.f", "Nat", "constant", operations)
    assert valid.refusal is None
    assert "trust beyond assuming a statement" in valid.checked


@pytest.mark.parametrize("reached,agreed,expected", [(False, False, "unavailable"), (False, True, "unavailable"), (True, False, "quarantine"), (True, True, "accept")])
def test_reader_unavailability_and_disagreement_are_distinct(policy_module, reached, agreed, expected):
    assert policy_module.AdmissionPolicy().faithfulness(reached=reached, agreed=agreed).value == expected


def test_paper_source_missing_or_unread_is_refused(policy_module):
    p = policy_module
    policy = p.AdmissionPolicy()
    assert policy.source_refusal(None) is not None
    artifact = ArtifactRef(uri="arxiv:2501.00001v2", digest="a" * 64, locator="Theorem 1")
    assert policy.source_refusal(p.SourceEvidence(artifact=artifact, statement_read=False)) is not None
    evidence = p.SourceEvidence(artifact=artifact, statement_read=True, subject=VersionRef(id="source", digest="b" * 64))
    assert policy.source_refusal(evidence) is None


def test_paper_selection_requires_held_source_and_records_exact_excerpt(policy_module):
    from hashlib import sha256
    from hardy.literature.statements import survey
    policy = policy_module.AdmissionPolicy()
    wanted, evidence, refusal = policy.paper_statement(None, None, "thm:one")
    assert wanted is evidence is None
    assert "source" in refusal
    reading = survey({"main.tex": r"\documentclass{article}\begin{document}\begin{theorem}\label{thm:one}A claim.\end{theorem}\end{document}"})
    record = SimpleNamespace(arxiv_id="2501.00001v2")
    wanted, evidence, refusal = policy.paper_statement(record, reading, "thm:one")
    assert refusal is None
    assert evidence.artifact.uri == "arxiv:2501.00001v2/statement-excerpt"
    assert evidence.artifact.locator == "main.tex#thm:one"
    assert evidence.artifact.digest == sha256(wanted.text.encode("utf-8")).hexdigest()
    missing, evidence, refusal = policy.paper_statement(record, reading, "thm:absent")
    assert missing is evidence is None
    assert "makes no statement" in refusal


@pytest.mark.parametrize("kind", ["LOCAL_BINDER", "LOCAL_HYPOTHESIS", "CONJECTURE"])
@pytest.mark.parametrize("route", ["_request_assumption", "_assume_statement"])
def test_excluded_category_refuses_before_any_adapter_effect_or_consumption(policy_module, kind, route):
    from hardy.workflows.interactive.admission import AssumptionAdmission
    p = policy_module
    adapter = AssumptionAdmission()
    adapter.attempted_inspection()
    result = getattr(adapter, route)(
        {}, search_available=True, operations=None,
        admission_request=p.AdmissionRequest(getattr(p.TrustRequestKind, kind)),
    )
    assert not result.ok
    assert "global" in result.output
    assert adapter.search.attempts == 1


def test_model_payload_cannot_select_preauthorized_mode():
    from hardy.workflows.interactive.admission import AssumptionAdmission
    result = AssumptionAdmission()._request_assumption(
        {"kind": "preauthorized_run_assumption"}, search_available=True, operations=None,
    )
    assert not result.ok
    assert "no `inspect_declarations`" in result.output


def test_preauthorized_declaration_uses_structural_gate_only(policy_module):
    from hardy.formal.contracts import DeclaredAssumption
    p = policy_module
    request = p.AdmissionRequest(p.TrustRequestKind.PREAUTHORIZED_RUN_ASSUMPTION)
    policy = p.AdmissionPolicy()
    good = DeclaredAssumption(name="f", statement="False", source="User's supplied file")
    assert policy.preauthorized_declaration(request, good) is None
    bad = good.model_copy(update={"source": ""})
    assert policy.preauthorized_declaration(request, bad)
    ordinary = p.AdmissionRequest(p.TrustRequestKind.GLOBAL_ASSUMPTION)
    assert policy.preauthorized_declaration(ordinary, good)
