"""Check a root's problems against each other, the files on disk, and the status rules.

A root holds one or more problems (`docs/reference/on-disk-layout.md`). Each
problem's ledger is read through Hardy's own store, so a damaged chain is
reported here as a failure of that problem rather than failing in a session,
and nothing here writes a ledger.

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
- every artifact an item or a relation references still matches the bytes on
  disk, whether or not the item is assessed;
- a `lean verified` item has a resolved `prove` obligation on its current
  revision whose acceptance the problem's own evidence readers authenticate
  from its `evidence/` journal; one such obligation suffices, and an open
  duplicate beside it is not a failure; `human verified` has no evidence
  mechanism yet and is refused.

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
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from hardy.formal.syntax import named_declarations
from hardy.foundation.paths import HARDY_DIR
from hardy.workflows.interactive import evidence as evidence_owner
from hardy.workflows.ledger.contracts import (
    Obligation,
    ObligationKind,
    ObligationStatus,
    ProjectItem,
    ProjectItemKind,
    Relation,
    RelationKind,
)
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.store import LedgerStore

STATUSES = ("open", "llm proved", "human verified", "lean verified")
INPUT_STATUSES = ("published input", "external research input", "imported")
RANK = {status: rank for rank, status in enumerate(STATUSES)}
LEDGER_DIR = "ledger"
LEAN_DIR = "lean"
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

    @property
    def board_line(self) -> str:
        counts = ", ".join(f"{k}: {v}" for k, v in self.board)
        return f"{self.slug}: ledger revision {self.revision}; {counts}"


@dataclass(frozen=True)
class RootReport:
    """Every problem in dependency order, or the problems whose mirrors form a cycle."""

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
    """The problems of a root: its subdirectories that hold a ledger."""
    return sorted(p for p in Path(root).iterdir()
                  if p.is_dir() and p.name != HARDY_DIR and (p / LEDGER_DIR).is_dir())


def lean_declarations(problem: Path) -> set[str]:
    """Qualified names of every declaration under the problem's `lean/` and the root's `.hardy/lean/`."""
    names: set[str] = set()
    for tree in (problem / LEAN_DIR, problem.parent / HARDY_DIR / LEAN_DIR):
        if not tree.is_dir():
            continue
        for path in sorted(tree.rglob("*.lean")):
            names.update(named_declarations(path.read_text(encoding="utf-8")))
    return names


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


def mermaid(slug: str, items: dict[str, ProjectItem], relations: list[Relation]) -> str:
    """The `depends_on`/`uses` graph as a Mermaid flowchart, prerequisite to dependent.

    Node identifiers are positional (`n0`, `n1`, ...) rather than derived from
    the ids, so two ids that differ only in punctuation stay two nodes.
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
        label = (item.name if item is not None else id_).replace('"', "'")
        lines.append(f'  {node[id_]}["{id_}<br/>{label}"]')
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


def _artifact_errors(problem: Path, owner: str, artifacts) -> list[str]:
    errors = []
    for ref in artifacts:
        path = (problem / ref.uri).resolve()
        if not path.is_file():
            errors.append(f"{owner}: artifact {ref.uri} is missing")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != ref.digest:
            errors.append(f"{owner}: artifact {ref.uri} changed since it was recorded")
    return errors


def check_problem(problem: Path, heads: dict[str, dict[str, ProjectItem]],
                  known: set[str]) -> ProblemReport:
    """Check one problem.

    `heads` carries the current items of every problem checked before it and
    `known` the names of every problem under the root.
    """
    snapshot = read_ledger(problem)
    if isinstance(snapshot, str):
        heads[problem.name] = {}
        return ProblemReport(slug=problem.name, revision=0, board=(), errors=(snapshot,), mermaid="")
    errors: list[str] = []
    items = {r.id: r for r in snapshot.current(ProjectItem)}
    all_relations = snapshot.current(Relation)
    relations = [r for r in all_relations if r.kind in {RelationKind.DEPENDS_ON, RelationKind.USES}]
    heads[problem.name] = items
    declared = lean_declarations(problem)
    policy = evidence_owner.ProjectOwners.reading(problem).policy
    obligations = snapshot.current(Obligation)

    for id_, item in items.items():
        for key, value in item.semantics:
            if key == "lean_declaration" and not declares(declared, value):
                errors.append(f"{id_}: lean_declaration {value} is not declared under lean/ or .hardy/lean/")
        errors.extend(_artifact_errors(problem, id_, item.artifacts))
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
            elif not any(policy.is_accepted(snapshot, o.resolution) for o in resolved):
                errors.append(f"{id_}: lean verified but its evidence does not authenticate")
    for rel in all_relations:
        errors.extend(_artifact_errors(problem, rel.id, rel.artifacts))

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

    state: dict[str, int] = {}

    def visit(node: str, trail: tuple[str, ...]) -> None:
        if state.get(node) == 2:
            return
        if state.get(node) == 1:
            errors.append("dependency cycle: " + " -> ".join((*trail, node)))
            return
        state[node] = 1
        for nxt in sorted(edges.get(node, ())):
            visit(nxt, (*trail, node))
        state[node] = 2

    for node in sorted(items):
        visit(node, ())

    for id_, item in items.items():
        if status_of(item) != "imported":
            continue
        meta = dict(item.semantics)
        upstream = meta.get("upstream_problem", "")
        if upstream not in known:
            errors.append(f"{id_}: mirror names problem {upstream!r}, which is not a problem of this root")
            continue
        if upstream not in heads:
            errors.append(f"{id_}: mirror names problem {upstream!r}, which is not checked before this one")
            continue
        source = heads[upstream].get(meta.get("upstream_item", id_))
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
    """Check every problem of `root`, upstream problems first."""
    found = problems(Path(root))
    ordered, cycle = order_problems(found)
    known = {problem.name for problem in found}
    heads: dict[str, dict[str, ProjectItem]] = {}
    reports = tuple(check_problem(problem, heads, known) for problem in ordered)
    return RootReport(order=tuple(p.name for p in ordered), problems=reports, cycle=tuple(cycle))
