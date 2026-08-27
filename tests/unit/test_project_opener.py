"""`ProjectOpener`: what a `/project switch` rebuilds, and what it keeps.

The keeping is the point. A problem's record, transcript, Lean namespace and
computer algebra kernel are its own and are rebuilt; the pinned Lake project
and the Mathlib environment behind the search tools belong to the root, cost
tens of seconds, and are carried across untouched. That difference is the
whole reason `/project switch` is not `exit` with extra steps.

The configuration a switch starts from is the one the SESSION is running, not
one the opener kept from launch: `/model` moves the live session and touches
nothing here, so a stored copy is stale from the moment anyone uses it.
"""

from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path

import pytest

from hardy import cli, layout, search_tools
from hardy import config as configuration
from hardy.retrieval import build_retriever


def _decline(proposal: dict) -> bool:
    """The approval gate a reopened session is handed. Never called here."""
    return False


class FakeKernel:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeCas:
    def __init__(self, cwd: Path):
        self.cwd = cwd
        self.session = FakeKernel()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "work"


@pytest.fixture
def args(tmp_path: Path, root: Path) -> argparse.Namespace:
    return argparse.Namespace(
        config=tmp_path / "config.toml",
        root=root,
        project=None,
        model=None,
        lean_command=None,
        lean_project=None,
        latex_command=None,
    )


@pytest.fixture
def live(args, root) -> configuration.Config:
    """The configuration the session is running: what a switch starts from."""
    root.mkdir(parents=True, exist_ok=True)
    config = configuration.load(args.config, root=root, project="sylow")
    cli.prepare_layout(config)
    return config


@pytest.fixture
def opener(monkeypatch, args, live):
    built: list[Path] = []

    def fake_build_runtime(**kwargs):
        built.append(kwargs["cwd"])
        return FakeCas(kwargs["cwd"]), "fake 1.0"

    monkeypatch.setattr(cli.cas_tools, "build_runtime", fake_build_runtime)
    monkeypatch.setattr(cli, "MathematicsSession", lambda *a, **k: object())
    made = cli.ProjectOpener(
        live.project,
        FakeCas(live.layout.cas),
        search=search_tools.SearchToolRuntime(
            object(), build_retriever(object(), live.limits), object()
        ),
        search_detail="Mathlib abc",
    )
    made.built = built
    return made


# -- what a switch opens --------------------------------------------------


def test_opening_another_problem_returns_its_configuration(opener, live, root):
    config, session = opener("burnside", _decline, live)
    assert config.project == "burnside"
    assert config.layout.problem == root / "burnside"
    assert session is not None


def test_the_new_problem_gets_its_trees_before_anything_writes(opener, live, root):
    opener("burnside", _decline, live)
    assert (root / "burnside" / "lean").is_dir()
    assert (root / "burnside" / ".build").is_dir()


def test_the_old_kernel_is_closed_and_the_new_one_runs_in_the_new_problem(opener, live):
    previous = opener.cas
    config, _ = opener("burnside", _decline, live)
    assert previous.session.closed
    assert opener.cas is not previous
    assert opener.cas.session.closed is False
    assert opener.built == [config.layout.cas]


def test_the_pinned_environment_is_carried_across_rather_than_rebuilt(opener, live, monkeypatch):
    """The expensive half of a launch. Rebuilding it would make a switch an exit."""
    monkeypatch.setattr(
        cli.search_tools, "build_runtime", lambda config: pytest.fail("search was rebuilt")
    )
    opener("burnside", _decline, live)


def test_each_problem_gets_its_own_retrieval_budget(opener, live, monkeypatch):
    """`PremiseRetriever._spent` accumulates for the retriever's whole life.

    Carried over, a problem opened after one that had spent its allowance
    ranked against a budget consumed by calls that appear nowhere in its own
    record -- which is the reproducible provenance `rank_premises` claims.
    """
    handed = {}
    monkeypatch.setattr(
        cli, "MathematicsSession", lambda *a, **k: handed.update(k) or object()
    )
    before = opener._search
    before.retriever._spent = float(live.limits.retrieval_seconds)
    assert before.retriever.seconds_remaining == 0.0

    opener("burnside", _decline, live)

    assert handed["search"] is not before
    assert handed["search"].retriever is not before.retriever
    assert handed["search"].retriever.seconds_remaining == live.limits.retrieval_seconds
    # The costly parts are the same objects, which is the point of renewing
    # rather than rebuilding.
    assert handed["search"].service is before.service
    assert handed["search"].modules is before.modules


def test_the_switch_is_remembered_so_the_next_launch_opens_it(opener, live, root):
    opener("burnside", _decline, live)
    written = configuration.read_file(root / layout.HARDY_DIR / "config.toml")
    assert written["project"] == "burnside"


def test_a_failed_open_closes_the_kernel_it_started_and_keeps_the_old_one(
    opener, live, monkeypatch
):
    previous = opener.cas

    def explode(*a, **k):
        raise layout.LayoutError("tex/ is a symlink out of the project")

    monkeypatch.setattr(cli, "MathematicsSession", explode)
    with pytest.raises(layout.LayoutError):
        opener("burnside", _decline, live)
    assert previous.session.closed is False
    assert opener.cas is previous


def test_a_slug_the_layout_refuses_never_reaches_the_filesystem(opener, live):
    with pytest.raises(layout.LayoutError):
        opener("../elsewhere", _decline, live)


# -- the model the session is actually running ----------------------------


def test_the_model_comes_from_the_configuration_handed_in_at_the_switch(opener, live):
    """`/model` moves the live session; a switch must not move it back.

    `handle_model` replaces the TUI's `State.config` and nothing else, so a
    model an opener stored at launch is stale the moment anyone runs `/model`.
    The configuration the session is running is the only source that cannot go
    stale, which is why it is an argument rather than a field.
    """
    moved = dataclasses.replace(live, model="claude-haiku-4-5-20251001")
    config, _ = opener("burnside", _decline, moved)
    assert config.model == "claude-haiku-4-5-20251001"


def test_the_live_model_wins_over_the_file_even_after_a_save(opener, live, args):
    """Saving makes the file agree, so this passes either way -- and the
    point is that it must pass by carrying the live value, not by luck."""
    configuration.write_setting(args.config, "model", "claude-haiku-4-5-20251001")
    moved = dataclasses.replace(live, model="claude-haiku-4-5-20251001")
    config, _ = opener("burnside", _decline, moved)
    assert config.model == "claude-haiku-4-5-20251001"


# -- the write into a checkout's own directory ----------------------------


def test_a_symlinked_temporary_never_reaches_the_file_it_points_at(opener, live, root, tmp_path):
    """`.hardy/` arrives with a clone, so its temporaries are attacker-chosen.

    A fixed `<name>.tmp` is written through before the rename, so a repository
    shipping `.hardy/config.toml.tmp` as a link to something outside gets that
    file truncated, overwritten and chmodded 0600 on the first switch -- and
    the rename afterwards moves the link itself over the config, leaving the
    victim destroyed. `WriteGuard.write_bytes` closed exactly this hole for
    the record; the project config has to go through the same door.
    """
    victim = tmp_path / "victim"
    victim.write_text("do not touch\n", encoding="utf-8")
    hardy = root / layout.HARDY_DIR
    hardy.mkdir(parents=True, exist_ok=True)
    (hardy / "config.toml.tmp").symlink_to(victim)

    opener("burnside", _decline, live)

    assert victim.read_text(encoding="utf-8") == "do not touch\n"
    assert configuration.read_file(hardy / "config.toml")["project"] == "burnside"


def test_a_symlinked_project_config_is_refused_rather_than_written_through(
    opener, live, root, tmp_path
):
    victim = tmp_path / "elsewhere.toml"
    victim.write_text('project = "theirs"\n', encoding="utf-8")
    hardy = root / layout.HARDY_DIR
    hardy.mkdir(parents=True, exist_ok=True)
    (hardy / "config.toml").symlink_to(victim)

    opener("burnside", _decline, live)

    assert victim.read_text(encoding="utf-8") == 'project = "theirs"\n'


def test_a_refused_record_of_the_switch_does_not_undo_the_switch(opener, live, root, capsys):
    """The problem is already open; a config file is not worth closing it for."""
    hardy = root / layout.HARDY_DIR
    hardy.mkdir(parents=True, exist_ok=True)
    (hardy / "config.toml").symlink_to(root / "elsewhere.toml")

    config, session = opener("burnside", _decline, live)

    assert config.project == "burnside"
    assert session is not None
    assert "config.toml" in capsys.readouterr().out


def test_a_multiline_value_is_replaced_whole_and_not_by_its_first_line(opener, live, root):
    """TOML is not line-oriented, and this file arrives hand-editable.

    `project = \"\"\"` with the slug on the next line is one valid assignment
    that `read_file` resolves to an ordinary slug. Editing the line it starts
    on leaves the continuation behind, which `tomllib` refuses -- so the first
    switch would brick every launch after it.
    """
    hardy = root / layout.HARDY_DIR
    hardy.mkdir(parents=True, exist_ok=True)
    (hardy / "config.toml").write_text('project = """\nsylow"""\n', encoding="utf-8")
    assert configuration.read_file(hardy / "config.toml") == {"project": "sylow"}

    opener("burnside", _decline, live)

    assert configuration.read_file(hardy / "config.toml") == {"project": "burnside"}


def test_a_key_this_layer_may_not_set_is_kept_rather_than_deleted(opener, live, root):
    """`load` reports it and ignores it; passing by is not a licence to drop it."""
    hardy = root / layout.HARDY_DIR
    hardy.mkdir(parents=True, exist_ok=True)
    (hardy / "config.toml").write_text(
        'project = "sylow"\nmodel = "someone-elses-choice"\n', encoding="utf-8"
    )

    opener("burnside", _decline, live)

    written = configuration.read_file(hardy / "config.toml")
    assert written["project"] == "burnside"
    assert written["model"] == "someone-elses-choice"


def test_only_a_setting_this_layer_may_hold_can_be_written(root):
    with pytest.raises(ValueError, match="may only set"):
        configuration.write_project_setting(root, "model", "claude-opus-5")


def test_a_value_that_cannot_be_rewritten_unchanged_stops_the_write(opener, live, root, capsys):
    """Preserving keys was the point of parsing; a mangled key is not preserved.

    `_render_toml_line` renders scalars, and a list or table reached it as
    `str(value)` -- so a switch silently turned `model = ["a", "b"]` into a
    quoted Python repr and a `[tectonic]` table into a string, in a tracked
    file, as a side effect of doing something else entirely.
    """
    hardy = root / layout.HARDY_DIR
    hardy.mkdir(parents=True, exist_ok=True)
    original = 'project = "sylow"\nmodel = ["a", "b"]\n\n[tectonic]\nbundle = "x"\n'
    (hardy / "config.toml").write_text(original, encoding="utf-8")

    config, session = opener("burnside", _decline, live)

    assert (hardy / "config.toml").read_text(encoding="utf-8") == original
    # The switch stands: only the note saying so for next time is lost.
    assert config.project == "burnside"
    assert session is not None
    assert "cannot rewrite" in capsys.readouterr().out


def test_the_refusal_names_the_settings_and_their_types(root):
    hardy = root / layout.HARDY_DIR
    hardy.mkdir(parents=True, exist_ok=True)
    (hardy / "config.toml").write_text('model = ["a"]\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r"model \(list\)"):
        configuration.write_project_setting(root, "project", "burnside")


def test_a_session_told_not_to_read_project_context_stays_told(opener, live, monkeypatch):
    """`--no-project-context` is a flag, so it lives only in the live config.

    Re-reading the layers cannot recover it, and leaving it out turned the
    project's own `AGENTS.md` back on for a user who had just asked for it to
    be left alone -- a deliberate choice reversed by an unrelated command.
    """
    handed = {}
    monkeypatch.setattr(cli, "MathematicsSession", lambda *a, **k: handed.update(k) or object())
    off = dataclasses.replace(live, project_context=False)

    config, _ = opener("burnside", _decline, off)

    assert config.project_context is False
    assert handed["project_context"] is False


def test_reading_project_context_is_carried_across_too(opener, live, monkeypatch):
    handed = {}
    monkeypatch.setattr(cli, "MathematicsSession", lambda *a, **k: handed.update(k) or object())

    config, _ = opener("burnside", _decline, dataclasses.replace(live, project_context=True))

    assert config.project_context is True
    assert handed["project_context"] is True


# -- one retrieval meter per problem, kept -------------------------------


def _search_handed(monkeypatch):
    handed = {}
    monkeypatch.setattr(cli, "MathematicsSession", lambda *a, **k: handed.update(k) or object())
    return handed


def test_returning_to_a_problem_resumes_its_meter_rather_than_refilling_it(
    opener, live, monkeypatch
):
    """`A -> B -> A` gave A a full allowance again, and the cycle repeats.

    A budget that is cumulative by construction became effectively unlimited,
    and the next ranking reported `prior_seconds_spent=0` over a trajectory
    that had already spent it. Returning to a problem resumes its record and
    its provider thread; its meter belongs with them.
    """
    handed = _search_handed(monkeypatch)

    opener("burnside", _decline, live)
    handed["search"].retriever._spent = 500.0
    opener("sylow", _decline, live)
    opener("burnside", _decline, live)

    assert handed["search"].retriever._spent == 500.0
    assert handed["search"].retriever.seconds_remaining == live.limits.retrieval_seconds - 500.0


def test_a_problem_opened_for_the_first_time_still_starts_full(opener, live, monkeypatch):
    handed = _search_handed(monkeypatch)

    opener("burnside", _decline, live)
    handed["search"].retriever._spent = 500.0
    opener("galois", _decline, live)

    assert handed["search"].retriever.seconds_remaining == live.limits.retrieval_seconds


def test_the_launch_problem_keeps_the_meter_its_first_session_was_given(
    opener, live, monkeypatch
):
    """Seeded, so returning to where the session started is not a refill."""
    handed = _search_handed(monkeypatch)
    opener._search.retriever._spent = 120.0

    opener(live.project, _decline, live)

    assert handed["search"] is opener._search
    assert handed["search"].retriever._spent == 120.0


# -- the configuration a switch runs under -------------------------------


def test_a_config_edited_while_hardy_runs_does_not_split_lean_from_search(
    opener, live, args, monkeypatch
):
    """Search carries over, so re-reading the toolchain would split the two.

    The new session would elaborate in the edited Lake project while the
    search tools kept describing and querying the launch-time one -- premises
    from a toolchain the session's own Lean cannot use.
    """
    handed = _search_handed(monkeypatch)
    args.config.write_text('lean_project = "/edited/project"\n', encoding="utf-8")

    config, _ = opener("burnside", _decline, live)

    assert config.lean_project == live.lean_project
    assert handed["search"] is not None


def test_every_setting_but_the_problem_comes_from_the_live_session(opener, live):
    """One rule instead of a list of fields patched one finding at a time."""
    moved = dataclasses.replace(
        live, model="claude-haiku-4-5-20251001", project_context=False, lean_timeout=42.0
    )

    config, _ = opener("burnside", _decline, moved)

    assert config.project == "burnside"
    assert dataclasses.replace(config, project=moved.project) == moved


# -- a switch nobody is waiting for -------------------------------------


class SlowKernel(FakeKernel):
    """A session whose probe is still running, reachable only by `escalate`."""

    def __init__(self) -> None:
        super().__init__()
        self.escalated = False

    def escalate(self) -> bool:
        self.escalated = True
        return True


def test_a_cancelled_reopen_commits_nothing(opener, live, root, monkeypatch):
    """The worker runs on after the await is gone; it must not finish the job.

    Otherwise a Ctrl+C during a switch still closed the kernel the user is
    using and rewrote the active project in a committed file, for a switch
    they cancelled.
    """
    previous = opener.cas

    def build(**kwargs):
        opener.cancel()                       # the user presses Ctrl+C mid-probe
        return FakeCas(kwargs["cwd"]), "fake 1.0"

    monkeypatch.setattr(cli.cas_tools, "build_runtime", build)

    with pytest.raises(cli.ReopenCancelled):
        opener("burnside", _decline, live)

    assert previous.session.closed is False   # the live kernel is untouched
    assert opener.cas is previous
    assert not (root / layout.HARDY_DIR / "config.toml").exists()


def test_a_cancelled_reopen_reaches_the_kernel_it_was_probing(opener, live, monkeypatch):
    """`escalate` takes `_signal_lock` and never `_lock`, which the probe holds.

    `process.interrupt_children` cannot do this -- its register deliberately
    excludes a persistent CAS kernel -- so the opener has to hold the session
    itself.
    """
    probing = SlowKernel()

    def build(*, on_session=None, **kwargs):
        on_session(probing)
        opener.cancel()
        raise AssertionError("unreachable: cancel is expected to stop this")

    monkeypatch.setattr(cli.cas_tools, "build_runtime", build)

    with pytest.raises(AssertionError):
        opener("burnside", _decline, live)

    assert probing.escalated


def test_a_cancel_arriving_before_the_kernel_exists_still_stops_it(opener, live, monkeypatch):
    """The gap between the call starting and the probe having a kernel.

    A cancel landing there has nothing to reach yet, so the handover escalates
    on arrival rather than leaving the probe to run its full limit out.
    """
    probing = SlowKernel()

    def build(*, on_session=None, **kwargs):
        opener.cancel()               # cancelled before there is a kernel
        on_session(probing)           # ... which arrives a moment later
        return None, "cancelled"

    monkeypatch.setattr(cli.cas_tools, "build_runtime", build)

    with pytest.raises(cli.ReopenCancelled):
        opener("burnside", _decline, live)

    assert probing.escalated


def test_a_cancel_for_one_switch_cannot_refuse_the_next(opener, live, root):
    """Per call, not per opener: a stale flag would refuse a later switch."""
    def build(**kwargs):
        opener.cancel()
        return FakeCas(kwargs["cwd"]), "fake 1.0"

    import hardy.cli as module
    original = module.cas_tools.build_runtime
    module.cas_tools.build_runtime = build
    try:
        with pytest.raises(cli.ReopenCancelled):
            opener("burnside", _decline, live)
    finally:
        module.cas_tools.build_runtime = original

    config, session = opener("galois", _decline, live)
    assert config.project == "galois"
    assert session is not None


def test_an_uncancelled_reopen_still_commits(opener, live, root):
    """The guard must not be able to refuse a switch nobody cancelled."""
    config, session = opener("burnside", _decline, live)
    assert config.project == "burnside"
    assert session is not None
    assert configuration.read_file(root / layout.HARDY_DIR / "config.toml")["project"] == "burnside"


def test_the_cancellation_guard_is_published_before_any_work(opener, live, monkeypatch):
    """A cancel can only mark a reopen it can see.

    The first version created the guard after `prepare_layout` and the search
    renewal, so a cancel arriving while directories were being made had
    nothing to mark and the switch completed -- a window the docstring
    described as far smaller than it was.
    """
    seen = []
    real = cli.prepare_layout
    monkeypatch.setattr(
        cli, "prepare_layout", lambda config: seen.append(opener._opening) or real(config)
    )

    opener("burnside", _decline, live)

    assert seen and seen[0] is not None


def test_a_cancel_during_directory_creation_commits_nothing(opener, live, root, monkeypatch):
    previous = opener.cas
    real = cli.prepare_layout
    monkeypatch.setattr(
        cli, "prepare_layout", lambda config: opener.cancel() or real(config)
    )

    with pytest.raises(cli.ReopenCancelled):
        opener("burnside", _decline, live)

    assert previous.session.closed is False
    assert opener.cas is previous
    assert not (root / layout.HARDY_DIR / "config.toml").exists()


def test_cancel_says_whether_there_was_anything_to_stop(opener):
    """The terminal reports what it did, so it must not claim a stop it made up."""
    assert opener.cancel() is False


def test_the_launch_registration_policy_is_carried(args, live):
    """`--no-register-lakefile` is about this process, not about one problem."""
    made = cli.ProjectOpener(
        live.project, None, search=None, search_detail="", register_lakefile=False
    )
    assert made.register_lakefile is False
    assert cli.ProjectOpener(live.project, None, search=None, search_detail="").register_lakefile is None


def test_arming_publishes_the_guard_before_the_worker_runs(opener):
    """The worker's first statement is still too late for a terminal.

    `_submit_key` resolves an Escape typed behind the Enter in the very same
    input batch, before the thread runs a line -- so a guard the worker creates
    for itself is a second, unmarked one and the switch completes.
    """
    assert opener._opening is None
    armed = opener.arm()
    assert opener._opening is armed
    assert opener.cancel() is True


def test_a_cancel_between_arming_and_the_worker_commits_nothing(opener, live, root):
    """The window this exists to close: cancelled before `__call__` runs at all."""
    previous = opener.cas
    opener.arm()
    opener.cancel()

    with pytest.raises(cli.ReopenCancelled):
        opener("burnside", _decline, live)

    assert previous.session.closed is False
    assert opener.cas is previous
    assert not (root / layout.HARDY_DIR / "config.toml").exists()


def test_a_failed_open_does_not_leave_a_guard_behind(opener, live, monkeypatch):
    """Left set, `cancel` answers True forever -- and `_stop_command` asks the
    opener first, so every later Escape would be swallowed by a reopen that is
    long over instead of interrupting the cell actually running."""

    def explode(*a, **k):
        raise layout.LayoutError("tex/ is a symlink out of the project")

    monkeypatch.setattr(cli, "MathematicsSession", explode)
    with pytest.raises(layout.LayoutError):
        opener("burnside", _decline, live)

    assert opener._opening is None
    assert opener.cancel() is False


def test_a_cancelled_open_does_not_leave_a_guard_behind(opener, live):
    opener.arm()
    opener.cancel()
    with pytest.raises(cli.ReopenCancelled):
        opener("burnside", _decline, live)

    assert opener._opening is None
    assert opener.cancel() is False


def test_a_successful_open_does_not_leave_a_guard_behind(opener, live):
    opener("burnside", _decline, live)
    assert opener._opening is None
    assert opener.cancel() is False


# -- the commit and the cancel cannot both win ---------------------------


def test_a_cancel_after_the_commit_stops_nothing_and_says_so(opener, live, root, monkeypatch):
    """Everything after the commit is irreversible.

    A cancel arriving while the old kernel is closing or `_remember` is
    blocked on I/O used to escalate the kernel of the session about to be
    returned -- while the switch was recorded anyway, and Escape had already
    told the user the project was unchanged.
    """
    answers = []
    previous = opener.cas
    real = opener._remember
    monkeypatch.setattr(
        opener, "_remember", lambda config: answers.append(opener.cancel()) or real(config)
    )

    config, session = opener("burnside", _decline, live)

    assert answers == [False]                      # nothing to stop; the commit had won
    assert config.project == "burnside"
    assert session is not None
    assert opener.cas.session.closed is False      # the returned session's kernel lives
    assert previous.session.closed is True
    assert configuration.read_file(root / layout.HARDY_DIR / "config.toml")["project"] == "burnside"


def test_a_cancel_before_the_commit_still_wins(opener, live, root, monkeypatch):
    """The other side of the same step: the commit refuses once cancelled."""
    previous = opener.cas
    real = cli.MathematicsSession
    monkeypatch.setattr(
        cli, "MathematicsSession", lambda *a, **k: opener.cancel() or real(*a, **k)
    )

    with pytest.raises(cli.ReopenCancelled):
        opener("burnside", _decline, live)

    assert previous.session.closed is False
    assert opener.cas is previous
    assert not (root / layout.HARDY_DIR / "config.toml").exists()


def test_committing_is_what_makes_a_later_cancel_harmless(opener):
    """The unit of the thing, without a session in the way."""
    opening = cli._Reopen()
    opening.commit(None)
    assert opening.cancel() is False
    assert opening.cancelled is False


def test_a_cancelled_reopen_cannot_then_be_committed(opener):
    opening = cli._Reopen()
    assert opening.cancel() is True
    with pytest.raises(cli.ReopenCancelled):
        opening.commit(None)


def test_a_cancel_before_the_worker_starts_leaves_no_scaffold(opener, live, root):
    """`arm` marks it on the loop, so the worker can be cancelled before line one.

    Checking after `prepare_layout` meant `/project new` reported the switch
    cancelled and left the target tree and its `.gitignore` in the checkout
    regardless.
    """
    opener.arm()
    opener.cancel()

    with pytest.raises(cli.ReopenCancelled):
        opener("burnside", _decline, live)

    assert not (root / "burnside").exists()


def test_a_scaffold_left_by_a_cancel_during_preparation_is_still_retryable(opener, live, root):
    """`prepare_layout` is not atomic, so that window is bounded, not closed.

    What makes it survivable is that Hardy recognises its own leftovers: the
    name is not burned by the attempt.
    """
    layout.Layout(root=root, slug="burnside").ensure()
    assert layout.Layout(root=root, slug="burnside").is_bare_scaffold()

    config, session = opener("burnside", _decline, live)

    assert config.project == "burnside"
    assert session is not None
