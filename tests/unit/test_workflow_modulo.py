"""A staged run that declares its assumptions, and is graded honestly on them."""

from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime
from pathlib import PurePosixPath
from types import SimpleNamespace
from uuid import UUID

from test_workflow import _config, _environment, _proposal, _review

NOW = datetime(2026, 7, 24, tzinfo=UTC)
RUN_ID = UUID("12345678-1234-5678-1234-567812345678")


def _assumption(domain, **overrides):
    fields = {
        "name": "Papers.perelman.no_local_collapsing",
        "statement": "True",
        "source": "arXiv:math.DG/0211159v1 (thm:collapse)",
        "justification": "Mathlib has no Ricci flow theory.",
    }
    fields.update(overrides)
    return domain.DeclaredAssumption(**fields)


def _controller(tmp_path, *, used=(), refuted=False, unreadable=False):
    """A workflow whose verifier reports `used` and whose Lean answers probes."""
    config_module = importlib.import_module("hardy.config")
    domain = importlib.import_module("hardy.domain")
    lean_module = importlib.import_module("hardy.lean")
    process = importlib.import_module("hardy.process")
    verifier_module = importlib.import_module("hardy.verifier")
    workflow = importlib.import_module("hardy.workflow")
    writeup = importlib.import_module("hardy.writeup")
    environment = _environment(domain)
    probes: list[str] = []

    class Runtime:
        backend = "fixture-backend"

        def start(self, *, model, run_dir, claim, isolated=False, phase=None, wall_seconds=None):
            return object()

        def run_structured(self, thread, stage, prompt, output_type):
            if stage == "faithfulness":
                return _review(domain)
            if stage == "formalization":
                return _proposal(domain)
            return writeup.WriteupContent(
                title="Two equals two",
                theorem_text="Two equals two.",
                proof_text="Reflexivity.",
                known_gaps=(),
            )

        def run_proof(self, thread, prompt):
            return importlib.import_module("hardy.codex_runtime").ProofSubmission(
                proof_body="by rfl", informal_proof="Reflexivity."
            )

        def cancel(self, thread):
            pass

    def _process(returncode=0):
        return process.ProcessResult(
            argv=("lake",),
            cwd=tmp_path,
            returncode=returncode,
            stdout="",
            stderr="",
            timed_out=False,
            output_overflow=False,
            duration_ms=1,
        )

    class Lean:
        def check_proof(self, claim, proof_body):
            return lean_module.LeanCheckResult(
                success=True,
                diagnostics=(),
                open_goals=(),
                process=_process(),
                source_sha256="a" * 64,
                toolchain=environment,
            )

        def check_scratch(self, source):
            probes.append(source)
            refute = importlib.import_module("hardy.refute")
            if unreadable:
                return lean_module.LeanCheckResult(
                    success=False,
                    diagnostics=(),
                    open_goals=(),
                    process=_process(returncode=1),
                    source_sha256="b" * 64,
                    toolchain=environment,
                )
            # Every probe line errors unless the statement is refuted, in
            # which case `decide` (the first) closes the negation.
            lines = source.splitlines()
            failing = [
                index + 1
                for index, line in enumerate(lines)
                if line.startswith("example")
                and not (refuted and line.endswith(f":= by {refute.TACTICS[0]}"))
                and not line.endswith(":= by sorry")
            ]
            return lean_module.LeanCheckResult(
                success=not failing,
                diagnostics=tuple(
                    lean_module.LeanDiagnostic(
                        severity="error", message="unsolved goals", line=line, column=0
                    )
                    for line in failing
                ),
                open_goals=(),
                process=_process(returncode=1 if failing else 0),
                source_sha256="b" * 64,
                toolchain=environment,
            )

    class Verifier:
        def verify(self, claim, proof_body, store, allowed=()):
            source = verifier_module.verification_source(claim, proof_body, allowed)
            identity = store.write_text(PurePosixPath("lean/Main.lean"), source)
            evidence = domain.VerificationEvidence(
                claim_sha256=claim.content_hash,
                source_sha256=identity.sha256,
                axioms=("propext", *used),
                toolchain=claim.environment,
            )
            result = verifier_module.VerificationResult(
                verified=True,
                reason=None,
                axioms=("propext", *used),
                diagnostics=(),
                source_sha256=identity.sha256,
                verification_sha256=evidence.digest,
                evidence=evidence,
                assumed=tuple(used),
            )
            store.write_json(PurePosixPath("lean/verification.json"), result)
            return result

    def build_document(claim, content, grades, verification, identities, store, **kwargs):
        tex = store.write_text(PurePosixPath("writeup/paper.tex"), "tex")
        pdf = store.write_bytes(PurePosixPath("writeup/paper.pdf"), b"%PDF")
        log = store.write_text(PurePosixPath("writeup/compile.log"), "log")
        return writeup.DocumentResult(
            status=domain.DocumentStatus.TEX_COMPILED,
            tex_artifact=tex,
            pdf_artifact=pdf,
            log_artifact=log,
            process=_process(),
        )

    controller = workflow.ProveWorkflow(
        config=_config(config_module, domain, tmp_path),
        environment=environment,
        doctor=lambda _: SimpleNamespace(healthy=True, authenticated=True),
        lean=Lean(),
        runtime_factory=lambda store: Runtime(),
        verifier=Verifier(),
        writeup_builder=build_document,
        identities_factory=lambda run_id, model: SimpleNamespace(run_id=run_id, model=model),
        now=lambda: NOW,
        monotonic=lambda: 0.0,
        uuid_factory=lambda: RUN_ID,
    )
    return workflow, domain, controller, probes


class Terminal:
    def show_formalization(self, proposal, elaboration):
        pass

    def choose_approval(self):
        return "approve"

    def revision_text(self):
        return ""

    def show_faithfulness(self, verdict):
        pass

    def acknowledge_unsafe_execution(self):
        return True

    def show_result(self, manifest):
        pass


def _run(controller, workflow, domain, assumptions):
    return controller.run(
        workflow.ProveRequest(
            text="Two equals two.",
            model="test-model",
            problem_slug="modulo",
            assumptions=assumptions,
        ),
        Terminal(),
    )


def test_a_run_may_declare_what_it_stands_on(tmp_path) -> None:
    workflow, domain, controller, _ = _controller(
        tmp_path, used=("Papers.perelman.no_local_collapsing",)
    )

    manifest = _run(controller, workflow, domain, (_assumption(domain),))

    assert manifest.grades.formal is domain.FormalStatus.VERIFIED_MODULO
    assert manifest.grades.assumed == ("Papers.perelman.no_local_collapsing",)


def test_a_run_that_declared_but_did_not_use_is_kernel_verified(tmp_path) -> None:
    workflow, domain, controller, _ = _controller(tmp_path, used=())

    manifest = _run(controller, workflow, domain, (_assumption(domain),))

    assert manifest.grades.formal is domain.FormalStatus.KERNEL_VERIFIED
    assert manifest.grades.assumed == ()


def test_a_run_declaring_nothing_is_graded_as_before(tmp_path) -> None:
    workflow, domain, controller, _ = _controller(tmp_path, used=())

    manifest = _run(controller, workflow, domain, ())

    assert manifest.grades.formal is domain.FormalStatus.KERNEL_VERIFIED


def test_every_declared_assumption_is_refutation_checked_before_proving(tmp_path) -> None:
    workflow, domain, controller, probes = _controller(
        tmp_path, used=("Papers.perelman.no_local_collapsing",)
    )

    _run(controller, workflow, domain, (_assumption(domain),))

    assert probes, "no refutation probe was run"
    assert "¬ (True)" in probes[0]


def test_a_refuted_assumption_stops_the_run_before_any_proving(tmp_path) -> None:
    """A false axiom makes everything provable, so the run that would have
    spent its budget proving on one is stopped instead."""
    workflow, domain, controller, _ = _controller(tmp_path, refuted=True)

    manifest = _run(controller, workflow, domain, (_assumption(domain),))

    assert manifest.terminal_reason is domain.TerminalReason.REFUTED_ASSUMPTION
    assert manifest.grades.formal is not domain.FormalStatus.KERNEL_VERIFIED
    assert any("negation" in gap or "refut" in gap for gap in manifest.grades.known_gaps)


def test_a_refutation_that_could_not_run_does_not_stop_the_run(tmp_path) -> None:
    """A machine whose Lean will not answer must not silently become one where
    nothing can be assumed -- but the run says so in its known gaps."""
    workflow, domain, controller, _ = _controller(
        tmp_path, used=("Papers.perelman.no_local_collapsing",), unreadable=True
    )

    manifest = _run(controller, workflow, domain, (_assumption(domain),))

    assert manifest.grades.formal is domain.FormalStatus.VERIFIED_MODULO
    assert any("refutation" in gap.lower() for gap in manifest.grades.known_gaps)


def test_the_declared_set_is_written_into_the_run_directory(tmp_path) -> None:
    """The manifest names what was used; the request names what was allowed,
    and a reader wants both."""
    workflow, domain, controller, _ = _controller(
        tmp_path, used=("Papers.perelman.no_local_collapsing",)
    )

    manifest = _run(controller, workflow, domain, (_assumption(domain),))

    run_dir = next(tmp_path.glob(f"*{manifest.run_id.hex[:8]}*"))
    declared = json.loads((run_dir / "assumptions.json").read_text(encoding="utf-8"))
    assert declared[0]["name"] == "Papers.perelman.no_local_collapsing"
    assert declared[0]["source"].startswith("arXiv:")
    assert "assumptions.json" in manifest.artifacts
