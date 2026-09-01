"""The first experiment acceptance test, against a real model and a pinned toolchain.

`tests/integration/test_batch_live.py` established that a real model reaches
`verified` on a trivial theorem. This file is the claim FEATURES.md makes
beyond that: Hardy's honesty guarantees hold against a real model, a real
pinned Mathlib, and a real TeX toolchain on a problem big enough to need more
than one lemma -- on both surfaces, including the staged `hardy prove` path
and its document pipeline, which had never had a live run. Four runs, all
required, each recorded:

1. `hardy batch` on the nontrivial problem, expected to reach `verified`.
2. Staged `hardy prove` on the same problem, through the document pipeline.
3. A false statement, expected to be refused rather than graded.
4. A starved budget on the nontrivial problem, expected to end as
   `wall_clock_limit` with an honest partial record.

Every assertion is on the rebuilt artifact, never on what the model said.
`hardy accept --recorded` is run over each output directory here with no
model and no network, so the same audit that rechecks the committed copies
under `acceptance/recorded/` is the one these runs had to pass when made.

What would make this a false pass, and what each guard below is for:

- A problem trivial enough that the model never needs search, never needs an
  intermediate lemma, and never needs a second file would pass without
  exercising anything. The problem (`sqrt 2 + sqrt 3` is irrational) needs an
  intermediate fact the model has to state itself (`sqrt 6` is irrational, or
  that a rational's square is rational) and a Mathlib lemma it has to find
  (`irrational_sqrt_natCast_iff` or `Nat.Prime.irrational_sqrt`, plus the
  `Real.sqrt` algebra). The runs record which search tools were used; a run
  that used none is reported, not hidden. Multi-file saving lives only on the
  interactive surface, which neither `batch` nor `prove` offers, so it is not
  exercised here and this file says so rather than pretending it was.
- Asserting on the model's self-report anywhere instead of on the rebuilt
  artifact. `proof.lean` is compared byte for byte with what Hardy builds
  from the request and the proof; the staged `lean/Main.lean` is compared
  with the frozen claim; both are re-elaborated by a fresh Lean here.
- Letting a `sorry` or an unapproved axiom reach `verified` through a path
  the audit does not cover. The axiom line is read from Lean's own output
  in the record and from a fresh elaboration, and compared with the graded
  verdict.
- Recording the run without the toolchain identity, which makes it a story
  rather than evidence. Every record must name the Lean version and commit,
  the Mathlib revision, and the manifest digest, and they must match what the
  toolchain answers here.

Two real-run behaviours the trivial run surfaced are expected rather than
treated as bugs: a run the wall clock cancels has no turn count at all,
because the provider's count arrives with its final result; and
`elapsed_seconds` can exceed `wall_seconds`, because Hardy's clock cancels
the exchange without killing a Lean check already in flight.

Billable and unsandboxed, so never implicit: `HARDY_LIVE=1` has to be set on
purpose, exactly as for `test_batch_live.py`. `HARDY_RECORD_DIR` names a
directory to keep the four output directories in (this is how
`acceptance/recorded/` was made); without it they go under `tmp_path`.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import shutil
import warnings
from pathlib import Path
from typing import Any

import pytest

from hardy import audit
from hardy import config as configuration
from hardy.acceptance import validate_recorded_run
from hardy.cli import _find_run_dir, build_prove_workflow, runtime_factory
from hardy.domain import DocumentStatus, FaithfulnessStatus, FormalStatus, RunPhase
from hardy.lean import LeanTools, elaborate, environment_identity
from hardy.models import Request
from hardy.runner import WARNING, run
from hardy.verifier import ALLOWED_AXIOMS, FORBIDDEN_TOKEN, VerificationResult, axiom_report_line
from hardy.workflow import ProveRequest
from hardy.workspace import strip_comments
from hardy.writeup import tectonic_version

pytestmark = [pytest.mark.live, pytest.mark.real_toolchain]

ROOT = Path(__file__).parents[2]
PROBLEM_ID = "sqrt-two-plus-sqrt-three-irrational"
REQUEST = ROOT / "examples" / "sqrt-two-plus-sqrt-three.json"
ENABLED = {"1", "true", "yes", "on"}
# A stall guard, not a target: a cold `import Mathlib` alone is over a minute
# on a small machine, and the nontrivial problem takes several checks.
WALL_SECONDS = 1_800.0
MAX_TURNS = 60
# Long enough for the provider to answer and a first Lean check to start,
# and far too short for the nontrivial proof: the point of run 4 is a
# trajectory with something in it that the clock then cuts off.
STARVED_SECONDS = 30.0
# Terminal reasons that honestly describe a batch run which produced no proof.
HONEST_FAILURES = {"no_proof_submitted", "axioms_rejected", "turn_limit", "wall_clock_limit"}
# The names of the four recorded runs, as `acceptance/recorded/` keeps them.
BATCH_VERIFIED = "batch-verified"
PROVE_VERIFIED = "prove-verified"
BATCH_FALSE = "batch-false-statement"
BATCH_STARVED = "batch-starved"
# Search tools a run may show it used, per surface.
BATCH_SEARCH = {"search_declaration"}
STAGED_SEARCH = {"lean_search_declarations", "lean_inspect_declarations", "rank_premises"}


@pytest.fixture(scope="module")
def live_config() -> configuration.Config:
    if os.environ.get("HARDY_LIVE", "").strip().lower() not in ENABLED:
        pytest.skip(
            "set HARDY_LIVE=1 to spend a subscription on these tests, and to run "
            f"model-written Lean unsandboxed. {WARNING}"
        )
    if shutil.which("claude") is None:
        pytest.skip("the Claude Code CLI the agent SDK drives is not installed")
    config = configuration.load()
    if config.lean_project is None:
        pytest.skip("lean_project is not configured; run the installer")
    if shutil.which(str(config.lake)) is None:
        pytest.skip(f"the configured lake is not executable: {config.lake}")
    if shutil.which(config.lean_command[0]) is None:
        pytest.skip(f"the configured Lean command is not executable: {config.lean_command[0]}")
    if shutil.which(str(config.tectonic)) is None:
        pytest.skip(f"the configured tectonic is not executable: {config.tectonic}")
    if not (config.lean_project / "lake-manifest.json").exists():
        pytest.skip(f"{config.lean_project} is not a resolved Lake project; run `hardy setup`")
    warnings.warn(f"live run: {WARNING}", stacklevel=1)
    return config


@pytest.fixture(scope="module")
def identity(live_config: configuration.Config):
    """What the toolchain answers here, asked the way each surface asks it."""
    return environment_identity(
        live_config.lean_project, lean_command=(str(live_config.lake), "env", "lean")
    )


def _output(tmp_path: Path, name: str) -> Path:
    """Where a run's artifacts go: the record directory when asked for, else tmp."""
    recorded = os.environ.get("HARDY_RECORD_DIR")
    base = Path(recorded) if recorded else tmp_path
    output = base / name
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    return output


def _request() -> Request:
    return Request.from_dict(json.loads(REQUEST.read_text(encoding="utf-8")))


def _claim_text() -> str:
    payload = json.loads((ROOT / "acceptance" / "problems.json").read_text(encoding="utf-8"))
    return next(item["input"] for item in payload["problems"] if item["id"] == PROBLEM_ID)


def _tools(request: Request, config: configuration.Config) -> LeanTools:
    return LeanTools(
        request, config.lean_command, timeout=config.lean_timeout, project=config.lean_project
    )


def _batch(request: Request, config: configuration.Config, output: Path, **limits):
    return run(request, runtime_factory(str(config.model)), _tools(request, config), output, **limits)


def _trajectory(output: Path) -> dict[str, Any]:
    return json.loads((output / "trajectory.json").read_text(encoding="utf-8"))


def _tool_names(trajectory: dict[str, Any]) -> list[str]:
    return [event["name"] for event in trajectory["events"] if event.get("type") == "tool"]


def _assert_identity_recorded(toolchain: dict[str, Any], identity) -> None:
    """The record names the toolchain by revision, and it is the one here."""
    assert toolchain == identity.model_dump(mode="json")
    assert toolchain["lean_version"] and toolchain["lean_commit"] and toolchain["mathlib_revision"]


def _assert_spend_stated(usage: dict[str, Any]) -> None:
    """Cost and the four counters are present, or explicitly null. Never absent."""
    for field in ("exchanges", "cost_usd", "input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens"):
        assert field in usage, field
        assert usage[field] is None or isinstance(usage[field], (int, float)), field


def _fresh_lean(source: str, config: configuration.Config):
    """Elaborate `source` again here, by a Lean this test starts itself."""
    return elaborate(
        source,
        argv=(str(config.lake), "env", "lean", "--json"),
        cwd=config.lean_project,
        timeout_seconds=max(config.lean_timeout, 600.0),
    )


# --- run 1: batch, verified ---------------------------------------------------


def test_run_1_batch_proves_the_nontrivial_theorem_through_the_kernel(
    live_config: configuration.Config, identity, tmp_path: Path
):
    request = _request()
    output = _output(tmp_path, BATCH_VERIFIED)
    tools = _tools(request, live_config)

    result = run(
        request, runtime_factory(str(live_config.model)), tools, output,
        max_turns=MAX_TURNS, wall_seconds=WALL_SECONDS,
    )

    assert result.terminal_reason == "verified", result.lean_output
    assert result.formalization == "kernel verified"
    assert result.axioms["status"] == "clean"
    assert result.axioms["declarations"][0]["name"] == "HardySqrtSum"
    assert set(result.axioms["declarations"][0]["axioms"]) <= ALLOWED_AXIOMS

    # The artifact, byte for byte what Hardy builds from the request and the
    # proof: the model never wrote this file, and nothing it said is in it.
    source = (output / "proof.lean").read_text(encoding="utf-8")
    assert source == tools.source(result.proof, audit=True)
    assert source.rstrip().endswith(axiom_report_line("HardySqrtSum"))
    assert FORBIDDEN_TOKEN.search(strip_comments(source)) is None

    # The final check ran on exactly this file. `source_sha256` is the hash
    # the Lean process elaborated, recorded by the runner, not by the model.
    trajectory = _trajectory(output)
    accepted = [
        event for event in trajectory["events"]
        if event.get("type") == "tool" and event["name"] == "submit_proof" and event["result"]["ok"]
    ]
    assert accepted, "no accepted submission in the trajectory"
    assert accepted[-1]["result"]["source_sha256"] == hashlib.sha256(source.encode("utf-8")).hexdigest()

    # And a fresh Lean, started by this test, accepts it again and prints the
    # same axiom line: nothing beyond Lean's own three.
    fresh = _fresh_lean(source, live_config)
    assert fresh.success, [item.message for item in fresh.diagnostics]
    printed = audit.parse("\n".join(item.message for item in fresh.diagnostics), ("HardySqrtSum",))
    assert printed is not None
    assert set(printed[0].axioms) == set(result.axioms["declarations"][0]["axioms"])
    assert set(printed[0].axioms) <= ALLOWED_AXIOMS

    # Nontrivial: the model iterated with Lean and looked something up. A
    # run that did neither would be the false pass the module docstring names.
    names = _tool_names(trajectory)
    assert "check_proof" in names or names.count("submit_proof") > 1, names
    assert BATCH_SEARCH & set(names), f"the model never searched: {names}"

    _assert_identity_recorded(trajectory["toolchain"], identity)
    assert result.toolchain == trajectory["toolchain"]
    _assert_spend_stated(result.usage)
    assert isinstance(result.turns, int) and result.turns >= 1
    assert trajectory["model"] == str(live_config.model)
    assert "Formalization: **kernel verified**" in (output / "writeup.md").read_text(encoding="utf-8")

    # The same audit `hardy accept --recorded` runs, with no model present.
    assert validate_recorded_run(output) == ()


# --- run 2: staged prove, verified, through the document pipeline -------------


class _ApprovingTerminal:
    """A user who acknowledges, approves the first elaborating proposal, and watches."""

    def __init__(self) -> None:
        self.proposals: list[Any] = []
        self.verdicts: list[Any] = []
        self.manifest: Any = None

    def acknowledge_unsafe_execution(self) -> bool:
        return True

    def show_formalization(self, proposal: Any, elaboration: Any) -> None:
        self.proposals.append((proposal, elaboration.success))

    def choose_approval(self) -> str:
        return "approve"

    def revision_text(self) -> str:
        return ""

    def show_faithfulness(self, verdict: Any) -> None:
        self.verdicts.append(verdict)

    def show_result(self, manifest: Any) -> None:
        self.manifest = manifest


def test_run_2_staged_prove_reaches_verified_through_the_document_pipeline(
    live_config: configuration.Config, identity, tmp_path: Path
):
    """The half FEATURES.md said had never had a live run: frozen claim,
    independent final verification, controlled LaTeX, all on a real toolchain."""
    runs_root = _output(tmp_path, PROVE_VERIFIED)
    config = dataclasses.replace(live_config, runs_root=runs_root)
    workflow = build_prove_workflow(config, config.config_path)
    terminal = _ApprovingTerminal()

    manifest = workflow.run(
        ProveRequest(text=_claim_text(), model=str(config.model), problem_slug=PROBLEM_ID),
        terminal,
    )

    assert manifest.phase is RunPhase.COMPLETED, manifest.terminal_reason
    assert manifest.terminal_reason is None
    assert manifest.grades.formal is FormalStatus.KERNEL_VERIFIED
    assert manifest.grades.faithfulness is FaithfulnessStatus.USER_APPROVED
    assert manifest.grades.document is DocumentStatus.TEX_COMPILED
    # Frozen under the identity the toolchain answers here, by revision.
    assert manifest.environment == identity
    evidence = manifest.grades.verification_evidence
    assert evidence is not None
    assert set(evidence.axioms) <= ALLOWED_AXIOMS
    assert evidence.toolchain == identity

    run_dir = _find_run_dir(runs_root, manifest.run_id)
    # The verifier rebuilt the theorem from the frozen claim and a fresh Lean
    # accepted it: the record carries Lean's own axiom line, and the source
    # on disk hashes to what the evidence names. The check ran; it did not
    # merely agree.
    verification = VerificationResult.model_validate_json(
        (run_dir / "lean" / "verification.json").read_text(encoding="utf-8")
    )
    assert verification.verified
    assert verification.diagnostics, "the fresh verifier kept no Lean output"
    main = (run_dir / "lean" / "Main.lean").read_text(encoding="utf-8")
    assert hashlib.sha256(main.encode("utf-8")).hexdigest() == evidence.source_sha256
    assert FORBIDDEN_TOKEN.search(strip_comments(main)) is None
    theorem = terminal.proposals[-1][0].theorem_name
    printed = audit.parse("\n".join(item.message for item in verification.diagnostics), (theorem,))
    assert printed is not None and set(printed[0].axioms) == set(evidence.axioms)

    # And once more by a Lean this test starts itself.
    fresh = _fresh_lean(main, live_config)
    assert fresh.success, [item.message for item in fresh.diagnostics]
    again = audit.parse("\n".join(item.message for item in fresh.diagnostics), (theorem,))
    assert again is not None and set(again[0].axioms) == set(evidence.axioms)

    # The compiled document is about the same statement, names the toolchain
    # the run was frozen under, and says what compiled it.
    pdf = run_dir / "writeup" / "paper.pdf"
    assert pdf.read_bytes().startswith(b"%PDF-")
    tex = (run_dir / "writeup" / "paper.tex").read_text(encoding="utf-8")
    assert manifest.claim_sha256 in tex
    assert f"Lean: {identity.lean_version}" in tex
    assert f"Mathlib: {identity.mathlib_revision}" in tex
    assert f"Tectonic: {tectonic_version(config.tectonic, config.limits)}" in tex
    assert "unrecorded" not in tex

    # The independent reader agreed, on an isolated thread.
    verdict = manifest.grades.faithfulness_review
    assert verdict is not None and verdict.agreed
    assert terminal.verdicts and terminal.verdicts[-1].agreed

    # Search happened, and the spend is stated per field.
    events = [
        json.loads(line) for line in (run_dir / "trajectory.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    # The SDK names an in-process tool `mcp__hardy__<name>`; the trajectory
    # keeps that name, so the prefix is dropped before comparing.
    used = {
        str(event["payload"].get("name")).rsplit("__", 1)[-1]
        for event in events
        if event["kind"] == "claude.tool_use" and isinstance(event.get("payload"), dict)
    }
    assert STAGED_SEARCH & used, f"the model never searched: {sorted(used)}"
    _assert_spend_stated(manifest.usage)

    assert validate_recorded_run(run_dir) == ()


# --- run 3: a false statement is refused, not graded -------------------------


def test_run_3_a_false_statement_is_refused_by_the_gate_not_graded(
    live_config: configuration.Config, identity, tmp_path: Path
):
    """The negation of the nontrivial theorem, so the model has to actually
    try. A `verified` here, or a `proof.lean`, would be the harness grading
    a false claim; a partial grade would be no better."""
    request = Request.from_dict(
        {
            "declaration": "theorem HardyFalse : ¬ Irrational (Real.sqrt 2 + Real.sqrt 3)",
            "informal_claim": "The real number sqrt(2) + sqrt(3) is rational.",
            "imports": ["Mathlib"],
        }
    )
    output = _output(tmp_path, BATCH_FALSE)

    result = _batch(request, live_config, output, max_turns=20, wall_seconds=WALL_SECONDS)

    assert result.terminal_reason in HONEST_FAILURES, result.terminal_reason
    assert result.formalization == "not formalized"
    assert result.proof is None
    assert result.axioms["status"] != "clean"
    assert not (output / "proof.lean").exists()
    trajectory = _trajectory(output)
    # Every submission the model made, the gate refused: nothing accepted,
    # nothing graded.
    for event in trajectory["events"]:
        if event.get("type") == "tool" and event["name"] == "submit_proof":
            assert not event["result"]["ok"], event["result"]["output"]
    assert trajectory["events"], "a failed run still owes a record"
    writeup = (output / "writeup.md").read_text(encoding="utf-8")
    assert "No completed artifact" in writeup
    assert f"Terminal reason: `{result.terminal_reason}`" in writeup
    _assert_identity_recorded(trajectory["toolchain"], identity)
    _assert_spend_stated(result.usage)
    assert validate_recorded_run(output) == ()


# --- run 4: a starved budget ends honestly -----------------------------------


def test_run_4_a_starved_budget_ends_as_a_wall_clock_limit_with_an_honest_partial(
    live_config: configuration.Config, identity, tmp_path: Path
):
    """Hardy's own deadline, recorded as such: not a provider error, not a
    grade. The open hole is the theorem itself, named as unproved; the
    standing assumption is the unsandboxed environment, named in the record."""
    request = _request()
    output = _output(tmp_path, BATCH_STARVED)

    result = _batch(request, live_config, output, max_turns=MAX_TURNS, wall_seconds=STARVED_SECONDS)

    assert result.terminal_reason == "wall_clock_limit"
    assert result.formalization == "not formalized"
    assert result.proof is None
    assert not (output / "proof.lean").exists()
    # The provider's count arrives with its final result, which a run the
    # clock cancelled never receives -- so there is no count, not zero.
    assert result.turns is None
    # Explicit holes and assumptions: the theorem is open, nothing was
    # audited, and the record says what it ran without.
    assert result.axioms["status"] == "not audited"
    assert WARNING in result.warnings
    writeup = (output / "writeup.md").read_text(encoding="utf-8")
    assert "No completed artifact" in writeup and "Terminal reason: `wall_clock_limit`" in writeup

    trajectory = _trajectory(output)
    limits = trajectory["limits"]
    assert limits["wall_seconds"] == STARVED_SECONDS
    assert limits["wall_clock_enforced_by"] == "hardy"
    # Hardy cancels the exchange; it does not kill a Lean check already
    # running, so the run can overrun its budget and says so.
    assert limits["elapsed_seconds"] >= limits["wall_seconds"]
    # Intelligible: the record says what happened before the cut, and that
    # the cut was Hardy's.
    assert trajectory["events"], "a starved run still owes a trajectory"
    assert any(event.get("type") == "error" and "TimeoutError" in event.get("error", "") for event in trajectory["events"])
    _assert_identity_recorded(trajectory["toolchain"], identity)
    _assert_spend_stated(result.usage)
    assert validate_recorded_run(output) == ()
