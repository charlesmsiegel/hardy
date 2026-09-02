"""`hardy evals` as a command: thin dispatch, honest exit codes."""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime

import pytest

from hardy import cli
from hardy.domain import EnvironmentIdentity
from hardy.evals import commands, scoreboard

IDENTITY = EnvironmentIdentity(lean_version="4.33.1", lean_commit="819816b2", mathlib_revision="v4.33.1", lake_manifest_sha256="m" * 64)
PROBLEMS = {"schema_version": 1, "entries": [
    {"id": "t", "input": "True.", "name": "T", "binders": "", "conclusion": "True", "imports": ["Mathlib"], "expected": "true", "twin_of": None, "source": "textbook", "area": "logic"},
]}


def _always_closes(source: str):
    # bare import: tests/ has no __init__.py, so pytest puts each test
    # directory on sys.path and this is the scripted shapes from Task 2.
    from test_evals_sweep import _elaboration, _msg

    lines = source.splitlines()
    msgs = [_msg(i, "information", "Used 10 heartbeats, which is less than the current maximum of 200000.") for i, line in enumerate(lines, 1) if line.startswith("#count_heartbeats")]
    if "#print axioms" in source:
        # Derived, not hardcoded: this mock is shared by a test whose twin's
        # stage-B theorem is named "F", and `audit.parse` matches by name
        # (`src/hardy/audit.py:_reports_for`), so a fixed "'T'" would leave the
        # twin's confirmation forever unconfirmed. Same fix as Task 2's mock.
        name = source.rsplit("#print axioms ", 1)[1].strip()
        msgs.append(_msg(len(lines), "information", f"'{name}' does not depend on any axioms"))
    if any(line.strip() == "sorry" for line in lines):
        msgs.append(_msg(3, "warning", "declaration uses 'sorry'"))
    return _elaboration(msgs)


def test_the_parser_knows_evals_and_its_three_verbs():
    parser = cli.build_parser()
    args = parser.parse_args(["evals", "baseline", "--problems", "p.json", "--out", "b.json"])
    assert args.command == "evals" and args.evals_command == "baseline"
    assert parser.parse_args(["evals", "check", "some/dir"]).evals_command == "check"
    assert parser.parse_args(["evals", "run", "--label", "x", "--acknowledge-unsafe-execution"]).evals_command == "run"


def test_repeats_below_one_is_refused_by_the_parser():
    """A `--repeats 0` (or negative) run would write a finished, empty
    scoreboard `hardy evals check` also accepts (item 5): refused before any
    row runs, not after.
    """
    parser = cli.build_parser()
    for bad in ("0", "-1"):
        with pytest.raises(SystemExit):
            parser.parse_args(["evals", "run", "--label", "x", "--acknowledge-unsafe-execution", "--repeats", bad])
    assert parser.parse_args(["evals", "run", "--label", "x", "--acknowledge-unsafe-execution", "--repeats", "1"]).repeats == 1


def test_wall_seconds_at_or_below_zero_is_refused_by_the_parser():
    """`ClaudeAgentRuntime._within_budget` treats a falsy `wall_seconds` (0)
    as no budget at all and skips `asyncio.wait_for` entirely, so `--wall-
    seconds 0` would let a batch run spend through its turn budget unbounded
    (item 6): refused before any row runs, not after.
    """
    parser = cli.build_parser()
    for bad in ("0", "-1", "-0.5"):
        with pytest.raises(SystemExit):
            parser.parse_args(["evals", "run", "--label", "x", "--acknowledge-unsafe-execution", "--wall-seconds", bad])
    assert parser.parse_args(["evals", "run", "--label", "x", "--acknowledge-unsafe-execution", "--wall-seconds", "10"]).wall_seconds == 10.0


def test_wall_seconds_non_finite_is_refused_by_the_parser():
    """`float()` accepts `inf` and `nan`, and both pass a bare positivity
    check: `asyncio.wait_for(..., timeout=inf)` imposes no effective
    deadline, so `inf` would let a batch run spend through its turn budget
    unbounded, while `nan <= 0` is `False` so `nan` slips past too and
    instead produces an immediate timeout (item 6). Both are refused before
    any row runs.
    """
    parser = cli.build_parser()
    for bad in ("inf", "-inf", "nan"):
        with pytest.raises(SystemExit):
            parser.parse_args(["evals", "run", "--label", "x", "--acknowledge-unsafe-execution", "--wall-seconds", bad])


def test_max_turns_below_one_is_refused_by_the_parser():
    parser = cli.build_parser()
    for bad in ("0", "-1"):
        with pytest.raises(SystemExit):
            parser.parse_args(["evals", "run", "--label", "x", "--acknowledge-unsafe-execution", "--max-turns", bad])
    assert parser.parse_args(["evals", "run", "--label", "x", "--acknowledge-unsafe-execution", "--max-turns", "1"]).max_turns == 1


def test_model_is_accepted_on_the_run_subparser():
    """`hardy evals run --model M`, the documented shape, used to be rejected
    because only the root parser defined `--model` (item 6); the run
    subparser's own copy is suppressed so it defers to the root default when
    omitted (cli.py:1545 does the same for `prove`).
    """
    parser = cli.build_parser()
    args = parser.parse_args(["evals", "run", "--label", "x", "--model", "m", "--acknowledge-unsafe-execution"])
    assert args.model == "m"
    without = parser.parse_args(["evals", "run", "--label", "x", "--acknowledge-unsafe-execution"])
    assert getattr(without, "model", None) is None


def test_baseline_writes_the_tier_file_and_exits_zero_when_the_list_is_clean(tmp_path):
    problems = tmp_path / "problems.json"
    problems.write_text(json.dumps(PROBLEMS), encoding="utf-8")
    out = tmp_path / "baseline.json"
    args = argparse.Namespace(problems=problems, out=out, acknowledge_unsafe_execution=True)
    code = commands.run_baseline(args, config=None, elaborate=_always_closes, identity=IDENTITY, now=lambda: datetime(2026, 9, 1, tzinfo=UTC))
    assert code == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["entries"]["t"]["tier"] == 0 and written["problems"] == []
    assert written["environment"]["lean_commit"] == "819816b2"
    # evals/baseline.json is repository evidence a digest is taken over
    # (`.gitattributes` marks it `-text`, no line-ending conversion); a
    # platform-translated write would checkin CRLF on Windows and shift
    # every later sha256_of comparison.
    assert b"\r" not in out.read_bytes()


def test_baseline_exits_one_but_still_writes_when_the_list_has_problems(tmp_path, capsys):
    twin = dict(PROBLEMS["entries"][0], id="f", name="F", conclusion="True", expected="false", twin_of="t")
    problems = tmp_path / "problems.json"
    problems.write_text(json.dumps({"schema_version": 1, "entries": [PROBLEMS["entries"][0], twin]}), encoding="utf-8")
    out = tmp_path / "baseline.json"
    args = argparse.Namespace(problems=problems, out=out, acknowledge_unsafe_execution=True)
    code = commands.run_baseline(args, config=None, elaborate=_always_closes, identity=IDENTITY, now=lambda: datetime(2026, 9, 1, tzinfo=UTC))
    assert code == 1 and out.exists()
    assert "f: a twin closed by" in capsys.readouterr().err


def test_baseline_refuses_a_missing_problems_file_instead_of_a_traceback(tmp_path, capsys):
    missing = tmp_path / "nope.json"
    args = argparse.Namespace(problems=missing, out=tmp_path / "baseline.json", acknowledge_unsafe_execution=True)
    code = commands.run_baseline(args, config=None)
    assert code == 2
    err = capsys.readouterr().err
    assert "Refused:" in err and str(missing) in err
    assert not (tmp_path / "baseline.json").exists()


def test_baseline_refuses_unacknowledged_unsafe_execution(tmp_path, capsys):
    """The sweep interpolates `evals/problems.json`'s imports, binders, and
    conclusion into real Lean source and elaborates it -- the same unsafe-
    execution contract `evals run` and the staged terminal already enforce
    (item 6). An untrusted problem file gets no free pass just because there
    is no model to hand the warning to.
    """
    from hardy.runner import WARNING

    problems = tmp_path / "problems.json"
    problems.write_text(json.dumps(PROBLEMS), encoding="utf-8")
    out = tmp_path / "baseline.json"
    args = argparse.Namespace(problems=problems, out=out, acknowledge_unsafe_execution=False)
    code = commands.run_baseline(args, config=None, elaborate=_always_closes, identity=IDENTITY, now=lambda: datetime(2026, 9, 1, tzinfo=UTC))
    assert code == 2
    err = capsys.readouterr().err
    assert WARNING in err and "--acknowledge-unsafe-execution" in err
    assert not out.exists()


def test_check_refuses_missing_problems_or_baseline_instead_of_a_traceback(tmp_path, capsys):
    missing_problems = tmp_path / "problems.json"
    args = argparse.Namespace(scoreboard=tmp_path / "board", problems=missing_problems, baseline=tmp_path / "baseline.json")
    code = scoreboard.check_command(args)
    assert code == 2
    err = capsys.readouterr().err
    assert "Refused:" in err and str(missing_problems) in err


def test_baseline_reports_a_toolchain_that_cannot_be_identified_instead_of_a_traceback(tmp_path, capsys, monkeypatch):
    def boom(*a, **kw):
        raise ValueError("no lake-manifest.json")

    # commands.py imports environment_identity by name at module scope, unlike
    # runner.py's per-call local import, so the patch target is the commands
    # module's own binding, not hardy.lean's.
    monkeypatch.setattr(commands, "environment_identity", boom)
    problems = tmp_path / "problems.json"
    problems.write_text(json.dumps(PROBLEMS), encoding="utf-8")
    out = tmp_path / "baseline.json"
    args = argparse.Namespace(problems=problems, out=out, acknowledge_unsafe_execution=True)
    config = argparse.Namespace(lean_project=None, lake="lake", limits=argparse.Namespace(lean_process_seconds=60))
    code = commands.run_baseline(args, config=config, elaborate=_always_closes, now=lambda: datetime(2026, 9, 1, tzinfo=UTC))
    assert code == 2
    assert "Refused: the Lean toolchain could not be identified" in capsys.readouterr().err
    assert not out.exists()
