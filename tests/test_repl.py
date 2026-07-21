import sys
from pathlib import Path

import pytest

from hardy.lean.repl import LeanRepl, ReplDied, ReplTimeout

FAKE = [sys.executable, str(Path(__file__).parent / "fake_repl.py")]


async def make_repl(**kwargs) -> LeanRepl:
    repl = LeanRepl(FAKE, **kwargs)
    await repl.start()
    return repl


async def test_run_command_round_trip():
    repl = await make_repl()
    resp = await repl.run_command("theorem t : True := trivial")
    assert resp.env == 0
    resp2 = await repl.run_command("theorem u : True := trivial")
    assert resp2.env == 1  # fake increments per command
    await repl.close()


async def test_error_messages_parsed():
    repl = await make_repl()
    resp = await repl.run_command("ERROR")
    assert resp.messages[0].severity == "error"
    assert resp.messages[0].pos.line == 1
    await repl.close()


async def test_sorries_parsed():
    repl = await make_repl()
    resp = await repl.run_command("theorem t : True := by sorry")
    assert resp.sorries[0].goal == "⊢ True"
    assert resp.sorries[0].proof_state == 0
    await repl.close()


async def test_env_field_forwarded():
    repl = await make_repl()
    resp = await repl.run_command("SHOW_ENV", env=0)
    assert resp.messages[0].data == "env=0"
    await repl.close()


async def test_run_tactic():
    repl = await make_repl()
    resp = await repl.run_tactic("intro h", proof_state=0)
    assert resp.proof_state == 1
    assert resp.goals == []
    await repl.close()


async def test_timeout_kills_worker():
    repl = await make_repl(default_timeout=0.5)
    with pytest.raises(ReplTimeout):
        await repl.run_command("HANG")
    assert not repl.alive  # dirty worker must be dead, not reusable
    await repl.close()


async def test_dead_process_raises_and_is_not_alive():
    repl = await make_repl()
    with pytest.raises(ReplDied):
        await repl.run_command("DIE")
    assert not repl.alive
    await repl.close()


async def test_malformed_json_treated_as_protocol_death():
    repl = await make_repl()
    with pytest.raises(ReplDied):
        await repl.run_command("BADJSON")
    assert not repl.alive  # unparseable protocol state = dirty worker
    await repl.close()


async def test_schema_invalid_response_treated_as_protocol_death():
    # Valid JSON that fails pydantic validation (e.g. a severity value from
    # a newer repl) must also kill the worker, not leak a ValidationError.
    repl = await make_repl()
    with pytest.raises(ReplDied):
        await repl.run_command("BADSCHEMA")
    assert not repl.alive
    await repl.close()


async def test_oversized_frame_treated_as_protocol_death():
    # A frame bigger than the stream limit must recycle the worker, not
    # leak an unhandled ValueError past the pool's error handling.
    repl = await make_repl(stream_limit=64 * 1024)
    with pytest.raises(ReplDied):
        await repl.run_command("HUGE")
    assert not repl.alive
    await repl.close()


async def test_default_stream_limit_accepts_large_goals():
    # Default limit (10 MB) must comfortably hold a ~1 MB goal state.
    repl = await make_repl()
    resp = await repl.run_command("HUGE")
    assert len(resp.messages[0].data) == 1 << 20
    await repl.close()


async def test_cleanup_argv_runs_on_close(tmp_path):
    marker = tmp_path / "marker"
    cleanup = [
        sys.executable,
        "-c",
        f"import pathlib; pathlib.Path({str(marker)!r}).touch()",
    ]
    repl = LeanRepl(FAKE, cleanup_argv=cleanup)
    await repl.start()
    await repl.close()
    assert marker.exists()
