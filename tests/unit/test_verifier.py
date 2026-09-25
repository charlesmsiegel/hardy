import hashlib
import importlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

NOW = datetime(2026, 7, 24, tzinfo=UTC)
RUN_ID = UUID('12345678-1234-5678-1234-567812345678')


def _claim(domain):
    proposal = domain.FormalizationProposal(
        restatement='Two equals two.',
        domains=(),
        quantifiers=(),
        assumptions=(),
        interpretation_choices=(),
        theorem_name='two_eq_two',
        binders='',
        proposition='2 = 2',
    )
    environment = domain.EnvironmentIdentity(
        lean_version='4.32.0',
        lean_commit='8c9756b',
        mathlib_revision='81a5d257',
        lake_manifest_sha256='b' * 64,
        imports=('Mathlib',),
    )
    return domain.freeze_claim('Two equals two.', proposal, environment, NOW)


def _store(storage, tmp_path):
    return storage.RunStore.create(tmp_path, 'verify', now=NOW, run_id=RUN_ID)


def _process_result(process, spec, *, stdout='', returncode=0, timed_out=False, overflow=False):
    return process.ProcessResult(
        argv=spec.argv,
        cwd=spec.cwd,
        returncode=returncode,
        stdout=stdout,
        stderr='',
        timed_out=timed_out,
        output_overflow=overflow,
        duration_ms=4,
    )


def _audit_line_report(process, spec, data, *, line=None):
    """What real Lean says for Hardy's `#print axioms`: a message positioned
    on the line that asked, which is the last line of the file it was given."""
    source = Path(spec.argv[-1]).read_text(encoding='utf-8')
    where = source.count('\n') if line is None else line
    stdout = json.dumps(
        {'severity': 'information', 'pos': {'line': where, 'column': 0}, 'data': data}
    )
    return _process_result(process, spec, stdout=stdout)


@pytest.mark.parametrize(
    'proof_body',
    (
        'by sorry',
        'by admit',
        'by?',
        'by exact sorryAx _ true',
        'by\n  axiom invented : False\n  trivial',
        'by\n  opaque invented : True := True.intro\n  trivial',
        # A raw string ending in a backslash. `\` is an ordinary character in
        # `r"..."`, so the literal ends at that quote -- but the scanner read it
        # as an escape, ran on, and blanked the `sorry` below out of existence.
        'by\n  have h := r"a\\"\n  sorry',
        # `r#"..."#` exists so the body may hold a bare `"`. Ending the literal
        # there left the rest of the line looking like code, and vice versa.
        'by\n  have h := r#"a " b"#\n  sorry',
    ),
)
def test_verifier_rejects_holes_and_declarations_before_running_lean(
    tmp_path, proof_body
) -> None:
    domain = importlib.import_module('hardy.workflows.contracts')
    storage = importlib.import_module('hardy.workflows.storage')
    verifier = importlib.import_module('hardy.formal.verifier')
    claim = _claim(domain)
    store = _store(storage, tmp_path)
    final = verifier.FinalVerifier(
        lake=tmp_path / 'lake.exe',
        lean_project=tmp_path,
        environment=claim.environment,
        limits=domain.RunLimits(),
        runner=lambda _: pytest.fail('forbidden source must not reach Lean'),
    )

    result = final.verify(claim, proof_body, store)

    assert not result.verified
    assert result.reason is domain.TerminalReason.FORBIDDEN_HOLE
    assert (store.path / 'lean' / 'last-attempt.lean').exists()
    assert not (store.path / 'lean' / 'Main.lean').exists()


def test_verifier_runs_fresh_lean_and_accepts_only_the_standard_axiom_allowlist(
    tmp_path,
) -> None:
    domain = importlib.import_module('hardy.workflows.contracts')
    process = importlib.import_module('hardy.foundation.process')
    storage = importlib.import_module('hardy.workflows.storage')
    verifier = importlib.import_module('hardy.formal.verifier')
    claim = _claim(domain)
    store = _store(storage, tmp_path)
    observed = {}
    message = (
        'two_eq_two depends on axioms: '
        '[propext, Quot.sound, Classical.choice]'
    )

    def runner(spec):
        observed['source'] = (Path(spec.argv[-1])).read_text(encoding='utf-8')
        observed['cwd'] = spec.cwd
        return _audit_line_report(process, spec, message)

    final = verifier.FinalVerifier(
        lake=tmp_path / 'lake.exe',
        lean_project=tmp_path / 'lean-project',
        environment=claim.environment,
        limits=domain.RunLimits(),
        runner=runner,
    )
    proof = (
        'by\n'
        '  -- ordinary prose may mention sorry, axiom, or opaque\n'
        '  have label : String := '
        + chr(34)
        + 'admit and by? and sorryAx'
        + chr(34)
        + '\n'
        '  rfl'
    )

    result = final.verify(claim, proof, store)

    assert result.verified
    assert result.reason is None
    assert result.axioms == ('propext', 'Quot.sound', 'Classical.choice')
    assert result.verification_sha256 is not None
    assert observed['cwd'] == tmp_path / 'lean-project'
    assert observed['source'].endswith('#print axioms two_eq_two\n')
    assert (store.path / 'lean' / 'Main.lean').read_text(encoding='utf-8') == observed[
        'source'
    ]
    assert not (store.path / 'lean' / 'last-attempt.lean').exists()


def test_verifier_rejects_a_changed_signature_hash_without_running_lean(
    tmp_path,
) -> None:
    domain = importlib.import_module('hardy.workflows.contracts')
    storage = importlib.import_module('hardy.workflows.storage')
    verifier = importlib.import_module('hardy.formal.verifier')
    claim = _claim(domain)
    changed = claim.model_copy(
        update={
            'proposal': claim.proposal.model_copy(update={'proposition': '2 = 3'})
        }
    )
    store = _store(storage, tmp_path)
    final = verifier.FinalVerifier(
        lake=tmp_path / 'lake.exe',
        lean_project=tmp_path,
        environment=claim.environment,
        limits=domain.RunLimits(),
        runner=lambda _: pytest.fail('a mismatched claim must not reach Lean'),
    )

    result = final.verify(changed, 'by rfl', store)

    assert result.reason is domain.TerminalReason.STATEMENT_MISMATCH
    assert not result.verified


def test_verifier_rejects_top_level_declarations_in_frozen_signature_fields(
    tmp_path,
) -> None:
    domain = importlib.import_module('hardy.workflows.contracts')
    storage = importlib.import_module('hardy.workflows.storage')
    verifier = importlib.import_module('hardy.formal.verifier')
    original = _claim(domain)
    injected_proposition = (
        'True := by trivial\n'
        'axiom invented : False\n'
        'theorem hidden : True'
    )
    proposal = original.proposal.model_copy(
        update={'proposition': injected_proposition}
    )
    claim = domain.freeze_claim(
        original.original_text,
        proposal,
        original.environment,
        original.approved_at,
    )
    store = _store(storage, tmp_path)
    final = verifier.FinalVerifier(
        lake=tmp_path / 'lake.exe',
        lean_project=tmp_path,
        environment=claim.environment,
        limits=domain.RunLimits(),
        runner=lambda _: pytest.fail('injected declarations must not reach Lean'),
    )

    result = final.verify(claim, 'by rfl', store)

    assert not result.verified
    assert result.reason is domain.TerminalReason.FORBIDDEN_HOLE


@pytest.mark.parametrize(
    ('returncode', 'timed_out', 'overflow', 'message', 'expected_reason'),
    (
        (1, False, False, '', 'lean_elaboration_failure'),
        (0, True, False, '', 'timeout_budget_exhausted'),
        (0, False, True, '', 'timeout_budget_exhausted'),
        (0, False, False, '', 'lean_elaboration_failure'),
        (
            0,
            False,
            False,
            'two_eq_two depends on axioms: [sorryAx]',
            'unexpected_axiom',
        ),
        (
            0,
            False,
            False,
            'two_eq_two depends on axioms: [invented]',
            'unexpected_axiom',
        ),
    ),
)
def test_verifier_fails_closed_for_process_and_axiom_failures(
    tmp_path, returncode, timed_out, overflow, message, expected_reason
) -> None:
    domain = importlib.import_module('hardy.workflows.contracts')
    process = importlib.import_module('hardy.foundation.process')
    storage = importlib.import_module('hardy.workflows.storage')
    verifier = importlib.import_module('hardy.formal.verifier')
    claim = _claim(domain)
    store = _store(storage, tmp_path)
    source = {}

    def runner(spec):
        source['text'] = Path(spec.argv[-1]).read_text(encoding='utf-8')
        # Positioned where Hardy's `#print axioms` sits, so each case fails for
        # its own reason rather than for a report on the wrong line.
        stdout = (
            json.dumps(
                {
                    'severity': 'information',
                    'pos': {'line': source['text'].count('\n'), 'column': 0},
                    'data': message,
                }
            )
            if message
            else ''
        )
        return _process_result(
            process,
            spec,
            stdout=stdout,
            returncode=returncode,
            timed_out=timed_out,
            overflow=overflow,
        )

    final = verifier.FinalVerifier(
        lake=tmp_path / 'lake.exe',
        lean_project=tmp_path,
        environment=claim.environment,
        limits=domain.RunLimits(),
        runner=runner,
    )

    result = final.verify(claim, 'by rfl', store)

    assert not result.verified
    assert result.reason.value == expected_reason
    assert (store.path / 'lean' / 'last-attempt.lean').exists()
    assert (store.path / 'lean' / 'verification.json').exists()
    assert not (store.path / 'lean' / 'Main.lean').exists()


def test_verification_result_rejects_a_verified_record_with_no_evidence(tmp_path) -> None:
    domain = importlib.import_module('hardy.workflows.contracts')
    verifier = importlib.import_module('hardy.formal.verifier')

    with pytest.raises(ValidationError, match='evidence'):
        verifier.VerificationResult(
            verified=True,
            reason=None,
            axioms=(),
            diagnostics=(),
            source_sha256='s' * 64,
            verification_sha256='v' * 64,
        )

    evidence = domain.VerificationEvidence(
        claim_sha256='a' * 64,
        source_sha256='s' * 64,
        axioms=(),
        toolchain=_claim(domain).environment,
    )
    with pytest.raises(ValidationError, match='evidence'):
        verifier.VerificationResult(
            verified=True,
            reason=None,
            axioms=(),
            diagnostics=(),
            source_sha256='s' * 64,
            verification_sha256='v' * 64,
            evidence=evidence,
        )
    with pytest.raises(ValidationError, match='evidence'):
        verifier.VerificationResult(
            verified=True,
            reason=None,
            axioms=('Classical.choice',),
            diagnostics=(),
            source_sha256='s' * 64,
            verification_sha256=evidence.digest,
            evidence=evidence,
        )


def test_rejected_verification_result_rejects_evidence(tmp_path) -> None:
    domain = importlib.import_module('hardy.workflows.contracts')
    verifier = importlib.import_module('hardy.formal.verifier')
    evidence = domain.VerificationEvidence(
        claim_sha256='a' * 64,
        source_sha256='s' * 64,
        axioms=(),
        toolchain=_claim(domain).environment,
    )

    with pytest.raises(ValidationError, match='evidence'):
        verifier.VerificationResult(
            verified=False,
            reason=domain.TerminalReason.LEAN_ELABORATION_FAILURE,
            axioms=(),
            diagnostics=(),
            source_sha256='s' * 64,
            verification_sha256=evidence.digest,
            evidence=evidence,
        )


def test_accepted_proof_carries_evidence_that_re_derives_its_digest(tmp_path) -> None:
    domain = importlib.import_module('hardy.workflows.contracts')
    process = importlib.import_module('hardy.foundation.process')
    storage = importlib.import_module('hardy.workflows.storage')
    verifier = importlib.import_module('hardy.formal.verifier')
    claim = _claim(domain)
    store = _store(storage, tmp_path)
    message = 'two_eq_two depends on axioms: [propext, Classical.choice]'
    final = verifier.FinalVerifier(
        lake=tmp_path / 'lake.exe',
        lean_project=tmp_path / 'lean-project',
        environment=claim.environment,
        limits=domain.RunLimits(),
        runner=lambda spec: _audit_line_report(process, spec, message),
    )

    result = final.verify(claim, 'by rfl', store)

    assert result.verified
    assert result.evidence is not None
    assert result.evidence.claim_sha256 == claim.content_hash
    assert result.evidence.toolchain == claim.environment
    assert result.evidence.axioms == ('propext', 'Classical.choice')
    assert result.evidence.source_sha256 == result.source_sha256
    assert result.verification_sha256 == result.evidence.digest
    source = (store.path / 'lean' / 'Main.lean').read_bytes()
    assert result.evidence.source_sha256 == hashlib.sha256(source).hexdigest()
    saved = verifier.VerificationResult.model_validate_json(
        (store.path / 'lean' / 'verification.json').read_text(encoding='utf-8')
    )
    assert saved == result


def _verify_reporting(tmp_path, name, report, proof='by rfl'):
    """Verify a claim named `name` against one Lean information message."""
    domain = importlib.import_module('hardy.workflows.contracts')
    process = importlib.import_module('hardy.foundation.process')
    storage = importlib.import_module('hardy.workflows.storage')
    verifier = importlib.import_module('hardy.formal.verifier')
    proposal = domain.FormalizationProposal(
        restatement='Two equals two.',
        domains=(),
        quantifiers=(),
        assumptions=(),
        interpretation_choices=(),
        theorem_name=name,
        binders='',
        proposition='2 = 2',
    )
    environment = domain.EnvironmentIdentity(
        lean_version='4.32.0',
        lean_commit='8c9756b',
        mathlib_revision='81a5d257',
        lake_manifest_sha256='b' * 64,
        imports=('Mathlib',),
    )
    claim = domain.freeze_claim('Two equals two.', proposal, environment, NOW)
    store = _store(storage, tmp_path)
    final = verifier.FinalVerifier(
        lake=tmp_path / 'lake.exe',
        lean_project=tmp_path,
        environment=environment,
        limits=domain.RunLimits(),
        runner=lambda spec: _audit_line_report(process, spec, report),
    )
    return final.verify(claim, proof, store)


def test_a_primed_theorem_name_can_be_audited(tmp_path) -> None:
    """`\\badd_comm'\\b` never matches, so this used to report no axiom report
    at all and refuse every primed declaration the kernel had accepted."""
    result = _verify_reporting(
        tmp_path,
        "add_comm'",
        "'add_comm'' depends on axioms: [propext]",
    )
    assert result.verified
    assert result.axioms == ('propext',)


def test_a_duplicated_axiom_report_is_not_resolved_by_position(tmp_path) -> None:
    """Two reports for one name means something other than Lean printed one."""
    result = _verify_reporting(
        tmp_path,
        'two_eq_two',
        "'two_eq_two' depends on axioms: [sorryAx]\\n"
        "'two_eq_two' does not depend on any axioms",
    )
    assert not result.verified
    assert result.reason.value == 'lean_elaboration_failure'


def test_a_report_for_another_declaration_is_not_this_ones(tmp_path) -> None:
    result = _verify_reporting(
        tmp_path,
        'two_eq_two',
        "'Other.two_eq_two' does not depend on any axioms",
    )
    assert not result.verified
    assert result.reason.value == 'lean_elaboration_failure'


def _final_verifier(tmp_path, runner):
    domain = importlib.import_module('hardy.workflows.contracts')
    verifier = importlib.import_module('hardy.formal.verifier')
    claim = _claim(domain)
    return claim, verifier.FinalVerifier(
        lake=tmp_path / 'lake.exe',
        lean_project=tmp_path,
        environment=claim.environment,
        limits=domain.RunLimits(),
        runner=runner,
    )


@pytest.mark.parametrize(
    'body',
    (
        'by trivial\n#exit',
        "by trivial\n#print \"'T' does not depend on any axioms\"",
        'by trivial\nmacro_rules | `(#print axioms $x) => `(#eval 0)',
        'by exact «sorryAx» _ false',
        # The issue's own reproduction: an escaped hole, a forged report, and
        # an `#exit` that stops Hardy's `#print axioms` from ever running.
        "by exact «sorryAx» _ false\n\n#print \"'two_eq_two' depends on axioms: [propext]\"\n#exit",
        'by trivial\n\ntheorem other : True := trivial',
        'by trivial\nattribute [simp] Nat.add_comm',
        'by trivial\nend',
        '@[simp] theorem x : True := trivial',
        # Commands Batteries and Mathlib add, stepping out of the body as any
        # other command does.
        'by trivial\ndeclare_simp_like_tactic mySimp "my_simp " fun c => c',
        'by trivial\nirreducible_def f : Nat := 1',
        'by trivial\nalias foo := Nat.add_comm',
        # Code run during elaboration can print a report of its own choosing
        # on the audit line and exit, so the ways into it are refused too.
        'by\n  run_tac pure ()',
        'by exact (by_elab pure (Lean.mkConst ``True.intro))',
        'by\n  conv => run_conv pure ()\n  trivial',
        'by exact eval% (2 = 2 : Bool)',
        'by exact eval%(2 = 2 : Bool)',
        'by trivial\n@[simp]',
        # A quotation is read like the rest of the body. Where it ends is
        # Lean's token table's to say, and a token the imports declare with an
        # unbalanced parenthesis (`⟪(`, `⸨)`) ends it somewhere no count here
        # can see, so blanking it could hide a real command. The cost is a
        # body that builds quoted command syntax, which is refused.
        "by\n  have _ := `(term| '(')\n  trivial\n#exit ')'",
        'by\n  have _ := `(term| «(»)\n  trivial\n#exit )',
        'by\n  have _ := `(command| axiom bad : False)\n  rfl',
        'by\n  have _ := `(term| ⟪( 1)\n  trivial\nmacro_rules | `(#print axioms $x) => `(#eval 0)\n⸨)',
    ),
)
def test_a_body_that_issues_commands_is_refused_before_lean_runs(tmp_path, body) -> None:
    domain = importlib.import_module('hardy.workflows.contracts')
    storage = importlib.import_module('hardy.workflows.storage')
    calls = []
    claim, final = _final_verifier(tmp_path, lambda spec: calls.append(spec))

    result = final.verify(claim, body, _store(storage, tmp_path))

    assert not result.verified
    assert result.reason is domain.TerminalReason.FORBIDDEN_HOLE
    assert calls == []


@pytest.mark.parametrize(
    'body',
    (
        # Lean ends a name at `#` and a numeral before a keyword, so each of
        # these issues its command -- checked against Lean 4.35.0-rc3, where
        # `rfl#exit` interrupts the file and `Eq.refl 1macro_rules ...` adds
        # a macro rule. The gate's lookbehind read the glued word as part of
        # the name before it and let all of them through.
        'rfl#exit',
        "rfl#print \"'two_eq_two' does not depend on any axioms\"",
        'Eq.refl 1macro_rules | `(#print axioms $x) => `(#eval 0)',
        'Eq.refl 0b1macro_rules | `(#print axioms $x) => `(#eval 0)',
        'by trivial\n1axiom cheat : False',
        'by exact (1,2).1elab_rules : term | _ => pure (Lean.mkConst ``True.intro)',
        'rfl@[simp] theorem x : True := trivial',
    ),
)
def test_a_command_glued_to_the_token_before_it_is_refused(tmp_path, body) -> None:
    domain = importlib.import_module('hardy.workflows.contracts')
    storage = importlib.import_module('hardy.workflows.storage')
    calls = []
    claim, final = _final_verifier(tmp_path, lambda spec: calls.append(spec))

    result = final.verify(claim, body, _store(storage, tmp_path))

    assert not result.verified
    assert result.reason is domain.TerminalReason.FORBIDDEN_HOLE
    assert calls == []


def test_a_report_on_any_line_but_the_audit_line_is_ignored(tmp_path) -> None:
    """A `#print "..."` a body smuggled in is positioned on its own line; only
    the line Hardy wrote its `#print axioms` on can speak for the audit."""
    domain = importlib.import_module('hardy.workflows.contracts')
    process = importlib.import_module('hardy.foundation.process')
    storage = importlib.import_module('hardy.workflows.storage')
    claim, final = _final_verifier(
        tmp_path,
        lambda spec: _audit_line_report(
            process, spec, "'two_eq_two' does not depend on any axioms", line=3
        ),
    )

    result = final.verify(claim, 'by trivial', _store(storage, tmp_path))

    assert not result.verified
    assert result.reason is domain.TerminalReason.LEAN_ELABORATION_FAILURE


def test_a_report_with_no_position_is_not_the_audit_lines(tmp_path) -> None:
    domain = importlib.import_module('hardy.workflows.contracts')
    process = importlib.import_module('hardy.foundation.process')
    storage = importlib.import_module('hardy.workflows.storage')
    claim, final = _final_verifier(
        tmp_path,
        lambda spec: _process_result(
            process,
            spec,
            stdout=json.dumps(
                {'severity': 'information', 'data': "'two_eq_two' does not depend on any axioms"}
            ),
        ),
    )

    result = final.verify(claim, 'by trivial', _store(storage, tmp_path))

    assert not result.verified
    assert result.reason is domain.TerminalReason.LEAN_ELABORATION_FAILURE


@pytest.mark.parametrize(
    'body',
    (
        'by simp',
        'by\n  intro h\n  exact h',
        '⟨_, _⟩',
        'by\n  open Classical in\n  simp',
        'by\n  set_option maxHeartbeats 400000 in\n  simp [Finset.sum_def, h.def, hdef, h_end]',
        # A guillemet name is a name, and an array literal is not a command.
        'by\n  have h : «my lemma» = 1 := rfl\n  exact #[1, 2].size_pos',
        'by\n  -- #exit is only a remark here\n  have s : String := "#print axioms"\n  rfl',
        # Syntax a proof builds and never runs.
        'by\n  have _ := `(tactic| simp)\n  rfl',
        # Names and numerals that only contain a command word.
        'by simp [h1def, x1theorem, Nat.end_of, mymacro_rules]',
        'by\n  have h : (0xdef : Nat) = 3567 := rfl\n  exact h ▸ rfl',
    ),
)
def test_ordinary_bodies_still_pass(tmp_path, body) -> None:
    process = importlib.import_module('hardy.foundation.process')
    storage = importlib.import_module('hardy.workflows.storage')
    claim, final = _final_verifier(
        tmp_path,
        lambda spec: _audit_line_report(
            process, spec, "'two_eq_two' depends on axioms: [propext]"
        ),
    )

    result = final.verify(claim, body, _store(storage, tmp_path))

    assert result.verified, result.diagnostics


def test_a_report_after_an_exit_is_not_graded(tmp_path) -> None:
    """Even with a report on the audit line, a run `#exit` interrupted is not
    one Hardy's own `#print axioms` finished in. The body gate refuses `#exit`
    before Lean runs; this is the check behind it, so a spelling the gate does
    not know still cannot produce a verified result."""
    domain = importlib.import_module('hardy.workflows.contracts')
    process = importlib.import_module('hardy.foundation.process')
    storage = importlib.import_module('hardy.workflows.storage')

    def runner(spec):
        source = Path(spec.argv[-1]).read_text(encoding='utf-8')
        lines = (
            {'severity': 'warning', 'pos': {'line': 2, 'column': 0},
             'data': "using 'exit' to interrupt Lean"},
            {'severity': 'information', 'pos': {'line': source.count('\n'), 'column': 0},
             'data': "'two_eq_two' does not depend on any axioms"},
        )
        return _process_result(process, spec, stdout='\n'.join(json.dumps(item) for item in lines))

    claim, final = _final_verifier(tmp_path, runner)
    result = final.verify(claim, 'by trivial', _store(storage, tmp_path))

    assert not result.verified
    assert result.reason is domain.TerminalReason.LEAN_ELABORATION_FAILURE


FORGE = "\n\nmacro_rules | `(#print axioms $_) => `(#print \"'two_eq_two' depends on axioms: []\")"


@pytest.mark.parametrize(
    'hiding_line',
    (
        # Review round 1, C1: after a symbol token ending in `'` (core's `]'`
        # and `×'`), `'"'` is not a char literal to Lean.
        "  have _q : Lean.MacroM Lean.Syntax := `(xs[0]'\"'\")",
        "  have _q : Lean.MacroM Lean.Syntax := `(Nat ×'\"'\")",
        # C2: inside an interpolation, and after `!` or `λ`, it is one.
        "  have _s : String := s!\"{'\"'}\"",
        "  have _q : Lean.MacroM Lean.Syntax := `(!'\"')",
        "  have _q : Lean.MacroM Lean.Syntax := `(λ'\"' => 0)",
    ),
)
def test_a_body_hiding_a_forged_report_behind_a_quote_is_refused(tmp_path, hiding_line) -> None:
    """Each of these elaborates under Lean 4.35.0-rc3 and puts the forged
    report on Hardy's audit line; with the lexer reading `'"'` one way when
    Lean reads it the other, the gate saw neither `«sorryAx»` nor
    `macro_rules`, and the verifier graded the proof of a `sorryAx` clean.
    Driven end to end: the runner must never be called."""
    domain = importlib.import_module('hardy.workflows.contracts')
    storage = importlib.import_module('hardy.workflows.storage')
    calls = []
    claim, final = _final_verifier(tmp_path, lambda spec: calls.append(spec))
    body = f"by\n{hiding_line}\n  exact «sorryAx» _ false{FORGE}"

    result = final.verify(claim, body, _store(storage, tmp_path))

    assert not result.verified
    assert result.reason is domain.TerminalReason.FORBIDDEN_HOLE
    assert calls == []


@pytest.mark.parametrize(
    'body',
    (
        # BitVec literals: `#` straight after a numeral is not a command.
        'by\n  have h : (0#w : BitVec w) = 0 := rfl\n  simp',
        'by\n  have h : x + 0#w = x := by simp\n  exact h',
        'by\n  have h : (1#(w+1)).toNat = 1 := by simp\n  simp [h, -1#w]',
        # A keyword after a projection dot is a field (Lean's `rawIdent`).
        'by\n  have e := (i).end\n  trivial',
        # A char literal where it certainly starts.
        "by\n  have c : Char := '\"'\n  decide",
        "by\n  have l : List Char := ['a', 'b']\n  rfl",
    ),
)
def test_bitvec_literals_fields_and_plain_chars_still_pass(tmp_path, body) -> None:
    process = importlib.import_module('hardy.foundation.process')
    storage = importlib.import_module('hardy.workflows.storage')
    claim, final = _final_verifier(
        tmp_path,
        lambda spec: _audit_line_report(
            process, spec, "'two_eq_two' depends on axioms: [propext]"
        ),
    )

    result = final.verify(claim, body, _store(storage, tmp_path))

    assert result.verified, result.diagnostics


@pytest.mark.parametrize(
    'body',
    ('rfl\n0#exit', 'by simp [0#eval]', 'Eq.refl 0#print', 'Eq.refl 0#where'),
)
def test_a_command_after_a_numeral_is_still_refused(tmp_path, body) -> None:
    """Lean splits `0#exit` into `0` and the `#exit` command: `#exit` is the
    longest token there. Only a short width variable is BitVec's."""
    domain = importlib.import_module('hardy.workflows.contracts')
    storage = importlib.import_module('hardy.workflows.storage')
    calls = []
    claim, final = _final_verifier(tmp_path, lambda spec: calls.append(spec))

    result = final.verify(claim, body, _store(storage, tmp_path))

    assert not result.verified
    assert result.reason is domain.TerminalReason.FORBIDDEN_HOLE
    assert calls == []


def test_a_body_with_too_many_readings_is_refused(tmp_path) -> None:
    """Past the lexer's bound nothing says where the body's literals end."""
    domain = importlib.import_module('hardy.workflows.contracts')
    storage = importlib.import_module('hardy.workflows.storage')
    calls = []
    claim, final = _final_verifier(tmp_path, lambda spec: calls.append(spec))
    body = "by\n" + '  have s := "{"\n' * 60 + "  trivial"

    result = final.verify(claim, body, _store(storage, tmp_path))

    assert not result.verified
    assert result.reason is domain.TerminalReason.FORBIDDEN_HOLE
    assert calls == []


def test_a_body_hiding_code_behind_a_one_reading_guillemet_is_refused(tmp_path) -> None:
    """Review round 2, N1. Lean 4.35.0-rc3 elaborates this with a `sorry` and
    the forged report on the audit line: `('«')` is a char to Lean, but the
    symbol reading opened a name there that ran to the `»` of `«Lean»`,
    hiding the `sorry` and the glued `2macro_rules`. Verified before the fix."""
    domain = importlib.import_module('hardy.workflows.contracts')
    storage = importlib.import_module('hardy.workflows.storage')
    calls = []
    claim, final = _final_verifier(tmp_path, lambda spec: calls.append(spec))
    body = (
        "by\n  exact (fun (_ : Char) (h : 2 = 2) => h) ('«') sorry |>.trans <| Eq.refl "
        "2macro_rules (kind := «Lean».Parser.Command.printAxioms) | `(#print axioms $_) => "
        "`(#print \"'two_eq_two' depends on axioms: []\")"
    )

    result = final.verify(claim, body, _store(storage, tmp_path))

    assert not result.verified
    assert result.reason is domain.TerminalReason.FORBIDDEN_HOLE
    assert calls == []
