"""The release audit reads a verified-modulo run as a verified run.

An audit that only recognised `kernel_verified` would treat every assumed run
as unverified and skip the checks that matter most for one -- the evidence, the
axiom report, the document -- which is the opposite of what a wider trust base
calls for.
"""

from __future__ import annotations

import importlib


def test_a_modulo_grade_is_audited_like_a_verified_one() -> None:
    acceptance = importlib.import_module("hardy.acceptance")
    domain = importlib.import_module("hardy.domain")

    assert domain.FormalStatus.VERIFIED_MODULO in acceptance.VERIFIED_GRADES
    assert domain.FormalStatus.KERNEL_VERIFIED in acceptance.VERIFIED_GRADES
    assert domain.FormalStatus.PARTIAL not in acceptance.VERIFIED_GRADES


def test_a_modulo_run_may_admit_exactly_the_axioms_it_declared() -> None:
    """The standard allowlist plus what the manifest says was assumed, and
    nothing else: an axiom in neither is the failure this check exists for."""
    acceptance = importlib.import_module("hardy.acceptance")

    assert acceptance.permitted_axioms(("Papers.a.one",)) == frozenset(
        {*acceptance.ALLOWED_AXIOMS, "Papers.a.one"}
    )
    assert acceptance.permitted_axioms(()) == frozenset(acceptance.ALLOWED_AXIOMS)


def test_a_hole_is_never_permitted_however_much_was_assumed() -> None:
    assert "sorryAx" not in acceptance_permitted()


def acceptance_permitted():
    acceptance = importlib.import_module("hardy.acceptance")
    return acceptance.permitted_axioms(("sorryAx",))


def test_a_recorded_run_predating_a_grade_field_still_reconciles(tmp_path) -> None:
    """The trajectory's terminal event and the manifest are written by one run
    and must agree. Comparing the recorded JSON against a re-serialized model
    made every field added afterwards look like a disagreement about a run
    that never disagreed -- so both sides are read through the same model, and
    a real difference in any grade still fails."""
    acceptance = importlib.import_module("hardy.acceptance")
    domain = importlib.import_module("hardy.domain")
    grades = domain.Grades(formal=domain.FormalStatus.PARTIAL, known_gaps=("one",))
    recorded = grades.model_dump(mode="json")
    recorded.pop("assumed")

    assert acceptance.grades_agree(recorded, grades)
    assert not acceptance.grades_agree({**recorded, "known_gaps": ["other"]}, grades)
    assert not acceptance.grades_agree({"nonsense": True}, grades)


# --- The audit must not take the allowlist from the run it is auditing ----------


def _deterministic(tmp_path):
    acceptance = importlib.import_module("hardy.acceptance")
    config_module = importlib.import_module("hardy.config")
    config = config_module.Config(
        model="deterministic-no-model",
        lean_command=("lake", "env", "lean"),
        lean_project=None,
        lean_timeout=30.0,
        latex_command=("tectonic",),
        root=tmp_path,
        project="workspace",
        runs_root=tmp_path,
    )
    return acceptance.run_deterministic_experiment(config, outcome="verified")


def _forge_modulo(run_dir, manifest, *, axiom="falsum", statement="False", declare=None):
    """Rewrite a verified run as a `verified_modulo` one resting on `axiom`.

    Everything a real run writes is rewritten consistently -- the source, the
    evidence over it, the verification record, the terminal event, and the
    artifact hashes -- so nothing but the audit's own cross-checks can tell
    the difference.
    """
    import hashlib
    import json

    domain = importlib.import_module("hardy.domain")
    verifier = importlib.import_module("hardy.verifier")

    main = run_dir / "lean" / "Main.lean"
    main.write_text(f"axiom {axiom} : {statement}\n\n" + main.read_text(encoding="utf-8"),
                    encoding="utf-8")
    source = main.read_text(encoding="utf-8")
    source_sha = hashlib.sha256(source.encode("utf-8")).hexdigest()
    claim = domain.FrozenClaim.model_validate_json(
        (run_dir / "formalization.json").read_text(encoding="utf-8")
    )
    evidence = domain.VerificationEvidence(
        claim_sha256=claim.content_hash,
        source_sha256=source_sha,
        axioms=(axiom, "propext"),
        toolchain=claim.environment,
    )
    result = verifier.VerificationResult(
        verified=True,
        reason=None,
        axioms=evidence.axioms,
        diagnostics=(),
        source_sha256=source_sha,
        verification_sha256=evidence.digest,
        evidence=evidence,
        assumed=(axiom,),
    )
    (run_dir / "lean" / "verification.json").write_text(
        result.model_dump_json(indent=2), encoding="utf-8"
    )
    if declare is not None:
        (run_dir / "assumptions.json").write_text(json.dumps(declare), encoding="utf-8")
    grades = manifest.grades.model_copy(
        update={
            "formal": domain.FormalStatus.VERIFIED_MODULO,
            "assumed": (axiom,),
            "verification_sha256": evidence.digest,
            "verification_evidence": evidence,
        }
    )
    forged = manifest.model_copy(update={"grades": grades})
    events = [
        json.loads(line)
        for line in (run_dir / "trajectory.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    events[-1]["payload"]["grades"] = grades.model_dump(mode="json")
    (run_dir / "trajectory.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
    artifacts = dict(forged.artifacts)
    for relative in list(artifacts):
        path = run_dir / relative
        if path.is_file():
            artifacts[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    if declare is not None:
        artifacts["assumptions.json"] = hashlib.sha256(
            (run_dir / "assumptions.json").read_bytes()
        ).hexdigest()
    forged = forged.model_copy(update={"artifacts": artifacts})
    # Written to disk as well, because the audit compares the manifest it is
    # handed against the one in the run directory.
    (run_dir / "manifest.json").write_text(forged.model_dump_json(indent=2), encoding="utf-8")
    return forged


def test_a_modulo_run_that_declared_nothing_is_refused(tmp_path) -> None:
    """The allowlist came from `manifest.grades.assumed`, which the audited run
    writes. Nothing read `assumptions.json`, so a run could name its own axiom
    and be believed -- `falsum : False` and every check passing."""
    acceptance = importlib.import_module("hardy.acceptance")
    run = _deterministic(tmp_path)
    forged = _forge_modulo(run.run_dir, run.manifest)

    issues = acceptance.validate_run_consistency(run.run_dir, forged)

    assert issues, "a modulo run with no declaration file was accepted"
    assert any("declar" in issue for issue in issues), issues


def test_a_modulo_run_may_not_assume_what_nobody_declared(tmp_path) -> None:
    acceptance = importlib.import_module("hardy.acceptance")
    run = _deterministic(tmp_path)
    forged = _forge_modulo(
        run.run_dir,
        run.manifest,
        declare=[{"name": "something_else", "statement": "True", "source": "arXiv:1v1"}],
    )

    issues = acceptance.validate_run_consistency(run.run_dir, forged)

    assert any("falsum" in issue for issue in issues), issues


def test_a_modulo_run_whose_lean_states_a_different_axiom_is_refused(tmp_path) -> None:
    """The declaration file says one thing and the file the kernel read says
    another. What was elaborated is what counts."""
    acceptance = importlib.import_module("hardy.acceptance")
    run = _deterministic(tmp_path)
    forged = _forge_modulo(
        run.run_dir,
        run.manifest,
        statement="False",
        declare=[{"name": "falsum", "statement": "True", "source": "arXiv:1v1"}],
    )

    issues = acceptance.validate_run_consistency(run.run_dir, forged)

    assert any("does not state" in issue or "statement" in issue for issue in issues), issues


def test_a_properly_declared_modulo_run_passes(tmp_path) -> None:
    """The check has to admit the honest case, or it is just a refusal."""
    acceptance = importlib.import_module("hardy.acceptance")
    run = _deterministic(tmp_path)
    forged = _forge_modulo(
        run.run_dir,
        run.manifest,
        statement="False",
        declare=[{"name": "falsum", "statement": "False", "source": "arXiv:1v1"}],
    )

    assert acceptance.validate_run_consistency(run.run_dir, forged) == ()
