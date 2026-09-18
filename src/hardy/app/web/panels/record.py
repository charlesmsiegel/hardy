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
    CitationContract,
    Obligation,
    ObligationStatus,
    ProjectItem,
    ProjectItemKind,
    Relation,
    RelationKind,
    Scope,
    VersionRef,
)
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.ledger.views import LedgerViews
from hardy.workflows.publication import PublicationRequest, plan_publication
from hardy.workflows.publish import assemble_publication

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


def _version_number(snapshot: LedgerSnapshot, ref: VersionRef) -> int | None:
    """`ref`'s 1-based position among the recorded revisions of its id, matching `ledger_item`'s own numbering.

    `None` when no revision of this id carries this exact digest -- a digest
    that has since been pruned, or a bug elsewhere -- so a caller can print
    "not reported" rather than a fabricated position.
    """
    for index, record in enumerate((r for r in snapshot.records if r.id == ref.id), start=1):
        if record.digest == ref.digest:
            return index
    return None


def graph(problem: Path) -> dict[str, Any]:
    """The project ledger's current heads and relations, with staleness against them.

    An edge is stale when either endpoint's pinned digest no longer matches
    that item's current head -- the relation was recorded against a version of
    the item that has since been revised, and nothing has re-checked it.

    `expected_version`/`current_version` name that revision precisely, when
    they can be: `LedgerViews.stale_artifacts()` (`ledger/views.py:158-166`)
    only reports on `documents`/`formalizes` relations whose *target* has
    moved past the pinned digest, each as a `StaleArtifact(record, relation,
    expected, current)` (`ledger/views.py:58-63`). An edge this task's own
    `stale` flag above marks stale for any other reason -- a `depends_on`
    relation, or one stale because its *source* moved rather than its target
    -- has no entry there, and both fields stay `None` rather than naming a
    version nobody measured.
    """
    snapshot = LedgerStore(problem).read()
    heads = {item.id: item for item in snapshot.current(ProjectItem)}
    stale_by_relation = {artifact.relation.id: artifact for artifact in LedgerViews(snapshot).stale_artifacts()}
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

        artifact = stale_by_relation.get(relation.id)
        edges.append({
            "id": relation.id, "kind": relation.kind.value, "source": relation.source.id,
            "target": relation.target.id, "evidence": sorted({evidence.kind.value for evidence in relation.evidence}),
            "stale": stale(relation.source) or stale(relation.target),
            "style": vocabulary.edge_style(relation.kind),
            "expected_version": _version_number(snapshot, artifact.expected) if artifact else None,
            "current_version": _version_number(snapshot, artifact.current) if artifact else None,
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


def _item_summary(item: ProjectItem) -> dict[str, Any]:
    """Every field a reader needs to tell one version of an item from another.

    Mirrors `graph()`'s node fields -- same names, same exact enum values --
    except `statement` is reported in full rather than sliced to
    `STATEMENT_LIMIT`: this is the one detail page for an item, not the map,
    and there is no per-status obligation count, because `ledger_item` reports
    every current obligation touching this id as its own list rather than
    folding it into a node's counts. Shared between the head and each entry
    in `versions` so the two are never a parallel, driftable spelling of the
    same facts.
    """
    return {
        "id": item.id,
        "digest": item.digest,
        "kind": item.kind.value,
        "family": vocabulary.family(item.kind),
        "name": item.name,
        "statement": item.statement,
        "origin": item.origin.value,
        "evidence": sorted({evidence.kind.value for evidence in item.evidence}),
        "artifacts": [artifact.uri for artifact in item.artifacts],
        "research": item.research.status if item.research else None,
    }


def ledger_item(problem: Path, item_id: str) -> dict[str, Any]:
    """One ledger item's head, plus every version history has ever held of it.

    `LedgerSnapshot`'s own promise (`state.py:2-3`) is that advancing a
    logical item never changes what an earlier theorem referenced, so every
    prior revision -- not only the current head -- stays addressable here,
    each carrying its own `digest` and its 1-based position in `versions`.
    `snapshot.records` is the full history; `snapshot.head` is `_heads`'s
    current entry (`state.py:31-44`).

    `relations` and `obligations` are filtered from the *current* heads of
    their own kinds -- matching `graph()` and `record_counts()` -- because a
    relation or an obligation is itself a versioned record with its own head;
    this page shows what currently touches this id, not a history of those
    records too. An id that names something other than a `ProjectItem` (an
    `Obligation`, a `Relation`, ...) is refused the same way an unknown id is:
    `ledger/item` is a page for items, and `validation.py`'s own rule --
    "record identity cannot change category" -- means every record sharing
    this id, once the head is confirmed a `ProjectItem`, is one too.
    """
    snapshot = LedgerStore(problem).read()
    head = snapshot.head(item_id)
    if not isinstance(head, ProjectItem):
        raise ValueError(f"{item_id!r} is not a project item")

    history = [record for record in snapshot.records if record.id == item_id]
    versions = [{**_item_summary(record), "version": index} for index, record in enumerate(history, start=1)]

    relations = []
    for relation in snapshot.current(Relation):
        if item_id in (relation.source.id, relation.target.id):
            relations.append({
                "id": relation.id, "kind": relation.kind.value,
                "source": relation.source.id, "target": relation.target.id,
                "evidence": sorted({evidence.kind.value for evidence in relation.evidence}),
                "style": vocabulary.edge_style(relation.kind),
            })

    obligations = []
    for obligation in snapshot.current(Obligation):
        if obligation.item.id == item_id:
            obligations.append({
                "id": obligation.id, "kind": obligation.kind.value,
                "status": obligation.status.value,
                "tone": vocabulary.obligation_tone(obligation.status),
                "reason": obligation.reason,
            })

    return {
        **_item_summary(head),
        "version": len(history),
        "versions": versions,
        "relations": relations,
        "obligations": obligations,
        "revision": snapshot.revision,
    }


def _session_state(problem: Path) -> dict[str, Any]:
    """`session.json` as a plain dict, degrading rather than raising.

    This is a read model over a problem directory, not a session: there may be
    no live `SessionRecord` at all, the file may be missing (a fresh project),
    or -- because `session.json` is versioned and gets merge-conflicted or
    hand-edited -- it may hold something that is not the schema-2 object this
    panel expects. Every one of those is "nothing has been recorded yet" for
    this panel's purposes, not a 500 for a problem the API otherwise renders
    fine. Unlike `sources()`'s corrupt-`bibliography.json` case (issue #169),
    nothing here reports a count a reader could mistake for a measurement --
    `{}` degrades every field downstream to `None`/absent through the same
    machinery a genuinely fresh project uses, so there is no exact-looking
    figure to get wrong. `SessionRecord._read_state` is deliberately not reused: it raises
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


def _lean_declarations(problem: Path) -> dict[str, dict[str, Any]]:
    """Every module's `declarations()` scan, straight off the files on disk.

    This is what `save_lean` itself scans before ever asking Lean anything,
    over the same tree the kernel later audits -- so a row exists here for
    every declaration a save could have registered, whether or not an audit
    of it has ever landed in `session.json`. Nothing here runs Lean or reads
    a build cache; a module the tree does not have is simply absent.

    Each entry also carries `"path"`, the file's location relative to
    `problem` (`"lean/Sylow.lean"`) -- the same string `workspace.files()`
    puts in a row's own `path`, so `file_verdicts` below can key its answer
    by it without either side recomputing the other's join of `LEAN_DIR` and
    the module's relative path. `results()` and `ledger_export()`, the two
    pre-existing callers, read only `found["theorem"]`/`"lemma"`/`"private"`
    and are unaffected by the extra key.
    """
    root = problem / LEAN_DIR
    if not root.is_dir():
        return {}
    return {
        module_name(relative): {**declarations(read_text(root, relative)), "path": f"{LEAN_DIR}/{relative}"}
        for relative in files_under(root, ".lean")
    }


def _shared_names(modules: Mapping[str, dict[str, Any]]) -> dict[str, tuple[str, ...]]:
    """Names more than one module declares -- the one thing `declaration_status` cannot grade."""
    occurrences: dict[str, list[str]] = {}
    for module, found in modules.items():
        for name in (*found["theorem"], *found["lemma"]):
            if name in found["private"]:
                continue
            occurrences.setdefault(name, []).append(module)
    return {name: tuple(mods) for name, mods in occurrences.items() if len(mods) > 1}


def file_verdicts(problem: Path) -> dict[str, dict[str, Any]]:
    """The worst grade among each lean/ file's own declarations, by path.

    Worst, not best: a file holding one verified theorem and one resting on a
    hole is not a verified file, and a header that said so would be the page
    making a claim the audit does not support. `GRADES` is ordered worst
    first, so the minimum index is the answer.

    A file that declares nothing has no verdict at all -- `None`, not
    `unaudited`. `unaudited` says "no stored verdict names it", which is a
    statement about a name; a file with no names has nothing for a verdict to
    be about.
    """
    modules = _lean_declarations(problem)
    shared = _shared_names(modules)
    state = _session_state(problem)
    audit_records = state.get("audit")
    audit_records = _expired_records(
        audit_records if isinstance(audit_records, dict) else {}, modules
    )
    out: dict[str, dict[str, Any]] = {}
    for info in modules.values():
        names: list[str] = []
        for declared_kind in ("theorem", "lemma"):
            for name in info[declared_kind]:
                if name in info["private"]:
                    continue
                names.append(name)
        if not names:
            out[info["path"]] = {"verdict": None, "declares": []}
            continue
        statuses = [audit_module.declaration_status(name, audit_records, shared=shared) for name in names]
        worst = min(statuses, key=lambda status: audit_module.GRADES.index(status.kind))
        out[info["path"]] = {
            "verdict": {"kind": worst.kind, "tone": vocabulary.verdict_tone(worst.kind),
                        "detail": str(worst)},
            "declares": names,
        }
    return out


def _expired_records(
    audit_records: Mapping[str, Mapping[str, Any]],
    modules: Mapping[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Stored audits, with the ones the tree has outgrown marked stale.

    The one part of `_still_current`'s job this panel can do honestly. A
    stored record grades declarations in a module; if that module is no longer
    in the tree, or no longer declares the name the record graded, then the
    record demonstrably does not describe what is on disk. `git checkout`, a
    rename, a deleted file and a hand-edit that removed a theorem all land
    here.

    What it cannot catch is a body edited in place under the same name: the
    name is still declared, and deciding whether the proof beneath it changed
    needs the build signature this panel cannot compute. `_kernel_lane`
    reports `revalidated: False` for that remainder rather than passing it off
    as checked.

    `stale` is set with a reason rather than the record being dropped:
    `declaration_status` reads the flag and answers `stale` with the reason
    attached, which is a different and more useful claim than `unaudited`
    ("no stored verdict names it"). Something did grade this name; it has
    since expired.
    """
    # Every name the module declares, whatever kind. `declarations()` groups
    # them per keyword (`theorem`, `lemma`, `def`, `private`, ...), and an
    # audit record can name any of them, so the union is what an expiry check
    # has to compare against -- reading only `theorem`/`lemma` would expire a
    # graded `def` on every read.
    declared: dict[str, set[str]] = {
        module: {
            str(name)
            for key, value in info.items()
            if key != "path" and isinstance(value, (list, tuple))
            for name in value
        }
        for module, info in modules.items()
    }
    out: dict[str, dict[str, Any]] = {}
    for module, record in audit_records.items():
        names = [str(entry.get("name")) for entry in record.get("declarations", ())]
        present = declared.get(module)
        if present is None:
            out[module] = {**record, "stale": True,
                           "reason": f"{module} is no longer in the Lean tree"}
            continue
        gone = [name for name in names if name not in present]
        if gone:
            out[module] = {
                **record, "stale": True,
                "reason": f"{module} no longer declares {', '.join(sorted(gone))}",
            }
            continue
        out[module] = dict(record)
    return out


def _kernel_lane(
    name: str, audit_records: Mapping[str, Mapping[str, Any]], shared: Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    """`⊢ kernel says`: the stored audit verdict for this declaration, and nothing else.

    Sourced from `session.json`'s own `audit` map -- what `formal.audit.classify`
    wrote the moment Lean's `#print axioms` was last read for this module --
    through `audit.declaration_status`, the one function every other surface
    (`/status`, `/export`) reads a per-declaration verdict through, so this
    page cannot grade a theorem differently than the terminal does.

    Revalidation is the hard part, and this panel can do only part of it.
    `_still_current` decides whether a stored record still describes the tree
    by recomputing `current_signatures()` -- a digest over the toolchain
    identity, the module source, its workspace dependencies and the olean
    stamps of everything it imports from outside. The hashing itself is pure,
    but the environment string and the external stamps come from a live
    `LeanWorkspace` over the configured Lake project, which this read-only
    panel has no access to.

    So two things happen instead of quietly assuming the record is current.

    First, the half that IS decidable from the problem directory alone:
    `_expired_records` marks a record stale when the tree no longer declares
    the name it graded -- a file deleted, a theorem renamed, a module gone
    after a `git checkout`. `declaration_status` already refuses to grade from
    a stale record, so such a name reads as expired rather than as verified.

    Second, for everything left -- a body edited in place under the same
    name, a rebuilt Lake project, a different Lean -- the verdict is reported
    with `revalidated: False`. The page prints the qualifier beside it. The
    verdict is still worth showing: it is the last thing the kernel actually
    established about that name. What it is not is a statement about the bytes
    now on disk, and the lane must not imply otherwise.
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
        # Never True from this panel: see the docstring. Carried as a field
        # rather than left to the client to remember, so a second surface
        # reading this lane cannot forget to say it.
        "revalidated": False,
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


def _recorded_names(state: Mapping[str, Any]) -> list[dict[str, str]]:
    """The durable Lean-to-LaTeX correspondences this session has recorded.

    Written by the session's own `record_name` tool, whose description says
    what it is for: "the durable correspondence between a Lean declaration and
    its LaTeX label/name". It is the ONLY source for "where is this theorem
    stated in tex/" -- a `ProjectItem` carries no label, and deriving one from
    a file name or a theorem name would be the UI asserting a correspondence
    nobody recorded.

    Served beside the theorem table rather than folded into each row: a
    correspondence can name a declaration the Lean tree no longer declares,
    and a row-only view would silently drop it. A reader looking at a writeup
    label that maps to nothing is looking at exactly the case worth seeing.
    """
    recorded = state.get("names")
    if not isinstance(recorded, list):
        return []
    return [
        {
            "formal_name": str(entry.get("formal_name", "")),
            "latex_name": str(entry.get("latex_name", "")),
            "description": str(entry.get("description", "")),
        }
        for entry in recorded
        if isinstance(entry, dict)
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
    reports = state.get("reports")
    reports = reports if isinstance(reports, list) else []

    modules = _lean_declarations(problem)
    shared = _shared_names(modules)
    audit_records = state.get("audit")
    audit_records = _expired_records(
        audit_records if isinstance(audit_records, dict) else {}, modules
    )

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
    return {"theorems": theorems, "names": _recorded_names(state), "revision": snapshot.revision}


#: `LedgerViews.publication` requires a `Scope`, but nothing in the schema
#: names *the* scope for a bare item id -- a `Scope` is a recorded
#: project/trust policy (`ledger/contracts.py:247-261`) that several claims
#: can share and a claim can appear in none, one, or several of. The one
#: unambiguous link from an item to a scope is `Scope.must_prove`, the set of
#: roots that scope commits to proving (`ledger/policy.py:257`,
#: `interactive/project_summary.py:66,71` use the same membership test). When
#: none names this item, this sentinel stands in only for the scope-blind
#: half of `publication()` -- `closure`, `obligations`, `stale`,
#: `required_declarations`, `required_bindings` and `citations_open`, none of
#: which read `scope` (`ledger/views.py:185-204`) -- so `ledger_export` can
#: still show a dependency closure. It is never persisted and its
#: `unestablished`/`ready` are always discarded: `LedgerPolicy._current_scope`
#: (`ledger/policy.py:304-306`) raises "stale or unrecorded scope" for any
#: item it is asked about, which `premise_allowed` swallows into an
#: unconditional `False` (`ledger/policy.py:226-231`) -- a blanket "not
#: established" that is an artifact of the sentinel, not a finding, and must
#: never be reported as one.
_UNSCOPED = Scope(id="unscoped")


def ledger_export(problem: Path, item_id: str) -> dict[str, Any]:
    """The Export proof card's dependency closure: one row per closure member.

    Each row reads `name . record . verdict . writeup`, mirroring the design:
    `record` and `verdict` are `_record_lane`/`_kernel_lane`, the same two
    lanes `results()` already builds, so a claim reads the same way on both
    pages. `writeup` is new here and deliberately thin -- of the design's
    three writeup states (`writeup: N words . reader_agreed`, *not written --
    statement only*, `approved assumption . no proof`), only two facts are
    actually recorded anywhere: `documented` (a current `RelationKind.DOCUMENTS`
    relation targets this item -- `ledger/contracts.py:347`, the same relation
    `project.py:link()`'s `"documents"` operation writes and
    `LedgerGraph.publication_closure` already follows, `ledger/graph.py:400-401`)
    and `assumed` (this item is in the selected scope's `allowed_background`
    or `allowed_interfaces` -- the same membership test
    `LedgerPolicy.trust_boundary` uses at `ledger/policy.py:257`). Nothing in
    the schema records a writeup's word count or whether a reader agreed with
    it -- `ProjectItem`, `Relation`, `CitationContract` and `EvidenceKind` all
    lack such a field (`ledger/contracts.py`), and `EvidenceKind.FAITHFULNESS`
    /`FaithfulnessOutcome.AGREED`/`FaithfulnessVerdict.agreed`
    (`workflows/contracts.py:122,210`) grade a Lean translation against a
    statement, not a reader's agreement with prose.
    So `words` and `reader_agreed` are always `None` here -- reported as
    genuinely absent, never guessed from `verdict` or from `record`.
    """
    snapshot = LedgerStore(problem).read()
    head = snapshot.head(item_id)
    if not isinstance(head, ProjectItem):
        raise ValueError(f"{item_id!r} is not a project item")

    views = LedgerViews(snapshot)
    scopes = snapshot.current(Scope)
    candidates = tuple(sorted((s for s in scopes if head.ref in s.must_prove), key=lambda s: s.id))
    scope = candidates[0] if candidates else None
    publication = views.publication(head.ref, scope if scope is not None else _UNSCOPED)

    documented = {relation.target.id for relation in views.graph.relations
                 if relation.kind == RelationKind.DOCUMENTS}
    assumed = set(scope.allowed_background + scope.allowed_interfaces) if scope is not None else set()
    unestablished = {ref.id for ref in publication.unestablished} if scope is not None else None
    stale_ids = {ref.id for artifact in publication.stale for ref in (artifact.record, artifact.expected)}

    state = _session_state(problem)
    modules = _lean_declarations(problem)
    shared = _shared_names(modules)
    audit_records = state.get("audit")
    audit_records = _expired_records(
        audit_records if isinstance(audit_records, dict) else {}, modules
    )

    rows = []
    for ref in publication.closure:
        record = snapshot.get(ref)
        if not isinstance(record, ProjectItem):
            continue
        kernel = _kernel_lane(record.name, audit_records, shared)
        rows.append({
            "id": record.id,
            "name": record.name,
            "family": vocabulary.family(record.kind),
            "kind": record.kind.value,
            "record": _record_lane(record, scopes),
            "verdict": kernel["verdict"],
            "tone": vocabulary.verdict_tone(kernel["verdict"]),
            "writeup": {
                "documented": record.id in documented,
                "words": None,
                "reader_agreed": None,
                "assumed": (ref in assumed) if scope is not None else None,
            },
            "unestablished": (record.id in unestablished) if scope is not None else None,
            "stale": record.id in stale_ids,
        })
    rows.sort(key=lambda row: row["name"])

    citations = [{
        "id": citation.id, "use_site": citation.use_site.id, "required_claim": citation.required_claim.id,
        "paper_id": citation.paper_id, "paper_version": citation.paper_version,
        "conclusion": citation.conclusion, "status": citation.status.value,
    } for citation in snapshot.current(CitationContract) if citation.ref in publication.citations_open]

    obligations = [{
        "id": obligation.id, "kind": obligation.kind.value, "status": obligation.status.value,
        "tone": vocabulary.obligation_tone(obligation.status), "item": obligation.item.id,
        "reason": obligation.reason,
    } for obligation in publication.obligations]

    return {
        "root": head.id,
        "name": head.name,
        "scope": scope.id if scope is not None else None,
        # More than one scope can commit to proving the same root; nothing in
        # the schema picks one over another, so every candidate is reported --
        # not only the one whose closure was actually computed above.
        "scope_candidates": [s.id for s in candidates],
        "ready": publication.ready if scope is not None else None,
        "rows": rows,
        "obligations": obligations,
        "citations_open": citations,
        "required_declarations": [d.id for d in publication.required_declarations],
        "required_bindings": [b.id for b in publication.required_bindings],
        # The design's card shows `/export proof X --deps --writeups --lean
        # --verdicts --format pdf` as a generated command. It does not exist:
        # `handlers.py:1529` registers `/export` with no `proof` subcommand
        # and none of those flags -- it writes one HTML account of the whole
        # session (`handle_export`, `handlers.py:864-894`), not a per-theorem
        # PDF. A control that cannot work is not offered: this reports why,
        # in place of the syntax the design invented, so the client renders
        # the button disabled with the reason visible rather than a command
        # that would fail the moment someone typed it.
        "export_command": {
            "available": False,
            "reason": ("hardy has no `/export proof` subcommand and no --deps/--writeups/--lean/--verdicts "
                      "flags (handlers.py:1529); the existing `/export` writes one shareable HTML account "
                      "of the whole session, not a per-theorem PDF."),
        },
        "revision": snapshot.revision,
    }


def publications(problem: Path) -> dict[str, Any]:
    """Every ledger item's publication presentation, plus a draft preview for each scope claiming it as a root.

    Two halves, matching the design's two builders. `items` is every current
    `ProjectItem`'s exact publication state -- `publication_visibility` and
    `publication_role` (`ledger/contracts.py:93,99`), the same fields
    `/project mark ITEM internal|public|omitted` reads and writes
    (`ProjectOperations.mark`, `workflows/interactive/project.py:54-68`) --
    plus `link_source_kinds`, which `/project link SOURCE
    illustrates|documents TARGET` relation kind(s) this item's own kind may
    originate as `SOURCE` (`vocabulary.link_source_kinds`, itself mirroring
    `ProjectOperations.link`'s own `allowed` dict, `project.py:71-74`). Any
    *other* current item is always a legal `TARGET` -- `link()` only requires
    `origin.ref != subject.ref` -- so nothing here narrows that side.

    `candidates` is one row per `(scope, root)` pair a `Scope.must_prove`
    already commits to -- exactly what `ProjectOperations.publish`'s own
    bare-id lookup treats as *the* publishable root for that scope
    (`project.py:99-106`) -- previewing what `/project publish ITEM --scope
    SCOPE --output BUNDLE` would report without running it. `plan_publication`
    is the free function `PublicationPlanner.plan` itself delegates to
    (`workflows/publication.py:191`, `snapshot=self.store.read()`); calling it
    directly against the one snapshot already read above -- rather than
    constructing a `PublicationPlanner` that would reread the store once per
    candidate -- keeps every row answering about the same instant, the same
    reason `results()` and `ledger_export()` read their `LedgerStore` once
    and reuse it. `assemble_publication` (pure text escaping and digesting,
    no LaTeX, no filesystem write) then turns the plan into the same
    `ready`/`gaps` the terminal prints (`handle_publication`,
    `tui/project.py:62-69`) -- the actual compile step, `PublishWorkflow.publish`
    (`workflows/publish.py:167-189`), is genuinely mutating (it creates
    `publications/<name>/` on disk) and is not reachable from this GET.

    A root that no longer resolves to a `ProjectItem` -- `Scope.must_prove`'s
    own schema only requires each entry resolve to *some* record
    (`ledger/validation.py`'s referential check), not a `ProjectItem`
    specifically -- or a blank name `assemble_publication` refuses as a title,
    is dropped from `candidates` rather than failing the whole list: one
    stale scope must not take the page listing every other candidate down
    with it, the same defence `runs()` applies per run directory.
    """
    snapshot = LedgerStore(problem).read()

    items = [
        {
            "id": item.id, "name": item.name, "kind": item.kind.value, "family": vocabulary.family(item.kind),
            "visibility": item.publication_visibility.value,
            "visibility_tone": vocabulary.publication_visibility_tone(item.publication_visibility),
            "role": item.publication_role.value if item.publication_role is not None else None,
            "link_source_kinds": list(vocabulary.link_source_kinds(item.kind)),
        }
        for item in snapshot.current(ProjectItem)
    ]

    candidates: list[dict[str, Any]] = []
    for scope in snapshot.current(Scope):
        for ref in scope.must_prove:
            try:
                plan = plan_publication(snapshot, PublicationRequest(roots=(ref,), scope=scope.ref))
                record = snapshot.get(ref)
                draft = assemble_publication(plan, title=record.name)
            except ValueError:
                continue
            candidates.append({
                "item": record.id, "name": record.name, "kind": record.kind.value,
                "family": vocabulary.family(record.kind), "scope": scope.id,
                "visibility": record.publication_visibility.value,
                "visibility_tone": vocabulary.publication_visibility_tone(record.publication_visibility),
                "role": record.publication_role.value if record.publication_role is not None else None,
                "ready": draft.ready,
                "closure": len(plan.closure),
                "gaps": list(draft.gaps),
            })
    candidates.sort(key=lambda row: (row["name"], row["scope"]))

    return {"items": items, "candidates": candidates, "revision": snapshot.revision}
