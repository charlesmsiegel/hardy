"""The set runner: refuses before it spends, writes rows as it goes, never pretends to be complete."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from corpus_helpers import write_corpus
from test_recorded_runs import FAKE_LEAN, _Runtime
from test_recorded_runs import IDENTITY as RAW_IDENTITY

from hardy.domain import EnvironmentIdentity
from hardy.evals import runner, sweep
from hardy.evals.corpus import load_corpus, manifest_digest
from hardy.evals.problems import Entry, sha256_of

HOST = sweep.host_info()
IDENTITY = EnvironmentIdentity(**RAW_IDENTITY)
ENTRIES = (
    Entry(id="t", input="True.", name="T", conclusion="True", expected="true", source="textbook", msc=("11A",), difficulty="routine", rationale="test fixture", witness=None, witness_note="test fixture"),
    Entry(id="u", input="True again.", name="U", conclusion="True", expected="true", source="classical", msc=("11A",), difficulty="routine", rationale="test fixture", witness=None, witness_note="test fixture"),
    Entry(id="f", input="False.", name="F", conclusion="True", expected="false", twin_of="t", source="textbook", msc=("11A",), difficulty="routine", rationale="test fixture", witness=None, witness_note="test fixture"),
)


# One representative closer per tier, so a fixture that only cares about
# *which* tier an entry lands in can still build an `EntryBaseline` that
# satisfies `tier_must_follow_its_closers` (item 7): `tier` must equal
# `tier_of(closed_by)`, and every name in `closed_by` needs a `closed`
# attempt to back it.
_TIER_CLOSERS: dict[int, tuple[str, ...]] = {0: ("simp",), 1: ("exact?",), 2: ("intros; simp_all",), 3: ()}


def _full_attempts(closed_by: tuple[str, ...] = ()) -> dict[str, sweep.Attempt]:
    """Every tactic `sweep.Baseline`'s completeness check now requires
    (item 4): the code's own `singles`/`chains`, each `closed` if named by
    `closed_by` and `failed` otherwise -- never a truncated subset.
    """
    return {name: sweep.Attempt(status="closed" if name in closed_by else "failed") for name in sweep.SINGLES + sweep.CHAINS}


def _entry_baseline(tier: int, *, twin: bool = False, **kw) -> sweep.EntryBaseline:
    closed_by = _TIER_CLOSERS[tier]
    base = dict(tier=tier, elaborates=True, attempts=_full_attempts(closed_by), closed_by=closed_by)
    if twin:
        # A twin's baseline must carry its A3 negation sweep, or `staleness`
        # refuses it as one taken while the entry was still labelled true.
        base["negation"] = sweep.NegationBaseline(attempts=_full_attempts(()), closed_by=())
    base.update(kw)
    return sweep.EntryBaseline(**base)


def _files(tmp_path: Path, tiers: dict[str, int] = None) -> tuple[Path, Path]:
    problems = write_corpus(tmp_path / "corpus", ENTRIES)
    tiers = tiers or {"t": 0, "u": 3, "f": 3}
    baseline = sweep.Baseline(created_at=datetime(2026, 9, 1, tzinfo=UTC), problems_sha256=manifest_digest(problems), environment=IDENTITY,
                              environment_digest=sweep.environment_digest_of(IDENTITY, HOST), procedure_digest=sweep.procedure_digest_of(600.0),
                              statement_digests={e.id: e.statement_digest() for e in ENTRIES},
                              heartbeat_budget=200000, wall_backstop_seconds=600.0, singles=sweep.SINGLES, chains=sweep.CHAINS, host=HOST, problems=(),
                              entries={k: _entry_baseline(v, twin=any(e.id == k and e.expected == "false" for e in ENTRIES))
                                       for k, v in tiers.items()})
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


def _scripted_batch(output: Path, script, *, declaration: str, informal_claim: str, imports: tuple[str, ...] | None = None, wall_seconds: float = 300.0) -> Path:
    """A batch run over the fake Lean, scripted, with the entry's own claim.

    Modelled on `test_recorded_runs._batch`, but the declaration and informal
    claim are the caller's rather than the hardcoded `HardyTarget`: a set run
    validates each row against its own entry, so three same-named theorems
    would collide both in `ProblemSet` (which refuses duplicate names) and at
    that later check. `imports`, when given, lets a caller build a run that is
    self-consistent under a *different* import list than the entry's own --
    the genuinely-inconsistent-with-the-entry case item 1/item 7's checks are
    for, as opposed to a run merely edited after the fact (which the
    recorded-run audit's own checks would catch first, per item 3).
    """
    import sys

    from hardy import models
    from hardy import runner as hardy_runner
    from hardy.lean import LeanTools

    payload = {"declaration": declaration, "informal_claim": informal_claim}
    if imports is not None:
        payload["imports"] = list(imports)
    request = models.Request.from_dict(payload)
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
    # The headline is 0 because every fixture entry is `candidate`: only
    # reviewed entries reach a headline (spec §2.2), while every row still
    # runs and every tier still reports. `floor.active` names the denominator
    # so this reads as "none of it is reviewed", not "nothing ran".
    assert board["aggregates"]["headline"]["n"] == 0 and board["aggregates"]["floor"]["active"] == 0
    assert board["aggregates"]["tiers"]["3"]["n"] == 1, "the measurement is not withheld, only the claim"
    assert board["aggregates"]["tiers"]["3"]["refused"] == 1
    assert board["interrupted"] is False and board["finished_at"] is not None
    assert board["baseline_sha256"] == sha256_of(baseline) and board["problems_sha256"] == manifest_digest(problems)
    assert (out / "runs" / "t" / "batch-0" / "result.json").exists()
    # A committed scoreboard is repository evidence too; a platform-
    # translated write would checkin CRLF on Windows.
    assert b"\r" not in (out / "scoreboard.json").read_bytes()


def test_repeats_and_selection(tmp_path):
    problems, baseline = _files(tmp_path)
    out = runner.run_set(label="x", problems_path=problems, baseline_path=baseline, scoreboards_root=tmp_path / "sb",
                         condition=_condition(repeats=2, selection={"only": None, "tiers": [3], "twins": False}), environment=IDENTITY,
                         batch_runner=_batch_runner({"u": GIVE_UP}), now=lambda: datetime(2026, 9, 1, tzinfo=UTC), report=lambda _: None)
    rows = json.loads((out / "scoreboard.json").read_text(encoding="utf-8"))["rows"]
    assert [(r["id"], r["repeat"]) for r in rows] == [("u", 0), ("u", 1)]


@pytest.mark.parametrize("break_it,needle", [
    ("statement", "changed since the baseline"), ("identity", "mathlib_revision"), ("problems_list", "records problems"), ("label", "already exists"),
])
def test_the_gates_refuse_before_anything_runs(tmp_path, break_it, needle):
    problems, baseline = _files(tmp_path)
    environment = IDENTITY
    if break_it == "statement":
        # An edit to one entry's Lean, not to the file's bytes: staleness is
        # per entry now, so a reworded prose line must *not* trip this gate
        # while a changed conclusion must.
        edited = tuple(e.model_copy(update={"conclusion": "True ∧ True"}) if e.id == "t" else e for e in ENTRIES)
        write_corpus(problems, edited)
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


@pytest.mark.parametrize("label", ["../x", "a/b", "..", "/etc/passwd", "C:\\temp\\evil"])
def test_a_label_that_is_not_a_single_path_component_is_refused(tmp_path, label):
    """`scoreboards_root / label` would otherwise resolve outside
    `evals/scoreboards` for a label like `../../new-eval` or an absolute
    path, and the run tree would be written there despite the documented
    output contract (item 4). Refused before the problem list is even read,
    so nothing -- not even `scoreboards_root` itself -- is created.
    """
    problems, baseline = _files(tmp_path)
    ran = []
    with pytest.raises(runner.RefusedRun, match="path component"):
        runner.run_set(label=label, problems_path=problems, baseline_path=baseline, scoreboards_root=tmp_path / "sb", condition=_condition(),
                       environment=IDENTITY, batch_runner=lambda *a: ran.append(a), now=lambda: datetime(2026, 9, 1, tzinfo=UTC), report=lambda _: None)
    assert ran == []
    assert not (tmp_path / "sb").exists()


def test_an_empty_selection_is_refused_before_the_output_directory_is_created(tmp_path):
    """`--tiers 2` against a baseline with no tier-2 entries (the test
    baseline here has none) would otherwise write a finished, zero-row
    scoreboard that `hardy evals check` also accepts -- the same empty
    selection derives the same empty expected order (item 5).
    """
    problems, baseline = _files(tmp_path)
    ran = []
    with pytest.raises(runner.RefusedRun, match="selection matches no entries"):
        runner.run_set(label="x", problems_path=problems, baseline_path=baseline, scoreboards_root=tmp_path / "sb",
                       condition=_condition(selection={"only": None, "tiers": [2], "twins": True}), environment=IDENTITY,
                       batch_runner=lambda *a: ran.append(a), now=lambda: datetime(2026, 9, 1, tzinfo=UTC), report=lambda _: None)
    assert ran == []
    assert not (tmp_path / "sb" / "x").exists()


class _FakeCompleted:
    def __init__(self, returncode: int, stdout: str) -> None:
        self.returncode, self.stdout = returncode, stdout


def test_source_revision_reads_head_and_marks_a_dirty_working_tree(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(runner, "_source_anchor", lambda: tmp_path)

    def fake_run(argv, **kw):
        if argv[:2] == ["git", "rev-parse"]:
            return _FakeCompleted(0, "abc123\n")
        if argv[:2] == ["git", "status"]:
            return _FakeCompleted(0, " M some/file\n")
        raise AssertionError(argv)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    assert runner.source_revision() == "abc123-dirty"


def test_source_revision_is_bare_when_the_tree_is_clean(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(runner, "_source_anchor", lambda: tmp_path)

    def fake_run(argv, **kw):
        if argv[:2] == ["git", "rev-parse"]:
            return _FakeCompleted(0, "abc123\n")
        if argv[:2] == ["git", "status"]:
            return _FakeCompleted(0, "")
        raise AssertionError(argv)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    assert runner.source_revision() == "abc123"


def test_source_revision_is_none_when_git_is_not_on_path(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(runner, "_source_anchor", lambda: tmp_path)

    def fake_run(argv, **kw):
        raise FileNotFoundError("git")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    assert runner.source_revision() is None


def test_source_revision_is_none_outside_a_git_repository(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(runner, "_source_anchor", lambda: tmp_path)

    def fake_run(argv, **kw):
        if argv[:2] == ["git", "rev-parse"]:
            return _FakeCompleted(128, "")
        raise AssertionError(argv)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    assert runner.source_revision() is None


def test_source_revision_is_none_when_the_anchor_has_no_git_above_it(monkeypatch, tmp_path):
    """The Hardy package's own directory anchors the search (item 1), walked
    upward for `.git`; a caller (or, here, a test) can move that anchor
    anywhere, and finding no `.git` above it at all -- as `tmp_path` never
    does -- must be `None`, not a crash or a `git` invocation.
    """
    monkeypatch.setattr(runner, "_source_anchor", lambda: tmp_path)
    ran = []
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **kw: ran.append(a))
    assert runner.source_revision() is None
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
    problems = load_corpus(problems_path)
    baseline = sweep.Baseline.model_validate_json(baseline_path.read_text(encoding="utf-8"))
    chosen = runner.select(problems, baseline, only=["u", "t", "u"], tiers=None, twins=True)
    assert [entry.id for entry in chosen] == ["u", "t"]


def test_select_refuses_an_only_naming_an_unknown_entry(tmp_path):
    problems_path, baseline_path = _files(tmp_path)
    problems = load_corpus(problems_path)
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


def _fake_config(**kw):
    from types import SimpleNamespace

    base = dict(model="config-model@test", lean_command=("some-other-lean-command",), lake=Path("lake"), lean_timeout=30.0, lean_project=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_the_batch_runner_uses_the_conditions_selected_model_not_configs(monkeypatch, tmp_path):
    """`--model override@test` selects a model the condition records
    (`run_set_command`'s `condition.model`), and every batch row must
    actually be produced by it -- not by whatever `config.model` happens to
    be, which under an override is a different model entirely (item 1).
    """
    from hardy import cli as cli_module
    from hardy import runner as hardy_runner

    seen: dict = {}
    monkeypatch.setattr(cli_module, "runtime_factory", lambda model: seen.setdefault("model", model))
    monkeypatch.setattr(hardy_runner, "run", lambda *a, **kw: seen.setdefault("called", True))

    run_one = runner._batch_runner(_fake_config(), "override@test")
    run_one(ENTRIES[0], tmp_path / "out", 3, 300.0)
    assert seen["model"] == "override@test"
    assert seen["called"] is True


def test_the_batch_runner_checks_proofs_with_the_recorded_toolchains_command(monkeypatch, tmp_path):
    """The batch runner used to build `LeanTools` from `config.lean_command`,
    which a global `--lean-command` could set to something other than
    `config.lake env lean` -- the very command `run_set_command` asks
    `environment_identity` about for the scoreboard's `environment` and the
    baseline sweep both use. A row's proof checks must run under that same
    command, or its checks could pass under a toolchain the experiment was
    never actually measured against (item 2).
    """
    from hardy import cli as cli_module
    from hardy import lean as lean_module
    from hardy import runner as hardy_runner

    seen: dict = {}

    class _FakeLeanTools:
        def __init__(self, request, lean_command, *, timeout, project):
            seen["lean_command"] = lean_command
            seen["timeout"] = timeout
            seen["project"] = project

    monkeypatch.setattr(lean_module, "LeanTools", _FakeLeanTools)
    monkeypatch.setattr(cli_module, "runtime_factory", lambda model: None)
    monkeypatch.setattr(hardy_runner, "run", lambda *a, **kw: None)

    config = _fake_config(lake=Path("customlake"), lean_timeout=45.0, lean_project=Path("project"))
    run_one = runner._batch_runner(config, "m@test")
    run_one(ENTRIES[0], tmp_path / "out", 3, 300.0)
    assert seen["lean_command"] == ("customlake", "env", "lean")
    assert seen["timeout"] == 45.0 and seen["project"] == Path("project")


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
