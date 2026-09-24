"""Check a root's problems against each other, the files on disk, and the status rules.

A root holds one or more problems (`docs/reference/on-disk-layout.md`). Each
problem's ledger is read through Hardy's own store, so a damaged chain fails
here before it fails in a session, and nothing here writes a ledger.

What it enforces, per problem:

- every assessed item carries a status from the vocabulary below;
- no item assessed above `open` depends on an item that is `open`;
- a `uses` relation targets an external result;
- the `depends_on`/`uses` graph is acyclic;
- a mirror of another problem's item (status `imported`, semantics naming the
  upstream problem, item, digest and status) names an item that still exists
  there with the same digest and status, and the problem graph is acyclic;
- every `lean_declaration` semantic names a declaration present in the
  problem's own `lean/` or the root's shared `.hardy/lean/`;
- every artifact reference still matches the bytes on disk;
- a `lean verified` item has a resolved `prove` obligation bound to its current
  revision whose acceptance the problem's own evidence readers authenticate
  from its `evidence/` journal; `human verified` has no evidence mechanism yet
  and is refused.

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
from pathlib import Path

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
from hardy.workflows.ledger.store import LedgerStore

STATUSES = ("open", "llm proved", "human verified", "lean verified")
INPUT_STATUSES = ("published input", "external research input", "imported")
RANK = {status: rank for rank, status in enumerate(STATUSES)}
LEDGER_DIR = "ledger"
LEAN_DIR = "lean"
#: Any top-level declaration keyword: a `lean_declaration` semantic may name a
#: definition or an axiom as well as a theorem, so the scanner is broader than
#: the theorem-and-lemma one a save uses.
DECLARATION = re.compile(
    r"^\s*(?:axiom|theorem|lemma|def|abbrev|structure|constant|opaque|noncomputable def)\s+"
    r"([A-Za-z_][A-Za-z0-9_'.]*)",
    re.M,
)
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
    """Bare names of every declaration under the problem's `lean/` and the root's `.hardy/lean/`."""
    names: set[str] = set()
    for tree in (problem / LEAN_DIR, problem.parent / HARDY_DIR / LEAN_DIR):
        if not tree.is_dir():
            continue
        for path in sorted(tree.rglob("*.lean")):
            names.update(name.split(".")[-1]
                         for name in DECLARATION.findall(path.read_text(encoding="utf-8")))
    return names


def status_of(item: ProjectItem) -> str | None:
    return item.research.status if item.research else None


def _node(id_: str) -> str:
    return id_.replace("-", "_").replace(":", "_")


def mermaid(slug: str, items: dict[str, ProjectItem], relations: list[Relation]) -> str:
    """The `depends_on`/`uses` graph as a Mermaid flowchart, prerequisite to dependent."""
    lines = ["```mermaid", "flowchart LR",
             f"%% {slug}: prerequisite --> dependent; from `hardy check --mermaid`",
             "classDef open fill:#f8d7da,stroke:#842029,color:#842029;",
             "classDef llm fill:#fff3cd,stroke:#997404,color:#664d03;",
             "classDef human fill:#cfe2ff,stroke:#084298,color:#052c65;",
             "classDef lean fill:#d1e7dd,stroke:#0f5132,color:#0a3622;",
             "classDef imported fill:#e2e3e5,stroke:#41464b,color:#41464b,stroke-dasharray: 4 2;",
             "classDef input fill:#f8f9fa,stroke:#adb5bd,color:#495057,stroke-dasharray: 2 2;"]
    used = {r.source.id for r in relations} | {r.target.id for r in relations}
    for id_, item in sorted(items.items()):
        if id_ not in used:
            continue
        label = item.name.replace('"', "'")
        lines.append(f'  {_node(id_)}["{id_}<br/>{label}"]')
        status = status_of(item)
        if status in _CLASSES:
            lines.append(f"  class {_node(id_)} {_CLASSES[status]};")
    for rel in sorted(relations, key=lambda r: (r.target.id, r.source.id)):
        arrow = "-->" if rel.kind == RelationKind.DEPENDS_ON else "-.->"
        lines.append(f"  {_node(rel.target.id)} {arrow} {_node(rel.source.id)}")
    lines.append("```")
    return "\n".join(lines)


def _upstreams(problem: Path) -> set[str]:
    snapshot = LedgerStore(problem).read()
    return {dict(i.semantics).get("upstream_problem", "") for i in snapshot.current(ProjectItem)
            if i.research and i.research.status == "imported"}


def order_problems(found: list[Path]) -> tuple[list[Path], list[str]]:
    """Upstream problems first, so a mirror can be compared with its source.

    Returns the order and, when the mirrors form a cycle, the problems left
    unordered.
    """
    needs = {problem: _upstreams(problem) for problem in found}
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


def check_problem(problem: Path, heads: dict[str, dict[str, ProjectItem]]) -> ProblemReport:
    """Check one problem; `heads` carries the current items of every problem checked before it."""
    errors: list[str] = []
    snapshot = LedgerStore(problem).read()
    items = {r.id: r for r in snapshot.current(ProjectItem)}
    relations = [r for r in snapshot.current(Relation)
                 if r.kind in {RelationKind.DEPENDS_ON, RelationKind.USES}]
    heads[problem.name] = items
    declared = lean_declarations(problem)
    policy = evidence_owner.ProjectOwners.reading(problem).policy
    obligations = snapshot.current(Obligation)

    for id_, item in items.items():
        status = status_of(item)
        if status is None:
            continue
        if status not in STATUSES and status not in INPUT_STATUSES:
            errors.append(f"{id_}: status {status!r} is not in the vocabulary")
        if status == "human verified":
            errors.append(f"{id_}: human verified has no evidence mechanism here yet")
        if status == "lean verified":
            proof = next((o for o in obligations
                          if o.item == item.ref and o.kind is ObligationKind.PROVE), None)
            if proof is None:
                errors.append(f"{id_}: lean verified without a prove obligation on its current revision")
            elif proof.status is not ObligationStatus.RESOLVED or proof.resolution is None:
                errors.append(f"{id_}: lean verified but its prove obligation is {proof.status.value}")
            elif not policy.is_accepted(snapshot, proof.resolution):
                errors.append(f"{id_}: lean verified but its evidence does not authenticate")
        for key, value in item.semantics:
            if key == "lean_declaration" and value.split(".")[-1] not in declared:
                errors.append(f"{id_}: lean_declaration {value} is not declared under lean/ or .hardy/lean/")
        for ref in item.artifacts:
            path = (problem / ref.uri).resolve()
            if not path.is_file():
                errors.append(f"{id_}: artifact {ref.uri} is missing")
            elif hashlib.sha256(path.read_bytes()).hexdigest() != ref.digest:
                errors.append(f"{id_}: artifact {ref.uri} changed since it was recorded")

    edges: dict[str, set[str]] = defaultdict(set)
    for rel in relations:
        source, target = items.get(rel.source.id), items.get(rel.target.id)
        if source is None or target is None:
            errors.append(f"{rel.id}: relation endpoint is not a current item")
            continue
        edges[source.id].add(target.id)
        s_status, t_status = status_of(source), status_of(target)
        if rel.kind == RelationKind.DEPENDS_ON and s_status in RANK:
            effective = t_status
            if t_status == "imported":
                effective = dict(target.semantics).get("upstream_status")
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
    ordered, cycle = order_problems(problems(Path(root)))
    heads: dict[str, dict[str, ProjectItem]] = {}
    reports = tuple(check_problem(problem, heads) for problem in ordered)
    return RootReport(order=tuple(p.name for p in ordered), problems=reports, cycle=tuple(cycle))
