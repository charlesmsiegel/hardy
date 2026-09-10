"""Synthetic upstream-shaped fixtures test byte preservation, not benchmark results."""
from __future__ import annotations

import hashlib
import importlib
import io
import json
import tarfile

import pytest

REVISION = "a" * 40
PACKAGE = b'''[package]
lean_version = "leanprover-community/lean:3.50.3"
[dependencies]
mathlib = {git = "https://github.com/leanprover-community/mathlib", rev = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}
'''


@pytest.fixture
def benchmarks():
    return importlib.import_module("hardy.evals.benchmarks")


def archive(tmp_path, files, *, root="miniF2F", extra=None):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as tar:
        for path, data in files.items():
            member = tarfile.TarInfo(f"{root}-{REVISION}/{path}")
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
        if extra is not None:
            tar.addfile(extra)
    path = tmp_path / "upstream.tar.gz"
    path.write_bytes(stream.getvalue())
    return path


def mini_files():
    return {"lean/LICENSE": b"Apache License\nVersion 2.0\nSynthetic fixture notice",
            "leanpkg.toml": PACKAGE, "lean/src/minif2f_import.lean": b"import data.nat.basic\r\n",
            "lean/src/test.lean": 'import minif2f_import\r\n-- theorem fake : False := sorry\r\ntheorem α (n : ℕ := 2) : n = n :=\r\nby refl\r\n'.encode(),
            "lean/src/valid.lean": b"import minif2f_import\ntheorem valid : True := by trivial\n"}


def admit(benchmarks, path, output, *, benchmark="minif2f", **kwargs):
    return benchmarks.import_archive(path, benchmark=benchmark, revision=REVISION,
        expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), output=output, **kwargs)


def test_minif2f_original_bytes_offsets_imports_and_lean3_identity_survive_restart(tmp_path, benchmarks):
    files = mini_files()
    path = archive(tmp_path, files)
    result = admit(benchmarks, path, tmp_path / "imported")
    root = tmp_path / "imported"
    stored = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert stored == result
    assert (root / "upstream.archive").read_bytes() == path.read_bytes()
    for name, data in files.items():
        assert (root / result["source_root"] / name).read_bytes() == data
    row = next(row for row in result["statements"] if row["id"] == "α")
    source = files[row["path"]]
    start, end = row["byte_span"]
    assert source[start:end] == 'theorem α (n : ℕ := 2) : n = n '.encode()
    assert row["sha256"] == hashlib.sha256(source[start:end]).hexdigest()
    assert row["identity_kind"] == "source-byte-slice"
    assert result["toolchain"]["mathlib_revision"] == "b" * 40
    assert result["compatibility"]["lean4"] is False
    assert result["verification"] == "not-run"
    assert result["coverage"]["status"] == "lexically-indexed"
    assert result["provenance"]["revision"] == REVISION
    assert result["license"]["path"] == "lean/LICENSE"


def test_putnam_factored_answer_and_context_are_not_rewritten(tmp_path, benchmarks):
    source = b"import Mathlib\n\nabbrev putnam_2000_a1_solution : Nat := sorry\n-- 2\ntheorem putnam_2000_a1 : 1 + 1 = putnam_2000_a1_solution := sorry\n"
    files = {"lean4/LICENSE": b"Apache License\nVersion 2.0", "lean4/lean-toolchain": b"leanprover/lean4:v4.27.0\n",
             "lean4/lakefile.lean": b"import Lake\n", "lean4/lake-manifest.json": json.dumps({"packages": [
                 {"name": "mathlib", "url": "https://github.com/leanprover-community/mathlib4", "rev": "b" * 40}]}).encode(),
             "lean4/src/putnam_2000_a1.lean": source}
    path = archive(tmp_path, files, root="PutnamBench")
    result = admit(benchmarks, path, tmp_path / "imported", benchmark="putnambench")
    row = result["statements"][0]
    assert source[slice(*row["byte_span"])] == b"theorem putnam_2000_a1 : 1 + 1 = putnam_2000_a1_solution "
    assert result["compatibility"]["lean4"] is True
    assert result["compatibility"]["environment_match"] == "unverified"
    assert any("factored" in note for note in result["notes"])
    assert (tmp_path / "imported" / result["source_root"] / row["path"]).read_bytes() == source


def test_proofnet_preserves_json_encoding_separately_from_decoded_value_identity(tmp_path, benchmarks):
    record = {"id": "Book|exercise", "formal_statement": "theorem α : True :=", "src_header": "import .common\r\n",
              "nl_statement": "A synthetic statement", "nl_proof": "A synthetic proof"}
    line = json.dumps(record, ensure_ascii=True).encode() + b"\r\n"
    files = {"LICENSE": b"MIT License\nSynthetic fixture", "leanpkg.toml": PACKAGE,
             "benchmark/test.jsonl": line, "benchmark/valid.jsonl": b"",
             "benchmark/benchmark_to_publish/formal/common.lean": b"import data.real.basic\n"}
    path = archive(tmp_path, files, root="ProofNet")
    result = admit(benchmarks, path, tmp_path / "imported", benchmark="proofnet")
    row = result["statements"][0]
    assert row["statement"] == record["formal_statement"]
    assert row["src_header"] == record["src_header"]
    assert row["identity_kind"] == "json-string-value-utf8"
    assert row["sha256"] == hashlib.sha256(record["formal_statement"].encode()).hexdigest()
    assert line[slice(*row["record_byte_span"])] == line
    assert row["record_sha256"] == hashlib.sha256(line).hexdigest()
    assert result["coverage"]["status"] == "incomplete"  # empty valid split remains explicit


@pytest.mark.parametrize("attack", ["hash", "revision", "traversal", "symlink", "wrong-root"])
def test_invalid_archive_identity_or_paths_never_publish_a_partial_import(tmp_path, benchmarks, attack):
    extra = None
    if attack in {"traversal", "symlink"}:
        extra = tarfile.TarInfo("../escaped" if attack == "traversal" else f"miniF2F-{REVISION}/link")
        if attack == "symlink":
            extra.type, extra.linkname = tarfile.SYMTYPE, "../../escaped"
    path = archive(tmp_path, mini_files(), root="wrong" if attack == "wrong-root" else "miniF2F", extra=extra)
    with pytest.raises(benchmarks.BenchmarkImportRefused):
        benchmarks.import_archive(path, benchmark="minif2f", revision="main" if attack == "revision" else REVISION,
            expected_sha256="0" * 64 if attack == "hash" else hashlib.sha256(path.read_bytes()).hexdigest(), output=tmp_path / "imported")
    assert not (tmp_path / "imported").exists()
    assert not (tmp_path / "escaped").exists()


def test_existing_output_is_preserved_and_duplicate_statement_ids_are_findings(tmp_path, benchmarks):
    files = mini_files()
    files["lean/src/valid.lean"] = files["lean/src/test.lean"]
    path = archive(tmp_path, files)
    result = admit(benchmarks, path, tmp_path / "imported")
    assert result["coverage"]["status"] == "incomplete"
    assert any("duplicate" in issue for issue in result["coverage"]["issues"])
    before = (tmp_path / "imported/manifest.json").read_bytes()
    with pytest.raises(benchmarks.BenchmarkImportRefused, match="exists"):
        admit(benchmarks, path, tmp_path / "imported")
    assert (tmp_path / "imported/manifest.json").read_bytes() == before


def test_bounds_and_missing_license_are_refused(tmp_path, benchmarks, monkeypatch):
    files = mini_files()
    files.pop("lean/LICENSE")
    path = archive(tmp_path, files)
    with pytest.raises(benchmarks.BenchmarkImportRefused, match="license"):
        admit(benchmarks, path, tmp_path / "imported")
    monkeypatch.setattr(benchmarks, "MAX_ARCHIVE_BYTES", 10)
    with pytest.raises(benchmarks.BenchmarkImportRefused, match="bytes"):
        admit(benchmarks, path, tmp_path / "imported")


def test_missing_local_support_and_unsupported_statement_are_explicit(tmp_path, benchmarks):
    files = mini_files()
    files.pop("lean/src/minif2f_import.lean")
    files["lean/src/test.lean"] = b"theorem incomplete : True\n"
    result = admit(benchmarks, archive(tmp_path, files), tmp_path / "imported")
    assert result["coverage"]["status"] == "incomplete"
    assert any("minif2f_import.lean" in issue for issue in result["coverage"]["issues"])
    assert any("proof boundary" in issue for issue in result["coverage"]["issues"])


def test_cli_imports_only_into_explicit_new_output_and_refuses_a_second_import(tmp_path, benchmarks, capsys):
    from hardy.app import evals
    from hardy.app.cli import build_parser

    path = archive(tmp_path, mini_files())
    argv = ["evals", "import-benchmark", "minif2f", "--archive", str(path), "--revision", REVISION,
            "--sha256", hashlib.sha256(path.read_bytes()).hexdigest(), "--output", str(tmp_path / "imported")]
    args = build_parser().parse_args(argv)
    assert evals.main(args, None) == 0
    assert json.loads(capsys.readouterr().out)["verification"] == "not-run"
    assert evals.main(args, None) == 2
    assert "exists" in capsys.readouterr().err


def test_excessive_declarations_remain_raw_with_incomplete_coverage(tmp_path, benchmarks, monkeypatch):
    files = mini_files()
    files["lean/src/test.lean"] = b"theorem first : True := sorry\ntheorem second : True := sorry\n"
    monkeypatch.setattr(benchmarks, "MAX_STATEMENTS", 1)
    result = admit(benchmarks, archive(tmp_path, files), tmp_path / "imported")
    assert len(result["statements"]) <= 1
    assert any("statement limit" in issue for issue in result["coverage"]["issues"])
    assert (tmp_path / "imported" / result["source_root"] / "lean/src/test.lean").read_bytes() == files["lean/src/test.lean"]


def test_commented_string_delimiters_and_namespace_offsets_preserve_unicode_crlf(tmp_path, benchmarks):
    files = mini_files()
    statement = '@[simp] theorem «quoted α» (s : String := ":=") : s = s '
    source = ('namespace First\r\n' + '-- α\r\n' * 10 + statement + ':= by rfl\r\nend First\r\n').encode()
    files["lean/src/test.lean"] = source
    result = admit(benchmarks, archive(tmp_path, files), tmp_path / "imported")
    row = next(row for row in result["statements"] if "quoted" in row["id"])
    assert row["id"] == "First.«quoted α»"
    assert source[slice(*row["byte_span"])] == statement.encode()
    assert row["context_sha256"] == hashlib.sha256(source[:row["byte_span"][0]]).hexdigest()


def test_large_unindexed_upstream_asset_is_retained_but_indexed_sources_stay_bounded(tmp_path, benchmarks, monkeypatch):
    files = mini_files()
    files["training/export.bin"] = b"x" * ((8 << 20) + 1)
    path = archive(tmp_path, files)
    result = admit(benchmarks, path, tmp_path / "imported")
    assert result["coverage"]["issues"] == []
    assert (tmp_path / "imported" / result["source_root"] / "training/export.bin").stat().st_size == len(files["training/export.bin"])
    monkeypatch.setattr(benchmarks, "MAX_INDEX_BYTES", 16)
    bounded = admit(benchmarks, path, tmp_path / "bounded")
    assert bounded["statements"] == []
    assert any("index byte limit" in issue for issue in bounded["coverage"]["issues"])
