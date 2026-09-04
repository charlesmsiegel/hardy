"""The corpus as a directory: sharded loading, tombstones, manifest, checks.

`corpus/` is data. This module is its only reader and writer, so the rule that
the corpus holds statements only (spec §1) has one place to be enforced.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from ..domain import FrozenModel
from . import taxonomy
from .problems import Entry, ProblemSet

DEFAULT_CORPUS = Path("corpus")

# Directories and files the manifest covers. `measurements/` is deliberately
# absent: re-sweeping a baseline against a new Mathlib revision changes no
# content and must not manufacture a corpus release (spec §3).
# `SCHEMA.md` is absent for the same reason: it is the reader's guide, not
# data, and hashing it would make an edit to a paragraph of prose a content
# release that invalidates every scoreboard bound to the manifest.
CONTENT_DIRS = ("problems", "taxonomy", "fixtures")
CONTENT_FILES = ("sources.json", "analysis-plan.json", "tombstones.json")
# `## 0.1.0 - 2026-09-03 - manifest <64 hex>`. The digest is in the heading
# because comparing version *strings* cannot see an unversioned edit: a shard
# changes while both strings stay put and the gate still passes, which makes a
# published version non-reproducible (spec §3). CHANGELOG.md is not itself a
# content file, so writing the digest into it is a fixed point.
HEAD = re.compile(
    r"^## (\d+\.\d+\.\d+)\s+-\s+(\S+)(?:\s+-\s+manifest\s+([0-9a-f]{64}))?\s*$",
    re.MULTILINE,
)
SEMVER = r"^\d+\.\d+\.\d+$"


class Shard(FrozenModel):
    """One `problems/<NN>.json` file, envelope included.

    Reading only `payload["entries"]` would let a shard declaring a format
    this build cannot read load as if it were this one, and the version it
    declares would mean nothing.
    """

    schema_version: Literal[2]
    corpus_version: str = Field(pattern=SEMVER)
    entries: tuple[Entry, ...] = Field(min_length=1)


class CorpusError(RuntimeError):
    """The corpus on disk is not one a consumer may trust."""


def shard_path(root: Path, code: str) -> Path:
    return root / "problems" / f"{code[:2]}.json"


def load_corpus(root: Path) -> ProblemSet:
    """Concatenate the shards into one validated set.

    The filename must agree with `entry.shard`, or the derivation of the shard
    from `msc[0][:2]` is a fiction nobody checks.
    """
    entries: list[Entry] = []
    seen: dict[str, Path] = {}
    # The whole load is scoped, `ProblemSet` construction included: entries
    # validate their own codes and pydantic revalidates them on the way into
    # the set, so anything outside this block would check a third party's
    # corpus against Hardy's own bundled tables (see `taxonomy.using`).
    with taxonomy.using(root):
        for path in _shards(root):
            for entry in _load_shard(path).entries:
                if entry.id in seen:
                    raise CorpusError(
                        f"duplicate id {entry.id!r} in {path.name} and {seen[entry.id].name}"
                    )
                if entry.shard != path.stem:
                    raise CorpusError(
                        f"{entry.id!r} is filed in {path.name} but belongs in shard {entry.shard}"
                    )
                seen[entry.id] = path
                entries.append(entry)
        try:
            # Cross-shard invariants -- a Lean `name` duplicated across two
            # shards, a twin whose target lives elsewhere -- fail here, not in
            # `_load_shard`, because each shard is individually valid. Raw,
            # this is a pydantic `ValidationError` that walks straight out of
            # `corpus check`, whose job is to report exactly this.
            return ProblemSet(entries=tuple(entries))
        except ValidationError as error:
            raise CorpusError(f"the shards do not form one valid corpus: {error}") from error


TAXONOMY_FILES = ("msc2020.json", "msc-to-arxiv.json")


def _shards(root: Path) -> list[Path]:
    # The taxonomy is corpus data, not Hardy configuration, and entries are
    # validated against *this* corpus's tables. Falling back to Hardy's own
    # would silently classify a third party's corpus by the wrong map, so a
    # corpus without its tables is not loadable at all.
    for name in TAXONOMY_FILES:
        if not (root / "taxonomy" / name).exists():
            raise CorpusError(
                f"missing taxonomy table: {root / 'taxonomy' / name}. The MSC tables are corpus "
                "data covered by the manifest; entries cannot be classified without them"
            )
    shards = sorted((root / "problems").glob("*.json"))
    if not shards:
        raise CorpusError(f"no problem shards under {root / 'problems'}")
    return shards


def _load_shard_scoped(path: Path, root: Path) -> Shard:
    with taxonomy.using(root):
        return _load_shard(path)


def _load_shard(path: Path) -> Shard:
    """Every parse and validation failure becomes a `CorpusError`.

    `check_issues` gathers objections rather than raising them, so a bare
    `KeyError` or `ValidationError` escaping the loader would crash the check
    instead of being reported by it.
    """
    try:
        return Shard.model_validate_json(path.read_text(encoding="utf-8"))
    except taxonomy.MalformedTaxonomy as error:
        raise CorpusError(str(error)) from error
    except (ValidationError, ValueError, OSError) as error:
        raise CorpusError(f"{path.name} is not a readable shard: {error}") from error


def load_sources(root: Path) -> dict[str, dict]:
    """The texts entries cite. Absent until phase 3 populates it."""
    path = root / "sources.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))["sources"]


def source_issues(problems: ProblemSet, sources: dict[str, dict]) -> list[str]:
    """Occurrences citing a text `sources.json` does not carry.

    The primary occurrence decides the field, the source level C6 reports on,
    and which prior results may be antecedents (spec §9.0); a citation
    pointing at no text decides all three from nothing.
    """
    return sorted(
        f"{entry.id!r}: occurrence cites {occurrence.source_id!r}, which is not in sources.json"
        for entry in problems.entries
        for occurrence in entry.occurrences
        if occurrence.source_id not in sources
    )


def load_tombstones(root: Path) -> dict[str, str]:
    path = root / "tombstones.json"
    if not path.exists():
        raise CorpusError(f"missing id registry: {path}")
    issued = json.loads(path.read_text(encoding="utf-8")).get("issued")
    # Checked rather than trusted: `issued: null` or a number returned straight
    # from here made `tombstone_issues` raise `TypeError` on iteration, which
    # `check_issues` does not catch -- a traceback out of the command asked to
    # report the malformed corpus.
    if not isinstance(issued, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in issued.items()
    ):
        raise CorpusError(f"{path.name}: 'issued' must map ids to dates")
    return issued


def registry_issues(issued: dict[str, str], prior_issued: dict[str, str]) -> list[str]:
    """The registry is append-only, which only a comparison can establish.

    Deleting an entry *and* its registry key leaves the current tree
    self-consistent -- live and issued still match -- so a file-level check
    sees nothing. An id that vanishes takes an external citation with it, and
    a date that moves rewrites when an id was issued (spec §2.2).
    """
    issues = [
        f"{id!r} was in the registry and is now gone: ids are issued once and never withdrawn"
        for id in prior_issued
        if id not in issued
    ]
    issues.extend(
        f"{id!r} was issued on {prior_issued[id]} and now reads {issued[id]}: an issue date is history"
        for id in prior_issued
        if id in issued and issued[id] != prior_issued[id]
    )
    return sorted(issues)


def tombstone_issues(problems: ProblemSet, issued: dict[str, str]) -> list[str]:
    """Live ids that were never registered, and issued ids that have vanished.

    The registry lists every id ever issued, so a live entry appearing in it is
    normal. What it prevents is a *new* entry claiming an id whose original
    entry was deleted rather than retired -- which the current-corpus
    uniqueness check cannot see (spec §2.2).
    """
    live = {entry.id: entry for entry in problems.entries}
    issues = [f"{id!r} is not registered in tombstones.json" for id in live if id not in issued]
    issues.extend(
        f"{id!r} was issued but is absent: a freed id must remain as a retired entry"
        for id in issued
        if id not in live
    )
    return sorted(issues)


def _content_paths(root: Path) -> list[Path]:
    paths = [root / name for name in CONTENT_FILES if (root / name).exists()]
    for directory in CONTENT_DIRS:
        paths.extend(sorted((root / directory).rglob("*.json")))
    return sorted(paths)


def manifest_digest(root: Path) -> str:
    """A hash over every content file. `measurements/` is deliberately absent."""
    hasher = hashlib.sha256()
    for path in _content_paths(root):
        # `.as_posix()`, not `str`: `str(Path)` yields backslashes on Windows,
        # so an unchanged corpus would hash differently per platform -- the
        # committed changelog would fail `corpus check` on Windows, and a
        # scoreboard made there could not carry the same corpus identity
        # anywhere else.
        hasher.update(path.relative_to(root).as_posix().encode("utf-8"))
        hasher.update(path.read_bytes())
    return hasher.hexdigest()


def head_of(text: str) -> tuple[str, str, str | None]:
    """`(version, date, manifest digest)` from changelog text.

    The digest is `None` on a heading written before the binding existed,
    which `version_issues` reports.
    """
    match = HEAD.search(text)
    if match is None:
        raise CorpusError("CHANGELOG.md has no version heading")
    return match.group(1), match.group(2), match.group(3)


def changelog_head(root: Path) -> tuple[str, str, str | None]:
    return head_of((root / "CHANGELOG.md").read_text(encoding="utf-8"))


def release_issues(root: Path, prior_changelog: str) -> list[str]:
    """Whether content moved since the previous release without a new version.

    `version_issues` compares two values from the same working tree, so
    updating a shard *and* rewriting the digest on the existing heading passes:
    the same version number then denotes different content, and a published
    release is no longer reproducible from its number. The anchor has to come
    from outside the tree — and the previous release's changelog head already
    carries both its version and the manifest digest it bound, so the merge
    base's `CHANGELOG.md` is the whole input (spec §3's merge-base gate).
    """
    prior_version, _, prior_manifest = head_of(prior_changelog)
    version = corpus_version(root)
    manifest = manifest_digest(root)
    if prior_manifest is None or manifest == prior_manifest:
        return []
    if version == prior_version:
        return [
            f"the corpus content changed since {prior_version} (manifest {prior_manifest[:12]}… is "
            f"now {manifest[:12]}…) but {version} is still the declared version: a released "
            "version must denote one content, so bump it and add a changelog entry citing the ids"
        ]
    if _parts(version) < _parts(prior_version):
        # Rejecting only an *unchanged* version would let 0.1.1 be replaced by
        # 0.0.1: the chronology moves backward, and patch/minor/major stops
        # telling a consumer anything about what changed between two numbers.
        return [
            f"the declared version {version} is below the released {prior_version}: a corpus "
            "version only ever goes up"
        ]
    return []


def _parts(version: str) -> tuple[int, int, int]:
    major, minor, patch = version.split(".")
    return int(major), int(minor), int(patch)


def corpus_version(root: Path) -> str:
    # Scoped: a `Shard` carries `Entry` objects that validate their own codes,
    # so re-parsing outside the corpus's own taxonomy would call a valid
    # external corpus invalid against Hardy's bundled tables.
    versions = {_load_shard_scoped(p, root).corpus_version for p in _shards(root)}
    if len(versions) != 1:
        raise CorpusError(f"shards disagree on corpus_version: {sorted(versions)}")
    return versions.pop()


def version_issues(root: Path) -> list[str]:
    """The release gate: the declared version, the changelog head, and the
    manifest digest the head binds must all agree."""
    declared = corpus_version(root)
    head, _, bound = changelog_head(root)
    issues = []
    if declared != head:
        issues.append(f"corpus_version {declared} does not match the changelog head {head}")
    if bound is None:
        issues.append(
            "the changelog head binds no manifest digest, so an unversioned edit to a shard, "
            "the taxonomy, a fixture or tombstones.json verifies clean (spec section 3); "
            f"write `## {declared} - <date> - manifest {manifest_digest(root)}`"
        )
    elif bound != (actual := manifest_digest(root)):
        issues.append(
            f"the changelog head binds manifest digest {bound}, but the corpus on disk hashes "
            f"to {actual}: content changed without a version and changelog entry"
        )
    return issues


def _gathered(issues: list[str], label: str, produce):
    """Run one check, turning any failure to *read* what it needs into an issue.

    `corpus check` exists to report malformed corpus state, so a missing
    tombstones.json or an unparseable changelog must be listed, not raised as
    a traceback out of the command that was asked about them.
    """
    try:
        issues.extend(produce())
    except (CorpusError, OSError, ValueError, KeyError) as error:
        issues.append(f"{label}: {error}")


def release(root: Path, version: str, notes: list[str], *, today: str) -> list[str]:
    """Bump every shard to `version` and write the changelog head it binds.

    Done by hand this is three steps in an order that is not obvious: the
    manifest covers the content, so the digest can only be computed *after* the
    entries land, and a changelog written first binds a hash of the corpus as
    it was. Every shard must also agree on `corpus_version`, which is easy to
    miss when only one of them gained an entry. Both are mechanical, and a
    mechanical step done by hand a hundred times is a hundred chances to get it
    wrong -- so this does them, in the one order that works.
    """
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise CorpusError(f"a corpus version is three numbers, not {version!r}")
    current, _, _ = head_of((root / "CHANGELOG.md").read_text(encoding="utf-8"))
    if _parts(version) <= _parts(current):
        raise CorpusError(
            f"{version} does not follow {current}: a corpus version only ever goes up. "
            "Correcting entries is a patch, adding them a minor, breaking the schema a major"
        )
    for shard in _shards(root):
        payload = json.loads(shard.read_text(encoding="utf-8"))
        payload["corpus_version"] = version
        shard.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8", newline="\n")

    changelog = root / "CHANGELOG.md"
    text = changelog.read_text(encoding="utf-8")
    entry = "\n".join(f"- {note}" for note in notes) or "- (no note given)"
    section = f"## {version} - {today} - manifest {manifest_digest(root)}\n\n{entry}\n\n"
    at = text.index(f"## {current}")
    changelog.write_text(text[:at] + section + text[at:], encoding="utf-8", newline="\n")
    return check_issues(root)


def check_issues(root: Path) -> list[str]:
    """Every mechanical objection to the corpus on disk, gathered not raised."""
    try:
        problems = load_corpus(root)
    except CorpusError as exc:
        return [str(exc)]
    issues: list[str] = []
    _gathered(issues, "tombstones.json", lambda: tombstone_issues(problems, load_tombstones(root)))
    _gathered(issues, "sources.json", lambda: source_issues(problems, load_sources(root)))
    _gathered(issues, "CHANGELOG.md", lambda: version_issues(root))
    _gathered(issues, "taxonomy", lambda: _code_issues(problems, root))
    return sorted(issues)


def _code_issues(problems: ProblemSet, root: Path) -> list[str]:
    issues: list[str] = []
    with taxonomy.using(root):
        for entry in problems.entries:
            for code in entry.msc:
                if not taxonomy.is_known(code):
                    issues.append(f"{entry.id!r}: unknown MSC code {code!r}")
                    continue
                # Every roll-up the corpus actually needs. Checking only that
                # the full code exists lets a manifest-bound but incomplete
                # mapping pass, and `corpus report` then raises `UnknownCode`
                # on the very corpus this call just declared clean.
                for table, lookup in (("fields", taxonomy.field_of),
                                      ("groups", taxonomy.group_of),
                                      ("arxiv", taxonomy.arxiv_of)):
                    try:
                        lookup(code)
                    except taxonomy.UnknownCode:
                        issues.append(
                            f"{entry.id!r}: {code!r} has no {table} entry for its class "
                            f"{code[:2]!r} in taxonomy/msc-to-arxiv.json"
                        )
    return issues


def report(root: Path) -> list[str]:
    """Coverage: where the corpus actually is, by group, status and difficulty."""
    problems = load_corpus(root)
    with taxonomy.using(root):
        groups = Counter(taxonomy.group_of(e.msc[0]) for e in problems.entries)
    statuses = Counter(e.status for e in problems.entries)
    difficulties = Counter(e.difficulty for e in problems.entries)
    twins = sum(1 for e in problems.entries if e.expected == "false")
    sources = Counter(o.source_id for e in problems.entries for o in e.occurrences)
    lines = [f"{len(problems.entries)} entries, {twins} twins", ""]
    for label, counter in (
        ("group", groups),
        ("status", statuses),
        ("difficulty", difficulties),
        ("source", sources),
    ):
        lines.append(f"by {label}:")
        lines.extend(f"  {key:<28} {count}" for key, count in sorted(counter.items()))
        if not counter:
            lines.append("  (none)")
        lines.append("")
    return lines
