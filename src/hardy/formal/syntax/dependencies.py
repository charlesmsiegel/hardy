"""The workspace module graph: build order, dependents and import cycles."""
from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from pathlib import Path

from hardy.formal.syntax.imports import import_table

Compile = Callable[[str, Path, Path, Path], tuple[bool, str]]


class ImportCycle(ValueError):
    """Workspace modules that import each other."""


def build_order(
    sources: Mapping[str, str],
    targets: Collection[str],
    imports: Mapping[str, Collection[str]] | None = None,
) -> tuple[str, ...]:
    """`targets` and their transitive internal dependencies, dependencies first.

    Sorted at each step rather than left in dictionary order: a build that
    compiled the same tree in a different order on different runs could not be
    compared against its own cache.
    """
    imports = import_table(sources) if imports is None else imports
    order: list[str] = []
    placed: set[str] = set()
    active: list[str] = []

    def visit(module: str) -> None:
        if module in placed:
            return
        if module in active:
            cycle = " -> ".join([*active[active.index(module) :], module])
            raise ImportCycle(f"workspace modules import each other: {cycle}")
        active.append(module)
        for dependency in sorted(dependency for dependency in imports[module] if dependency in sources):
            visit(dependency)
        active.pop()
        placed.add(module)
        order.append(module)

    for target in sorted(targets):
        visit(target)
    return tuple(order)


def dependents(sources: Mapping[str, str], module: str) -> frozenset[str]:
    """Every module importing `module`, transitively, including after its deletion."""
    direct: dict[str, set[str]] = {name: set() for name in (*sources, module)}
    for name, dependencies in import_table(sources).items():
        for dependency in dependencies:
            if dependency in direct:
                direct[dependency].add(name)
    found: set[str] = set()
    frontier = list(direct.get(module, ()))
    while frontier:
        name = frontier.pop()
        if name in found or name == module:
            continue
        found.add(name)
        frontier.extend(direct.get(name, ()))
    return frozenset(found)
