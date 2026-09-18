#!/usr/bin/env python3
"""Build the Lean tree, audit it with Hardy's own axiom audit, and record the outcome.

    uv run --project /path/to/hardy python tools/record_lean.py

What one run does, per problem, through Hardy's own code paths:

1. compiles the shared library and the problem's modules with `lake env lean`
   against the pinned Mathlib, exactly as a session's save would;
2. asks Lean `#print axioms` for every public theorem and lemma and grades each
   with `hardy.formal.audit` (clean, modulo approved axioms, open hole, rejected);
3. hands the verdicts to `hardy.workflows.interactive.evidence.ProjectOwners.record_saved`,
   which writes one ledger item per declaration (`lean:<name>`), a `prove`
   obligation for it, and, for a declaration the kernel verified on standard
   axioms alone, a `FormalEvidence` record in `evidence/`, a `Decision`, and
   the resolved obligation; and stores the verdicts in `session.json` so
   `/status --full` grades the declarations from the record;
4. links each declaration to the claim it formalises, from `tools/lean-map.json`:
   a `formalizes` relation always, and for a declaration that states the whole
   claim (`"faithfulness": "full"`) and was verified, the claim's status moves to
   `lean verified` and its own `prove` obligation is closed on evidence minted
   for it; a declaration that proves only a part (`"core"`) or only states the
   claim (`"statement"`) leaves the status alone and records what was done;
5. refreshes the downstream mirrors of every upstream item whose head moved.

Rerunnable: a declaration already recorded and unchanged is left as it is by
Hardy's own reconciliation; a changed one is re-audited and re-accepted.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath

from hardy.formal import audit
from hardy.formal.lean import LeanTools
from hardy.formal.syntax import build_order, declarations, module_name
from hardy.formal.workspace import LeanWorkspace
from hardy.workflows.interactive import evidence as ev
from hardy.workflows.interactive.record import SessionRecord
from hardy.workflows.ledger.contracts import (
    ArtifactRef,
    Obligation,
    ObligationKind,
    ObligationStatus,
    ProjectItem,
    Relation,
    RelationKind,
    ResearchState,
    Resolution,
    Scope,
)
from hardy.workflows.ledger.store import LedgerStore

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / ".hardy" / "lean"
SHARED_BUILD = ROOT / ".hardy" / ".build" / "lean"
LAKE_PROJECT = Path(os.environ.get("HARDY_LEAN_PROJECT", Path.home() / ".local/share/hardy/lean"))
MAP = ROOT / "tools" / "lean-map.json"
ORDER = ("keller-groupoids", "keller-threefold", "keller-groupoids-rank-two")
FIRST = "00000000000000000001.json"


def lean_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{Path.home() / '.elan/bin'}:{env.get('PATH', '')}"
    return env


def run_lean(args: list[str], *, lean_path: str) -> subprocess.CompletedProcess:
    env = lean_env()
    env["LEAN_PATH"] = lean_path
    return subprocess.run(["lake", "env", "lean", *args], cwd=LAKE_PROJECT, env=env,
                          capture_output=True, text=True, timeout=1800)


def lean_version() -> str:
    out = subprocess.run(["lean", "--version"], cwd=LAKE_PROJECT, env=lean_env(), capture_output=True, text=True)
    return out.stdout.strip()


def sources_of(root: Path) -> dict[str, str]:
    return {module_name(PurePosixPath(p.relative_to(root).as_posix())): p.read_text(encoding="utf-8")
            for p in sorted(root.rglob("*.lean"))}


def compile_tree(root: Path, build: Path, sources: dict[str, str], lean_path: str) -> None:
    for module in build_order(sources, tuple(sources)):
        source = root / Path(*module.split(".")).with_suffix(".lean")
        olean = build / Path(*module.split(".")).with_suffix(".olean")
        olean.parent.mkdir(parents=True, exist_ok=True)
        stamp = olean.with_suffix(".sha256")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if olean.is_file() and stamp.is_file() and stamp.read_text() == digest:
            continue
        print(f"  compiling {module}")
        result = run_lean([f"--root={root}", "-o", str(olean), "-i", str(olean.with_suffix('.ilean')), str(source)],
                          lean_path=lean_path)
        errors = [line for line in (result.stdout + result.stderr).splitlines() if "error" in line]
        if result.returncode != 0 or errors:
            raise SystemExit(f"{module} does not compile:\n" + "\n".join(errors[:20]))
        stamp.write_text(digest)


def audit_module(module: str, source: str, lean_path: str, scratch: Path) -> dict:
    found = declarations(source)
    names = [n for n in found["theorem"] + found["lemma"] if n not in found["private"]]
    if not names:
        return audit.unestablished(f"no theorem or lemma is declared in {module}")
    probe = LeanTools.with_audit(f"import {module}\n", [f"axioms {n}" for n in names])
    scratch.mkdir(parents=True, exist_ok=True)
    path = scratch / "Probe.lean"
    path.write_text(probe, encoding="utf-8")
    result = run_lean([f"--root={scratch}", str(path)], lean_path=lean_path)
    output = result.stdout + "\n" + result.stderr
    reports = audit.parse(output, names)
    if reports is None:
        raise SystemExit(f"the axiom audit of {module} did not answer for every declaration:\n{output[-3000:]}")
    return audit.classify(reports, approved=()).as_dict()


def owners_for(problem: Path, sources: dict[str, str]) -> ev.ProjectOwners:
    gates = ev.SaveGates(final_gates=lambda _s: None, missing_names=lambda _a, _b: [],
                         head_sources=lambda: sources)
    return ev.ProjectOwners(problem, audit=lambda *_a, **_k: None, gates=gates)


def link_claims(problem: Path, owners: ev.ProjectOwners, table: dict, records: dict, sources: dict[str, str],
                signatures: dict[str, str]) -> list[str]:
    """The `formalizes`/`supports` edges, the status promotions, and the notes on the rest."""
    store = LedgerStore(problem)
    notes: list[str] = []
    graded: dict[str, tuple[str, list[str]]] = {}
    for module, record in records.items():
        for entry in record.get("declarations", ()):
            graded[str(entry["name"])] = (module, [str(a) for a in entry.get("axioms", ())])
    spec = table["problems"][problem.name]
    snapshot = store.read()
    batch: list = []
    touched: dict[str, ProjectItem] = {}

    def head_item(id_: str) -> ProjectItem:
        return touched.get(id_) or snapshot.head(id_)

    # Which claims get which declarations; strip the packet's stale lean semantics first.
    per_claim: dict[str, list[dict]] = {}
    for decl, link in spec["declarations"].items():
        per_claim.setdefault(link["item"], []).append({"declaration": decl, **link})
    for claim_id, entry in spec.get("inputs", {}).items():
        item = snapshot.head(claim_id)
        semantics = [(k, v) for k, v in item.semantics
                     if k not in {"lean_declaration", "lean_file", "lean_field", "formalization"}]
        if entry.get("declaration"):
            semantics += [("lean_declaration", entry["declaration"]), ("lean_file", entry["file"])]
            if entry.get("field"):
                semantics.append(("lean_field", entry["field"]))
            if entry.get("note"):
                semantics.append(("formalization", entry["note"]))
        else:
            semantics.append(("formalization", entry["note"]))
        revised = item.model_copy(update={"semantics": tuple(semantics)})
        if revised != item:
            batch.append(revised)
            touched[claim_id] = revised
    for claim_id in sorted(set(per_claim) | set(spec.get("not_expressible", {}))):
        item = snapshot.head(claim_id)
        assert isinstance(item, ProjectItem), claim_id
        semantics = [(k, v) for k, v in item.semantics
                     if k not in {"lean_declaration", "lean_file", "formalization", "formal_proof"}]
        links = per_claim.get(claim_id, [])
        status = item.research.status if item.research else None
        reason = item.research.reason if item.research else None
        promote = None
        for link in links:
            decl = link["declaration"]
            module, axioms = graded.get(decl, (None, None))
            if module is None:
                raise SystemExit(f"{claim_id}: {decl} is in the map but was not audited")
            state = audit.declaration_status(decl, {module: records[module]}).kind
            semantics.append(("lean_declaration", decl))
            semantics.append(("lean_file", f"lean/{module.replace('.', '/')}.lean"))
            semantics.append(("formal_proof", f"{decl}: {link['faithfulness']}; audit {state}; {link['note']}"))
            if link["faithfulness"] == "full" and state == "verified":
                promote = (decl, module, axioms)
        if claim_id in spec.get("not_expressible", {}):
            semantics.append(("formalization", "not expressible over Mathlib v4.33.1: " + spec["not_expressible"][claim_id]))
        research = item.research
        if promote is not None and status in {"open", "llm proved"}:
            decl, module, axioms = promote
            research = ResearchState(
                status="lean verified",
                reason=f"{decl} kernel-checked in {module} (axioms: {', '.join(axioms) or 'none'}); "
                       f"previously {status}" + (f": {reason}" if reason else ""),
                author=f"tools/record_lean.py; {lean_version()}")
        revised = item.model_copy(update={"semantics": tuple(semantics), "research": research})
        if revised != item:
            batch.append(revised)
            touched[claim_id] = revised
            notes.append(f"{claim_id}: {'promoted to lean verified' if research is not item.research else 'formalization noted'}")
    if batch:
        snapshot = store.append(batch, expected_revision=snapshot.revision)

    # Edges from each declaration's own item to the claim.
    edges: list = []
    for decl, link in spec["declarations"].items():
        lean_id = ev.item_id(decl)
        try:
            source_item = snapshot.head(lean_id)
        except ValueError:
            notes.append(f"{decl}: no ledger item was recorded for it")
            continue
        kind = RelationKind.FORMALIZES if link["faithfulness"] in {"full", "statement"} else RelationKind.SUPPORTS
        rel_id = f"{kind.value}:{lean_id}:{link['item']}"
        target = head_item(link["item"])
        existing = next((r for r in snapshot.current(Relation) if r.id == rel_id), None)
        rel = Relation(id=rel_id, kind=kind, source=source_item.ref, target=target.ref)
        if existing is None or existing != rel:
            edges.append(rel)
    if edges:
        snapshot = store.append(edges, expected_revision=snapshot.revision)

    # Close the claim's own proof obligation on evidence minted for the claim itself. An
    # obligation is bound to an exact item revision, so a promoted (revised) claim gets a fresh
    # obligation for its new revision and the one bound to the old revision is dismissed as
    # superseded; the fresh one is then resolved the way Hardy's save path resolves its own.
    scope = next(s for s in snapshot.current(Scope) if s.id == "scope")
    for claim_id, links in per_claim.items():
        claim = head_item(claim_id)
        if not (claim.research and claim.research.status == "lean verified"):
            continue
        current = next((o for o in snapshot.current(Obligation)
                        if o.item == claim.ref and o.kind is ObligationKind.PROVE), None)
        if current is None:
            stale = [o for o in snapshot.current(Obligation)
                     if o.item.id == claim_id and o.kind is ObligationKind.PROVE
                     and o.status is ObligationStatus.OPEN]
            for old in stale:
                dismissed = Obligation.model_validate({**old.model_dump(), "previous": old.ref, "status": "dismissed",
                                                       "reason": f"superseded: {claim_id} was revised to "
                                                                 f"{claim.digest[:12]} on kernel evidence"})
                snapshot = store.append((dismissed,), expected_revision=snapshot.revision,
                                        validate=owners.policy.validate)
            current = Obligation(id=f"prove:{claim_id}:{claim.digest[:12]}", item=claim.ref,
                                 kind=ObligationKind.PROVE, scope=scope, status=ObligationStatus.OPEN)
            snapshot = store.append((current,), expected_revision=snapshot.revision, validate=owners.policy.validate)
        if current.status is ObligationStatus.RESOLVED:
            continue
        link = next(entry for entry in links if entry["faithfulness"] == "full")
        decl = link["declaration"]
        module, axioms = graded[decl]
        reference = owners._mint(subject=claim.ref, scope=scope.ref, context=current.context,
                                 module=module, declaration=decl, statement=decl, axioms=axioms,
                                 signature=signatures.get(module, ""), source=sources[module])
        proposal = Resolution(id=f"{current.id}:resolution:{reference.artifact.digest[:12]}",
                              obligation=current.ref, item=claim.ref, evidence=(reference,),
                              explanation=f"{decl} in {module}: verified by the kernel")
        receipt = owners.decide(snapshot, proposal)
        accepted = owners.policy.accept(snapshot, proposal, receipt)
        closed = Obligation.model_validate({**current.model_dump(), "previous": current.ref,
                                            "status": "resolved", "resolution": accepted, "reason": None})
        snapshot = store.append((closed,), expected_revision=snapshot.revision, validate=owners.policy.validate)
        notes.append(f"{claim_id}: proof obligation resolved on {decl}")
    return notes


def refresh_mirrors(problem: Path, heads: dict[str, LedgerStore]) -> list[str]:
    store = LedgerStore(problem)
    snapshot = store.read()
    batch: list = []
    for item in snapshot.current(ProjectItem):
        meta = dict(item.semantics)
        upstream = meta.get("upstream_problem")
        if not upstream or not (item.research and item.research.status == "imported"):
            continue
        source = heads[upstream].read().head(meta["upstream_item"])
        if source.digest == meta.get("upstream_digest"):
            continue
        ledger_file = ROOT / upstream / "ledger" / FIRST
        refreshed = item.model_copy(update={
            "statement": source.statement,
            "artifacts": (ArtifactRef(uri=f"../{upstream}/ledger/{FIRST}",
                                      digest=hashlib.sha256(ledger_file.read_bytes()).hexdigest(),
                                      locator=f"item:{item.id}"),),
            "research": ResearchState(status="imported",
                                      reason=f"mirror of {item.id} in {upstream}; its status there is "
                                             f"{source.research.status if source.research else 'unassessed'}",
                                      author=item.research.author),
            "semantics": (("upstream_problem", upstream), ("upstream_item", item.id),
                          ("upstream_digest", source.digest), ("upstream_kind", source.kind.value),
                          ("upstream_status", source.research.status if source.research else "unassessed")),
        })
        batch.append(refreshed)
    if batch:
        store.append(batch, expected_revision=snapshot.revision)
    return [f"{problem.name}: refreshed mirror {r.id}" for r in batch]


def main() -> int:
    table = json.loads(MAP.read_text(encoding="utf-8"))
    version = lean_version()
    print(f"Lean: {version}")
    shared_sources = sources_of(SHARED)
    SHARED_BUILD.mkdir(parents=True, exist_ok=True)
    print("shared library")
    compile_tree(SHARED, SHARED_BUILD, shared_sources, str(SHARED_BUILD))
    stores = {slug: LedgerStore(ROOT / slug) for slug in ORDER}
    for slug in ORDER:
        problem = ROOT / slug
        root, build = problem / "lean", problem / ".build" / "lean"
        sources = sources_of(root)
        if not sources:
            continue
        print(slug)
        lean_path = f"{build}:{SHARED_BUILD}"
        build.mkdir(parents=True, exist_ok=True)
        compile_tree(root, build, sources, lean_path)
        records = {module: audit_module(module, source, lean_path, build / "audit")
                   for module, source in sources.items()}
        for module, record in records.items():
            print(f"  {module}: {record.get('status')}; " + ", ".join(
                f"{d['name'].rsplit('.', 1)[-1]}[{','.join(a.rsplit('.', 1)[-1] for a in d['axioms']) or 'none'}]"
                for d in record.get("declarations", ())))
        workspace = LeanWorkspace(root, build, compile=None, environment=version,
                                  external=lambda name: hashlib.sha256(
                                      shared_sources.get(name, "").encode("utf-8")).hexdigest())
        signatures = workspace.current_signatures(sources)
        owners = owners_for(problem, sources)
        note = owners.record_saved(sources, records, signatures)
        print("  " + note.strip().replace("\n", " "))
        record = SessionRecord(problem)
        record.load()
        record.publish_audit(records, signatures)
        record._save_state()
        for line in link_claims(problem, owners, table, records, sources, signatures):
            print("  " + line)
    for slug in ORDER:
        for line in refresh_mirrors(ROOT / slug, stores):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
