"""Standalone Python-stdlib replay runner; copied verbatim into result bundles.

This file deliberately imports no Hardy modules. Its adjacent audit.py is the
exact stdlib-only audit parser used when the bundle was created.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath


def digest(data):
    return hashlib.sha256(data).hexdigest()


def guarded(root, relative):
    name = PurePosixPath(relative)
    if name.is_absolute() or not name.parts or any(part in {".", ".."} for part in name.parts):
        raise ValueError("invalid manifest path")
    path = root.joinpath(*name.parts)
    if path.resolve() != path.absolute() or path.is_symlink():
        raise ValueError("source path changed through a link")
    return path


def source_inventory(root, generated_files=()):
    found = {}
    for directory, directories, names in os.walk(root):
        directories[:] = sorted(name for name in directories if name not in {".lake", ".git", "__pycache__"})
        for name in directories:
            if (Path(directory) / name).is_symlink():
                raise ValueError("source directory changed through a link")
        for name in sorted(names):
            if name.endswith(".lean") or name in {"lakefile.toml", "lake-manifest.json", "lean-toolchain"}:
                path = Path(directory) / name
                if path.absolute() in generated_files:
                    continue
                relative = path.relative_to(root).as_posix()
                found[relative] = digest(guarded(root, relative).read_bytes())
    return found


def compiled_inventory(root):
    """Exact cache bytes used by a replay; this does not prove their source origin."""
    found = {}
    library = root / ".lake" / "build" / "lib"
    if not library.is_dir():
        return found
    for directory, directories, names in os.walk(library):
        for name in directories:
            if (Path(directory) / name).is_symlink():
                raise ValueError("compiled dependency directory changed through a link")
        for name in sorted(names):
            if name.endswith((".olean", ".olean.private", ".olean.server", ".ir", ".so", ".dylib", ".dll", ".a")):
                relative = (Path(directory) / name).relative_to(root).as_posix()
                checksum = hashlib.sha256()
                with guarded(root, relative).open("rb") as stream:
                    for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                        checksum.update(block)
                found[relative] = checksum.hexdigest()
    return found


def run(argv, project, *, env=None, timeout=300):
    process = subprocess.run(argv, cwd=project, env=env, capture_output=True, timeout=timeout, check=False)
    if process.returncode != 0:
        detail = (process.stdout + process.stderr).decode("utf-8", errors="replace")
        raise ValueError(f"Lean/Lake failed with exit {process.returncode}: {detail}")
    return (process.stdout + process.stderr).decode("utf-8", errors="strict")


def main():
    parser = argparse.ArgumentParser(description="Rebuild exact selected Lean sources and audit axioms without Hardy or a provider")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--lean", required=True)
    parser.add_argument("--lake", required=True)
    parser.add_argument("--manifest-sha256", help="Optional independently recorded SHA256 for manifest.json")
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parent
    raw = guarded(root, "manifest.json").read_bytes()
    if arguments.manifest_sha256 and digest(raw) != arguments.manifest_sha256:
        raise ValueError("replay manifest digest mismatch")
    manifest = json.loads(raw)
    if manifest.get("schema") != "hardy.standalone-lean-replay/v1":
        raise ValueError("unsupported replay manifest")
    for relative, expected in manifest["files"].items():
        if digest(guarded(root, relative).read_bytes()) != expected:
            raise ValueError(f"bundle source digest mismatch: {relative}")
    # An output nested in its configured project adds copied sources after the
    # environment inventory was measured. Exclude only exact hash-checked
    # generated files, never an entire reports/workspace directory. Additional
    # importable source files remain visible to the inventory comparison.
    generated_files = {guarded(root, relative).absolute() for relative in manifest["files"]}
    outer_path = root.parent / "manifest.json"
    if outer_path.is_file():
        outer = json.loads(guarded(root.parent, "manifest.json").read_bytes())
        if (outer.get("schema") == "hardy.selected-artifact-bundle/v1"
                and outer.get("files", {}).get("replay/manifest.json") == digest(raw)):
            for relative, expected in outer["files"].items():
                path = guarded(root.parent, relative)
                if digest(path.read_bytes()) != expected:
                    raise ValueError(f"bundle provenance digest mismatch: {relative}")
                generated_files.add(path.absolute())
    environment = manifest["environment"]
    project = arguments.project.resolve()
    for relative, expected in environment["metadata"].items():
        if digest(guarded(project, relative).read_bytes()) != expected:
            raise ValueError(f"environment metadata mismatch: {relative}")
    if source_inventory(project, generated_files) != environment["project_sources"]:
        raise ValueError("configured project sources changed")
    if compiled_inventory(project) != environment["project_compiled"]:
        raise ValueError("configured project compiled dependency cache mismatch")
    for package in environment["packages"]:
        path = guarded(project, package["path"])
        if run(["git", "rev-parse", "HEAD"], path, timeout=30).strip() != package["revision"]:
            raise ValueError(f"dependency revision mismatch: {package['name']}@{package['revision']}")
        if source_inventory(path, generated_files) != package["sources"]:
            raise ValueError(f"dependency source mismatch: {package['name']}@{package['revision']}")
        if compiled_inventory(path) != package["compiled"]:
            raise ValueError(f"compiled dependency cache mismatch: {package['name']}@{package['revision']}")
    child_env = {key: value for key, value in os.environ.items()
                 if key not in {"LEAN_PATH", "LEAN_SRC_PATH", "LEAN_SYSROOT", "ELAN_TOOLCHAIN"}}
    # Lake prepends configured package/project paths to inherited LEAN_PATH.
    # Resolve its environment once, then invoke Lean directly so fresh retained
    # modules precede every installed module for BOTH compilation and audit.
    lake_env = json.loads(run([arguments.lake, "env", sys.executable, "-I", "-c",
        "import json, os; print(json.dumps(dict(os.environ)))"], project, env=child_env))
    if not isinstance(lake_env, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in lake_env.items()):
        raise ValueError("Lake environment is unreadable")
    spoken = run([arguments.lean, "--version"], project, env=lake_env)
    version = re.search(r"version ([^\s,)]+)", spoken)
    commit = re.search(r"commit ([0-9a-fA-F]+)", spoken)
    if (version is None or commit is None or version.group(1) != environment["lean_version"]
            or commit.group(1) != environment["lean_commit"]):
        raise ValueError("actual Lean toolchain version/commit mismatch")
    if run([arguments.lake, "--version"], project, env=child_env).strip() != environment["lake_version"]:
        raise ValueError("actual Lake toolchain mismatch")
    spec = importlib.util.spec_from_file_location("bundled_hardy_audit", guarded(root, "audit.py"))
    audit = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = audit
    spec.loader.exec_module(audit)
    with tempfile.TemporaryDirectory(prefix="hardy-replay-") as temporary:
        build = Path(temporary) / "build"
        build.mkdir()
        lake_env["LEAN_PATH"] = os.pathsep.join(value for value in (str(build), lake_env.get("LEAN_PATH", "")) if value)
        for module in manifest["build_order"]:
            relative = module.replace(".", "/")
            source = guarded(root, f"lean/{relative}.lean")
            target = build / f"{relative}.olean"
            target.parent.mkdir(parents=True, exist_ok=True)
            run([arguments.lean, f"--root={root / 'lean'}", "-o", str(target), str(source)], project, env=lake_env)
        probe = Path(temporary) / "FreshAudit.lean"
        probe.write_text(f"import {manifest['module']}\n#print axioms {manifest['declaration']}\n", encoding="utf-8")
        spoken = run([arguments.lean, str(probe)], project, env=lake_env)
        reports = audit.parse(spoken, (manifest["declaration"],))
        if reports is None:
            raise ValueError("fresh axiom audit missing, duplicate, or truncated")
        verdict = audit.classify(reports, manifest["approved_external_axioms"])
        if verdict.status not in {"clean", "modulo"}:
            raise ValueError(f"fresh axiom audit refused: {verdict.status}; forbidden={verdict.forbidden}; unapproved={verdict.unapproved}")
        axioms = reports[0].axioms
        if set(axioms) != set(manifest["recorded_axioms"]):
            raise ValueError("fresh axioms mismatch historical selected artifact")
        print(json.dumps({"schema": "hardy.fresh-replay-result/v1",
            "status": "verified_modulo" if verdict.assumed else "kernel_verified",
            "declaration": manifest["declaration"], "axioms": list(axioms),
            "standard_axioms": sorted(set(axioms) & audit.STANDARD), "external_axioms_used": list(verdict.assumed),
            "allowed_external_axioms_unused": sorted(set(manifest["approved_external_axioms"]) - set(axioms)),
            "historical_build_signature": manifest["historical_build_signature"],
            "historical_evidence_restamped": False, "manifest_sha256": digest(raw), "audit_output": spoken}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"Replay refused: {error}", file=sys.stderr)
        raise SystemExit(1) from None
