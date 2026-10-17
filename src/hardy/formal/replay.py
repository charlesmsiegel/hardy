"""Pinned source/environment projections for standalone selected-artifact replay."""
from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from hardy.formal import audit, replay_runtime
from hardy.formal.lean import lean_identity
from hardy.formal.syntax import build_order, module_path, parse_imports, safe_relative
from hardy.foundation.files import read_bytes

#: The project files whose bytes pin a Lake environment, in the order recorded.
METADATA_FILES = ("lake-manifest.json", "lean-toolchain", "lakefile.toml", "lakefile.lean")
#: Module roots the pinned Lean release ships, rather than any Lake package.
DISTRIBUTION_ROOTS = frozenset({"Init", "Lean", "Std", "Lake"})
#: How long one `lake --version` or `git` question may take.
COMMAND_SECONDS = 30


def _json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def capture_replay_environment(project: Path | None, lean_command: tuple[str, ...]) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Small native-save snapshot; full dependency/cache hashing stays at export."""
    if project is None:
        raise ValueError("Native replay has no configured pinned Lake project")
    version, commit = lean_identity(lean_command, project)
    metadata = {name: read_bytes(project, name) for name in METADATA_FILES if (project / name).is_file()}
    if not {"lake-manifest.json", "lean-toolchain"}.issubset(metadata):
        raise ValueError("Native replay requires retained Lake manifest and lean-toolchain")
    manifest = json.loads(metadata["lake-manifest.json"])
    packages = manifest.get("packages")
    if not isinstance(packages, list) or any(not isinstance(value, dict) for value in packages):
        raise ValueError("Native replay manifest has no exact packages list")
    revision = next((value.get("rev") for value in packages if value.get("name") == "mathlib"), None)
    return {"lean_version": version, "lean_commit": commit, "mathlib_revision": revision,
        "lake_manifest_sha256": hashlib.sha256(metadata["lake-manifest.json"]).hexdigest()}, metadata


def snapshot_environment(project: Path, lean_command: tuple[str, ...], *, expected: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Interrogate real commands and pin exact source bytes; never invent an identity."""
    if len(lean_command) != 3 or lean_command[1] != "env":
        raise ValueError("Standalone replay requires explicit Lake env Lean commands")
    version, commit = lean_identity(lean_command, project)
    raw = read_bytes(project, "lake-manifest.json")
    manifest = json.loads(raw)
    packages = manifest.get("packages")
    if not isinstance(packages, list):
        raise ValueError("Pinned Lake manifest has no packages list")
    metadata = {name: hashlib.sha256(read_bytes(project, name)).hexdigest()
                for name in METADATA_FILES if (project / name).is_file()}
    if "lean-toolchain" not in metadata:
        raise ValueError("Pinned lean-toolchain is unavailable")
    lake = subprocess.run([lean_command[0], "--version"], cwd=project, capture_output=True, timeout=COMMAND_SECONDS, check=False)
    if lake.returncode or not lake.stdout.strip():
        raise ValueError("Actual Lake version is unavailable")
    packages_dir = str(manifest.get("packagesDir", ".lake/packages"))
    result: dict[str, Any] = {"lean_version": version, "lean_commit": commit,
        "lake_version": lake.stdout.decode().strip(), "metadata": metadata,
        "metadata_bytes": {name: read_bytes(project, name).decode() for name in metadata},
        "project_sources": replay_runtime.source_inventory(project), "project_compiled": replay_runtime.compiled_inventory(project),
        "compiled_cache_captured_at": "export", "compiled_cache_source_derivation": "not_independently_established",
        "packages": [_pinned_package(project, packages_dir, package) for package in packages]}
    mathlib = next((value["revision"] for value in result["packages"] if value["name"] == "mathlib"), None)
    result["mathlib_revision"] = mathlib
    if expected is not None and (version != expected["lean_version"] or commit != expected["lean_commit"]
            or hashlib.sha256(raw).hexdigest() != expected["lake_manifest_sha256"]
            or mathlib != expected["mathlib_revision"]):
        raise ValueError("Current environment mismatch historical formal evidence")
    return result


def _pinned_package(project: Path, packages_dir: str, package: object) -> dict[str, Any]:
    """One manifest dependency, checked out at its pinned revision with no local edits."""
    if (not isinstance(package, dict) or package.get("type") != "git" or not package.get("rev")
            or not package.get("url") or not package.get("name")):
        raise ValueError("Replay requires exact pinned git dependencies; local/path dependencies must be bundled explicitly")
    relative = packages_dir + "/" + package["name"]
    root = replay_runtime.guarded(project, relative)
    if not root.is_dir():
        raise ValueError(f"Pinned dependency sources unavailable: {package['name']}")
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, timeout=COMMAND_SECONDS, check=False)
    if revision.returncode or revision.stdout.decode().strip() != package["rev"]:
        raise ValueError(f"Actual dependency revision mismatch: {package['name']}")
    dirty = subprocess.run(["git", "diff", "--quiet", "HEAD", "--"], cwd=root, timeout=COMMAND_SECONDS, check=False)
    untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=root,
        capture_output=True, timeout=COMMAND_SECONDS, check=False)
    if dirty.returncode or untracked.returncode or any(name.endswith(b".lean") for name in untracked.stdout.split(b"\0")):
        raise ValueError(f"Pinned dependency sources modified: {package['name']}")
    return {"name": package["name"], "path": relative, "revision": package["rev"],
        "url": package["url"], "sources": replay_runtime.source_inventory(root), "compiled": replay_runtime.compiled_inventory(root)}


def replay_payload(*, module: str, declaration: str, sources: Mapping[str, str], environment: Mapping[str, Any],
                   axioms: Sequence[str], approved: Sequence[str], signature: str | None) -> dict[str, bytes]:
    """Exact local import closure, audited historical assumptions and executable assets."""
    safe_relative(str(module_path(module)))
    if not declaration or any(value in declaration for value in ("\n", "\r", " ", ";")):
        raise ValueError("Replay declaration must be one exact Lean name")
    verdict = audit.classify((audit.AxiomReport(declaration, tuple(axioms)),), approved)
    if verdict.status not in (audit.AuditStatus.CLEAN, audit.AuditStatus.MODULO):
        raise ValueError("Selected artifact has forbidden or unapproved axioms")
    order = build_order(sources, (module,))
    _check_external_provenance(order, sources, environment)
    payload = {f"replay/lean/{module_path(name).as_posix()}": sources[name].encode() for name in order}
    payload["replay/replay.py"] = Path(replay_runtime.__file__).read_bytes()
    payload["replay/audit.py"] = Path(audit.__file__).read_bytes()
    environment = dict(environment)
    metadata = environment.pop("metadata_bytes", {})
    for name, content in metadata.items():
        payload[f"replay/environment/{name}"] = content.encode()
    manifest = {"schema": replay_runtime.REPLAY_SCHEMA, "module": module, "declaration": declaration,
        "build_order": order, "recorded_axioms": list(axioms), "approved_external_axioms": sorted(set(approved) - audit.STANDARD),
        "historical_build_signature": signature, "environment": environment,
        "files": {name.removeprefix("replay/"): hashlib.sha256(data).hexdigest() for name, data in payload.items()}}
    payload["replay/manifest.json"] = _json(manifest)
    manifest_sha = hashlib.sha256(payload["replay/manifest.json"]).hexdigest()
    payload["replay/README.txt"] = _readme(environment, manifest_sha).encode()
    return payload


def _check_external_provenance(order: Sequence[str], sources: Mapping[str, str], environment: Mapping[str, Any]) -> None:
    """Refuse a closure importing a module no pinned inventory holds the source of."""
    external = {name for selected in order for name in parse_imports(sources[selected])} - set(sources)
    inventories = [environment["project_sources"], *(value["sources"] for value in environment["packages"])]
    for imported in sorted(external):
        if imported.split(".", 1)[0] in DISTRIBUTION_ROOTS:
            continue  # Pinned Lean distribution, rather than a Lake package.
        if not any(module_path(imported).as_posix() in inventory for inventory in inventories):
            raise ValueError(f"External module source provenance unavailable: {imported}")


def _readme(environment: Mapping[str, Any], manifest_sha: str) -> str:
    """How to set up and run the replay, and what its answer does and does not establish."""
    setup = ["Selected Lean artifact replay", "", "No Hardy installation or provider credentials are required.",
        "Use Python 3, the recorded Lean/Lake release and an independently prepared pinned Lake project.",
        f"Lean version: {environment['lean_version']}; commit: {environment['lean_commit']}.",
        "Copy environment/lean-toolchain, lake-manifest.json and lakefile into that project preserving their bytes.",
        "Dependencies are not bundled. Fetch each exact URL/revision below into its recorded path, then build with the pinned Lake/Lean.",
        "The original configured project's root source tree is also hash-pinned in manifest.json environment.project_sources.",
        "Custom root .lean files outside the selected retained import closure are not bundled: obtain their exact original bytes separately at those paths.",
        "If that root source material is unavailable, this bundle alone cannot reconstruct that configured project; the runner refuses incomplete or changed inventories.",
        "The runner verifies actual package git revisions, source inventories, metadata and tool versions before rebuilding local inputs in an empty temporary build.",
        "Only this bundle's individually hash-checked generated files are excluded when its output lies inside the configured project.", ""]
    for package in environment["packages"]:
        setup.extend((f"{package['name']}: git clone {shlex.quote(package['url'])} {shlex.quote(package['path'])}",
            f"git -C {shlex.quote(package['path'])} checkout --detach {shlex.quote(package['revision'])}"))
    setup.extend(("", "After dependency setup (Mathlib's pinned cache may be fetched with lake exe cache get):",
        f"python3 -I replay.py --project /absolute/pinned-project --lake /absolute/toolchain/bin/lake --lean /absolute/toolchain/bin/lean --manifest-sha256 {manifest_sha}",
        "", "The final JSON is a fresh axiom audit. Exit zero alone never establishes verification.",
        "Historical build signatures remain unchanged. Source hashes provide integrity, not an independent signature of authorship.",
        "Dependency compiled libraries are hash-pinned at export and must match exactly at replay. Their derivation from source is not independently established by these hashes.",
        "Fetch the pinned platform's canonical dependency cache or reproduce those exact compiled bytes. A different locally rebuilt cache is refused even when its source revision matches.",
        "A clean dependency rebuild establishes a new environment; regenerate the replay environment manifest explicitly rather than changing this bundle or restamping historical evidence.",
        "Lean code executes in the configured environment. The audit has Hardy's documented trust boundary and is not an independent kernel implementation.", ""))
    return "\n".join(setup)
