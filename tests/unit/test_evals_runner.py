"""The set runner: refuses before it spends, writes rows as it goes, never pretends to be complete."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_recorded_runs import FAKE_LEAN, _Runtime
from test_recorded_runs import IDENTITY as RAW_IDENTITY

from hardy.domain import EnvironmentIdentity
from hardy.evals import runner, sweep
from hardy.evals.problems import Entry, ProblemSet, load_problems, sha256_of

IDENTITY = EnvironmentIdentity(**RAW_IDENTITY)
ENTRIES = (
    Entry(id="t", input="True.", name="T", conclusion="True", expected="true", source="textbook", area="logic"),
    Entry(id="u", input="True again.", name="U", conclusion="True", expected="true", source="classical", area="logic"),
    Entry(id="f", input="False.", name="F", conclusion="True", expected="false", twin_of="t", source="textbook", area="logic"),
)


def _files(tmp_path: Path, tiers: dict[str, int] = None) -> tuple[Path, Path]:
    problems = tmp_path / "problems.json"
    problems.write_text(json.dumps(ProblemSet(entries=ENTRIES).model_dump(mode="json")), encoding="utf-8")
    tiers = tiers or {"t": 0, "u": 3, "f": 3}
    baseline = sweep.Baseline(created_at=datetime(2026, 9, 1, tzinfo=UTC), problems_sha256=sha256_of(problems), environment=IDENTITY,
                              heartbeat_budget=200000, wall_backstop_seconds=600.0, singles=sweep.SINGLES, chains=sweep.CHAINS, host={}, problems=(),
                              entries={k: sweep.EntryBaseline(tier=v, elaborates=True, attempts={}, closed_by=()) for k, v in tiers.items()})
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(baseline.model_dump(mode="json")), encoding="utf-8")
    return problems, path


def _condition(**kw) -> runner.Condition:
    # Both key pairs, always: a batch-mode condition reads max_turns/
    # wall_seconds and a staged-mode condition's twin batch rows read
    # twin_max_turns/twin_wall_seconds (item 3) -- carrying both spares every
    # caller of this helper from having to know which mode it is building.
    base = dict(model="fake-model@test", backend="claude", mode="batch", staged_prompt_set_sha256="p" * 64, batch_prompt_set_sha256="q" * 64, hardy_version="0.1.0",
                limits={"max_turns": 3, "wall_seconds": 300.0, "twin_max_turns": 3, "twin_wall_seconds": 300.0},
                repeats=1, selection={"only": None, "tiers": None, "twins": True})
    base.update(kw)
    return runner.Condition(**base)


def _scripted_batch(output: Path, script, *, declaration: str, informal_claim: str, wall_seconds: float = 300.0) -> Path:
    """A batch run over the fake Lean, scripted, with the entry's own claim.

    Modelled on `test_recorded_runs._batch`, but the declaration and informal
    claim are the caller's rather than the hardcoded `HardyTarget`: a set run
    validates each row against its own entry, so three same-named theorems
    would collide both in `ProblemSet` (which refuses duplicate names) and at
    that later check.
    """
    import sys

    from hardy import models
    from hardy import runner as hardy_runner
    from hardy.lean import LeanTools

    request = models.Request.from_dict({"declaration": declaration, "informal_claim": informal_claim})
    lean = LeanTools(request, (sys.executable, str(FAKE_LEAN)))
    hardy_runner.run(
        request,
        lambda model=None, **context: _Runtime(script, **context),
        lean,
        output,
        max_turns=3,
        wall_seconds=wall_seconds,
        toolchain=RAW_IDENTITY,
    )
    return output


def _batch_runner(scripts: dict[str, list]):
    def run_one(entry: Entry, output: Path, max_turns: int, wall_seconds: float) -> None:
        _scripted_batch(output, scripts[entry.id], declaration=entry.declaration(), informal_claim=entry.input)
    return run_one


SOLVE = [("submit_proof", {"proof": "by exact True.intro"})]
GIVE_UP = [("check_proof", {"proof": "by sorry"})]


def test_a_batch_set_run_writes_rows_a_scoreboard_and_aggregates(tmp_path):
    problems, baseline = _files(tmp_path)
    out = runner.run_set(label="first", problems_path=problems, baseline_path=baseline, scoreboards_root=tmp_path / "sb",
                         condition=_condition(), environment=IDENTITY, batch_runner=_batch_runner({"t": SOLVE, "u": GIVE_UP, "f": GIVE_UP}),
                         now=lambda: datetime(2026, 9, 1, tzinfo=UTC), report=lambda _: None)
    board = json.loads((out / "scoreboard.json").read_text(encoding="utf-8"))
    assert out == tmp_path / "sb" / "first"
    assert [(r["id"], r["outcome"], r["tier"]) for r in board["rows"]] == [("t", "solved", 0), ("u", "unsolved", 3), ("f", "refused", 3)]
    assert board["aggregates"]["headline"]["n"] == 1 and board["aggregates"]["headline"]["solved"] == 0
    assert board["aggregates"]["tiers"]["3"]["refused"] == 1
    assert board["interrupted"] is False and board["finished_at"] is not None
    assert board["baseline_sha256"] == sha256_of(baseline) and board["problems_sha256"] == sha256_of(problems)
    assert (out / "runs" / "t" / "batch-0" / "result.json").exists()


def test_repeats_and_selection(tmp_path):
    problems, baseline = _files(tmp_path)
    out = runner.run_set(label="x", problems_path=problems, baseline_path=baseline, scoreboards_root=tmp_path / "sb",
                         condition=_condition(repeats=2, selection={"only": None, "tiers": [3], "twins": False}), environment=IDENTITY,
                         batch_runner=_batch_runner({"u": GIVE_UP}), now=lambda: datetime(2026, 9, 1, tzinfo=UTC), report=lambda _: None)
    rows = json.loads((out / "scoreboard.json").read_text(encoding="utf-8"))["rows"]
    assert [(r["id"], r["repeat"]) for r in rows] == [("u", 0), ("u", 1)]


@pytest.mark.parametrize("break_it,needle", [
    ("problems", "different problems.json"), ("identity", "mathlib_revision"), ("problems_list", "records problems"), ("label", "already exists"),
])
def test_the_gates_refuse_before_anything_runs(tmp_path, break_it, needle):
    problems, baseline = _files(tmp_path)
    environment = IDENTITY
    if break_it == "problems":
        problems.write_text(problems.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    if break_it == "identity":
        environment = IDENTITY.model_copy(update={"mathlib_revision": "v9"})
    if break_it == "problems_list":
        payload = json.loads(baseline.read_text(encoding="utf-8"))
        payload["problems"] = ["f: a twin closed by simp, so it is true"]
        baseline.write_text(json.dumps(payload), encoding="utf-8")
    if break_it == "label":
        (tmp_path / "sb" / "x").mkdir(parents=True)
    ran = []
    with pytest.raises(runner.RefusedRun, match=needle):
        runner.run_set(label="x", problems_path=problems, baseline_path=baseline, scoreboards_root=tmp_path / "sb", condition=_condition(),
                       environment=environment, batch_runner=lambda *a: ran.append(a), now=lambda: datetime(2026, 9, 1, tzinfo=UTC), report=lambda _: None)
    assert ran == []


def test_the_gates_refuse_a_staged_run_with_no_staged_runner(tmp_path):
    problems, baseline = _files(tmp_path)
    ran = []
    with pytest.raises(runner.RefusedRun, match="staged runner"):
        runner.run_set(label="x", problems_path=problems, baseline_path=baseline, scoreboards_root=tmp_path / "sb",
                       condition=_condition(mode="staged"), environment=IDENTITY, batch_runner=lambda *a: ran.append(a),
                       now=lambda: datetime(2026, 9, 1, tzinfo=UTC), report=lambda _: None)
    assert ran == []


def test_select_dedupes_only_preserving_first_occurrence(tmp_path):
    problems_path, baseline_path = _files(tmp_path)
    problems = load_problems(problems_path)
    baseline = sweep.Baseline.model_validate_json(baseline_path.read_text(encoding="utf-8"))
    chosen = runner.select(problems, baseline, only=["u", "t", "u"], tiers=None, twins=True)
    assert [entry.id for entry in chosen] == ["u", "t"]


def test_select_refuses_an_only_naming_an_unknown_entry(tmp_path):
    problems_path, baseline_path = _files(tmp_path)
    problems = load_problems(problems_path)
    baseline = sweep.Baseline.model_validate_json(baseline_path.read_text(encoding="utf-8"))
    with pytest.raises(runner.RefusedRun, match="nope"):
        runner.select(problems, baseline, only=["nope"], tiers=None, twins=True)


def test_an_interrupted_run_keeps_its_rows_and_says_so(tmp_path):
    problems, baseline = _files(tmp_path)
    calls = {"n": 0}

    def flaky(entry: Entry, output: Path, max_turns: int, wall_seconds: float) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        _scripted_batch(output, SOLVE, declaration=entry.declaration(), informal_claim=entry.input)

    with pytest.raises(KeyboardInterrupt):
        runner.run_set(label="x", problems_path=problems, baseline_path=baseline, scoreboards_root=tmp_path / "sb", condition=_condition(),
                       environment=IDENTITY, batch_runner=flaky, now=lambda: datetime(2026, 9, 1, tzinfo=UTC), report=lambda _: None)
    board = json.loads((tmp_path / "sb" / "x" / "scoreboard.json").read_text(encoding="utf-8"))
    assert board["interrupted"] is True and board["finished_at"] is None and len(board["rows"]) == 1


def test_twins_run_batch_even_under_staged_mode(tmp_path):
    problems, baseline = _files(tmp_path)
    modes = []

    def batch(entry, output, max_turns, wall_seconds):
        modes.append(("batch", entry.id))
        _scripted_batch(output, GIVE_UP, declaration=entry.declaration(), informal_claim=entry.input)

    def staged(entry, row_dir, model):
        modes.append(("staged", entry.id))
        raise KeyboardInterrupt  # stop after the first staged row; this test is about routing

    with pytest.raises(KeyboardInterrupt):
        runner.run_set(label="x", problems_path=problems, baseline_path=baseline, scoreboards_root=tmp_path / "sb",
                       condition=_condition(mode="staged", selection={"only": ["f", "t"], "tiers": None, "twins": True}), environment=IDENTITY,
                       batch_runner=batch, staged_runner=staged, now=lambda: datetime(2026, 9, 1, tzinfo=UTC), report=lambda _: None)
    assert ("batch", "f") in modes and ("staged", "t") in modes
