"""Import pinned benchmark source bytes without turning them into Hardy claims.

Original repositories are the authority for statements and their context. The
manifest indexes lexical spans or explicitly decoded JSON values; it never ports
Lean, inlines answers, normalizes statements, or certifies a runnable environment.
Archive extraction owns resource/path bounds. New upstream formats belong in
explicit profiles, not guesses based on whichever files happen to parse.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import tomllib
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from hardy.formal import syntax
from hardy.foundation.locking import FileLock, LockTimeout
from hardy.literature import archives

MAX_ARCHIVE_BYTES = 64 << 20
MAX_STATEMENTS = 10_000
MAX_INDEX_BYTES = 8 << 20
# Original upstream archives also contain large training assets. Store those
# under archive quotas; only selected benchmark inputs enter the smaller index.
LIMITS = archives.Limits(max_files=6000, max_file_bytes=64 << 20, max_total_bytes=128 << 20)


@dataclass(frozen=True)
class Profile:
    repository: str
    license_path: str
    declared_license: str
    lean_major: int
    metadata: tuple[str, ...]
    inputs: tuple[str, ...]
    support: tuple[str, ...] = ()


# These identify formats, not moving releases. Every call pins its own commit
# and archive digest. The original Lean3 datasets are deliberately not ports.
PROFILES = {
    "minif2f": Profile("openai/miniF2F", "lean/LICENSE", "Apache-2.0", 3,
        ("leanpkg.toml",), ("lean/src/test.lean", "lean/src/valid.lean"), ("lean/src/minif2f_import.lean",)),
    "putnambench": Profile("trishullab/PutnamBench", "lean4/LICENSE", "Apache-2.0", 4,
        ("lean4/lean-toolchain", "lean4/lakefile.lean", "lean4/lake-manifest.json"),
        ("lean4/src/putnam_*.lean",)),
    "proofnet": Profile("zhangir-azerbayev/ProofNet", "LICENSE", "MIT", 3,
        ("leanpkg.toml",), ("benchmark/test.jsonl", "benchmark/valid.jsonl"),
        ("benchmark/benchmark_to_publish/formal/common.lean",)),
}


class BenchmarkImportRefused(ValueError):
    """An import cannot preserve the requested identity or output boundary."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _lean_rows(data: bytes, path: str, split: str, issues: list[str], limit: int) -> list[dict[str, Any]]:
    source = data.decode("utf-8")
    # Keep character positions, including CRLF. The scope scanner counts LF
    # lines; replacing CR with a space preserves its offsets into the original.
    text = syntax.strip_comments(source).replace("\r", " ")
    scanned = syntax._scan(text)
    if sum(match.group(2) == "theorem" for match, _ in scanned) > limit:
        issues.append(f"{path}: statement limit exceeded; original file retained without indexing")
        return []
    rows = []
    source_digest = _sha(data)
    char_cursor = byte_cursor = 0
    context_digest = hashlib.sha256()
    for number, (match, prefix) in enumerate(scanned):
        if match.group(2) != "theorem":
            continue
        bound = scanned[number + 1][0].start() if number + 1 < len(scanned) else len(text)
        end = syntax._statement_end(text, match.end(), bound)
        if text[end:end + 2] != ":=":
            issues.append(f"{path}: {match.group(3)} has no supported := proof boundary")
            continue
        start = match.start()  # attributes/modifiers and original indentation belong to the source
        context = source[char_cursor:start].encode()
        context_digest.update(context)
        byte_cursor += len(context)
        char_cursor = start
        statement = source[start:end].encode()
        byte_start, byte_end = byte_cursor, byte_cursor + len(statement)
        rows.append({"id": syntax.declared_name(match.group(3), prefix), "path": path, "split": split,
            "identity_kind": "source-byte-slice", "byte_span": [byte_start, byte_end],
            "sha256": _sha(statement), "source_sha256": source_digest,
            "context_byte_span": [0, byte_start], "context_sha256": context_digest.hexdigest()})
    if not rows:
        issues.append(f"{path}: no supported theorem statements; original bytes retained")
    return rows


def _json_rows(data: bytes, path: str, split: str, issues: list[str], limit: int) -> list[dict[str, Any]]:
    rows, offset = [], 0
    source_digest = _sha(data)
    for number, line in enumerate(data.splitlines(keepends=True), 1):
        start, offset = offset, offset + len(line)
        if not line.strip():
            continue
        if len(rows) >= limit:
            issues.append(f"{path}: statement limit exceeded; remaining records retained without indexing")
            break
        try:
            record = json.loads(line, object_pairs_hook=_json_object)
            if not isinstance(record, dict) or any(not isinstance(record.get(key), str)
                for key in ("id", "formal_statement", "src_header")):
                raise ValueError("id, formal_statement and src_header must be strings")
            if not record["id"] or not record["formal_statement"]:
                raise ValueError("empty id or formal_statement")
            statement = record["formal_statement"].encode("utf-8")
            header = record["src_header"].encode("utf-8")
        except (ValueError, UnicodeError) as error:
            issues.append(f"{path}:{number}: {error}; original record retained")
            continue
        rows.append({"id": record["id"], "path": path, "split": split,
            "identity_kind": "json-string-value-utf8", "statement": record["formal_statement"],
            "src_header": record["src_header"], "sha256": _sha(statement), "header_sha256": _sha(header),
            "record_byte_span": [start, offset], "record_sha256": _sha(line), "source_sha256": source_digest})
    if not rows:
        issues.append(f"{path}: no supported statement records; original bytes retained")
    return rows


def _toolchain(root: Path, profile: Profile, issues: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {"lean_version": None, "mathlib_revision": None, "mathlib_repository": None,
                              "files": []}
    for name in profile.metadata:
        path = root / name
        if not path.is_file():
            issues.append(f"missing toolchain metadata: {name}")
            continue
        result["files"].append({"path": name, "sha256": _sha(path.read_bytes())})
    try:
        if profile.lean_major == 3:
            package = tomllib.loads((root / "leanpkg.toml").read_bytes().decode("utf-8"))
            result["lean_version"] = package["package"]["lean_version"]
            dependency = package["dependencies"]["mathlib"]
            result.update(mathlib_revision=dependency["rev"], mathlib_repository=dependency["git"])
        else:
            result["lean_version"] = (root / "lean4/lean-toolchain").read_bytes().decode("utf-8").strip()
            manifest = json.loads((root / "lean4/lake-manifest.json").read_bytes(), object_pairs_hook=_json_object)
            dependencies = [item for item in manifest["packages"] if item["name"] == "mathlib"]
            if len(dependencies) != 1:
                raise ValueError("expected exactly one Mathlib dependency")
            result.update(mathlib_revision=dependencies[0]["rev"], mathlib_repository=dependencies[0]["url"])
        if not isinstance(result["lean_version"], str) or not result["lean_version"]:
            raise ValueError("missing Lean version")
        if not isinstance(result["mathlib_revision"], str) or not re.fullmatch(r"[0-9a-f]{40}", result["mathlib_revision"]):
            raise ValueError("Mathlib dependency is not pinned to a full commit")
    except (OSError, ValueError, KeyError, TypeError) as error:
        issues.append(f"toolchain metadata is incomplete or unsupported: {error}")
    return result


def _manifest(root: Path, profile: Profile, benchmark: str, revision: str, digest: str,
              inventory: list[dict[str, Any]]) -> dict[str, Any]:
    if not (root / profile.license_path).is_file() or not (root / profile.license_path).read_bytes().strip():
        raise BenchmarkImportRefused(f"missing license source: {profile.license_path}")
    issues, rows, input_files = [], [], []
    for name in profile.support:
        if not (root / name).is_file():
            issues.append(f"missing local support source: {name}")
    for pattern in profile.inputs:
        paths = sorted(root.glob(pattern))
        if not paths:
            issues.append(f"missing benchmark input: {pattern}")
        for path in paths:
            name = path.relative_to(root).as_posix()
            input_files.append(name)
            split = path.stem if benchmark != "putnambench" else "unspecified"
            with path.open("rb") as stream:
                data = stream.read(MAX_INDEX_BYTES + 1)
            if len(data) > MAX_INDEX_BYTES:
                issues.append(f"{name}: index byte limit exceeded; original file retained without indexing")
                continue
            try:
                parser = _json_rows if benchmark == "proofnet" else _lean_rows
                rows.extend(parser(data, name, split, issues, MAX_STATEMENTS - len(rows)))
            except UnicodeError as error:
                issues.append(f"{name}: invalid UTF-8: {error}; original bytes retained")
    for name, count in Counter(row["id"] for row in rows).items():
        if count > 1:
            issues.append(f"duplicate statement id {name!r}: {count} records, including across splits")
    toolchain = _toolchain(root, profile, issues)
    lean_version = toolchain["lean_version"]
    detected = (4 if isinstance(lean_version, str) and re.search(r"(?:^|:)v?4\.", lean_version)
                else 3 if isinstance(lean_version, str) and re.search(r"(?:^|:)v?3\.", lean_version) else None)
    if detected != profile.lean_major:
        issues.append("recorded Lean version does not match the selected upstream format")
    repository = f"https://github.com/{profile.repository}"
    return {"schema_version": 1, "benchmark": benchmark, "profile": asdict(profile),
        "provenance": {"repository": repository, "revision": revision, "archive_sha256": digest,
            "archive_url": f"https://codeload.github.com/{profile.repository}/tar.gz/{revision}",
            "authentication": "caller-pinned archive digest; upstream commit attribution supplied by caller"},
        "source_root": f"source/{root.name}", "files": inventory, "statements": rows,
        "license": {"declared_by_profile": profile.declared_license, "path": profile.license_path,
                    "sha256": _sha((root / profile.license_path).read_bytes())},
        "toolchain": toolchain, "verification": "not-run",
        "compatibility": {"lean4": detected == 4 if detected else None, "environment_match": "unverified"},
        "coverage": {"status": "incomplete" if issues else "lexically-indexed", "issues": issues,
                     "input_files": input_files, "indexed_statements": len(rows), "semantic_coverage": "unverified"},
        "notes": [
            "Original archive, files, licenses and source context are retained byte-for-byte. No files were executed or compiled.",
            "Lean declarations are lexical byte spans, not elaborated claims. Their context may include earlier proofs and must not be silently exposed as fresh benchmark input.",
            "JSON string-value identities hash decoded UTF-8; raw JSON record/file bytes retain their separate identities. No whitespace or Unicode normalization is applied.",
            "PutnamBench factored answer definitions remain as published; no answers are inlined and no theorem is rewritten.",
            "Lean3 inputs are not converted to Lean4. A matching major version does not establish toolchain or library compatibility.",
            "Coverage is limited to the selected profile's declared files and lexical format. Other languages and datasets in the archive are retained but not indexed.",
        ]}


def import_archive(archive: Path, *, benchmark: str, revision: str, expected_sha256: str,
                   output: Path) -> dict[str, Any]:
    """Publish an import into a new explicit output directory; never execute it.

    Obtain the profile's codeload archive at a full commit outside this operation,
    then pin its SHA-256. Attribution is explicit caller provenance, not a claim
    that checking an archive digest authenticates a Git commit's object graph.
    """
    if benchmark not in PROFILES:
        raise BenchmarkImportRefused(f"unknown benchmark profile: {benchmark}")
    if not re.fullmatch(r"[0-9a-f]{40}", revision) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise BenchmarkImportRefused("full lowercase commit revision and SHA-256 archive digest are required")
    profile = PROFILES[benchmark]
    try:
        with Path(archive).open("rb") as stream:
            data = stream.read(MAX_ARCHIVE_BYTES + 1)
        if len(data) > MAX_ARCHIVE_BYTES:
            raise BenchmarkImportRefused(f"archive exceeds {MAX_ARCHIVE_BYTES} bytes")
        if _sha(data) != expected_sha256:
            raise BenchmarkImportRefused("archive SHA-256 does not match the pinned digest")
        output = Path(output).absolute()
        output.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(output.parent / f".{output.name}.import.lock", timeout=10):
            if output.exists() or output.is_symlink():
                raise BenchmarkImportRefused(f"output already exists: {output}")
            with tempfile.TemporaryDirectory(prefix=".benchmark-import-", dir=output.parent) as temporary:
                payload = Path(temporary) / "payload"
                sources = payload / "source"
                sources.mkdir(parents=True)
                extraction = archives.extract(data, sources, limits=LIMITS)
                expected_root = f"{profile.repository.split('/')[-1]}-{revision}"
                if extraction.kind != "tar" or any(not item.path.startswith(expected_root + "/") for item in extraction.files):
                    raise BenchmarkImportRefused("archive root does not match the pinned repository and revision")
                inventory = [asdict(item) | {"path": item.path[len(expected_root) + 1:]} for item in extraction.files]
                result = _manifest(sources / expected_root, profile, benchmark, revision, expected_sha256, inventory)
                (payload / "upstream.archive").write_bytes(data)
                # JSON round-trip makes tuple-valued profile fields match the
                # durable manifest returned after a restart.
                serialized = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
                (payload / "manifest.json").write_text(serialized + "\n", encoding="utf-8")
                os.rename(payload, output)
                return json.loads(serialized)
    except (OSError, ValueError, LockTimeout) as error:
        raise BenchmarkImportRefused(str(error)) from error
