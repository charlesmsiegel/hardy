"""Check a root's problems against each other, the files on disk, and the status rules.

A root holds one or more problems (`docs/reference/on-disk-layout.md`): a
problem is a subdirectory with a record (`session.json`) or a ledger. Each
problem's ledger is read through Hardy's own store, so a damaged chain is
reported here as a failure of that problem rather than failing in a session; a
recorded problem with no ledger yet is listed and not failed; and nothing here
writes a ledger.

What it enforces, per problem:

- every assessed item carries a status from the vocabulary below;
- no item assessed above `open` depends on an item that is `open`, following
  a mirror to the item it mirrors, through as many mirrors as it takes;
- a `uses` relation targets an external result;
- the `depends_on`/`uses` graph is acyclic;
- a mirror of another problem's item (status `imported`, semantics naming the
  upstream problem, item, digest and status) names a problem of this root that
  is checked first, and an item that still exists there with the same digest
  and status; the problem graph itself is acyclic;
- every `lean_declaration` semantic names a declaration under the problem's
  own `lean/` or the root's shared `.hardy/lean/`, by its qualified name or a
  suffix of it (`Foo.bar` is named by `Foo.bar` or `bar`, never by `Baz.bar`);
- every artifact an item or a relation references by a path relative to the
  problem, directly or behind its evidence, still matches the bytes on disk,
  whether or not the item is assessed; the path must name a file inside the
  root (a mirror names the upstream problem's ledger record as
  `../<problem>/ledger/...`), read through the same guard every project read
  goes through, so a path that leaves the root, an absolute path, or a
  symlink is refused rather than hashed; an artifact named by a URI with a
  scheme (`arxiv:`, `manuscript:`, `file:`) belongs to the store that issued
  it and is not read here, and a Windows drive (`C:\\...`) is a path, not a
  scheme, so it is refused as absolute;
- a `lean verified` item has a resolved `prove` obligation on its current
  revision whose acceptance the problem's own evidence readers authenticate
  from its `evidence/` journal, and whose formal evidence describes the Lean
  sources as they stand: the module the kernel checked, and every module of
  the problem it imports, is still under `lean/` or `.hardy/lean/` with the
  digest the evidence recorded, so an edit that keeps a declaration's name
  and changes what it says, or what it rests on, is not verified by the old
  record; one such obligation suffices, and an open duplicate beside it is
  not a failure; `human verified` has no evidence mechanism yet and is
  refused. The shared sources and the toolchain are in the evidence's build
  signature, which only a save with a workspace recomputes; the check binds
  what it can read.

Lean sources are read as the workspace reads them: a symlink anywhere under
`lean/` or `.hardy/lean/` is refused, since a declaration or a digest read
through it would come from outside the problem.

A problem whose mirrors form a cycle with another's is still checked, after
the problems that could be ordered: its own rules hold whatever the order,
and only a mirror of a problem not yet checked is reported as unordered.

Relations are read at their current endpoints: a relation names its endpoints
by stable id, and the check is of the board as it stands, so a relation that
pins an older revision of an item is compared against that item's current
status, not the status it had when the relation was recorded.

The research-status vocabulary is the ladder `open`, `llm proved`,
`human verified`, `lean verified`, and the three input statuses
`published input`, `external research input` and `imported`. No status
certifies truth; the check enforces the rules between them, not the truth of
any claim.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from hardy.formal.syntax import module_path, named_declarations
from hardy.foundation.files import LayoutError, files_under, read_bytes, read_text
from hardy.foundation.paths import HARDY_DIR
from hardy.workflows.interactive import evidence as evidence_owner
from hardy.workflows.layout import RECORD
from hardy.workflows.ledger.contracts import (
    ArtifactRef,
    Obligation,
    ObligationKind,
    ObligationStatus,
    ProjectItem,
    ProjectItemKind,
    Relation,
    RelationKind,
    Resolution,
)
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.store import LedgerStore

STATUSES = ("open", "llm proved", "human verified", "lean verified")
INPUT_STATUSES = ("published input", "external research input", "imported")
RANK = {status: rank for rank, status in enumerate(STATUSES)}
LEDGER_DIR = "ledger"
LEAN_DIR = "lean"
#: A URI with a scheme names something a store issued, not a file beside the ledger.
#: Two characters at least: a single letter before the colon is a Windows drive
#: (`C:\Users\...`, `C:notes.md`), and reading one as a scheme skipped the
#: artifact altogether -- neither refused as outside the root nor hashed.
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]+:")
_CLASSES = {"open": "open", "llm proved": "llm", "human verified": "human", "lean verified": "lean",
            "imported": "imported", "published input": "input", "external research input": "input"}


@dataclass(frozen=True)
class ProblemReport:
    """One problem's board and failures; `mermaid` is its dependency graph."""

    slug: str
    revision: int
    board: tuple[tuple[str, int], ...]
    errors: tuple[str, ...]
    mermaid: str
    ledger: bool = True

    @property
    def board_line(self) -> str:
        if not self.ledger:
            return f"{self.slug}: no ledger yet"
        counts = ", ".join(f"{k}: {v}" for k, v in self.board)
        return f"{self.slug}: ledger revision {self.revision}; {counts}"


@dataclass(frozen=True)
class RootReport:
    """Every problem in the order it was checked; `cycle` names those whose mirrors could not be ordered."""

    order: tuple[str, ...]
    problems: tuple[ProblemReport, ...]
    cycle: tuple[str, ...] = ()

    @property
    def failures(self) -> tuple[str, ...]:
        found = [f"{p.slug}: {e}" for p in self.problems for e in p.errors]
        if self.cycle:
            found.append("problem dependencies form a cycle: " + ", ".join(self.cycle))
        return tuple(found)

    @property
    def ok(self) -> bool:
        return not self.failures

    def lines(self, *, mermaid: bool = False) -> list[str]:
        out = ["problem order: " + " -> ".join(self.order)] if self.order else []
        for problem in self.problems:
            out.append(problem.board_line)
            if mermaid:
                out.append(problem.mermaid)
        out.extend(self.failures)
        out.append("checks passed" if self.ok else f"{len(self.failures)} check(s) failed")
        return out


def problems(root: Path) -> list[Path]:
    """The problems of a root: its subdirectories that hold a record or a ledger."""
    return sorted(p for p in Path(root).iterdir()
                  if p.is_dir() and not p.name.startswith(".")
                  and ((p / RECORD).is_file() or (p / LEDGER_DIR).is_dir()))


def lean_declarations(problem: Path) -> tuple[set[str], list[str]]:
    """Qualified names of every declaration under the problem's `lean/` and the root's `.hardy/lean/`.

    Returns the names and the sources that could not be read, each a failure
    of the problem; a source that does not read leaves the names it declares
    unknown, so the declaration checks are withheld rather than failed.
    """
    names: set[str] = set()
    unreadable: list[str] = []
    for tree in lean_trees(problem):
        if not tree.is_dir():
            continue
        try:
            found = files_under(tree, ".lean")
        except (LayoutError, OSError) as error:
            unreadable.append(f"lean tree {tree.relative_to(problem.parent).as_posix()} does not read: {error}")
            continue
        for relative in found:
            try:
                names.update(named_declarations(read_text(tree, relative)))
            except (LayoutError, OSError, UnicodeDecodeError) as error:
                unreadable.append(
                    f"lean source {(tree / relative).relative_to(problem.parent).as_posix()} does not read: {error}"
                )
    return names, unreadable


def lean_trees(problem: Path) -> tuple[Path, Path]:
    """Where a problem's Lean sources live: its own `lean/`, then the root's shared `.hardy/lean/`."""
    return problem / LEAN_DIR, problem.parent / HARDY_DIR / LEAN_DIR


def declares(declared: set[str], name: str) -> bool:
    """Whether `name` names one of `declared`: the qualified name itself, or a suffix of it."""
    return name in declared or any(full.endswith("." + name) for full in declared)


def status_of(item: ProjectItem) -> str | None:
    return item.research.status if item.research else None


def effective_status(item: ProjectItem, heads: dict[str, dict[str, ProjectItem]]) -> str | None:
    """The ladder status behind an item, following a mirror to what it mirrors.

    A mirror of a mirror records `imported` as its upstream status, so one
    metadata hop stops short of the ladder; the upstream problems are checked
    first, so their current items are at hand to follow instead. A mirror
    whose source cannot be found falls back to the status it recorded.
    """
    seen: set[tuple[str, str]] = set()
    current = item
    while status_of(current) == "imported":
        meta = dict(current.semantics)
        key = (meta.get("upstream_problem", ""), meta.get("upstream_item", current.id))
        source = heads.get(key[0], {}).get(key[1])
        if source is None or key in seen:
            return meta.get("upstream_status")
        seen.add(key)
        current = source
    return status_of(current)


def _label(text: str) -> str:
    """A name or id as Mermaid quoted-label text, unable to close the label or the fence.

    Mermaid reads `#quot;`, `#lt;` and `#gt;` as the characters themselves, so
    the text is shown as written; backticks go, since the graph sits in a
    Markdown fence, and a newline becomes a space.
    """
    text = " ".join(text.split())
    return (text.replace("`", "").replace('"', "#quot;")
            .replace("<", "#lt;").replace(">", "#gt;"))


def mermaid(slug: str, items: dict[str, ProjectItem], relations: list[Relation]) -> str:
    """The `depends_on`/`uses` graph as a Mermaid flowchart, prerequisite to dependent.

    Node identifiers are positional (`n0`, `n1`, ...) rather than derived from
    the ids, so two ids that differ only in punctuation stay two nodes; labels
    are escaped so a name cannot inject markup or close the fence.
    """
    lines = ["```mermaid", "flowchart LR",
             f"%% {slug}: prerequisite --> dependent; from `hardy check --mermaid`",
             "classDef open fill:#f8d7da,stroke:#842029,color:#842029;",
             "classDef llm fill:#fff3cd,stroke:#997404,color:#664d03;",
             "classDef human fill:#cfe2ff,stroke:#084298,color:#052c65;",
             "classDef lean fill:#d1e7dd,stroke:#0f5132,color:#0a3622;",
             "classDef imported fill:#e2e3e5,stroke:#41464b,color:#41464b,stroke-dasharray: 4 2;",
             "classDef input fill:#f8f9fa,stroke:#adb5bd,color:#495057,stroke-dasharray: 2 2;"]
    used = {r.source.id for r in relations} | {r.target.id for r in relations}
    node = {id_: f"n{index}" for index, id_ in enumerate(sorted(used))}
    for id_ in sorted(used):
        item = items.get(id_)
        label = _label(item.name if item is not None else id_)
        lines.append(f'  {node[id_]}["{_label(id_)}<br/>{label}"]')
        status = status_of(item) if item is not None else None
        if status in _CLASSES:
            lines.append(f"  class {node[id_]} {_CLASSES[status]};")
    for rel in sorted(relations, key=lambda r: (r.target.id, r.source.id)):
        arrow = "-->" if rel.kind == RelationKind.DEPENDS_ON else "-.->"
        lines.append(f"  {node[rel.target.id]} {arrow} {node[rel.source.id]}")
    lines.append("```")
    return "\n".join(lines)


def read_ledger(problem: Path) -> LedgerSnapshot | str:
    """The problem's ledger, or the reason it does not read.

    A damaged chain (a sequence gap, a digest mismatch, malformed JSON) is
    what the check exists to catch, so it is reported as that problem's
    failure rather than ending the check before the other problems are seen.
    """
    try:
        return LedgerStore(problem).read()
    except (ValueError, OSError) as error:
        return f"ledger does not read: {error}"


def _upstreams(snapshot: LedgerSnapshot) -> set[str]:
    return {dict(i.semantics).get("upstream_problem", "") for i in snapshot.current(ProjectItem)
            if i.research and i.research.status == "imported"}


def order_problems(found: list[Path]) -> tuple[list[Path], list[str]]:
    """Upstream problems first, so a mirror can be compared with its source.

    Only dependencies among the problems found count towards the order: a
    mirror naming a problem that is not under this root is that problem's
    failure, reported when it is checked, not a reason to leave it unchecked.
    Returns the order and, when the mirrors among found problems form a
    cycle, the problems left unordered.
    """
    known = {problem.name for problem in found}
    needs: dict[Path, set[str]] = {}
    for problem in found:
        snapshot = read_ledger(problem)
        needs[problem] = (_upstreams(snapshot) & known) if not isinstance(snapshot, str) else set()
    ordered: list[Path] = []
    while needs:
        done = {q.name for q in ordered}
        ready = [p for p, need in needs.items() if need <= done]
        if not ready:
            return ordered, sorted(p.name for p in needs)
        for problem in ready:
            ordered.append(problem)
            del needs[problem]
    return ordered, []


def artifacts_of(record: ProjectItem | Relation) -> list[ArtifactRef]:
    """Every artifact a record references: its own, and those behind its evidence.

    An item's research assessment carries evidence of its own, and a
    relation's evidence is what backs the relation; a file any of them names
    is as much the record's as one in its `artifacts`.
    """
    found = list(record.artifacts) + [e.artifact for e in record.evidence]
    if isinstance(record, ProjectItem) and record.research is not None:
        found.extend(e.artifact for e in record.research.evidence)
    return found


def within_root(problem: Path, uri: str) -> PurePosixPath | None:
    """`uri`, relative to the problem, as a path relative to the root; None if it leaves the root.

    A mirror names the upstream problem's ledger record as
    `../<problem>/ledger/<sequence>.json`, which is inside the root and is
    what the check exists to compare; `../../elsewhere` and an absolute path
    are not, and a file there matching the digest says nothing about the root.
    """
    # `anchor` catches every Windows form a POSIX reading misses -- `C:\x`,
    # the drive-relative `C:x`, `\\server\share` -- whichever platform reads
    # the ledger: a path written on one machine is checked on another.
    if uri.startswith(("/", "\\")) or PureWindowsPath(uri).anchor:
        return None
    parts: list[str] = []
    for part in (PurePosixPath(problem.name) / uri.replace("\\", "/")).parts:
        if part == "..":
            if not parts:
                return None
            parts.pop()
        elif part != ".":
            parts.append(part)
    return PurePosixPath(*parts) if len(parts) > 1 else None


def _artifact_errors(problem: Path, owner: str, artifacts: list[ArtifactRef]) -> list[str]:
    """The artifacts that are missing, changed, outside the root, or unreadable.

    Read through the same guard as every project file, rooted at the root:
    a path that leaves it, an absolute path, or a symlink anywhere on the
    way is refused rather than hashed.
    """
    errors = []
    for ref in artifacts:
        if SCHEME.match(ref.uri):
            continue
        relative = within_root(problem, ref.uri)
        if relative is None:
            errors.append(f"{owner}: artifact {ref.uri} is not a file inside the root")
            continue
        try:
            content = read_bytes(problem.parent, relative)
        except FileNotFoundError:
            errors.append(f"{owner}: artifact {ref.uri} is missing")
        except LayoutError as error:
            errors.append(f"{owner}: artifact {ref.uri} is not a file inside the root: {error}")
        except OSError as error:
            errors.append(f"{owner}: artifact {ref.uri} does not read: {error}")
        else:
            if hashlib.sha256(content).hexdigest() != ref.digest:
                errors.append(f"{owner}: artifact {ref.uri} changed since it was recorded")
    return errors


def source_binding(problem: Path, owners: evidence_owner.ProjectOwners, resolution: Resolution) -> str | None:
    """Why the resolution's formal evidence no longer describes the Lean sources on disk, or None.

    The evidence records the module the kernel checked, every module of the
    problem it imports, and the digest of each source as verified; a module
    edited since, whether or not a declaration kept its name, is not what was
    verified. Each is looked for under the problem's `lean/`, then the root's
    `.hardy/lean/`, and read through the workspace's own guard, so a symlink
    is refused and the digests compare like for like. A record from before
    the inputs were kept binds its own module only.
    """
    for reference in resolution.evidence:
        record = owners.formal_record(reference)
        if record is None:
            return "its evidence does not authenticate"
        for module, digest in record.inputs or ((record.module, record.source_sha256),):
            against = "" if module == record.module else f" against {module}"
            relative = module_path(module)
            tree = next((t for t in lean_trees(problem) if (t / relative).is_file()), None)
            if tree is None:
                return f"the kernel checked {record.module}{against}, which is no longer under lean/ or .hardy/lean/"
            try:
                source = read_text(tree, relative)
            except (LayoutError, OSError, UnicodeDecodeError) as error:
                return f"the kernel checked {record.module}{against}, which does not read: {error}"
            if hashlib.sha256(source.encode("utf-8")).hexdigest() != digest:
                return f"the kernel checked {record.module}{against}, which has changed since"
    return None


def cycles(nodes: list[str], edges: dict[str, set[str]]) -> list[str]:
    """Every dependency cycle, one line each, found without recursion.

    A depth-first walk on an explicit stack, so a chain as long as a board
    can grow is walked without touching the interpreter's recursion limit.
    Each node is entered once; a back edge to a node still on the path is a
    cycle, reported as the path from that node round to itself, without the
    nodes that only led into it. Successors
    are taken in name order, from the end of a reversed list, so a node of
    high degree costs its degree and not its square.
    """
    found: list[str] = []
    state: dict[str, int] = {}
    for start in nodes:
        if state.get(start) == 2:
            continue
        path: list[str] = [start]
        pending: list[list[str]] = [sorted(edges.get(start, ()), reverse=True)]
        state[start] = 1
        while path:
            if pending[-1]:
                nxt = pending[-1].pop()
                if state.get(nxt) == 1:
                    found.append("dependency cycle: " + " -> ".join((*path[path.index(nxt):], nxt)))
                elif state.get(nxt) != 2:
                    state[nxt] = 1
                    path.append(nxt)
                    pending.append(sorted(edges.get(nxt, ()), reverse=True))
            else:
                state[path.pop()] = 2
                pending.pop()
    return found


def check_problem(problem: Path, heads: dict[str, dict[str, ProjectItem]],
                  known: set[str]) -> ProblemReport:
    """Check one problem.

    `heads` carries the current items of every problem checked before it and
    `known` the names of every problem under the root.
    """
    if not (problem / LEDGER_DIR).is_dir():
        heads[problem.name] = {}
        return ProblemReport(slug=problem.name, revision=0, board=(), errors=(), mermaid="", ledger=False)
    snapshot = read_ledger(problem)
    if isinstance(snapshot, str):
        heads[problem.name] = {}
        return ProblemReport(slug=problem.name, revision=0, board=(), errors=(snapshot,), mermaid="")
    errors: list[str] = []
    items = {r.id: r for r in snapshot.current(ProjectItem)}
    all_relations = snapshot.current(Relation)
    relations = [r for r in all_relations if r.kind in {RelationKind.DEPENDS_ON, RelationKind.USES}]
    heads[problem.name] = items
    declared, unreadable = lean_declarations(problem)
    errors.extend(unreadable)
    owners = evidence_owner.ProjectOwners.reading(problem)
    policy = owners.policy
    obligations = snapshot.current(Obligation)

    for id_, item in items.items():
        for key, value in item.semantics:
            if key == "lean_declaration" and not unreadable and not declares(declared, value):
                errors.append(f"{id_}: lean_declaration {value} is not declared under lean/ or .hardy/lean/")
        errors.extend(_artifact_errors(problem, id_, artifacts_of(item)))
        status = status_of(item)
        if status is None:
            continue
        if status not in STATUSES and status not in INPUT_STATUSES:
            errors.append(f"{id_}: status {status!r} is not in the vocabulary")
        if status == "human verified":
            errors.append(f"{id_}: human verified has no evidence mechanism here yet")
        if status == "lean verified":
            proofs = [o for o in obligations if o.item == item.ref and o.kind is ObligationKind.PROVE]
            resolved = [o for o in proofs
                        if o.status is ObligationStatus.RESOLVED and o.resolution is not None]
            if not proofs:
                errors.append(f"{id_}: lean verified without a prove obligation on its current revision")
            elif not resolved:
                found = ", ".join(sorted({o.status.value for o in proofs}))
                errors.append(f"{id_}: lean verified but its prove obligation is {found}")
            else:
                accepted = [o for o in resolved if policy.is_accepted(snapshot, o.resolution)]
                stale = [source_binding(problem, owners, o.resolution) for o in accepted]
                if not accepted:
                    errors.append(f"{id_}: lean verified but its evidence does not authenticate")
                elif all(stale):
                    errors.append(f"{id_}: lean verified but {stale[0]}")
    for rel in all_relations:
        errors.extend(_artifact_errors(problem, rel.id, artifacts_of(rel)))

    edges: dict[str, set[str]] = defaultdict(set)
    for rel in relations:
        source, target = items.get(rel.source.id), items.get(rel.target.id)
        if source is None or target is None:
            errors.append(f"{rel.id}: relation endpoint is not a current item")
            continue
        edges[source.id].add(target.id)
        s_status = status_of(source)
        if rel.kind == RelationKind.DEPENDS_ON and s_status in RANK:
            effective = effective_status(target, heads)
            if effective in RANK and RANK[effective] == 0 and RANK[s_status] > 0:
                errors.append(f"{source.id} ({s_status}) depends on {target.id}, which is open")
        if rel.kind == RelationKind.USES and target.kind != ProjectItemKind.EXTERNAL_RESULT:
            errors.append(f"{source.id} uses {target.id}, which is not an external result")

    errors.extend(cycles(sorted(items), edges))

    for id_, item in items.items():
        if status_of(item) != "imported":
            continue
        meta = dict(item.semantics)
        absent = [key for key in ("upstream_problem", "upstream_item", "upstream_digest", "upstream_status")
                  if key not in meta]
        if absent:
            errors.append(f"{id_}: mirror lacks the semantics {', '.join(absent)}")
            continue
        upstream = meta["upstream_problem"]
        if upstream not in known:
            errors.append(f"{id_}: mirror names problem {upstream!r}, which is not a problem of this root")
            continue
        if upstream not in heads:
            errors.append(f"{id_}: mirror names problem {upstream!r}, which is not checked before this one")
            continue
        source = heads[upstream].get(meta["upstream_item"])
        if source is None:
            errors.append(f"{id_}: mirror of an item {upstream} no longer holds")
        elif source.digest != meta.get("upstream_digest"):
            errors.append(f"{id_}: mirror is stale; {upstream} now holds {source.digest[:12]}")
        elif status_of(source) != meta.get("upstream_status"):
            errors.append(f"{id_}: upstream status moved to {status_of(source)!r}; refresh the mirror")

    counts: dict[str, int] = defaultdict(int)
    for item in items.values():
        counts[status_of(item) or f"({item.kind.value})"] += 1
    return ProblemReport(slug=problem.name, revision=snapshot.revision,
                         board=tuple(sorted(counts.items())), errors=tuple(errors),
                         mermaid=mermaid(problem.name, items, relations))


def check_root(root: Path) -> RootReport:
    """Check every problem of `root`, upstream problems first.

    The problems whose mirrors could not be ordered are checked last, in name
    order: a problem's own rules do not depend on the order, and a mirror
    of a problem not yet checked is that mirror's failure; the cycle itself
    is the root's.
    """
    found = problems(Path(root))
    ordered, cycle = order_problems(found)
    checked = ordered + [problem for problem in found if problem.name in cycle]
    known = {problem.name for problem in found}
    heads: dict[str, dict[str, ProjectItem]] = {}
    reports = tuple(check_problem(problem, heads, known) for problem in checked)
    return RootReport(order=tuple(p.name for p in checked), problems=reports, cycle=tuple(cycle))
