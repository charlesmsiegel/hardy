"""What the recorded-run audit reports for the checked-in runs and fixed forgeries of them.

`hardy accept --recorded` is a reader: it refuses evidence that could not
have happened as described, and the exact findings it gives are what a
reviewer and a script read. The checked-in runs under `acceptance/recorded/`
are copied, broken in one named way each, and audited; every finding is
pinned, in order and word for word. A refactor of the reader must leave this
file unchanged. A deliberate change to a finding regenerates it in the same
commit, so the diff explains itself in review:

    uv run python tests/unit/test_recorded_golden.py
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

import pytest

from hardy.workflows.recorded import refusal_issues, validate_recorded_run

ROOT = Path(__file__).resolve().parents[2]
RECORDED = ROOT / "acceptance" / "recorded"
GOLDEN = ROOT / "tests" / "fixtures" / "recorded-audit.json"


def _staged(root: Path) -> Path:
    return next(child for child in root.iterdir() if (child / "manifest.json").exists())


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _edit(path: Path, change: Callable[[dict], None]) -> None:
    payload = _load(path)
    change(payload)
    _dump(path, payload)


def _rehash(run: Path, *names: str) -> None:
    """Restamp the manifest's artifact hashes over files a forgery changed,
    so the audit has to find the forgery somewhere other than the hash."""
    def change(manifest: dict) -> None:
        for name in names:
            manifest["artifacts"][name] = hashlib.sha256((run / name).read_bytes()).hexdigest()
    _edit(run / "manifest.json", change)


# --- forgeries of the staged run ------------------------------------------------

def _altered_hash(run: Path) -> None:
    _edit(run / "manifest.json", lambda m: m["artifacts"].__setitem__("lean/Main.lean", "0" * 64))


def _edited_source(run: Path) -> None:
    main = run / "lean" / "Main.lean"
    main.write_bytes(main.read_bytes() + b"\n-- an edit after verification\n")


def _edited_source_rehashed(run: Path) -> None:
    _edited_source(run)
    _rehash(run, "lean/Main.lean")


def _wrong_signature(run: Path) -> None:
    main = run / "lean" / "Main.lean"
    text = main.read_text(encoding="utf-8")
    main.write_text(text.replace("Irrational", "Transcendental", 1), encoding="utf-8")
    _rehash(run, "lean/Main.lean")


def _absent_verification(run: Path) -> None:
    (run / "lean" / "verification.json").unlink()


def _unauthorized_axiom(run: Path) -> None:
    def change(verification: dict) -> None:
        verification["axioms"].append("sorryAx")
        verification["evidence"]["axioms"].append("sorryAx")
    _edit(run / "lean" / "verification.json", change)
    _rehash(run, "lean/verification.json")


def _manifest_unauthorized_axiom(run: Path) -> None:
    _edit(run / "manifest.json",
          lambda m: m["grades"]["verification_evidence"]["axioms"].append("Hardy.cheat"))


def _undeclared_assumption(run: Path) -> None:
    _dump(run / "assumptions.json", [{"name": "Hardy.cheat", "statement": "False"}])


def _forged_formal_grade(run: Path) -> None:
    _edit(run / "manifest.json", lambda m: m["grades"].__setitem__("formal", "verified_modulo"))


def _forged_faithfulness(run: Path) -> None:
    _edit(run / "manifest.json",
          lambda m: m["grades"]["faithfulness_review"].__setitem__("outcome", "disagreed"))


def _missing_pdf(run: Path) -> None:
    (run / "writeup" / "paper.pdf").unlink()


def _malformed_trajectory(run: Path) -> None:
    path = run / "trajectory.jsonl"
    path.write_bytes(path.read_bytes() + b"{not json\n")
    _rehash(run, "trajectory.jsonl")


def _truncated_trajectory(run: Path) -> None:
    path = run / "trajectory.jsonl"
    lines = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(b"".join(lines[:-1]))
    _rehash(run, "trajectory.jsonl")


def _manifest_not_json(run: Path) -> None:
    (run / "manifest.json").write_text("{", encoding="utf-8")


def _non_utf8_source(run: Path) -> None:
    main = run / "lean" / "Main.lean"
    main.write_bytes(main.read_bytes() + b"\xff\n")
    _rehash(run, "lean/Main.lean")


STAGED = {
    "altered-hash": _altered_hash,
    "edited-source": _edited_source,
    "edited-source-rehashed": _edited_source_rehashed,
    "wrong-signature": _wrong_signature,
    "absent-verification": _absent_verification,
    "unauthorized-axiom": _unauthorized_axiom,
    "manifest-unauthorized-axiom": _manifest_unauthorized_axiom,
    "undeclared-assumption": _undeclared_assumption,
    "forged-formal-grade": _forged_formal_grade,
    "forged-faithfulness": _forged_faithfulness,
    "missing-pdf": _missing_pdf,
    "malformed-trajectory": _malformed_trajectory,
    "truncated-trajectory": _truncated_trajectory,
    "manifest-not-json": _manifest_not_json,
    "non-utf8-source": _non_utf8_source,
}


# --- forgeries of the batch runs ------------------------------------------------

def _forged_batch_grade(run: Path) -> None:
    _edit(run / "result.json", lambda r: r.__setitem__("formalization", "verified modulo"))


def _edited_proof(run: Path) -> None:
    path = run / "proof.lean"
    path.write_text(path.read_text(encoding="utf-8") + "\n-- edited\n", encoding="utf-8")


def _deleted_proof(run: Path) -> None:
    (run / "proof.lean").unlink()


def _batch_trajectory_not_json(run: Path) -> None:
    (run / "trajectory.json").write_text("[1, 2", encoding="utf-8")


def _batch_trajectory_not_object(run: Path) -> None:
    (run / "trajectory.json").write_text("[]", encoding="utf-8")


def _dropped_event(run: Path) -> None:
    _edit(run / "trajectory.json", lambda t: t["events"].pop())


def _inconsistent_turns(run: Path) -> None:
    _edit(run / "result.json", lambda r: r.__setitem__("turns", (r["turns"] or 0) + 7))


def _usage_field_absent(run: Path) -> None:
    _edit(run / "result.json", lambda r: r["usage"].pop(next(iter(r["usage"]))))


def _batch_unauthorized_axiom(run: Path) -> None:
    def change(result: dict) -> None:
        for value in result["axioms"].values():
            if isinstance(value, list):
                value.append("sorryAx")
    _edit(run / "result.json", change)


def _toolchain_absent(run: Path) -> None:
    _edit(run / "result.json", lambda r: r.pop("toolchain"))


def _relabelled_reason(run: Path) -> None:
    _edit(run / "result.json", lambda r: r.__setitem__("terminal_reason", "no_proof_submitted"))


def _writeup_removed(run: Path) -> None:
    (run / "writeup.md").unlink()


BATCH = {
    "forged-grade": _forged_batch_grade,
    "edited-proof": _edited_proof,
    "deleted-proof": _deleted_proof,
    "trajectory-not-json": _batch_trajectory_not_json,
    "trajectory-not-object": _batch_trajectory_not_object,
    "dropped-event": _dropped_event,
    "inconsistent-turns": _inconsistent_turns,
    "usage-field-absent": _usage_field_absent,
    "unauthorized-axiom": _batch_unauthorized_axiom,
    "toolchain-absent": _toolchain_absent,
    "relabelled-reason": _relabelled_reason,
    "writeup-removed": _writeup_removed,
}


def cases() -> list[tuple[str, Path, Callable[[Path], None] | None]]:
    staged = _staged(RECORDED / "prove-verified")
    found: list[tuple[str, Path, Callable[[Path], None] | None]] = [
        ("prove-verified", staged, None),
        *((f"prove-verified/{name}", staged, forge) for name, forge in STAGED.items()),
    ]
    for run in ("batch-verified", "batch-false-statement", "batch-starved"):
        found.append((run, RECORDED / run, None))
        for name, forge in BATCH.items():
            if forge in (_edited_proof, _deleted_proof) and not (RECORDED / run / "proof.lean").exists():
                continue
            found.append((f"{run}/{name}", RECORDED / run, forge))
    return found


def _normalise(issues: tuple[str, ...], run: Path) -> list[str]:
    spellings = sorted({str(run), run.as_posix(), str(run.resolve()), run.resolve().as_posix()},
                       key=len, reverse=True)
    out = []
    for issue in issues:
        for spelling in spellings:
            issue = issue.replace(spelling, "<run>")
        out.append(issue)
    return out


def audit(source: Path, forge: Callable[[Path], None] | None) -> dict[str, list[str]]:
    with tempfile.TemporaryDirectory() as scratch:
        run = Path(scratch) / "run"
        shutil.copytree(source, run)
        if forge is not None:
            forge(run)
        found = {"recorded": _reading(lambda: validate_recorded_run(run), run)}
        if (run / "result.json").exists() or (run / "trajectory.json").exists():
            found["refusal"] = _reading(lambda: refusal_issues(run), run)
        return found


def _reading(read: Callable[[], tuple[str, ...]], run: Path) -> list[str]:
    """The findings, or the exception the reader raised instead of a finding.

    Pinned as it stands: a forgery the reader crashes on is behaviour to keep
    through a refactor and change only in a commit of its own.
    """
    try:
        return _normalise(read(), run)
    except Exception as error:  # noqa: BLE001 - recorded, not handled
        return _normalise((f"raised {type(error).__name__}: {error}",), run)


def load() -> dict[str, dict[str, list[str]]]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_every_case_is_pinned():
    assert sorted(load()) == sorted(name for name, _, _ in cases())


@pytest.mark.parametrize(("name", "source", "forge"), cases(), ids=[name for name, _, _ in cases()])
def test_the_audit_reports_what_the_golden_file_recorded(name, source, forge):
    assert audit(source, forge) == load()[name]


def test_the_checked_in_runs_pass_and_the_exceptions_are_named():
    """Each checked-in run passes; each mutation it does not refuse with a
    finding is named below, so a refactor that newly accepts or newly crashes
    on one is a visible change to these sets rather than a quiet one."""
    golden = load()
    for name, _, forge in cases():
        findings = golden[name]["recorded"]
        if forge is None:
            assert findings == [], name
            continue
        assert (not findings) == (name in ACCEPTED), name
        assert any(issue.startswith("raised ") for issue in findings) == (name in RAISES), name


# Mutations the reader accepts. Some change nothing the reader is meant to
# judge (a false-statement run already ends `no_proof_submitted`; an
# `assumptions.json` beside a kernel-verified run declares nothing the
# evidence uses); the others are recorded as found. Changing what the reader
# accepts is a behaviour change and belongs in a commit of its own.
ACCEPTED = frozenset({
    "prove-verified/undeclared-assumption",
    "batch-verified/dropped-event",
    "batch-verified/inconsistent-turns",
    "batch-false-statement/dropped-event",
    "batch-false-statement/inconsistent-turns",
    "batch-false-statement/unauthorized-axiom",
    "batch-false-statement/relabelled-reason",
    "batch-starved/unauthorized-axiom",
})

# Mutations the reader meets with an exception rather than a finding. Nothing
# is accepted, but `hardy accept --recorded` reports a traceback, not a reason.
RAISES = frozenset({"prove-verified/malformed-trajectory"})


def regenerate() -> None:
    golden = {name: audit(source, forge) for name, source, forge in cases()}
    GOLDEN.write_text(json.dumps(golden, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    regenerate()
    sys.exit(0)
