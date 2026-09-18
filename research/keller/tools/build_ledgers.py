#!/usr/bin/env python3
"""Write the three problems' ledgers from the migration table, once.

This is the record of how the packet of 2026-09-14 became Hardy ledgers, kept so
the derivation can be read and rerun on a fresh checkout of the same inputs. It
refuses to run against a problem that already has a ledger: after the first
run the ledgers are the source of truth and the table is history.

Run in the environment Hardy is installed in, which puts its ledger code on the
path; from a Hardy checkout:

    uv run --project /path/to/hardy python tools/build_ledgers.py

Every record is written through `hardy.workflows.ledger.store.LedgerStore`, so
the files are exactly what a session would have written: content-addressed,
hash-chained, validated by Hardy's structural checks and its policy.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from hardy.workflows.ledger.contracts import (
    ArtifactRef,
    Obligation,
    ObligationKind,
    ObligationStatus,
    ProjectItem,
    ProjectItemKind,
    ProjectOrigin,
    Relation,
    RelationKind,
    ResearchState,
    Scope,
    VersionRef,
)
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.store import LedgerStore

ROOT = Path(__file__).resolve().parents[1]
TABLE = ROOT / "tools" / "packet-claims.json"
FIRST = "00000000000000000001.json"

CLAIM_KINDS = {
    ProjectItemKind.THEOREM, ProjectItemKind.LEMMA, ProjectItemKind.PROPOSITION,
    ProjectItemKind.COROLLARY, ProjectItemKind.CLAIM, ProjectItemKind.CONJECTURE,
}
OPEN_KINDS = {ProjectItemKind.QUESTION, ProjectItemKind.GOAL}


def digest_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifact(problem: Path, spec: dict | None) -> tuple[ArtifactRef, ...]:
    if not spec:
        return ()
    target = (problem / spec["file"]).resolve()
    if not target.is_file():
        raise SystemExit(f"{problem.name}: artifact {spec['file']} is not a file")
    return (ArtifactRef(uri=spec["file"], digest=digest_of(target), locator=spec.get("locator")),)


def semantics(item: dict, extra: dict[str, str]) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []
    for alias in item.get("aliases", ()):
        pairs.append(("alias", alias))
    for name in item.get("lean", ()):
        pairs.append(("lean_declaration", name))
    if item.get("lean_file"):
        pairs.append(("lean_file", item["lean_file"]))
    if item.get("lean_source_mapping"):
        pairs.append(("lean_source_mapping", item["lean_source_mapping"]))
    if item.get("paper"):
        pairs.append(("paper_label", item["paper"]))
    for key, value in item.get("semantics", {}).items():
        pairs.append((key, value))
    for key, value in extra.items():
        pairs.append((key, value))
    return tuple(pairs)


def build(slug: str, spec: dict, table: dict, built: dict[str, LedgerSnapshot]) -> LedgerSnapshot:
    problem = ROOT / slug
    if (problem / "ledger").exists():
        raise SystemExit(f"{slug}: ledger/ already exists; this script writes a ledger once")
    author = table["packet"]["author"]
    records: list = []
    refs: dict[str, VersionRef] = {}
    own_ids = {item["id"] for item in spec["items"]}
    statuses: dict[str, str] = {}

    # 1. Own items.
    for item in spec["items"]:
        kind = ProjectItemKind(item["kind"])
        if kind == ProjectItemKind.EXTERNAL_RESULT:
            origin = ProjectOrigin.BACKGROUND_PAPER
            research = ResearchState(status=item["status"], reason=item.get("reason"), author=author)
        elif kind in CLAIM_KINDS or kind in OPEN_KINDS:
            origin = ProjectOrigin.IMPORTED_PROJECT
            research = ResearchState(status=item["status"], reason=item.get("reason"), author=author)
        else:
            origin = ProjectOrigin.IMPORTED_PROJECT
            research = None
        record = ProjectItem(
            id=item["id"], kind=kind, name=item["name"], origin=origin,
            statement=item.get("statement"), artifacts=artifact(problem, item.get("artifact")),
            research=research, semantics=semantics(item, {}),
        )
        records.append(record)
        refs[item["id"]] = record.ref
        statuses[item["id"]] = item.get("status", "")

    # 2. Mirrors of upstream items this problem depends on.
    needed: dict[str, str] = {}
    for item in spec["items"]:
        for dep in (*item.get("depends_on", ()), *item.get("uses", ())):
            if dep in own_ids:
                continue
            for upstream, snapshot in built.items():
                if any(r.id == dep for r in snapshot.records):
                    needed[dep] = upstream
                    break
            else:
                raise SystemExit(f"{slug}: {item['id']} depends on {dep}, which no earlier problem holds")
    for rel in spec.get("relations", ()):
        for end in (rel["source"], rel["target"]):
            if end not in own_ids and end not in needed:
                for upstream, snapshot in built.items():
                    if any(r.id == end for r in snapshot.records):
                        needed[end] = upstream
                        break
                else:
                    raise SystemExit(f"{slug}: relation names {end}, which no problem holds")
    for dep, upstream in sorted(needed.items()):
        source = built[upstream].head(dep)
        assert isinstance(source, ProjectItem)
        ledger_file = ROOT / upstream / "ledger" / FIRST
        record = ProjectItem(
            id=dep, kind=ProjectItemKind.EXTERNAL_RESULT, name=source.name,
            origin=ProjectOrigin.IMPORTED_PROJECT, statement=source.statement,
            artifacts=(ArtifactRef(uri=f"../{upstream}/ledger/{FIRST}", digest=digest_of(ledger_file),
                                   locator=f"item:{dep}"),),
            research=ResearchState(
                status="imported",
                reason=f"mirror of {dep} in {upstream}; its status there is {source.research.status if source.research else 'unassessed'}",
                author=author),
            semantics=(("upstream_problem", upstream), ("upstream_item", dep),
                       ("upstream_digest", source.digest),
                       ("upstream_kind", source.kind.value),
                       ("upstream_status", source.research.status if source.research else "unassessed")),
        )
        records.append(record)
        refs[dep] = record.ref
        statuses[dep] = "imported"

    # 3. Documents.
    for doc in spec.get("documents", ()):
        record = ProjectItem(
            id=doc["id"], kind=ProjectItemKind(doc["kind"]), name=doc["name"],
            origin=ProjectOrigin.IMPORTED_PROJECT, statement=doc.get("statement"),
            artifacts=artifact(problem, {"file": doc["file"]}),
        )
        records.append(record)
        refs[doc["id"]] = record.ref

    # 4. Scope and obligations: everything this problem claims is its own to prove.
    must_prove = tuple(refs[i["id"]] for i in spec["items"] if ProjectItemKind(i["kind"]) in CLAIM_KINDS)
    scope = Scope(id="scope", must_prove=must_prove)
    records.append(scope)
    for item in spec["items"]:
        kind = ProjectItemKind(item["kind"])
        if kind in CLAIM_KINDS:
            reason = item["status"] + (f"; {item['reason']}" if item.get("reason") else "")
            records.append(Obligation(id=f"prove:{item['id']}", item=refs[item["id"]], kind=ObligationKind.PROVE,
                                      scope=scope, status=ObligationStatus.OPEN, reason=reason))

    # 5. Relations.
    def relation(kind: RelationKind, source: str, target: str) -> None:
        records.append(Relation(id=f"{kind.value}:{source}:{target}", kind=kind,
                                source=refs[source], target=refs[target]))

    for item in spec["items"]:
        for dep in item.get("depends_on", ()):
            relation(RelationKind.DEPENDS_ON, item["id"], dep)
        for dep in item.get("uses", ()):
            relation(RelationKind.USES, item["id"], dep)
    for doc in spec.get("documents", ()):
        for target in doc.get("documents", ()):
            relation(RelationKind.DOCUMENTS, doc["id"], target)
    for rel in spec.get("relations", ()):
        relation(RelationKind(rel["kind"]), rel["source"], rel["target"])

    return LedgerStore(problem).append(records, expected_revision=0)


def main() -> None:
    table = json.loads(TABLE.read_text(encoding="utf-8"))
    built: dict[str, LedgerSnapshot] = {}
    for slug in table["order"]:
        built[slug] = build(slug, table["problems"][slug], table, built)
        print(f"{slug}: {len(built[slug].records)} records in ledger revision {built[slug].revision}")


if __name__ == "__main__":
    sys.exit(main())
