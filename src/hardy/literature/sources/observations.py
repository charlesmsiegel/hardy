"""Structural observations: what a document's own structure says, and where.

An observation is local evidence with an anchor and a producer: a PDF outline
entry, a heading, a "Theorem 4.2." at the start of a line, a "Proof." marker,
a reference such as "by Proposition 4.7". Observations are stored write-once
per producer so that a tree built from them can be audited against the exact
evidence it used; they are not a tree and they assert nothing about
mathematics. Text producers here work over a normalized-text representation
and never guess at a theorem number the text does not print.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from hardy.foundation.files import LayoutError, WriteGuard, read_text

from .contracts import (
    DerivedRepresentation,
    ObservationKind,
    RepresentationSpan,
    SourceAnchor,
    StructuralObservation,
)

PRODUCER = "hardy.observe.text"
PRODUCER_VERSION = "1"

STATEMENT_KINDS = ("theorem", "lemma", "proposition", "corollary", "definition", "claim", "conjecture",
                   "example", "exercise", "remark", "construction", "solution")
STATEMENT_START = re.compile(
    r"^(?P<kind>Theorem|Lemma|Proposition|Corollary|Definition|Claim|Conjecture|Example|Exercise|Remark|Construction|Solution)"
    r"(?:\s+(?P<number>[A-Z]?[0-9]+(?:\.[0-9]+)*))?\s*(?:\((?P<name>[^)]*)\))?\s*[.:]\s*",
    re.MULTILINE,
)
PROOF_START = re.compile(r"^(?P<label>Proof|Beweis|Démonstration)(?:\s+of\s+[^.]*)?\.?\s*", re.MULTILINE)
PROOF_END = re.compile(r"(?:∎|□|■|\bQ\.E\.D\.|\bQED\b|\bq\.e\.d\.)", re.MULTILINE)
HEADING = re.compile(
    r"^(?:(?P<chapter>Chapter|Part|Appendix)\s+(?P<chapter_number>[0-9IVXLC]+)\.?\s*(?P<chapter_title>[^\n]*)"
    r"|(?P<section_number>[0-9]+(?:\.[0-9]+){0,3})\s+(?P<section_title>[A-Z][^\n]{0,120}))$",
    re.MULTILINE,
)
REFERENCE = re.compile(
    r"\b(?P<kind>Theorem|Lemma|Proposition|Corollary|Definition|Claim|Conjecture|Example|Exercise|Remark|Equation)"
    r"\s+(?P<number>[A-Z]?[0-9]+(?:\.[0-9]+)+|[0-9]+)\b",
)
EQUATION_NUMBER = re.compile(r"\((?P<number>[0-9]+(?:\.[0-9]+)*[a-z]?)\)\s*$", re.MULTILINE)
PAGE_BREAK = "\f"


def _obs_id(artifact: str, kind: str, start: int, end: int, extra: str = "") -> str:
    return "obs-" + hashlib.sha256(f"{artifact}|{kind}|{start}|{end}|{extra}".encode()).hexdigest()[:16]


def observe_text(representation: DerivedRepresentation, text: str, *, producer_version: str = PRODUCER_VERSION) -> tuple[StructuralObservation, ...]:
    """Deterministic observations over one normalized-text representation."""
    sha = representation.artifact_sha256
    rep = representation.id
    found: list[StructuralObservation] = []

    def anchor(start: int, end: int, derivation: str) -> SourceAnchor:
        return SourceAnchor(artifact_sha256=sha, locator=RepresentationSpan(representation=rep, start=start, end=end), derivation=derivation)

    def add(kind: ObservationKind, start: int, end: int, payload: tuple[tuple[str, str], ...], derivation: str, confidence: float | None = None, extra: str = "") -> None:
        found.append(StructuralObservation(
            id=_obs_id(sha, kind.value, start, end, extra), artifact_sha256=sha, kind=kind, anchor=anchor(start, end, derivation),
            payload=payload, producer=PRODUCER, producer_version=producer_version, confidence=confidence,
        ))

    position = 0
    for index, _ in enumerate(text.split(PAGE_BREAK)):
        if index > 0:
            add(ObservationKind.PAGE_BREAK, position - 1, position, (("page_boundary", str(index)),), "form feed between pages", 1.0)
        position = text.find(PAGE_BREAK, position) + 1 if PAGE_BREAK in text[position:] else len(text)

    for match in HEADING.finditer(text):
        if match.group("chapter"):
            payload = (("level", match.group("chapter").lower()), ("number", match.group("chapter_number")), ("title", match.group("chapter_title").strip()))
        else:
            depth = match.group("section_number").count(".") + 1
            payload = (("level", "section" if depth == 1 else "subsection"), ("number", match.group("section_number")), ("title", match.group("section_title").strip()))
        add(ObservationKind.HEADING, match.start(), match.end(), payload, "heading pattern at line start", 0.8)

    for match in STATEMENT_START.finditer(text):
        number = match.group("number") or ""
        payload = (("kind", match.group("kind").lower()), ("number", number), ("number_origin", "explicit" if number else "none"), ("name", match.group("name") or ""))
        add(ObservationKind.STATEMENT_START, match.start(), match.end(), payload, "statement keyword at line start", 0.9)

    for match in PROOF_START.finditer(text):
        add(ObservationKind.PROOF_START, match.start(), match.end(), (("label", match.group("label")),), "proof keyword at line start", 0.9)

    for match in PROOF_END.finditer(text):
        add(ObservationKind.PROOF_END, match.start(), match.end(), (("marker", match.group(0)),), "end-of-proof marker", 0.9)

    for match in REFERENCE.finditer(text):
        line_start = text.rfind("\n", 0, match.start()) + 1
        if match.start() == line_start:
            continue  # a statement heading, not a reference to one
        add(ObservationKind.REFERENCE, match.start(), match.end(), (("kind", match.group("kind").lower()), ("number", match.group("number"))), "in-text reference to a numbered unit", 0.7)

    for match in EQUATION_NUMBER.finditer(text):
        add(ObservationKind.EQUATION_NUMBER, match.start(), match.end(), (("number", match.group("number")),), "parenthesized number at line end", 0.6)

    return tuple(sorted(found, key=lambda o: (o.anchor.locator.start, o.kind.value)))  # type: ignore[union-attr]


class ObservationStore:
    """Write-once observation sets per artifact and producer under <trees>/<sha>/observations."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _dir(self, artifact_sha256: str) -> Path:
        return self.root / artifact_sha256 / "observations"

    def admit(self, artifact_sha256: str, set_id: str, observations: tuple[StructuralObservation, ...]) -> tuple[StructuralObservation, ...]:
        for observation in observations:
            if observation.artifact_sha256 != artifact_sha256:
                raise ValueError("an observation set belongs to one artifact")
        guard = WriteGuard(self._dir(artifact_sha256), create=True)
        name = f"{set_id}.json"
        target = guard.reserve(name)
        if target.is_file():
            return self.get(artifact_sha256, set_id)
        payload = json.dumps([o.model_dump(mode="json") for o in observations], ensure_ascii=False, sort_keys=True).encode("utf-8")
        guard.write_bytes(name, payload)
        return self.get(artifact_sha256, set_id)

    def get(self, artifact_sha256: str, set_id: str) -> tuple[StructuralObservation, ...]:
        try:
            raw = read_text(self.root, f"{artifact_sha256}/observations/{set_id}.json")
        except (FileNotFoundError, LayoutError):
            return ()
        return tuple(StructuralObservation.model_validate(o) for o in json.loads(raw))

    def sets(self, artifact_sha256: str) -> tuple[str, ...]:
        directory = self._dir(artifact_sha256)
        if not directory.is_dir() or directory.is_symlink():
            return ()
        return tuple(sorted(p.stem for p in directory.iterdir() if p.suffix == ".json" and not p.is_symlink()))

    def all(self, artifact_sha256: str) -> tuple[StructuralObservation, ...]:
        found: list[StructuralObservation] = []
        for set_id in self.sets(artifact_sha256):
            found.extend(self.get(artifact_sha256, set_id))
        return tuple(found)
