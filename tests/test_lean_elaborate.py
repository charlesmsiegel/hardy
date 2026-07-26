from __future__ import annotations

from pathlib import Path

from hardy.lean import LeanTools, elaborate
from hardy.models import Request
from hardy.process import ProcessResult, ProcessSpec


def recorder(seen: list[ProcessSpec]):
    def run(spec: ProcessSpec) -> ProcessResult:
        seen.append(spec)
        return ProcessResult(
            argv=spec.argv,
            cwd=spec.cwd,
            returncode=0,
            stdout="",
            stderr="",
            duration_ms=1,
            timed_out=False,
            output_overflow=False,
        )

    return run


def test_elaborate_passes_the_environment_through(tmp_path: Path):
    seen: list[ProcessSpec] = []
    elaborate(
        "def a := 1\n",
        argv=("lean",),
        cwd=tmp_path,
        timeout_seconds=5,
        env={"LEAN_PATH": "X"},
        runner=recorder(seen),
    )
    assert seen[0].env == {"LEAN_PATH": "X"}


def test_elaborate_uses_a_given_source_path_rather_than_a_copy(tmp_path: Path):
    seen: list[ProcessSpec] = []
    target = tmp_path / "Group" / "Sylow.lean"
    target.parent.mkdir(parents=True)
    target.write_text("def a := 1\n", encoding="utf-8")
    elaborate(
        "def a := 1\n",
        argv=("lean",),
        cwd=tmp_path,
        timeout_seconds=5,
        source_path=target,
        runner=recorder(seen),
    )
    assert seen[0].argv[-1] == str(target)


def test_elaborate_still_uses_a_temporary_file_by_default(tmp_path: Path):
    seen: list[ProcessSpec] = []
    elaborate("def a := 1\n", argv=("lean",), cwd=tmp_path, timeout_seconds=5, runner=recorder(seen))
    assert seen[0].argv[-1].endswith("Main.lean")
    assert not seen[0].argv[-1].startswith(str(tmp_path))
    assert seen[0].env == {}


def test_compile_module_names_the_root_and_the_output(tmp_path: Path):
    seen: list[ProcessSpec] = []
    root = tmp_path / "lean"
    build = tmp_path / "build"
    source = root / "Group" / "Sylow.lean"
    source.parent.mkdir(parents=True)
    source.write_text("def a := 1\n", encoding="utf-8")
    tools = LeanTools(
        Request("example : True", "workspace", ()),
        ("lake", "env", "lean"),
        project=tmp_path,
        runner=recorder(seen),
    )
    tools.compile_module(root, build, source)
    spec = seen[0]
    assert f"--root={root}" in spec.argv
    assert "-o" in spec.argv
    assert spec.argv[spec.argv.index("-o") + 1] == str(build / "Group" / "Sylow.olean")
    assert spec.argv[-1] == str(source)
    assert spec.env == {"LEAN_PATH": str(build)}
    # Lean does not create output directories; the parent must exist first.
    assert (build / "Group").is_dir()
