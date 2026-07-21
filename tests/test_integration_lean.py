"""Integration tests against the real Lean toolchain.

Prereq: scripts/setup_lean.sh has completed. Run with: pytest -m lean
"""

import json
import subprocess

import pytest

from hardy.lean.launch import LEAN_PROJECT, REPL_BIN, repl_argv

pytestmark = pytest.mark.lean


def test_repl_smoke():
    assert REPL_BIN.exists(), "run scripts/setup_lean.sh first"
    proc = subprocess.run(
        repl_argv(),
        cwd=LEAN_PROJECT,
        input='{"cmd": "theorem t : 2 + 2 = 4 := rfl"}\n\n',
        capture_output=True,
        text=True,
        timeout=300,
    )
    first = json.loads(proc.stdout.split("\n\n")[0])
    assert first.get("env") == 0
    assert not first.get("messages")


async def test_lean_repl_wrapper_checks_a_proof():
    from hardy.lean.repl import LeanRepl

    repl = LeanRepl(repl_argv(), cwd=LEAN_PROJECT, default_timeout=300)
    await repl.start()
    try:
        resp = await repl.run_command("theorem t : 2 + 2 = 4 := rfl")
        assert resp.env is not None
        assert not resp.messages
        bad = await repl.run_command("theorem u : 2 + 2 = 5 := rfl")
        assert any(m.severity == "error" for m in bad.messages)
    finally:
        await repl.close()


async def test_declarations_do_not_leak_between_runs():
    from hardy.lean.pool import ReplPool

    pool = ReplPool(
        size=1,
        argv=repl_argv(),
        cwd=LEAN_PROJECT,
        imports="import Mathlib.Tactic",  # lighter than full Mathlib; enough here
        command_timeout=120,
    )
    await pool.start()
    try:
        v1 = await pool.check_proof("def leaky : Nat := 7\ntheorem t1 : leaky = 7 := rfl")
        assert v1.complete
        # `leaky` must be unknown in a fresh check — same worker, pristine env.
        v2 = await pool.check_proof("theorem t2 : leaky = 7 := rfl")
        assert not v2.complete
        assert any("leaky" in e.data for e in v2.errors)
    finally:
        await pool.close()
