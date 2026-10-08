"""What both recorded surfaces owe: strict source reads, the axiom allowlist,
a toolchain named by revision and a spend stated per field.

Read-only. Nothing here constructs a runtime, a provider or a controller.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from hardy.formal import audit
from hardy.formal.contracts import FormalStatus
from hardy.formal.verifier import ALLOWED_AXIOMS

#: The formal grades that carry verification evidence and are audited as
#: verified runs. `verified_modulo` is one of them: a wider trust base is a
#: reason to check the evidence, the axiom report and the document harder, not
#: a reason to skip them, and an audit that recognised only `kernel_verified`
#: would have skipped all three on exactly the runs that need them.
VERIFIED_GRADES = frozenset({FormalStatus.KERNEL_VERIFIED, FormalStatus.VERIFIED_MODULO})


def _exact_source(path: Path, *, label: str) -> tuple[str | None, str | None]:
    """`path`'s bytes decoded as UTF-8, with no newline translation.

    `Path.read_text` runs the platform's universal-newline translation before
    handing back a string -- CRLF and lone CR both become LF -- so a source
    hashed or compared byte for byte against that translated text is not the
    source that was actually hashed or read. A `Main.lean` or `proof.lean`
    with CRLF line endings whose evidence hashes those bytes would then read
    as identical to the LF-only source the verifier renders, which is exactly
    the source a byte-exact check exists to tell apart. Reading the raw bytes
    and decoding them strictly keeps that comparison honest, and turns a
    source that is not UTF-8 at all into a reported issue instead of a crash.

    Returns `(text, None)` on success, or `(None, issue)` naming `label` when
    the file cannot be read or decoded.
    """
    try:
        return path.read_bytes().decode("utf-8"), None
    except OSError as error:
        return None, f"{label} could not be read: {error}"
    except UnicodeDecodeError as error:
        return None, f"{label} is not valid UTF-8: {error}"


def permitted_axioms(assumed: Sequence[str] = ()) -> frozenset[str]:
    """Lean's own axioms, plus exactly the assumptions the manifest names.

    `sorryAx` can never be permitted: a hole is not an assumption, no
    declaration may launder one, and `audit.classify` refuses it before
    approval is consulted at all. Enforced here too rather than relied upon,
    because this function is what a reader of a stored run is checked against
    and the manifest it reads was written by the run being audited.
    """
    return frozenset(ALLOWED_AXIOMS) | (
        {name for name in assumed if name not in audit.FORBIDDEN}
    )


# --- recorded runs ---------------------------------------------------------
#
# A run that was actually paid for and kept -- a real model, a real Lean, a
# real Tectonic -- is checked here with none of the three present. What the
# audit establishes is the same self-consistency `validate_run_consistency`
# establishes for a staged run, plus the things a *recorded* run owes that a
# fixture does not: a toolchain named by revision, a spend stated per field
# or explicitly null, and an axiom line that came out of a Lean process
# rather than out of the model's own account of itself.

# The counters `Usage.summary` states, each present or `None`. A key that is
# missing is a run that never said, which reads as free.
USAGE_FIELDS = ("cost_usd", "input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens")
IDENTITY_FIELDS = ("lean_version", "lean_commit", "mathlib_revision", "lake_manifest_sha256")


# The tool names that count as "the model looked something up", per surface.
BATCH_SEARCH = frozenset({"search_declaration"})
STAGED_SEARCH = frozenset({"lean_search_declarations", "lean_inspect_declarations", "rank_premises"})


def _toolchain_issues(toolchain: Any, where: str) -> list[str]:
    """A recorded run names its Lean and Mathlib by revision, or it is a story."""
    if not isinstance(toolchain, dict) or not toolchain:
        return [f"{where} records no toolchain identity"]
    if "unrecorded" in toolchain:
        return [f"{where} toolchain identity is unrecorded: {toolchain['unrecorded']}"]
    missing = [field for field in IDENTITY_FIELDS if not toolchain.get(field)]
    if missing:
        return [f"{where} toolchain identity lacks " + ", ".join(missing)]
    return []


def _usage_issues(usage: Any, where: str) -> list[str]:
    """Cost, the four counters, and the exchange count: present or explicitly null."""
    if not isinstance(usage, dict):
        return [f"{where} records no usage"]
    issues = []
    for field in ("exchanges", *USAGE_FIELDS):
        if field not in usage:
            issues.append(f"{where} usage does not state {field} (absent is not null)")
        elif usage[field] is not None and not isinstance(usage[field], (int, float)):
            issues.append(f"{where} usage states a non-numeric {field}")
    return issues
