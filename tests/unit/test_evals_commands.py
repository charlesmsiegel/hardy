"""`hardy evals` as a command: thin dispatch, honest exit codes."""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime

from hardy import cli
from hardy.domain import EnvironmentIdentity
from hardy.evals import commands

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


def test_baseline_writes_the_tier_file_and_exits_zero_when_the_list_is_clean(tmp_path):
    problems = tmp_path / "problems.json"
    problems.write_text(json.dumps(PROBLEMS), encoding="utf-8")
    out = tmp_path / "baseline.json"
    args = argparse.Namespace(problems=problems, out=out)
    code = commands.run_baseline(args, config=None, elaborate=_always_closes, identity=IDENTITY, now=lambda: datetime(2026, 9, 1, tzinfo=UTC))
    assert code == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["entries"]["t"]["tier"] == 0 and written["problems"] == []
    assert written["environment"]["lean_commit"] == "819816b2"


def test_baseline_exits_one_but_still_writes_when_the_list_has_problems(tmp_path, capsys):
    twin = dict(PROBLEMS["entries"][0], id="f", name="F", conclusion="True", expected="false", twin_of="t")
    problems = tmp_path / "problems.json"
    problems.write_text(json.dumps({"schema_version": 1, "entries": [PROBLEMS["entries"][0], twin]}), encoding="utf-8")
    out = tmp_path / "baseline.json"
    code = commands.run_baseline(argparse.Namespace(problems=problems, out=out), config=None, elaborate=_always_closes, identity=IDENTITY, now=lambda: datetime(2026, 9, 1, tzinfo=UTC))
    assert code == 1 and out.exists()
    assert "f: a twin closed by" in capsys.readouterr().err
