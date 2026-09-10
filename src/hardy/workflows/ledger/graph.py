"""Pure exact-version project queries; graph reachability never establishes truth.

Theory: dependency, lexical context, research and transport are distinct graphs.
Refs never float to record heads. Relation heads describe current sources; the
last relation revision for each historical source preserves its outgoing edges.
Historical snapshots are required to recover earlier edges on an unchanged source.
Reused: immutable A0 records, snapshot lookup, stdlib queues and sets.
Cost: closure is linear in the selected graph; enumerating simple paths can be
exponential. No providers, filesystem reads, or unbounded recursive walks.
Watch: resolution/transport predicates must authenticate capability evidence;
without them even recorded acceptance and research status confer no authority.
"""
from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from hardy.workflows.ledger.contracts import (
    DeclarationRole,
    MathematicalContext,
    Obligation,
    ProjectItem,
    ProjectItemKind,
    Relation,
    RelationKind,
    ScopedBinding,
    VersionRef,
)
from hardy.workflows.ledger.state import LedgerSnapshot

DEPENDENCIES = frozenset({RelationKind.DEPENDS_ON, RelationKind.USES,
                          RelationKind.TYPED_BY, RelationKind.BLOCKED_BY})
TRANSPORT = frozenset({RelationKind.EQUIVALENT_TO, RelationKind.IDENTIFIED_WITH,
                       RelationKind.TRANSPORTED_FROM})
RESEARCH = frozenset({RelationKind.POSES, RelationKind.TARGETS, RelationKind.PURSUES,
                      RelationKind.PRODUCES, RelationKind.BLOCKED_BY,
                      RelationKind.COUNTEREXAMPLE_TO, RelationKind.SUPPORTS})
Resolved = Callable[[Obligation], bool]


def _key(ref: VersionRef) -> tuple[str, str]:
    return ref.id, ref.digest


def _ordered(refs: Iterable[VersionRef]) -> tuple[VersionRef, ...]:
    return tuple(sorted(set(refs), key=_key))


@dataclass(frozen=True)
class ActiveContext:
    context: VersionRef | None
    declarations: tuple[ProjectItem, ...] = ()
    bindings: tuple[ScopedBinding, ...] = ()


class LedgerGraph:
    def __init__(self, snapshot: LedgerSnapshot):
        self.snapshot = snapshot
        self._records = {record.ref: record for record in snapshot.records}
        heads = {record.id: record.ref for record in snapshot.records}
        relations: dict[tuple[str, VersionRef], Relation] = {}
        for record in snapshot.records:
            if isinstance(record, Relation):
                relations[record.id, record.source] = record
        self.relations = tuple(sorted((r for r in relations.values()
                                      if heads[r.id] == r.ref
                                      or heads[r.source.id] != r.source),
                                     key=lambda r: _key(r.ref)))

    def _roots(self, roots: VersionRef | Iterable[VersionRef]) -> tuple[VersionRef, ...]:
        refs = (roots,) if isinstance(roots, VersionRef) else tuple(roots)
        for ref in refs:
            self.snapshot.get(ref)
        return _ordered(refs)

    def _adjacency(self, kinds=None) -> dict[VersionRef, set[VersionRef]]:
        selected = DEPENDENCIES if kinds is None else frozenset(kinds)
        adjacency: dict[VersionRef, set[VersionRef]] = defaultdict(set)
        for relation in self.relations:
            if relation.kind in selected:
                adjacency[relation.source].add(relation.target)
            if kinds is None and relation.justification and relation.kind in DEPENDENCIES | TRANSPORT:
                adjacency[relation.source].add(relation.justification)
        if kinds is None:
            for ref, record in self._records.items():
                if isinstance(record, ProjectItem) and record.declaration:
                    adjacency[ref].update(record.declaration.dependencies)
                    if record.declaration.justification:
                        adjacency[ref].add(record.declaration.justification)
                elif isinstance(record, ScopedBinding) and record.target:
                    adjacency[ref].add(record.target)
                elif isinstance(record, Obligation) and record.resolution:
                    adjacency[ref].update(record.resolution.outstanding)
        return adjacency

    def _closure(self, roots, adjacency, include_roots=False):
        seeds = self._roots(roots)
        found = set(seeds)
        pending = list(seeds)
        while pending:
            for target in adjacency.get(pending.pop(), ()):
                self.snapshot.get(target)
                if target not in found:
                    found.add(target)
                    pending.append(target)
        return _ordered(found if include_roots else found.difference(seeds))

    def dependency_closure(self, roots, *, include_roots=False) -> tuple[VersionRef, ...]:
        return self._closure(roots, self._adjacency(), include_roots)

    def reverse_closure(self, roots, *, include_roots=False) -> tuple[VersionRef, ...]:
        """Blast radius includes context provenance as well as used prerequisites.

        An item's context and a child's parent affect their semantics, but are
        not evidence that every ambient declaration was used by the item.
        Keep these provenance arcs out of minimal dependency closure.
        """
        reverse: dict[VersionRef, set[VersionRef]] = defaultdict(set)
        for source, targets in self._adjacency().items():
            for target in targets:
                reverse[target].add(source)
        for ref, record in self._records.items():
            if isinstance(record, ProjectItem) and record.context:
                reverse[record.context].add(ref)
            elif isinstance(record, MathematicalContext) and record.parent:
                reverse[record.parent].add(ref)
        return self._closure(roots, reverse, include_roots)

    def representation_users(self, representation: VersionRef) -> tuple[VersionRef, ...]:
        """All actual dependents; sibling representations do not share identity."""
        return self.reverse_closure(representation)

    def paths(self, source: VersionRef, target: VersionRef, *, kinds=None
              ) -> tuple[tuple[VersionRef, ...], ...]:
        self._roots((source, target))
        adjacency = self._adjacency(kinds)
        paths = []
        pending = deque([(source,)])
        while pending:
            path = pending.popleft()
            if path[-1] == target:
                paths.append(path)
                continue
            for neighbor in sorted(adjacency.get(path[-1], ()), key=_key):
                if neighbor not in path:
                    pending.append((*path, neighbor))
        return tuple(paths)

    def strongly_connected_components(self, *, kinds=None
                                      ) -> tuple[tuple[VersionRef, ...], ...]:
        """Iterative Kosaraju traversal includes isolated mathematical items."""
        adjacency = self._adjacency(kinds)
        nodes = {r.ref for r in self.snapshot.current(ProjectItem)}
        nodes.update(adjacency)
        reverse: dict[VersionRef, set[VersionRef]] = defaultdict(set)
        for source, targets in adjacency.items():
            nodes.update(targets)
            for target in targets:
                reverse[target].add(source)
        visited, finish = set(), []
        for root in sorted(nodes, key=_key):
            pending = [(root, False)]
            while pending:
                node, exiting = pending.pop()
                if exiting:
                    finish.append(node)
                elif node not in visited:
                    visited.add(node)
                    pending.append((node, True))
                    pending.extend((n, False) for n in sorted(adjacency.get(node, ()), key=_key))
        visited.clear()
        components = []
        for root in reversed(finish):
            if root in visited:
                continue
            component, pending = set(), [root]
            while pending:
                node = pending.pop()
                if node not in visited:
                    visited.add(node)
                    component.add(node)
                    pending.extend(reverse.get(node, ()))
            components.append(_ordered(component))
        return tuple(sorted(components, key=lambda c: tuple(map(_key, c))))

    def _unresolved(self, is_resolved: Resolved | None) -> tuple[Obligation, ...]:
        return tuple(sorted((o for o in self.snapshot.current(Obligation)
                             if is_resolved is None or not is_resolved(o)),
                            key=lambda o: _key(o.ref)))

    def blockers(self, root: VersionRef, *, is_resolved: Resolved | None = None
                 ) -> tuple[Obligation, ...]:
        """Unresolved obligations on prerequisites, excluding the root's own work."""
        dependencies = set(self.dependency_closure(root))
        return tuple(o for o in self._unresolved(is_resolved)
                     if o.item in dependencies or o.ref in dependencies)

    def ready_obligations(self, *, is_resolved: Resolved | None = None
                          ) -> tuple[Obligation, ...]:
        adjacency = self._adjacency()
        ready = []
        for obligation in self._unresolved(is_resolved):
            cyclic = any(
                root in adjacency.get(ref, ())
                for root in (obligation.item, obligation.ref)
                for ref in self.dependency_closure(root, include_roots=True)
            )
            if not cyclic and not self.blockers(obligation.item, is_resolved=is_resolved) \
                    and not self.blockers(obligation.ref, is_resolved=is_resolved):
                ready.append(obligation)
        return tuple(ready)

    def critical_branches(self, root: VersionRef, *, is_resolved: Resolved | None = None
                          ) -> tuple[tuple[VersionRef, ...], ...]:
        """Maximal simple paths to unresolved work, longest first; cycles terminate."""
        blockers = self.blockers(root, is_resolved=is_resolved)
        paths = {path for o in blockers for target in (o.item, o.ref)
                 for path in self.paths(root, target)}
        maximal = [p for p in paths if not any(len(q) > len(p) and q[:len(p)] == p for q in paths)]
        return tuple(sorted(maximal, key=lambda p: (-len(p), tuple(map(_key, p)))))

    def context_chain(self, context: VersionRef) -> tuple[MathematicalContext, ...]:
        chain, seen = [], set()
        current: VersionRef | None = context
        while current is not None:
            if current in seen:
                raise ValueError("cyclic mathematical context ancestry")
            seen.add(current)
            record = self.snapshot.get(current)
            if not isinstance(record, MathematicalContext):
                raise ValueError("context reference must identify a mathematical context")
            chain.append(record)
            current = record.parent
        return tuple(reversed(chain))

    def _context_members(self, context: VersionRef):
        for ancestor in self.context_chain(context):
            for ref in ancestor.declarations + ancestor.bindings:
                record = self.snapshot.get(ref)
                if not (isinstance(record, ScopedBinding)
                        or isinstance(record, ProjectItem) and record.declaration):
                    raise ValueError("context member must be a declaration or scoped binding")
                yield record

    def active_context(self, context: VersionRef) -> ActiveContext:
        symbols = {}
        for record in self._context_members(context):
            symbol = record.symbol if isinstance(record, ScopedBinding) else record.declaration.symbol
            symbols[symbol] = record
        return ActiveContext(context,
                             tuple(r for r in symbols.values() if isinstance(r, ProjectItem)),
                             tuple(r for r in symbols.values() if isinstance(r, ScopedBinding)))

    def minimal_context(self, item: VersionRef) -> ActiveContext:
        record = self.snapshot.get(item)
        if not isinstance(record, ProjectItem):
            raise ValueError("minimal context requires a project item")
        if record.context is None:
            return ActiveContext(None)
        needed = set(self.dependency_closure(item, include_roots=True))
        members = tuple(r for r in self._context_members(record.context) if r.ref in needed)
        return ActiveContext(record.context,
                             tuple(r for r in members if isinstance(r, ProjectItem)),
                             tuple(r for r in members if isinstance(r, ScopedBinding)))

    def established_under(self, hypothesis: VersionRef) -> tuple[ProjectItem, ...]:
        """Context provenance only: these claims need not be proved or use H."""
        record = self.snapshot.get(hypothesis)
        if not isinstance(record, ProjectItem) or not record.declaration \
                or record.declaration.role != DeclarationRole.LOCAL_HYPOTHESIS:
            raise ValueError("expected a local hypothesis")
        return tuple(sorted((item for item in self.snapshot.current(ProjectItem)
                             if item.context and any(hypothesis in c.declarations
                                                     for c in self.context_chain(item.context))),
                            key=lambda item: _key(item.ref)))

    def open_goals(self, *, is_established: Callable[[ProjectItem], bool] | None = None
                   ) -> tuple[ProjectItem, ...]:
        kinds = {ProjectItemKind.QUESTION, ProjectItemKind.CONJECTURE, ProjectItemKind.GOAL}
        return tuple(sorted((item for item in self.snapshot.current(ProjectItem)
                             if item.kind in kinds and (is_established is None or not is_established(item))),
                            key=lambda item: _key(item.ref)))

    def _items(self, refs: Iterable[VersionRef]) -> tuple[ProjectItem, ...]:
        records = (self.snapshot.get(ref) for ref in _ordered(refs))
        return tuple(r for r in records if isinstance(r, ProjectItem))

    def approaches(self, goal: VersionRef) -> tuple[ProjectItem, ...]:
        self.snapshot.get(goal)
        return tuple(r for r in self._items(relation.source for relation in self.relations
                                           if relation.target == goal and relation.kind in {
                                               RelationKind.PURSUES, RelationKind.TARGETS})
                     if r.kind == ProjectItemKind.APPROACH)

    def produced_by(self, approach: VersionRef) -> tuple[ProjectItem, ...]:
        self.snapshot.get(approach)
        return self._items(r.target for r in self.relations
                           if r.source == approach and r.kind == RelationKind.PRODUCES)

    def research_neighborhood(self, root: VersionRef) -> tuple[ProjectItem, ...]:
        adjacency = self._adjacency(RESEARCH)
        for source, targets in tuple((s, tuple(ts)) for s, ts in adjacency.items()):
            for target in targets:
                adjacency[target].add(source)
        return self._items(self._closure(root, adjacency, include_roots=True))

    def transport_paths(self, source: VersionRef, target: VersionRef, *,
                        is_justified: Callable[[Relation], bool] | None = None
                        ) -> tuple[tuple[Relation, ...], ...]:
        """Directed reuse: X' transported_from X permits X -> X'; equality is symmetric."""
        self._roots((source, target))
        if source == target:
            return ((),)
        adjacency = defaultdict(list)
        for relation in self.relations:
            if relation.kind not in TRANSPORT or relation.justification is None \
                    or is_justified is None or not is_justified(relation):
                continue
            self.snapshot.get(relation.justification)
            adjacency[relation.target].append((relation.source, relation))
            if relation.kind != RelationKind.TRANSPORTED_FROM:
                adjacency[relation.source].append((relation.target, relation))
        pending = deque([(source, (source,), ())])
        paths = []
        while pending:
            current, visited, path = pending.popleft()
            for neighbor, relation in adjacency[current]:
                if neighbor in visited:
                    continue
                extended = (*path, relation)
                if neighbor == target:
                    paths.append(extended)
                else:
                    pending.append((neighbor, (*visited, neighbor), extended))
        return tuple(paths)

    def publication_closure(self, roots) -> tuple[VersionRef, ...]:
        """Structural source closure; visibility and admissibility belong to views/policy."""
        found = set(self.dependency_closure(roots, include_roots=True))
        while True:
            attached = {r.source for r in self.relations if r.target in found
                        and r.kind in {RelationKind.DOCUMENTS, RelationKind.ILLUSTRATES}}
            citations = {r.target for r in self.relations if r.source in found
                         and r.kind == RelationKind.CITES}
            expanded = set(self.dependency_closure(found | attached | citations, include_roots=True))
            if expanded == found:
                return _ordered(found)
            found = expanded
