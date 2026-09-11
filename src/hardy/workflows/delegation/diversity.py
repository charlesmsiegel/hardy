"""Semantic diversity: what makes one worker's search different from another's.

The axes run strongest to weakest. A portfolio is built from qualitatively
different roles before any role is repeated, and a repeated role differs by
seed, never by rewording. Every brief records exactly which axes made it
different, so blind versus cross-pollinated performance can be studied later.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from hardy.workflows.delegation.context import ResearchBrief
from hardy.workflows.ledger.contracts import VersionRef


class DiversityAxis(str, Enum):
    TASK_MODE = "task_mode"
    REPRESENTATION = "representation"
    INFORMATION_EXPOSURE = "information_exposure"
    METHOD = "method"
    RETRIEVAL_INTENT = "retrieval_intent"
    MODEL = "model"
    SAMPLING = "sampling"


@dataclass(frozen=True)
class Role:
    name: str
    task_mode: str
    framing: str
    reasoning_direction: str
    independence: Literal["shared", "blind"] = "shared"
    retrieval_intent: str = ""
    preferred_representations: tuple[str, ...] = ()
    required_methods: tuple[str, ...] = ()
    discouraged_methods: tuple[str, ...] = ()
    forbidden_methods: tuple[str, ...] = ()


DEFAULT_PORTFOLIO: tuple[Role, ...] = (
    Role("direct_proof", "prove", "Prove the statement directly from its recorded dependencies.", "forward"),
    Role("blind_proof", "prove",
         "Prove the statement independently. You are deliberately kept blind to other workers' findings "
         "and to the currently favoured route.", "forward", independence="blind"),
    Role("falsification", "refute",
         "Look for a counterexample or an obstruction to the statement, a minimal one if any exists.",
         "minimal-counterexample"),
    Role("literature_reduction", "reduce", "Reduce the statement to known results.", "literature",
         retrieval_intent="results with a matching conclusion, and stronger theorems that imply it"),
    Role("examples_computation", "compute",
         "Test the statement on explicit examples and small cases before believing it.", "experiment"),
    Role("alternate_representation", "prove",
         "Work in a different representation of the objects involved from the one the project records.",
         "translate", preferred_representations=("a representation other than the recorded one",)),
    Role("specialize_generalize", "generalize",
         "Prove a sharper or more general statement, or first settle the key special case.", "generalize"),
    Role("wildcard", "prove", "Assume the obvious route fails.", "backward",
         forbidden_methods=("the dominant method of the recorded approaches",)),
)


def assign_briefs(target: VersionRef, count: int, *, task_mode: str | None = None,
                  seeds: Iterable[int] | None = None, model: str | None = None,
                  portfolio: tuple[Role, ...] = DEFAULT_PORTFOLIO) -> tuple[ResearchBrief, ...]:
    """`count` briefs over one target: distinct roles first, then repeated roles with distinct seeds."""
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("a portfolio needs at least one worker")
    seed_list = list(seeds) if seeds is not None else [None] * count
    if len(seed_list) < count:
        raise ValueError("fewer seeds than workers")
    briefs = []
    for index in range(count):
        role = portfolio[index % len(portfolio)]
        mode = task_mode or role.task_mode
        seed = seed_list[index]
        diversity = [
            ("role", role.name),
            (DiversityAxis.TASK_MODE.value, mode),
            (DiversityAxis.METHOD.value, role.reasoning_direction),
            (DiversityAxis.INFORMATION_EXPOSURE.value, role.independence),
        ]
        if role.preferred_representations:
            diversity.append((DiversityAxis.REPRESENTATION.value, "; ".join(role.preferred_representations)))
        if role.retrieval_intent:
            diversity.append((DiversityAxis.RETRIEVAL_INTENT.value, role.retrieval_intent))
        if model is not None:
            diversity.append((DiversityAxis.MODEL.value, model))
        if seed is not None:
            diversity.append((DiversityAxis.SAMPLING.value, str(seed)))
        briefs.append(ResearchBrief(
            target=target, task_mode=mode, framing=role.framing,
            reasoning_direction=role.reasoning_direction,
            preferred_representations=role.preferred_representations,
            required_methods=role.required_methods, discouraged_methods=role.discouraged_methods,
            forbidden_methods=role.forbidden_methods, retrieval_intent=role.retrieval_intent,
            independence=role.independence, diversity=tuple(diversity), model=model, seed=seed,
        ))
    return tuple(briefs)
