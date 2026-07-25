"""Bounds, spill, and the guarantee that every binding shares one runtime."""

from __future__ import annotations

import json

from hardy.cas import CasSession, backend_for
from hardy.cas_tools import CAS_TOOL_NAMES, CasToolRuntime, build_runtime
from hardy.domain import RunLimits


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
    from hardy.chat import MathematicsSession
    from hardy.staged import ClaudeStagedRuntime

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
    finally:
        runtime.session.close()


def test_the_tool_names_are_the_ones_the_bindings_route_on() -> None:
    assert set(CAS_TOOL_NAMES) == {"cas_run", "cas_state", "cas_reset", "cas_export"}
