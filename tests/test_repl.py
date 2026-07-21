import asyncio
import sys
from pathlib import Path

import pytest

from hardy.lean.repl import LeanRepl, LeanReplError, ReplDied, ReplTimeout

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


async def test_cumulative_frame_bound_treated_as_protocol_death():
    # Many short lines below the per-line limit must still be bounded in
    # aggregate, or a worker could exhaust host memory before its timeout.
    repl = await make_repl(stream_limit=64 * 1024)
    with pytest.raises(ReplDied):
        await repl.run_command("FLOOD")
    assert not repl.alive
    await repl.close()


async def test_timeout_covers_stdin_drain():
    # A worker that never reads stdin can block drain() on a large request; the
    # per-command timeout must cover the drain phase, not just the read phase.
    argv = [sys.executable, "-c", "import time; time.sleep(3600)"]
    repl = LeanRepl(argv, default_timeout=0.5)
    await repl.start()
    big = "x" * (4 * 1024 * 1024)  # exceed the OS pipe buffer so drain blocks
    with pytest.raises(ReplTimeout):
        await repl.run_command(big)
    assert not repl.alive
    await repl.close()


async def test_default_stream_limit_accepts_large_goals():
    # Default limit (10 MB) must comfortably hold a ~1 MB goal state.
    repl = await make_repl()
    resp = await repl.run_command("HUGE")
    assert len(resp.messages[0].data) == 1 << 20
    await repl.close()


async def test_cancelled_request_kills_process():
    # A cancelled request (e.g. outer wait_for) leaves the command running in
    # the process; the instance must be killed so a stale response can't desync
    # the next request.
    repl = await make_repl(default_timeout=30)
    task = asyncio.create_task(repl.run_command("HANG"))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not repl.alive
    await repl.close()


async def test_double_start_rejected():
    # A second start() on a live instance would orphan the first process.
    repl = await make_repl()
    with pytest.raises(LeanReplError):
        await repl.start()
    assert repl.alive
    await repl.close()


async def test_restart_after_close_rejected():
    # LeanRepl is single-use; restarting after close would leak sandbox cleanup.
    repl = await make_repl()
    await repl.close()
    with pytest.raises(LeanReplError):
        await repl.start()


async def test_explicit_zero_timeout_is_honored():
    # timeout=0.0 (budget exhausted) must not fall back to the default and let
    # the command run for another default window.
    repl = await make_repl(default_timeout=30)
    with pytest.raises(ReplTimeout):
        await repl.run_command("HANG", timeout=0)
    assert not repl.alive
    await repl.close()


async def test_close_bounds_a_hanging_cleanup():
    # A cleanup helper that never exits must not make close() (hence the
    # per-command timeout that calls it) hang forever.
    cleanup = [sys.executable, "-c", "import time; time.sleep(3600)"]
    repl = LeanRepl(FAKE, cleanup_argv=cleanup, cleanup_timeout=0.5)
    await repl.start()
    await asyncio.wait_for(repl.close(), timeout=5)


async def test_cancelled_close_retains_cleanup_for_retry():
    # If close() is cancelled mid-cleanup, the cleanup command must be retained
    # so a later close() can retry it (else a sandbox container would leak).
    cleanup = [sys.executable, "-c", "import time; time.sleep(1)"]
    repl = LeanRepl(FAKE, cleanup_argv=cleanup, cleanup_timeout=30)
    await repl.start()
    task = asyncio.create_task(repl.close())
    await asyncio.sleep(0.2)  # close() has killed the repl and is awaiting cleanup
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert repl._cleanup_argv is not None  # retained, not consumed
    repl._cleanup_argv = None  # avoid the 1s sleep on the final close
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
