"""A replay runs without Hardy and refuses incomplete or changed evidence."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def executable(path, source):
    path.write_text("#!/usr/bin/env python3\n" + source)
    path.chmod(0o755)
    return str(path)


def fixture_bundle(tmp_path, *, report="'selected' does not depend on any axioms", axioms=(), approved=(), shadow=False):
    from hardy.formal.replay import replay_payload, snapshot_environment
    project = tmp_path / "project"
    project.mkdir()
    (project / "lake-manifest.json").write_text('{"packages": []}\n')
    (project / "lean-toolchain").write_text("leanprover/lean4:v4.35.0-rc3\n")
    compiled = project / ".lake" / "build" / "lib" / "lean"
    compiled.mkdir(parents=True)
    (compiled / "Cached.olean").write_bytes(b"exact dependency cache fixture")
    lean = executable(tmp_path / "lean", """import os, pathlib, sys
if '--version' in sys.argv:
    print('Lean (version 4.35.0-rc3, x86_64, commit 123abc, Release)')
elif '-o' in sys.argv:
    pathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_bytes(b'fresh compiled fixture')
else:
    print("'selected' depends on axioms: [sorryAx]" if SHADOW and os.environ.get('LEAN_PATH', '').split(os.pathsep)[0] == 'HOSTILE' else REPORT)
""".replace("REPORT", repr(report)).replace("SHADOW", repr(shadow)))
    lake = executable(tmp_path / "lake", """import os, sys
if sys.argv[1:] == ['--version']:
    print('Lake version 5.0.0 (Lean version 4.35.0-rc3)')
else:
    if SHADOW:
        os.environ['LEAN_PATH'] = 'HOSTILE' + os.pathsep + os.environ.get('LEAN_PATH', '')
    os.execv(sys.argv[2], sys.argv[2:])
""".replace("SHADOW", repr(shadow)))
    environment = snapshot_environment(project, (lake, "env", lean))
    payload = replay_payload(module="Main", declaration="selected", sources={"Main": "theorem selected : True := True.intro\n"},
        environment=environment, axioms=axioms, approved=approved, signature="original-build-signature")
    bundle = tmp_path / "bundle"
    for name, data in payload.items():
        destination = bundle / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    return bundle / "replay", project, lean, lake


def run_replay(replay, project, lean, lake):
    # Running a script under -I from a foreign directory excludes the checkout.
    return subprocess.run([sys.executable, "-I", str(replay / "replay.py"), "--project", str(project),
        "--lean", lean, "--lake", lake], cwd=project, env={**os.environ, "PYTHONPATH": ""},
        capture_output=True, text=True, check=False)


def test_standalone_replay_keeps_original_signature_and_requires_fresh_audit(tmp_path):
    replay, project, lean, lake = fixture_bundle(tmp_path)
    result = run_replay(replay, project, lean, lake)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["status"] == "kernel_verified"
    assert receipt["axioms"] == []
    assert receipt["historical_build_signature"] == "original-build-signature"
    assert receipt["historical_evidence_restamped"] is False


@pytest.mark.parametrize("report", ["", "'selected' depends on axioms: [sorryAx]", "'selected' depends on axioms: [propext",
    "'selected' does not depend on any axioms\n'selected' does not depend on any axioms"])
def test_standalone_replay_refuses_missing_duplicate_truncated_or_hole_audit(tmp_path, report):
    replay, project, lean, lake = fixture_bundle(tmp_path, report=report)
    result = run_replay(replay, project, lean, lake)
    assert result.returncode != 0


@pytest.mark.parametrize("changed", ["source", "toolchain", "manifest", "extra_source", "compiled_cache"])
def test_standalone_replay_refuses_tampering_or_environment_drift(tmp_path, changed):
    replay, project, lean, lake = fixture_bundle(tmp_path)
    if changed == "source":
        (replay / "lean" / "Main.lean").write_text("theorem selected : False := by sorry\n")
    elif changed == "toolchain":
        path = Path(lean)
        path.write_text(path.read_text(encoding="utf-8").replace("123abc", "badbad"), encoding="utf-8")
    elif changed == "manifest":
        (project / "lake-manifest.json").write_text('{"packages": [], "changed": true}')
    elif changed == "extra_source":
        (project / "Unexpected.lean").write_text("axiom injected : False\n")
    else:
        (project / ".lake" / "build" / "lib" / "lean" / "Cached.olean").write_bytes(b"cache from a different source checkout")
    result = run_replay(replay, project, lean, lake)
    assert result.returncode != 0
    assert "mismatch" in result.stderr.lower() or "changed" in result.stderr.lower()


def test_environment_capture_refuses_modified_dependency_even_with_pinned_head(tmp_path):
    from hardy.formal.replay import snapshot_environment
    _replay, project, lean, lake = fixture_bundle(tmp_path)
    package = project / ".lake" / "packages" / "fixture"
    package.mkdir(parents=True)
    (package / "Fixture.lean").write_text("theorem fixture : True := True.intro\n")
    def git(*args):
        return subprocess.run(["git", *args], cwd=package, check=True, capture_output=True, text=True).stdout.strip()
    git("init", "-q")
    git("add", "Fixture.lean")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")
    revision = git("rev-parse", "HEAD")
    (project / "lake-manifest.json").write_text(json.dumps({"packages": [
        {"type": "git", "name": "fixture", "rev": revision, "url": "https://example.invalid/fixture"}]}))
    (package / "Fixture.lean").write_text("axiom fixture : False\n")
    with pytest.raises(ValueError, match="modified|dirty|changed"):
        snapshot_environment(project, (lake, "env", lean))


def test_replay_discloses_actual_external_axioms_separately_from_unused_allowances(tmp_path):
    replay, project, lean, lake = fixture_bundle(tmp_path, report="'selected' depends on axioms: [external_used, propext]",
        axioms=("external_used", "propext"), approved=("external_used", "external_unused"))
    result = run_replay(replay, project, lean, lake)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["status"] == "verified_modulo"
    assert receipt["standard_axioms"] == ["propext"]
    assert receipt["external_axioms_used"] == ["external_used"]
    assert receipt["allowed_external_axioms_unused"] == ["external_unused"]


def test_replay_refuses_external_module_without_pinned_source_provenance(tmp_path):
    from hardy.formal.replay import replay_payload
    replay, _project, _lean, _lake = fixture_bundle(tmp_path)
    environment = json.loads((replay / "manifest.json").read_text())["environment"]
    with pytest.raises(ValueError, match="source.*Ghost|Ghost.*source"):
        replay_payload(module="Main", declaration="selected", sources={"Main": "import Ghost\ntheorem selected : True := True.intro\n"},
            environment=environment, axioms=(), approved=(), signature="original")


def test_fresh_replay_modules_precede_paths_lake_prepends(tmp_path):
    replay, project, lean, lake = fixture_bundle(tmp_path, shadow=True)
    result = run_replay(replay, project, lean, lake)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["axioms"] == []


def test_replay_rechecks_package_git_revision_with_identical_source_bytes(tmp_path):
    from hardy.formal.replay import replay_payload, snapshot_environment
    replay, project, lean, lake = fixture_bundle(tmp_path)
    package = project / ".lake" / "packages" / "fixture"
    package.mkdir(parents=True)
    (package / "Fixture.lean").write_text("theorem fixture : True := True.intro\n")
    def git(*args):
        return subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *args],
            cwd=package, check=True, capture_output=True, text=True).stdout.strip()
    git("init", "-q")
    git("add", "Fixture.lean")
    git("commit", "-qm", "pinned")
    revision = git("rev-parse", "HEAD")
    (project / "lake-manifest.json").write_text(json.dumps({"packages": [
        {"type": "git", "name": "fixture", "rev": revision, "url": "https://example.invalid/fixture"}]}))
    environment = snapshot_environment(project, (lake, "env", lean))
    payload = replay_payload(module="Main", declaration="selected", sources={"Main": "theorem selected : True := True.intro\n"},
        environment=environment, axioms=(), approved=(), signature="original")
    for name, data in payload.items():
        path = replay.parent / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    assert run_replay(replay, project, lean, lake).returncode == 0
    git("commit", "--allow-empty", "-qm", "different identity, identical sources")
    result = run_replay(replay, project, lean, lake)
    assert result.returncode != 0
    assert "revision mismatch" in result.stderr


def test_nested_bundle_excludes_only_its_exact_hash_checked_generated_files(tmp_path):
    import hashlib
    import shutil
    replay, project, lean, lake = fixture_bundle(tmp_path)
    nested = project / "nested-workspace" / "reports" / "selected.bundle"
    nested.parent.mkdir(parents=True)
    shutil.move(str(replay.parent), nested)
    provenance = nested / "provenance" / "workflow-artifacts" / "source.lean"
    provenance.parent.mkdir(parents=True)
    provenance.write_text("theorem selected : True := True.intro\n")
    files = {path.relative_to(nested).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in nested.rglob("*") if path.is_file()}
    (nested / "manifest.json").write_text(json.dumps({"schema": "hardy.selected-artifact-bundle/v1", "files": files}))
    replay = nested / "replay"
    result = run_replay(replay, project, lean, lake)
    assert result.returncode == 0, result.stderr
    (nested / "Unrecorded.lean").write_text("axiom injected : False\n")
    result = run_replay(replay, project, lean, lake)
    assert result.returncode != 0
    assert "sources changed" in result.stderr
