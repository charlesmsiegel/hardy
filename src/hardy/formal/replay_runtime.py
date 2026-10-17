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
from collections.abc import Collection
from pathlib import Path, PurePosixPath
from typing import Any

#: The bundle manifest this runner reads, and the selected-artifact bundle a
#: replay directory may sit inside.
REPLAY_SCHEMA = "hardy.standalone-lean-replay/v1"
BUNDLE_SCHEMA = "hardy.selected-artifact-bundle/v1"
#: Read in blocks this size, so a large compiled library is never held whole.
BLOCK_BYTES = 4 * 1024 * 1024
#: Lean's own variables, dropped from the inherited environment so Lake and Lean
#: see only what the pinned project gives them.
LEAN_VARIABLES = frozenset({"LEAN_PATH", "LEAN_SRC_PATH", "LEAN_SYSROOT", "ELAN_TOOLCHAIN"})


def digest(data: bytes) -> str:
    """The SHA-256 every manifest in a bundle records, in hex."""
    return hashlib.sha256(data).hexdigest()


def guarded(root: Path, relative: str) -> Path:
    """`relative` under `root`, refused if it climbs out or passes through a link."""
    name = PurePosixPath(relative)
    if name.is_absolute() or not name.parts or any(part in {".", ".."} for part in name.parts):
        raise ValueError("invalid manifest path")
    path = root.joinpath(*name.parts)
    if path.resolve() != path.absolute() or path.is_symlink():
        raise ValueError("source path changed through a link")
    return path


def source_inventory(root: Path, generated_files: Collection[Path] = ()) -> dict[str, str]:
    """Every Lean source and Lake pin under `root`, by relative path, hashed.

    `generated_files` are this bundle's own hash-checked outputs, left out
    when the bundle sits inside the project it inventories.
    """
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


def compiled_inventory(root: Path) -> dict[str, str]:
    """Exact cache bytes used by a replay; this does not prove their source origin."""
    library = root / ".lake" / "build" / "lib"
    if not library.is_dir():
        return {}
    found = {}
    for directory, directories, names in os.walk(library):
        for name in directories:
            if (Path(directory) / name).is_symlink():
                raise ValueError("compiled dependency directory changed through a link")
        for name in sorted(names):
            if name.endswith((".olean", ".olean.private", ".olean.server", ".ir", ".so", ".dylib", ".dll", ".a")):
                relative = (Path(directory) / name).relative_to(root).as_posix()
                found[relative] = _file_digest(guarded(root, relative))
    return found


def _file_digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(BLOCK_BYTES), b""):
            checksum.update(block)
    return checksum.hexdigest()


def run(argv: list[str], project: Path, *, env: dict[str, str] | None = None, timeout: float = 300) -> str:
    """What a tool printed, or a refusal naming its exit status and output."""
    process = subprocess.run(argv, cwd=project, env=env, capture_output=True, timeout=timeout, check=False)
    if process.returncode != 0:
        detail = (process.stdout + process.stderr).decode("utf-8", errors="replace")
        raise ValueError(f"Lean/Lake failed with exit {process.returncode}: {detail}")
    return (process.stdout + process.stderr).decode("utf-8", errors="strict")


def main() -> None:
    """Check the bundle, the project and the toolchain, rebuild, and audit afresh.

    Every check before the rebuild refuses rather than warns: a replay that
    ran against a changed source, cache or toolchain would print a fresh
    verdict about something other than the recorded artifact.
    """
    parser = argparse.ArgumentParser(description="Rebuild exact selected Lean sources and audit axioms without Hardy or a provider")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--lean", required=True)
    parser.add_argument("--lake", required=True)
    parser.add_argument("--manifest-sha256", help="Optional independently recorded SHA256 for manifest.json")
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parent
    raw, manifest = _checked_manifest(root, arguments.manifest_sha256)
    environment = manifest["environment"]
    project = arguments.project.resolve()
    _check_project(project, environment, _generated_files(root, manifest, raw))
    child_env = {key: value for key, value in os.environ.items() if key not in LEAN_VARIABLES}
    lake_env = _lake_environment(arguments.lake, project, child_env)
    _check_toolchain(arguments, project, environment, child_env, lake_env)
    audit = _bundled_audit(root)
    spoken = _fresh_audit_output(arguments.lean, root, project, manifest, lake_env)
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


def _checked_manifest(root: Path, expected_sha256: str | None) -> tuple[bytes, dict[str, Any]]:
    """The bundle manifest, once it and every file it names hash as recorded."""
    raw = guarded(root, "manifest.json").read_bytes()
    if expected_sha256 and digest(raw) != expected_sha256:
        raise ValueError("replay manifest digest mismatch")
    manifest = json.loads(raw)
    if manifest.get("schema") != REPLAY_SCHEMA:
        raise ValueError("unsupported replay manifest")
    for relative, expected in manifest["files"].items():
        if digest(guarded(root, relative).read_bytes()) != expected:
            raise ValueError(f"bundle source digest mismatch: {relative}")
    return raw, manifest


def _generated_files(root: Path, manifest: dict[str, Any], raw: bytes) -> set[Path]:
    """This bundle's own files, to leave out of a project inventory they sit in.

    An output nested in its configured project adds copied sources after the
    environment inventory was measured. Exclude only exact hash-checked
    generated files, never an entire reports/workspace directory. Additional
    importable source files remain visible to the inventory comparison.
    """
    generated_files = {guarded(root, relative).absolute() for relative in manifest["files"]}
    if not (root.parent / "manifest.json").is_file():
        return generated_files
    outer = json.loads(guarded(root.parent, "manifest.json").read_bytes())
    if outer.get("schema") != BUNDLE_SCHEMA or outer.get("files", {}).get("replay/manifest.json") != digest(raw):
        return generated_files
    for relative, expected in outer["files"].items():
        path = guarded(root.parent, relative)
        if digest(path.read_bytes()) != expected:
            raise ValueError(f"bundle provenance digest mismatch: {relative}")
        generated_files.add(path.absolute())
    return generated_files


def _check_project(project: Path, environment: dict[str, Any], generated_files: set[Path]) -> None:
    """Refuse unless the project's pins, sources, caches and packages are as recorded."""
    for relative, expected in environment["metadata"].items():
        if digest(guarded(project, relative).read_bytes()) != expected:
            raise ValueError(f"environment metadata mismatch: {relative}")
    if source_inventory(project, generated_files) != environment["project_sources"]:
        raise ValueError("configured project sources changed")
    if compiled_inventory(project) != environment["project_compiled"]:
        raise ValueError("configured project compiled dependency cache mismatch")
    for package in environment["packages"]:
        path = guarded(project, package["path"])
        pinned = f"{package['name']}@{package['revision']}"
        if run(["git", "rev-parse", "HEAD"], path, timeout=30).strip() != package["revision"]:
            raise ValueError(f"dependency revision mismatch: {pinned}")
        if source_inventory(path, generated_files) != package["sources"]:
            raise ValueError(f"dependency source mismatch: {pinned}")
        if compiled_inventory(path) != package["compiled"]:
            raise ValueError(f"compiled dependency cache mismatch: {pinned}")


def _lake_environment(lake: str, project: Path, child_env: dict[str, str]) -> dict[str, str]:
    """The environment `lake env` gives a child, read once.

    Lake prepends configured package/project paths to inherited LEAN_PATH.
    Resolve its environment once, then invoke Lean directly so fresh retained
    modules precede every installed module for BOTH compilation and audit.
    """
    lake_env = json.loads(run([lake, "env", sys.executable, "-I", "-c",
        "import json, os; print(json.dumps(dict(os.environ)))"], project, env=child_env))
    if not isinstance(lake_env, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in lake_env.items()):
        raise ValueError("Lake environment is unreadable")
    return lake_env


def _check_toolchain(
    arguments: argparse.Namespace, project: Path, environment: dict[str, Any],
    child_env: dict[str, str], lake_env: dict[str, str],
) -> None:
    """Refuse unless the Lean and Lake given here are the recorded releases."""
    spoken = run([arguments.lean, "--version"], project, env=lake_env)
    version = re.search(r"version ([^\s,)]+)", spoken)
    commit = re.search(r"commit ([0-9a-fA-F]+)", spoken)
    if (version is None or commit is None or version.group(1) != environment["lean_version"]
            or commit.group(1) != environment["lean_commit"]):
        raise ValueError("actual Lean toolchain version/commit mismatch")
    if run([arguments.lake, "--version"], project, env=child_env).strip() != environment["lake_version"]:
        raise ValueError("actual Lake toolchain mismatch")


def _bundled_audit(root: Path) -> Any:
    """The audit parser this bundle was made with, loaded from its own copy."""
    spec = importlib.util.spec_from_file_location("bundled_hardy_audit", guarded(root, "audit.py"))
    audit = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = audit
    spec.loader.exec_module(audit)
    return audit


def _fresh_audit_output(
    lean: str, root: Path, project: Path, manifest: dict[str, Any], lake_env: dict[str, str]
) -> str:
    """Rebuild the retained modules in an empty build and print the selected axioms."""
    with tempfile.TemporaryDirectory(prefix="hardy-replay-") as temporary:
        build = Path(temporary) / "build"
        build.mkdir()
        lake_env["LEAN_PATH"] = os.pathsep.join(value for value in (str(build), lake_env.get("LEAN_PATH", "")) if value)
        for module in manifest["build_order"]:
            relative = module.replace(".", "/")
            source = guarded(root, f"lean/{relative}.lean")
            target = build / f"{relative}.olean"
            target.parent.mkdir(parents=True, exist_ok=True)
            run([lean, f"--root={root / 'lean'}", "-o", str(target), str(source)], project, env=lake_env)
        probe = Path(temporary) / "FreshAudit.lean"
        probe.write_text(f"import {manifest['module']}\n#print axioms {manifest['declaration']}\n", encoding="utf-8")
        return run([lean, str(probe)], project, env=lake_env)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"Replay refused: {error}", file=sys.stderr)
        raise SystemExit(1) from None
