"""Read-only views of `/prove` runs, kept under `config.runs_root` -- outside the problem tree.

Every other panel in this package reads under the problem directory and is
confined by `workspace.confine()`: a relative path proven to resolve to its
own parent's immediate child, all the way down (`workspace.py:33-47`). Runs
are not there -- `config.runs_root` (`app/config.py:274`, env
`HARDY_RUNS_ROOT`) names a directory the *problem* does not own, so
`confine()` does not apply, and this module is the one place that reads
outside it. What confines a read here instead:

- A run is addressed by its `run_id` (a `UUID`), never by a path component
  the browser supplies. `run_id` is only ever *compared* -- against the
  trailing 8 hex characters of a directory name `runs_root.iterdir()` itself
  already produced -- so it can select an existing sibling of `runs_root` or
  nothing at all; it is never joined into a path and so cannot walk out of
  `runs_root` the way a crafted relative path could walk out of a problem
  directory. `run_id` is proven to be a well-formed `UUID` before it is used
  for anything (`UUID(run_id)` raises `ValueError` on anything else), which
  is what makes an unknown or malformed `run_id` the same clean 400
  `ledger_item`/`ledger_export` already give an unknown ledger id.
- The matched run directory, and the three files read inside it
  (`manifest.json`, `formalization.json`, `trajectory.jsonl`), are each proven with
  `resolve_named_child` to be their own parent's real, non-symlink child --
  the same proof `workspace.confine()` builds for the problem tree
  (`foundation/files.py:21-48`). A directory or file planted as a symlink
  inside `runs_root` is refused, not followed: nothing here ever serves a
  byte from outside `runs_root` itself.
- `_find_run_dir` (`cli.py:828-833`, called at `cli.py:811`) is reused for the match itself:
  enumerate `runs_root`'s own immediate children, keep directories whose name
  ends in `-<run_id.hex[:8]>`, and require exactly one. `_locate_run_dir`
  below adds the symlink and identity proofs above; the matching rule is the
  one `cli.py` already trusted, not a second one invented for this reader.

A run's own artifacts are not trust-boundary-adjacent in the sense
`AGENTS.md` means when it says "nothing confines generated Lean, TeX,
downloaded papers, or helper processes": Hardy wrote every byte under a run
directory itself, through `RunStore` (`workflows/storage.py`). The guard
above is about geography, not content -- the browser is being handed a view
of a directory the *problem* does not own, and the proof is that the view
cannot wander past it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from hardy.app.config import Config
from hardy.app.web.panels import vocabulary
from hardy.formal.contracts import FrozenClaim, freeze_claim
from hardy.foundation.files import LayoutError, resolve_named_child
from hardy.workflows.contracts import Grades, RunManifest
from hardy.workflows.storage import TrajectoryEvent

#: `RunStore`'s own filenames (`workflows/storage.py:53,108`), read directly
#: here rather than through `RunStore`/`RunStore.open`: that class exists to
#: continue a *live* run's trajectory (a write lease, a sequence counter that
#: must stay contiguous for the next append) -- machinery a read-only panel,
#: which may be looking at a run nobody is writing to any more, has no
#: business paying for or being tripped by. Matches this package's own
#: precedent: `record.py`'s `SESSION_RECORD` reads `session.json` directly
#: for the identical reason.
MANIFEST = "manifest.json"
TRAJECTORY = "trajectory.jsonl"
#: The frozen, human-approved claim, written by `prove.py` the moment the
#: user approves a formalization and read back from disk there before
#: anything is proved against it -- so this file *is* the statement every
#: check in the run was made against, and `RunManifest.claim_sha256` is its
#: `content_hash`.
FORMALIZATION = "formalization.json"


def _grade_tones(grades: Grades) -> dict[str, str]:
    """The three tones the design's `⊢ formal` / faithfulness / document pills need.

    Read once here so a run's list row and its detail page always agree on
    the colour a given grade draws in, rather than each computing it -- and
    so the client is never asked to map `Grades.formal`/`faithfulness`/
    `document` to a colour itself, which is exactly the judgment
    `panels/vocabulary.py`'s docstring reserves for the server.
    """
    return {
        "formal": vocabulary.formal_tone(grades.formal),
        "faithfulness": vocabulary.faithfulness_tone(grades.faithfulness),
        "document": vocabulary.document_tone(grades.document),
    }


def _read_manifest(run_dir: Path) -> tuple[RunManifest | None, str | None]:
    """`run_dir`'s `manifest.json`, or `(None, reason)` when it cannot be trusted.

    `RunManifest` is `schema_version: 5` with `extra="forbid"` -- a manifest
    written by an older Hardy, or one damaged mid-write, fails
    `model_validate_json` with a `pydantic.ValidationError`, which is a
    `ValueError` subclass, exactly like a missing file's `FileNotFoundError`
    is an `OSError` and a planted symlink's `LayoutError`
    (`foundation/files.py:17`) is a `ValueError` subclass too. All three are
    "this run's manifest cannot be read" for this function's purposes, and
    are reported as the same thing: `None` plus why, never a manifest with
    some fields guessed from what did parse.
    """
    try:
        path = resolve_named_child(run_dir / MANIFEST, run_dir)
        manifest = RunManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return None, str(error)
    return manifest, None


def _row(run_dir: Path) -> dict[str, Any]:
    """One list row: every field the brief asks for, or an honest refusal to omit it.

    A run directory that exists and whose manifest cannot be read is not the
    same fact as no run -- silently leaving it out of the list would report a
    smaller, confident-looking count than the truth. So every directory
    `runs()` finds gets a row: `readable` and `error` say whether the rest of
    the row could be filled in, and every other field is `None` -- not a
    guessed default -- when it could not.
    """
    manifest, error = _read_manifest(run_dir)
    if manifest is None:
        return {
            "dir": run_dir.name, "readable": False, "error": error,
            "run_id": None, "created_at": None, "phase": None, "model": None,
            "claim_sha256": None, "grades": None, "tones": None, "terminal_reason": None,
        }
    return {
        "dir": run_dir.name, "readable": True, "error": None,
        "run_id": str(manifest.run_id),
        "created_at": manifest.created_at.isoformat(),
        "phase": manifest.phase.value,
        "model": manifest.model,
        # The page's whole point (Task 5 brief): every check this run made
        # was against exactly this frozen statement, and this hash is how a
        # reader can tell one run's claim from another's without reading the
        # Lean itself. `None` when the run never reached a frozen claim --
        # `RunManifest.claim_sha256` is itself optional for exactly that case.
        "claim_sha256": manifest.claim_sha256,
        # `model_dump(mode="json")` renders every `str, Enum` field (`formal`,
        # `faithfulness`, `informal`, `document`) as its exact `.value`, the
        # same thing `workspace.py:167` already does for a `Bibliography`
        # entry -- pydantic's own serialization, not a hand-built dict that
        # could drift from `Grades`'s real fields.
        "grades": manifest.grades.model_dump(mode="json"),
        "tones": _grade_tones(manifest.grades),
        "terminal_reason": manifest.terminal_reason.value if manifest.terminal_reason is not None else None,
    }


def runs(config: Config) -> dict[str, Any]:
    """Every run under `config.runs_root`, one row per directory, newest first.

    A fresh project, or one where `HARDY_RUNS_ROOT` names nothing yet, has no
    `runs_root` at all -- an honest empty list, matching `ledger_list`'s own
    fresh-project case (issue #171's test), not an error. A `runs_root` that
    is itself a symlink is refused the same way: reported as no runs found,
    never followed.

    Sorted by directory name, descending: `RunStore.create` names each
    directory `<timestamp>-<slug>-<runid8>` with a sortable
    `%Y%m%dT%H%M%S%z` prefix (`workflows/storage.py:66-67`), so this is newest
    first without opening a single manifest to find out -- which matters
    because opening one is exactly the operation `_row` above may have to
    report as failed.
    """
    root = config.runs_root
    if not root.is_dir() or root.is_symlink():
        return {"runs": []}
    rows = [_row(child) for child in root.iterdir() if child.is_dir() and not child.is_symlink()]
    rows.sort(key=lambda row: row["dir"], reverse=True)
    return {"runs": rows}


def _locate_run_dir(runs_root: Path, run_id: UUID) -> Path:
    """`runs_root`'s own child whose name ends in this run's 8-hex suffix, proven real.

    Mirrors `cli.py:828-833`'s `_find_run_dir`: `run_id` is only ever
    compared against filenames `runs_root.iterdir()` already produced, never
    joined into a path, so it cannot address anything outside `runs_root`.
    The one addition over `cli.py`'s version is the symlink check plus
    `resolve_named_child` at the end -- `cli.py`'s caller is a run `hardy
    prove`/`batch` just created in-process and trusts; this one answers
    whatever `run_id` a browser request named.
    """
    if not runs_root.is_dir() or runs_root.is_symlink():
        raise ValueError(f"no runs directory at {runs_root}")
    resolved_root = runs_root.resolve()
    suffix = "-" + run_id.hex[:8]
    candidates = [
        child for child in runs_root.iterdir()
        if child.is_dir() and not child.is_symlink() and child.name.endswith(suffix)
    ]
    if len(candidates) != 1:
        raise ValueError(f"no run found for {run_id}")
    return resolve_named_child(candidates[0], resolved_root)


def _statement(claim: FrozenClaim) -> str:
    """The frozen claim as one Lean signature, exactly as the verifier states it.

    The same rendering `formal/lean.py`'s `render_source` builds (minus the
    trailing `:=` that opens the proof) and `workflows/recorded.py` requires
    a paper to quote verbatim: `theorem <name> <binders> : <proposition>`,
    with no binder slot at all when the proposal has no binders. Restated
    here rather than imported because `lean.py` renders a whole source file,
    proof included, and this page has no proof to hand it.
    """
    proposal = claim.proposal
    binders = f" {proposal.binders.strip()}" if proposal.binders.strip() else ""
    return f"theorem {proposal.theorem_name}{binders} : {proposal.proposition.strip()}"


def _frozen_claim(run_dir: Path, manifest: RunManifest) -> tuple[dict[str, Any] | None, str | None]:
    """The statement `manifest.claim_sha256` is the hash of, or `(None, why not)`.

    Issue #174: the detail page's whole point is the frozen statement beside
    its hash, and the hash alone says only that *something* was pinned. The
    text lives in `formalization.json`, so this reads it -- under three
    proofs, because a statement shown beside a hash it is not the hash of
    would be exactly the fabrication the page exists to refuse:

    - `manifest.claim_sha256` is `None`: the run never reached an approved
      formalization, so there is no statement to look for. `(None, None)` --
      the caller renders *does not apply*, not a complaint, and the file is
      not even opened.
    - The file is missing, a planted symlink, unreadable, or does not parse
      as a `FrozenClaim`: `(None, reason)`. The manifest promised a claim
      this run directory does not carry, and *this run* says so, rather than
      the page implying the text is never served.
    - The file parses but its `content_hash` is not the manifest's, or the
      claim re-frozen from its own fields through `freeze_claim` (as
      `prove.py` re-checks it on write, "persisted Frozen Claim hash
      mismatch") is not equal to it, field for field: `(None, reason)`. The
      first catches a file from another run; the second catches a statement
      edited under a hash it no longer earns, including a field the hash
      never covered. `validate_run_consistency`
      (`workflows/recorded.py`) makes the first check for `hardy accept
      --recorded`; the second is the one a browser-facing read owes on top.
    """
    if manifest.claim_sha256 is None:
        return None, None
    try:
        path = resolve_named_child(run_dir / FORMALIZATION, run_dir)
        claim = FrozenClaim.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        # `validate_run_consistency`'s own words for the same finding; the
        # run's `dir` is already on the page, so the path adds nothing.
        return None, f"{FORMALIZATION} is missing"
    except (OSError, ValueError) as error:
        return None, f"{FORMALIZATION} could not be read: {error}"
    if claim.content_hash != manifest.claim_sha256:
        return None, (
            f"{FORMALIZATION} carries hash {claim.content_hash}, which differs from the manifest's "
            f"{manifest.claim_sha256}; it is not the statement this run's checks were against"
        )
    refrozen = freeze_claim(
        claim.original_text, claim.proposal, claim.environment, claim.approved_at,
        semantic_context=claim.semantic_context,
    )
    # Compared whole, not hash to hash: `freeze_claim` rebuilds `imports`
    # from `environment.imports` and hashes those, so a file whose top-level
    # `imports` were edited still re-freezes to the hash it carries -- and
    # `imports` is exactly what the page serves beside it. Model equality
    # covers every field, hashed or not (Codex, PR #179).
    if refrozen != claim:
        return None, (
            f"{FORMALIZATION} does not re-freeze to the claim it carries under hash "
            f"{claim.content_hash}; its text is not the one that hash was taken over"
        )
    return {
        "content_hash": claim.content_hash,
        # `request.text` as the user gave it -- `prove.py` freezes exactly
        # that as `original_text` -- and the model's own reading of it.
        "original_text": claim.original_text,
        "restatement": claim.proposal.restatement,
        "theorem_name": claim.proposal.theorem_name,
        "binders": claim.proposal.binders,
        "proposition": claim.proposal.proposition,
        "statement": _statement(claim),
        "imports": list(claim.imports),
        "approved_at": claim.approved_at.isoformat(),
    }, None


def _trajectory(run_dir: Path, run_id: UUID) -> list[dict[str, Any]] | None:
    """Every `trajectory.jsonl` event in order, or `None` when the file cannot be trusted.

    Mirrors `chats._activity`'s rule for a chat transcript (issue #169): a run
    that has not appended anything yet has no `trajectory.jsonl` at all,
    which is an honest `[]`, not "unknown". A file that exists and holds one
    line that does not parse as a genuine `TrajectoryEvent` belonging to this
    exact run bails out entirely instead, rather than returning however many
    events happened to parse before the bad line -- which would look like a
    complete trajectory and would not be one.
    """
    try:
        path = resolve_named_child(run_dir / TRAJECTORY, run_dir)
    except LayoutError:
        return None
    if not path.exists():
        return []
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    events: list[TrajectoryEvent] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            event = TrajectoryEvent.model_validate_json(line)
        except ValueError:
            return None
        if event.run_id != run_id:
            return None
        # `RunStore.open()` requires an event's sequence to be its position:
        # 0, 1, 2, ... A line deleted, duplicated or reordered leaves every
        # surviving event valid on its own, so without this the page would
        # present an incomplete or shuffled experimental history as intact.
        # The whole trajectory is refused rather than the offending line
        # skipped -- a run's trajectory is one account, and an account with a
        # hole in it is not a shorter account, it is an unreliable one. The
        # caller renders *not reported*, which is the honest answer.
        if event.sequence != len(events):
            return None
        events.append(event)
    return [
        {
            "sequence": event.sequence, "timestamp": event.timestamp.isoformat(),
            "phase": event.phase.value, "kind": event.kind, "payload": event.payload,
        }
        for event in events
    ]


def run_item(config: Config, run_id: str) -> dict[str, Any]:
    """One run's manifest, in full, plus its frozen statement and its trajectory.

    `run_id` must parse as a `UUID` and must name a run whose manifest can be
    read; both refuse with `ValueError`, which `server.py`'s existing 400
    path already turns into a clean refusal for `ledger_item`/`ledger_export`
    (Tasks 3 and 4) -- an unknown or malformed `run_id` refuses the same way
    here.

    Unlike `runs()`, which must still show a run it cannot read (a run that
    exists and cannot be read is not the same fact as no run), this is a
    detail page *for* one run: there is nothing to detail if the one artifact
    naming what happened cannot be parsed, so this refuses outright rather
    than returning a page with every field `None`.

    `_locate_run_dir` matches purely on the trailing 8 hex characters of the
    directory name (mirroring `cli.py`), so once a manifest is actually
    parsed this checks it names the exact `run_id` that was asked for, not
    only a directory whose name suggests it does. Normal operation can never
    diverge -- `RunStore.create` names a directory from its own `run_id`
    (`workflows/storage.py:66-67`), and a genuine suffix collision between two
    still-present directories is already refused by `_locate_run_dir`'s own
    `len(candidates) != 1` check -- but a directory hand-corrupted, half
    migrated, or restored from elsewhere could have a name and a manifest
    that disagree, and a page whose whole point is *which exact run* a
    verdict belongs to must not serve that silently. The same defence
    `_trajectory` already applies per event (`event.run_id != run_id`) one
    level down.
    """
    parsed = UUID(str(run_id))
    run_dir = _locate_run_dir(config.runs_root, parsed)
    manifest, error = _read_manifest(run_dir)
    if manifest is None:
        raise ValueError(f"the manifest for run {run_id} could not be read: {error}")
    if manifest.run_id != parsed:
        raise ValueError(
            f"run directory {run_dir.name!r} names {parsed} but its manifest names {manifest.run_id}"
        )
    claim, claim_error = _frozen_claim(run_dir, manifest)
    return {
        "run_id": str(manifest.run_id),
        "dir": run_dir.name,
        "created_at": manifest.created_at.isoformat(),
        "phase": manifest.phase.value,
        "model": manifest.model,
        "prompt_set_sha256": manifest.prompt_set_sha256,
        "claim_sha256": manifest.claim_sha256,
        # The statement that hash is the hash of, proven so (`_frozen_claim`),
        # or `claim_error` saying why this run cannot show it. Both `None`
        # exactly when `claim_sha256` is: no claim was ever frozen.
        "claim": claim,
        "claim_error": claim_error,
        "limits": manifest.limits.model_dump(mode="json"),
        "environment": manifest.environment.model_dump(mode="json") if manifest.environment is not None else None,
        "grades": manifest.grades.model_dump(mode="json"),
        "tones": _grade_tones(manifest.grades),
        "terminal_reason": manifest.terminal_reason.value if manifest.terminal_reason is not None else None,
        "artifacts": dict(manifest.artifacts),
        "timings_ms": dict(manifest.timings_ms),
        # `Usage.summary`'s own shape (`agents/usage.py:319-340`, cited by
        # `RunManifest.usage`'s docstring, `contracts.py:356-360`): each
        # figure is `None` where nothing was reported, never coerced to 0.
        # Passed through unchanged -- there is nothing here that needs
        # reshaping, and reshaping it would be the one place that risked
        # turning an unreported figure into a reported zero.
        "usage": dict(manifest.usage),
        "trajectory": _trajectory(run_dir, parsed),
    }
