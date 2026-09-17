"""Read-only views of the project ledger."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from hardy.app.web.panels import vocabulary
from hardy.formal import audit as audit_module
from hardy.formal.syntax import declarations, module_name
from hardy.foundation.files import files_under, read_text
from hardy.workflows.ledger.contracts import (
    Obligation,
    ObligationStatus,
    ProjectItem,
    ProjectItemKind,
    Relation,
    Scope,
)
from hardy.workflows.ledger.store import LedgerStore

#: How much of a ledger item's statement the graph panel carries per node. The
#: graph is a map of the project, not a reader for full statements -- `tex/`
#: and the lean tree already serve those in full.
STATEMENT_LIMIT = 400

#: Where the Lean tree an interactive session owns sits under a problem
#: directory. Matches `hardy.workflows.interactive.session.LEAN_DIR`; not
#: imported from there because that module wires a live session together
#: (a runtime, a runner, a confirm callback) and this panel reads files only.
LEAN_DIR = "lean"

#: `session.json`'s own name (`hardy.workflows.layout.RECORD`), read directly
#: here rather than through `SessionRecord`: that owner holds write locks and
#: replays the transcript on load, machinery a read-only panel over a problem
#: directory -- which may have no live session at all -- has no business
#: paying for. Every other artifact panel in this package (`workspace.py`,
#: `graph`, `record_counts` above) reads its files the same way.
SESSION_RECORD = "session.json"

#: `ProjectItem` kinds a `⊢`-lane row's `§` lane may match against: what an
#: `ObligationKind.PROVE`/`RESOLVE_GOAL` obligation can be filed against, per
#: `hardy.workflows.ledger.policy._FACTS`. Kept as its own frozenset rather
#: than imported, because `policy._FACTS` is private and this reader has no
#: business depending on its exact object identity -- only on which kinds are
#: results, which is public knowledge about the schema.
RESULT_KINDS = frozenset({
    ProjectItemKind.THEOREM, ProjectItemKind.LEMMA, ProjectItemKind.PROPOSITION,
    ProjectItemKind.COROLLARY, ProjectItemKind.CLAIM, ProjectItemKind.EXTERNAL_RESULT,
    ProjectItemKind.CONJECTURE,
})


def graph(problem: Path) -> dict[str, Any]:
    """The project ledger's current heads and relations, with staleness against them.

    An edge is stale when either endpoint's pinned digest no longer matches
    that item's current head -- the relation was recorded against a version of
    the item that has since been revised, and nothing has re-checked it.
    """
    snapshot = LedgerStore(problem).read()
    heads = {item.id: item for item in snapshot.current(ProjectItem)}
    counts: dict[str, Counter[str]] = {}
    for obligation in snapshot.current(Obligation):
        # The exact status, not a bucket. Six values go in and six come out:
        # `investigating` and `blocked` are not `other`, and a reader deciding
        # whether to trust an item needs to know which one it is.
        counts.setdefault(obligation.item.id, Counter())[obligation.status.value] += 1
    nodes = []
    for item in heads.values():
        statement = item.statement or ""
        nodes.append({
            "id": item.id, "digest": item.digest, "kind": item.kind.value, "name": item.name,
            "statement": statement[:STATEMENT_LIMIT], "origin": item.origin.value,
            "evidence": sorted({evidence.kind.value for evidence in item.evidence}),
            "artifacts": [artifact.uri for artifact in item.artifacts],
            "research": item.research.status if item.research else None,
            "family": vocabulary.family(item.kind),
            "obligations": dict(counts.get(item.id, Counter())),
        })
    edges = []
    for relation in snapshot.current(Relation):
        def stale(ref) -> bool:
            head = heads.get(ref.id)
            return head is None or head.digest != ref.digest

        edges.append({
            "id": relation.id, "kind": relation.kind.value, "source": relation.source.id,
            "target": relation.target.id, "evidence": sorted({evidence.kind.value for evidence in relation.evidence}),
            "stale": stale(relation.source) or stale(relation.target),
            "style": vocabulary.edge_style(relation.kind),
        })
    return {
        "nodes": nodes, "edges": edges, "revision": snapshot.revision,
        # A constant map over the enum, not a property of any one node, so it
        # rides the payload once rather than being repeated per-obligation or
        # rebuilt in the browser -- the same reason `family` and `style` are
        # decided here: an obligation status the browser doesn't recognise
        # must fail loudly (`vocabulary.obligation_tone` raises `KeyError`),
        # not fall back to some default colour picked client-side.
        "tones": {status.value: vocabulary.obligation_tone(status) for status in ObligationStatus},
    }


def record_counts(problem: Path) -> dict[str, Any]:
    """What the project ledger currently holds, counted every way Home asks.

    Counted over heads, not over history: an item revised five times is one
    item. Both groupings are returned because the family is what the card
    colours by and the kind is what it prints -- deriving one from the other in
    the browser is how a grouped label quietly becomes the displayed one.
    """
    snapshot = LedgerStore(problem).read()
    items = list(snapshot.current(ProjectItem))
    by_family: Counter[str] = Counter()
    by_kind: Counter[str] = Counter()
    evidence: Counter[str] = Counter()
    for item in items:
        by_family[vocabulary.family(item.kind)] += 1
        by_kind[item.kind.value] += 1
        for reference in item.evidence:
            evidence[reference.kind.value] += 1
    obligations: Counter[str] = Counter()
    for obligation in snapshot.current(Obligation):
        obligations[obligation.status.value] += 1
    return {
        "items": len(items),
        "by_family": dict(by_family),
        "by_kind": dict(by_kind),
        "evidence": dict(evidence),
        "obligations": dict(obligations),
        "revision": snapshot.revision,
    }


def ledger_list(problem: Path) -> dict[str, Any]:
    """Every ledger head, one row each, for the browser's own filter pills.

    The prototype's pills -- `kind`, `origin`, `evidence`, `open obligations`,
    `stale only` -- all run against this one list in the browser: the list is
    small and a round trip per pill would cost more than it saves. So every
    head comes back unfiltered, with the exact enum values a pill needs to
    group by and the label needs to print. `family` is the one exception,
    carried alongside `kind` rather than derived from it client-side -- the
    server classifies, the client only colours.
    """
    snapshot = LedgerStore(problem).read()
    counts: dict[str, Counter[str]] = {}
    for obligation in snapshot.current(Obligation):
        # The exact status, matching `graph()` rather than reinventing it:
        # six values go in, six come out.
        counts.setdefault(obligation.item.id, Counter())[obligation.status.value] += 1
    items = []
    for item in snapshot.current(ProjectItem):
        items.append({
            "id": item.id,
            "kind": item.kind.value,
            "family": vocabulary.family(item.kind),
            "name": item.name,
            "origin": item.origin.value,
            "evidence": sorted({evidence.kind.value for evidence in item.evidence}),
            "obligations": dict(counts.get(item.id, Counter())),
            # How many records in the full history -- not just the current
            # heads `current()` returns -- share this id. `_heads` in
            # `ledger/state.py` keeps only the latest per id; `records` keeps
            # every revision, so this is the one place that still knows how
            # many times an item has been revised.
            "version": len([record for record in snapshot.records if record.id == item.id]),
        })
    return {"items": items, "revision": snapshot.revision}


def _session_state(problem: Path) -> dict[str, Any]:
    """`session.json` as a plain dict, degrading rather than raising.

    This is a read model over a problem directory, not a session: there may be
    no live `SessionRecord` at all, the file may be missing (a fresh project),
    or -- because `session.json` is versioned and gets merge-conflicted or
    hand-edited -- it may hold something that is not the schema-2 object this
    panel expects. Every one of those is "nothing has been recorded yet" for
    this panel's purposes, the same degrade `sources()` gives a corrupt
    `bibliography.json`, not a 500 for a problem the API otherwise renders
    fine. `SessionRecord._read_state` is deliberately not reused: it raises
    `SchemaError` on all of this, which is the right contract for the one
    writer of the file and the wrong one for a reader that must never take a
    problem page down over it.
    """
    path = problem / SESSION_RECORD
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _lean_declarations(problem: Path) -> dict[str, dict[str, tuple[str, ...]]]:
    """Every module's `declarations()` scan, straight off the files on disk.

    This is what `save_lean` itself scans before ever asking Lean anything,
    over the same tree the kernel later audits -- so a row exists here for
    every declaration a save could have registered, whether or not an audit
    of it has ever landed in `session.json`. Nothing here runs Lean or reads
    a build cache; a module the tree does not have is simply absent.
    """
    root = problem / LEAN_DIR
    if not root.is_dir():
        return {}
    return {
        module_name(relative): declarations(read_text(root, relative))
        for relative in files_under(root, ".lean")
    }


def _shared_names(modules: Mapping[str, dict[str, tuple[str, ...]]]) -> dict[str, tuple[str, ...]]:
    """Names more than one module declares -- the one thing `declaration_status` cannot grade."""
    occurrences: dict[str, list[str]] = {}
    for module, found in modules.items():
        for name in (*found["theorem"], *found["lemma"]):
            if name in found["private"]:
                continue
            occurrences.setdefault(name, []).append(module)
    return {name: tuple(mods) for name, mods in occurrences.items() if len(mods) > 1}


def _kernel_lane(
    name: str, audit_records: Mapping[str, Mapping[str, Any]], shared: Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    """`⊢ kernel says`: the stored audit verdict for this declaration, and nothing else.

    Sourced from `session.json`'s own `audit` map -- what `formal.audit.classify`
    wrote the moment Lean's `#print axioms` was last read for this module --
    through `audit.declaration_status`, the one function every other surface
    (`/status`, `/export`) reads a per-declaration verdict through, so this
    page cannot grade a theorem differently than the terminal does.

    What this panel does NOT do: ask whether that stored record is still
    current against the tree on disk. `_still_current` answers that by
    rebuilding `current_signatures()` against a live `LeanWorkspace` and its
    build cache -- machinery this read-only panel, which runs no Lean and
    opens no build tree, does not have. So a verdict here is the one last
    established, not necessarily the one a fresh save would report; a stale
    verdict from a moved toolchain reads as whatever it last said rather than
    as `stale`. Documented here rather than silently assumed current.
    """
    status = audit_module.declaration_status(name, audit_records, shared=shared)
    axioms: list[str] | None = None
    signature: str | None = None
    if status.kind not in audit_module.UNESTABLISHED:
        for record in audit_records.values():
            match = next(
                (entry for entry in record.get("declarations", ()) if str(entry.get("name")) == name), None,
            )
            if match is not None:
                axioms = [str(item) for item in match.get("axioms", ())]
                signature = record.get("signature") or None
                break
    return {
        "verdict": status.kind,
        "detail": str(status),
        "axioms": axioms,
        "sorry": _carries_a_hole(axioms) if axioms is not None else None,
        "assumed": list(status.assumed),
        "unapproved": list(status.unapproved),
        "modules": list(status.modules),
        # The build signature the module's verdict was stamped with, not a
        # human-readable Lean/Mathlib version: that identity (`lean-toolchain`,
        # `lake-manifest.json`) lives beside a `lean_project` this panel is
        # never given, configured once per machine rather than per problem.
        # The signature is what the verdict actually rests on -- it already
        # folds the toolchain, the environment and every import in -- so it
        # is reported as exactly that rather than invented as a version string.
        "signature": signature,
    }


def _carries_a_hole(axioms: Sequence[str]) -> bool:
    """Whether `sorryAx` -- Lean's own marker for an unfinished proof -- is among these."""
    return any(axiom in audit_module.FORBIDDEN for axiom in axioms)


def _match_claim(name: str, claims_by_name: Mapping[str, ProjectItem]) -> ProjectItem | None:
    """The ledger item this Lean declaration corresponds to, if any names it.

    `ProjectItem` carries no field that pins it to a Lean declaration -- no
    interactive workflow writes one when a theorem is saved (`AdmissionOwners`,
    the one bridge the schema offers, is never constructed by the running
    app today). `name` is the only correlation the schema offers at all, so it
    is matched exactly, against the qualified name Lean reports and against its
    bare leaf -- the same two spellings `report_result` itself accepts. A
    project that names its ledger items to match is found; one that does not
    renders `Absent` rather than guessing.
    """
    leaf = name.rsplit(".", 1)[-1]
    return claims_by_name.get(name) or claims_by_name.get(leaf)


def _record_lane(item: ProjectItem, scopes: Sequence[Scope]) -> dict[str, Any]:
    """`§ record says`: this ledger item and its own evidence claims, verbatim.

    `evidence` here is what the `ProjectItem` itself carries -- `EvidenceRef`s
    nobody has re-authenticated for this read. `LedgerPolicy` is what checks an
    `EvidenceRef` against its owning capability's durable record before trusting
    it for anything load-bearing; this panel does none of that; it reports the
    claim, not a re-verified one, which is exactly why it is drawn in its own
    lane rather than folded into `⊢`.

    `claimed_in` and `assumptions` come from `Scope`, not from the kernel's own
    axiom audit: a `Scope` states what the *project* has permitted a claim to
    rest on, which is a policy fact and can differ from what the kernel actually
    found -- a scope may allow more than a proof used, or a kernel audit may
    name something the scope never admitted. Surfacing both is the point.
    """
    claimed_in: list[dict[str, str]] = []
    assumptions: set[str] = set()
    for scope in scopes:
        if item.ref in scope.must_prove:
            claimed_in.append({"scope": scope.id, "role": "must_prove"})
            assumptions.update(ref.id for ref in (*scope.allowed_background, *scope.allowed_interfaces))
        if item.ref in scope.allowed_background:
            claimed_in.append({"scope": scope.id, "role": "allowed_background"})
        if item.ref in scope.allowed_interfaces:
            claimed_in.append({"scope": scope.id, "role": "allowed_interfaces"})
    return {
        "id": item.id, "name": item.name, "kind": item.kind.value, "origin": item.origin.value,
        "statement": item.statement,
        "evidence": [
            {
                "kind": reference.kind.value, "producer": reference.producer,
                "artifact": {
                    "uri": reference.artifact.uri, "digest": reference.artifact.digest,
                    "locator": reference.artifact.locator,
                },
            }
            for reference in item.evidence
        ],
        "claimed_in": claimed_in,
        "assumptions": sorted(assumptions),
    }


def _model_lane(name: str, reports: Sequence[Any]) -> list[dict[str, Any]] | None:
    """`" model said "`: every `report_result` that named this theorem, quoted.

    Sourced from `session.json`'s own `reports` list -- written once, appended
    only, by `SessionRecord._report_result` -- and never touched again by
    anything else in this codebase; `summary` here is exactly the string the
    model wrote, never re-derived from the kernel's verdict or the ledger's
    claim. `None` when no report ever named this theorem, not an empty list:
    the theorem was simply never reported on, which is a different fact from
    a report that said nothing.
    """
    matches = [
        entry for entry in reports
        if isinstance(entry, dict) and name in (entry.get("theorems") or ())
    ]
    if not matches:
        return None
    return [
        {
            "summary": str(entry.get("summary", "")),
            "status": entry.get("status"),
            "open": list(entry.get("open") or ()),
            "assumptions": list(entry.get("assumptions") or ()),
            "statement": (entry.get("statements") or {}).get(name),
        }
        for entry in matches
    ]


def results(problem: Path) -> dict[str, Any]:
    """The theorem table: one row per saved Lean declaration, three lanes each.

    A row exists for every `theorem`/`lemma` the Lean tree currently declares,
    whether or not anything has ever audited, claimed or reported on it -- the
    tree is the one artifact that cannot lie about what is saved. Each row then
    carries `⊢ kernel says` (`session.json`'s own audit records), `§ record
    says` (a matching `ProjectItem`, if the ledger has one) and `" model said "`
    (`session.json`'s own `reports`), built by three functions that each read
    one source and never one another's result -- see `_kernel_lane`,
    `_record_lane` and `_model_lane`.
    """
    snapshot = LedgerStore(problem).read()
    claims_by_name: dict[str, ProjectItem] = {}
    for item in snapshot.current(ProjectItem):
        if item.kind in RESULT_KINDS:
            claims_by_name.setdefault(item.name, item)
    scopes = snapshot.current(Scope)

    state = _session_state(problem)
    audit_records = state.get("audit")
    audit_records = audit_records if isinstance(audit_records, dict) else {}
    reports = state.get("reports")
    reports = reports if isinstance(reports, list) else []

    modules = _lean_declarations(problem)
    shared = _shared_names(modules)

    theorems: list[dict[str, Any]] = []
    for module in sorted(modules):
        found = modules[module]
        for declared_kind in ("theorem", "lemma"):
            for name in found[declared_kind]:
                if name in found["private"]:
                    continue
                kernel = _kernel_lane(name, audit_records, shared)
                claim = _match_claim(name, claims_by_name)
                record = _record_lane(claim, scopes) if claim is not None else None
                model = _model_lane(name, reports)
                theorems.append({
                    "id": f"{module}:{name}", "name": name, "module": module,
                    "declared_kind": declared_kind,
                    "family": vocabulary.declared_family(declared_kind),
                    "verdict": kernel["verdict"],
                    "tone": vocabulary.verdict_tone(kernel["verdict"]),
                    "axioms": kernel["axioms"],
                    "sorry": kernel["sorry"],
                    "kernel": kernel,
                    "record": record,
                    "model": model,
                })
    theorems.sort(key=lambda row: (row["module"], row["name"]))
    return {"theorems": theorems, "revision": snapshot.revision}
