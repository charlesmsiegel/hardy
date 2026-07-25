"""The human's own way into the kernel, and the config that finds it."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hardy import config as configuration
from hardy.cas_tools import CasToolRuntime
from hardy.cli import _read_block, cas_command


def stub(runtime, tmp_path):
    return SimpleNamespace(cas=runtime, workspace=tmp_path)


def scripted(lines):
    supply = iter(lines)
    return lambda _prompt: next(supply)


def test_a_block_keeps_its_indentation(tmp_path) -> None:
    """The chat loop strips input. A cell must not be read that way.

    Stripping a Python cell silently changes what the user wrote, and the
    failure surfaces much later as a syntax error they did not make.
    """
    source = _read_block(scripted(["for i in range(3):", "    print(i)", "/end"]))
    assert source == "for i in range(3):\n    print(i)"


def test_a_block_ended_by_eof_yields_nothing(tmp_path) -> None:
    def ask(_prompt):
        raise EOFError

    assert _read_block(ask) == ""


def test_a_human_cell_lands_in_the_same_log_as_the_models(tmp_path, cas_session) -> None:
    session = cas_session()
    runtime = CasToolRuntime(session=session, observation_bytes=32 * 1024)
    printed: list[str] = []

    cas_command("mine", stub(runtime, tmp_path), out=printed.append)

    record = session.accepted()[0]
    assert record.author == "human"
    assert record.source == "mine"
    assert "1" in printed


def test_cas_commands_report_state_and_reset(tmp_path, cas_session) -> None:
    session = cas_session()
    runtime = CasToolRuntime(session=session, observation_bytes=32 * 1024)
    target = stub(runtime, tmp_path)
    printed: list[str] = []

    cas_command("a", target, out=printed.append)
    cas_command("state", target, out=printed.append)
    assert any("segment 0" in line for line in printed)

    cas_command("reset", target, out=printed.append)
    assert session.segment == 1
    # The human asked, so the human is on the boundary record. The default is
    # the model's, since `cas_reset` is a tool the model calls.
    assert session.records()[-1].author == "human"


def test_without_a_backend_the_escape_says_so_rather_than_failing(tmp_path) -> None:
    printed: list[str] = []
    cas_command("anything", stub(None, tmp_path), out=printed.append)
    assert "doctor" in printed[0]


def test_an_unknown_backend_is_rejected_when_the_config_is_read(tmp_path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('cas_backend = "mathematica"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="cas_backend must be one of"):
        configuration.load(path)


def test_the_default_backend_needs_no_configuration(tmp_path) -> None:
    config = configuration.load(tmp_path / "absent.toml")
    assert config.cas_backend == "sympy"
    assert config.cas_command is None
