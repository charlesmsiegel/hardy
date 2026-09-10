"""Bounds, spill, and the guarantee that every binding shares one runtime."""

from __future__ import annotations

import json

import pytest

from hardy.algebra.cas import CasSession, backend_for
from hardy.algebra.tools import CAS_TOOL_NAMES, CasToolRuntime, build_runtime
from hardy.workflows.contracts import RunLimits


def make_runtime(session, spilled: dict, observation_bytes: int = 32 * 1024) -> CasToolRuntime:
    def spill(name: str, text: str) -> str:
        spilled[name] = text
        return f"process/{name}"

    return CasToolRuntime(
        session=session, observation_bytes=observation_bytes, spill=spill
    )


def test_a_normal_result_is_returned_whole(tmp_path, cas_session) -> None:
    spilled: dict = {}
    runtime = make_runtime(cas_session(), spilled)
    result = runtime.run("a")
    assert result.status == "ok"
    assert result.observation_truncated is False
    assert result.output_artifact is None
    assert spilled == {}


@pytest.mark.parametrize("source", ["warning;", "flood;"])
def test_merged_capture_is_disclosed_in_results_and_human_notes(sentinel_session, source):
    runtime = make_runtime(sentinel_session(), {}, observation_bytes=2048)
    result = runtime.run(source)
    assert result.model_dump()["capture_mode"] == "merged"
    assert "stdout and stderr" in (result.note or "")
    assert "stream origin" in (result.note or "")
    assert result.observation_truncated == (source == "flood;")


def test_an_oversized_answer_is_spilled_and_points_at_the_live_value(tmp_path, cas_session) -> None:
    """The model cannot open files, so the summary has to leave it a way back in."""
    spilled: dict = {}
    runtime = make_runtime(cas_session(), spilled, observation_bytes=2_048)

    result = runtime.run("flood")

    assert result.observation_truncated is True
    assert result.output_artifact == "process/cas-cell-0-0.json"
    assert len(result.stdout) < 400_000
    assert "`_`" in (result.note or "")
    # The whole captured output is on disk even though only a prefix came back.
    assert len(json.loads(spilled["cas-cell-0-0.json"])["stdout"]) > len(result.stdout)


def test_a_truncated_capture_says_so_in_the_note(tmp_path, cas_session) -> None:
    spilled: dict = {}
    runtime = make_runtime(cas_session(cas_output_bytes=4_096), spilled, observation_bytes=2_048)
    result = runtime.run("flood")
    assert result.capture_truncated is True
    assert "missing this cell's tail" in (result.note or "")


def test_state_lists_the_cells_that_built_the_session(tmp_path, cas_session) -> None:
    runtime = make_runtime(cas_session(), {})
    runtime.run("first")
    runtime.run("boom")
    runtime.run("second")
    state = runtime.state()
    assert [line.split("] ")[1] for line in state.accepted] == ["first", "second"]
    assert state.kernel == "live"


def test_state_reports_the_session_spend_and_this_process_guard(tmp_path, cas_session) -> None:
    """`seconds_remaining` was computed from a total that reset on reopen.

    The model was told it had budget the session had already spent. What it
    is told now is what the session has spent, across every process that has
    opened it, and separately what this process will still allow.
    """
    first = make_runtime(cas_session(cas_cell_seconds=30), {})
    first.run("slow")
    first.run("slow")
    first.session.close()

    runtime = make_runtime(cas_session(cas_cell_seconds=30, cas_session_seconds=900), {})
    before = runtime.state()
    assert before.seconds_spent >= 1
    assert before.process_seconds_remaining == 900

    runtime.run("x")
    after = runtime.state()
    assert after.seconds_spent >= 2
    assert after.process_seconds_remaining <= 899


def test_reset_clears_the_state_the_model_can_see(tmp_path, cas_session) -> None:
    runtime = make_runtime(cas_session(), {})
    runtime.run("a")
    assert runtime.state().accepted
    after = runtime.reset()
    assert after.accepted == ()
    assert after.segment == 1


def test_every_binding_dispatches_into_the_same_runtime_and_budget(tmp_path, cas_session) -> None:
    """Chat, staged, and MCP are three doors into one kernel.

    The point of a shared runtime is that a cell costs the same wherever it was
    asked for, so the three dispatchers are driven against one session here.
    """
    from hardy.agents.staged import ClaudeStagedRuntime
    from hardy.workflows.interactive.session import MathematicsSession

    session = cas_session()
    runtime = make_runtime(session, {})

    chat = MathematicsSession.__new__(MathematicsSession)
    chat.cas = runtime
    chat.workspace = tmp_path

    staged = ClaudeStagedRuntime.__new__(ClaudeStagedRuntime)
    staged._cas = runtime
    staged._cas_directory = tmp_path / "cas"

    chat_result = chat._cas_tool("cas_run", {"source": "one"})
    staged_result = staged._cas_dispatch("cas_run", {"source": "two"})
    mcp_result = runtime.run("three")  # what the MCP tool body calls

    assert chat_result.ok and staged_result.ok
    assert json.loads(chat_result.output)["value_repr"] == "1"
    assert json.loads(staged_result.output)["value_repr"] == "2"
    assert mcp_result.value_repr == "3"
    # One log, one kernel, one budget, whichever door was used.
    assert [record.source for record in session.accepted()] == ["one", "two", "three"]


def test_a_model_reset_is_recorded_as_the_models(tmp_path, cas_session) -> None:
    """`cas_reset` is a tool the model can call. A state-destroying model
    action recorded as the human's makes the timeline lie about why earlier
    definitions disappeared."""
    session = cas_session()
    runtime = make_runtime(session, {})
    runtime.run("a")
    runtime.reset()
    boundary = session.records()[-1]
    assert boundary.segment == 1
    assert boundary.author == "model"


def test_reopening_a_workspace_restores_the_state_it_lists(tmp_path) -> None:
    """Discovery must not be mistaken for a rebuilt kernel.

    `build_runtime` probes the backend, which starts a kernel. That left the
    session looking live, so the first cell after reopening a saved workspace
    skipped recovery: the accepted cells were listed back to the user and then
    answered from an empty namespace.
    """
    log = tmp_path / "cells.jsonl"
    first = CasSession(
        backend=backend_for("sympy"),
        command=None,
        log_path=log,
        limits=RunLimits(cas_cell_seconds=60),
        cwd=tmp_path,
    )
    try:
        assert first.execute("x = 41").status == "ok"
        assert first.execute("x + 1").value_repr == "42"
    finally:
        first.close()

    runtime, detail = build_runtime(
        backend_name="sympy",
        command=None,
        limits=RunLimits(cas_cell_seconds=60),
        log_path=log,
        cwd=tmp_path,
    )
    assert runtime is not None, detail
    try:
        assert len(runtime.state().accepted) == 2
        result = runtime.run("x + 1")
        assert result.status == "ok", result.stderr
        assert result.value_repr == "42"
        # Rebuilt, and said so as a reopen rather than as an incident: nothing
        # died here, and "kernel restarted" on the first cell of a session
        # nobody had run yet reads as a fault report.
        assert "reopened" in result.restart_note
        assert "restarted" not in result.restart_note
    finally:
        runtime.session.close()


def test_the_tool_names_are_the_ones_the_bindings_route_on() -> None:
    assert set(CAS_TOOL_NAMES) == {"cas_run", "cas_state", "cas_reset", "cas_export"}


def test_a_truncated_envelope_is_measured_before_it_is_returned(tmp_path, cas_session) -> None:
    """Issue #37: the entry test was against the encoded envelope, but the
    truncated one was sliced by a byte-derived character count and returned
    unmeasured, so multibyte output still walked past the cap on the way out."""
    from hardy.algebra.contracts import CellRecord

    runtime = make_runtime(cas_session(), {}, observation_bytes=2_048)
    record = CellRecord(
        seq=0, segment=0, author="model", source="wide", status="ok", accepted=True,
        stdout="é" * 5_000, stderr="漢" * 5_000, value_repr="ü" * 5_000,
    )
    result = runtime._bound(record)
    assert result.observation_truncated is True
    assert len(result.model_dump_json().encode("utf-8")) <= 2_048
    assert result.stdout, "the cap must leave the model some of the output"


def test_state_is_bounded_by_the_observation_budget(tmp_path, cas_session) -> None:
    """Issue #37: the accepted-cell listing grew with the session and had no
    relationship to `model_observation_bytes`."""
    runtime = make_runtime(cas_session(), {}, observation_bytes=1_024)
    for index in range(40):
        runtime.run(f"cell{index} " + "z" * 60)
    state = runtime.state()
    assert len(state.model_dump_json().encode("utf-8")) <= 1_024
    assert state.omitted > 0
    assert state.omitted + len(state.accepted) == 40
    # The most recent cells are the ones a model is building on.
    assert state.accepted[-1].startswith("[39]")
    assert "omitted" in (state.note or "")


def test_a_small_state_is_listed_whole(tmp_path, cas_session) -> None:
    runtime = make_runtime(cas_session(), {})
    runtime.run("first")
    runtime.run("second")
    state = runtime.state()
    assert state.omitted == 0 and state.note is None
    assert len(state.accepted) == 2


@pytest.mark.parametrize("budget", [1_024, 32_768])
def test_a_long_recovery_warning_is_bounded_without_hiding_the_state_gap(cas_session, budget) -> None:
    from hardy.algebra.contracts import CellRecord

    spilled = {}
    runtime = make_runtime(cas_session(), spilled, observation_bytes=budget)
    warning = (
        f"[cell(s) {list(range(10_000))} failed before being accepted and were not replayed: "
        "anything they changed on the way to failing is not in the rebuilt state.]"
    )
    record = CellRecord(
        seq=10_000, segment=0, author="model", source="pass", status="ok", accepted=True,
        restart_note=warning,
    )
    result = runtime._bound(record)
    assert len(result.model_dump_json().encode("utf-8")) <= budget
    assert result.observation_truncated
    assert "details omitted" in result.restart_note
    assert "state may differ" in result.restart_note
    assert json.loads(next(iter(spilled.values())))["restart_note"] == warning


@pytest.mark.parametrize("operation", ["cell", "state"])
def test_an_observation_budget_that_cannot_hold_the_envelope_is_refused(cas_session, operation) -> None:
    from hardy.algebra.contracts import CasError, CellRecord

    runtime = make_runtime(cas_session(), {}, observation_bytes=1)
    record = CellRecord(seq=0, segment=0, author="model", source="pass", status="ok", accepted=True)
    with pytest.raises(CasError, match="observation budget is too small"):
        if operation == "cell":
            runtime._bound(record)
        else:
            runtime.state()
