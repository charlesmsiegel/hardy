"""`hardy library`: the minimal control surface over the personal mathematical library.

Import a file, list and inspect what is held, build a source tree, confirm a
bibliographic grouping, and seed the active problem with a source. Each verb
prints what the backend recorded and nothing it inferred; a rich browser is a
separate, later effort.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from hardy.app.config import Config
from hardy.literature.sources.artifacts import ArtifactError, ImportRefused, ImportRequest
from hardy.literature.sources.catalog import CatalogError
from hardy.literature.sources.contracts import (
    AccessPolicy,
    GroupingDecision,
    GroupingProposal,
    IdentityEvidence,
)
from hardy.literature.sources.library import ManagedLibrary
from hardy.literature.sources.reading import SourceReader, SourceUnavailable
from hardy.literature.sources.seeds import SeedStore, new_seed
from hardy.literature.sources.tools import library_root
from hardy.literature.sources.trees import TreeError


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    library = subparsers.add_parser("library", help="import, inspect and seed literature sources in the personal library")
    verbs = library.add_subparsers(dest="library_command")

    imp = verbs.add_parser("import", help="copy a scholarly file into the managed library and extract it")
    imp.add_argument("path", help="the file to import; the original is never read again after admission")
    imp.add_argument("--access", choices=[a.value for a in AccessPolicy], default=AccessPolicy.PRIVATE_LOCAL.value, help="access policy recorded on the artifact")
    imp.add_argument("--title", help="a title to propose the bibliographic identity from, when the file carries none")
    imp.add_argument("--author", help="an author to record beside the title")
    imp.add_argument("--no-extract", action="store_true", help="admit the bytes without running the format adapter")

    verbs.add_parser("list", help="list the artifacts held in the library")

    show = verbs.add_parser("show", help="print one artifact's record, provenance, representations, trees and identity proposals")
    show.add_argument("artifact", help="artifact digest or a unique prefix of it")

    tree = verbs.add_parser("tree", help="build and admit a deterministic source tree for one artifact")
    tree.add_argument("artifact", help="artifact digest or a unique prefix of it")

    source_map = verbs.add_parser("map", help="print the structural map of one artifact's preferred tree")
    source_map.add_argument("artifact", help="artifact digest or a unique prefix of it")
    source_map.add_argument("--depth", type=int, default=2, help="container depth to expand")

    confirm = verbs.add_parser("confirm", help="record a human confirmation that an artifact is a copy of an edition")
    confirm.add_argument("artifact", help="artifact digest or a unique prefix of it")
    confirm.add_argument("edition", help="the edition id from `hardy library show`")
    confirm.add_argument("--reason", default="confirmed by the user", help="why the user is sure")

    seed = verbs.add_parser("seed", help="seed the active problem with one artifact so the session may read it")
    seed.add_argument("artifact", help="artifact digest or a unique prefix of it")
    seed.add_argument("--priority", type=int, default=0, help="retrieval priority among seeds; higher first")
    seed.add_argument("--intent", help="what the problem wants from this source")

    unseed = verbs.add_parser("unseed", help="withdraw a seed from the active problem")
    unseed.add_argument("seed", help="the seed id from `hardy library seeds`")

    verbs.add_parser("seeds", help="list the active problem's seeds")

    verbs.add_parser("report", help="print evaluation counts derived from the library's records, per dimension")


def main(args: argparse.Namespace, config: Config, *, library: ManagedLibrary | None = None) -> int:
    held = library if library is not None else ManagedLibrary(library_root())
    command = args.library_command
    if command == "import":
        return _import(args, held)
    if command == "list":
        return _list(held)
    if command == "show":
        return _show(args, held)
    if command == "tree":
        return _tree(args, held)
    if command == "map":
        return _map(args, held)
    if command == "confirm":
        return _confirm(args, held)
    if command in {"seed", "unseed", "seeds"}:
        return _seeds(args, config, held)
    if command == "report":
        return _report(held)
    print("usage: hardy library {import,list,show,tree,map,confirm,seed,unseed,seeds,report}")
    return 2


def _resolve(held: ManagedLibrary, prefix: str) -> str | None:
    matches = [sha for sha in held.artifacts.stored() if sha.startswith(prefix)]
    if len(matches) == 1:
        return matches[0]
    print(f"{prefix!r} matches {len(matches)} artifacts in the library" if matches else f"{prefix!r} matches no artifact in the library")
    return None


def _import(args: argparse.Namespace, held: ManagedLibrary) -> int:
    metadata = tuple((k, v) for k, v in (("title", args.title), ("author", args.author)) if v)
    try:
        report = held.import_source(ImportRequest(path=Path(args.path), access=AccessPolicy(args.access), user_metadata=metadata), extract=not args.no_extract)
    except (ImportRefused, ArtifactError, OSError) as error:
        print(f"import refused: {error}")
        return 1
    artifact = report.outcome.artifact
    print(f"artifact {artifact.sha256}")
    print(f"  format {artifact.format.value}  bytes {artifact.byte_size}  access {artifact.access.value}  {'reused' if report.outcome.reused else 'admitted'}")
    if report.extraction is not None:
        print(f"  extraction {report.extraction.status}" + (f" via {report.extraction.adapter}" if report.extraction.adapter else ""))
        for record in report.extraction.representations:
            print(f"    {record.kind.value:16} {record.id}  quality {record.quality.status}")
        for diagnostic in report.extraction.diagnostics:
            print(f"    {diagnostic.severity}: {diagnostic.code}: {diagnostic.detail}")
    for proposal in report.proposals:
        print(f"  proposed edition {proposal.edition} ({', '.join(e.kind for e in proposal.evidence)}); not authoritative until confirmed")
    return 0


def _list(held: ManagedLibrary) -> int:
    stored = held.artifacts.stored()
    if not stored:
        print("the library holds no artifacts")
        return 0
    snapshot = held.catalog.snapshot()
    grouped = snapshot.authoritative()
    for sha in stored:
        record = held.artifacts.record(sha)
        edition = snapshot.edition(grouped[sha]) if sha in grouped else None
        work = snapshot.work(edition.work) if edition else None
        title = work.title if work else "(edition not confirmed)"
        trees = len(held.trees.list(sha))
        print(f"{sha[:16]}  {record.format.value:8} {title}  trees {trees}")
    return 0


def _show(args: argparse.Namespace, held: ManagedLibrary) -> int:
    sha = _resolve(held, args.artifact)
    if sha is None:
        return 1
    record = held.artifacts.record(sha)
    print(json.dumps(record.model_dump(mode="json"), indent=2))
    print("availability", held.availability(sha).status)
    for provenance in held.artifacts.provenance(sha):
        print(f"provenance {provenance.id}: {provenance.original_path or provenance.source_url or provenance.original_name} at {provenance.imported_at}")
    for representation in held.representations.list(sha):
        print(f"representation {representation.id}: {representation.kind.value} by {representation.extractor}/{representation.extractor_version} quality {representation.quality.status}")
    for tree in held.trees.list(sha):
        print(f"tree {tree.id}: version {tree.version} nodes {len(tree.nodes)} diagnostics {len(tree.diagnostics)}")
    edition = held.catalog.edition_of(sha)
    print("edition", edition.id if edition else "not authoritatively grouped")
    for proposal in held.catalog.candidates_for(sha):
        print(f"candidate edition {proposal.edition} proposed by {proposal.proposer} ({', '.join(e.kind for e in proposal.evidence)})")
    return 0


def _tree(args: argparse.Namespace, held: ManagedLibrary) -> int:
    sha = _resolve(held, args.artifact)
    if sha is None:
        return 1
    try:
        tree = held.build_tree(sha)
    except (ValueError, TreeError) as error:
        print(f"tree not built: {error}")
        return 1
    print(f"tree {tree.id} version {tree.version} nodes {len(tree.nodes)} edges {len(tree.edges)}")
    for diagnostic in tree.diagnostics:
        print(f"  {diagnostic.severity}: {diagnostic.code}: {diagnostic.detail}")
    return 0


def _map(args: argparse.Namespace, held: ManagedLibrary) -> int:
    sha = _resolve(held, args.artifact)
    if sha is None:
        return 1
    source_map = SourceReader(held).source_map(sha, depth=max(1, args.depth))
    if source_map.unavailable:
        print(source_map.unavailable)
        return 1
    print(f"{source_map.title or sha[:16]}: tree {source_map.tree}, {source_map.node_count} nodes, {source_map.statement_count} statements")
    for entry in source_map.entries:
        label = " ".join(p for p in (entry.kind.value, entry.number or "", entry.title or "") if p)
        print(f"{'  ' * entry.depth}{label}  [{entry.node}]" + (f" pages {list(entry.pages)}" if entry.pages else ""))
    if source_map.truncated:
        print("... map truncated")
    return 0


def _confirm(args: argparse.Namespace, held: ManagedLibrary) -> int:
    sha = _resolve(held, args.artifact)
    if sha is None:
        return 1
    snapshot = held.catalog.snapshot()
    if snapshot.edition(args.edition) is None:
        print(f"edition {args.edition} is not in the catalog")
        return 1
    proposal = next((p for p in held.catalog.candidates_for(sha) if p.edition == args.edition), None)
    try:
        if proposal is None:
            proposal = GroupingProposal(id=f"prop-user-{sha[:16]}-{args.edition[-8:]}", artifact_sha256=sha, edition=args.edition, proposer="user",
                                        evidence=(IdentityEvidence(kind="human_confirmation", value=args.reason, provenance="hardy library confirm"),))
            snapshot = held.catalog.propose_grouping(proposal, expected_revision=snapshot.revision)
        held.catalog.decide_grouping(GroupingDecision(id=f"dec-user-{proposal.id}", proposal=proposal.id, status="authoritative", decided_by="user:cli", reason=args.reason),
                                     expected_revision=snapshot.revision)
    except CatalogError as error:
        print(f"not confirmed: {error}")
        return 1
    print(f"artifact {sha[:16]} is now an authoritative copy of edition {args.edition}")
    return 0


def _seeds(args: argparse.Namespace, config: Config, held: ManagedLibrary) -> int:
    problem = config.layout.problem
    store = SeedStore(problem)
    if args.library_command == "seeds":
        seeds = store.seeds()
        if not seeds:
            print(f"problem {config.project} has no seeds")
        for seed in seeds:
            print(f"{seed.id}  {seed.artifact_sha256[:16]}  priority {seed.priority}  {seed.intent or ''}")
        return 0
    if args.library_command == "unseed":
        try:
            store.remove(args.seed, expected_revision=store.revision(), reason="hardy library unseed")
        except ValueError as error:
            print(f"not removed: {error}")
            return 1
        print(f"removed {args.seed}")
        return 0
    sha = _resolve(held, args.artifact)
    if sha is None:
        return 1
    edition = held.catalog.edition_of(sha)
    try:
        tree = held.trees.preferred(sha)
    except SourceUnavailable:
        tree = None
    problem.mkdir(parents=True, exist_ok=True)
    seed = new_seed(sha, edition=edition.id if edition else None, tree=tree.id if tree else None, priority=args.priority, intent=args.intent)
    store.add(seed, expected_revision=store.revision())
    print(f"seeded {config.project} with {sha[:16]} as {seed.id}" + ("" if tree else "; no tree yet, run `hardy library tree`"))
    return 0


def _report(held: ManagedLibrary) -> int:
    from hardy.literature.sources.metrics import source_report
    from hardy.workflows.shared.claims import LinkStore
    from hardy.workflows.shared.ledger import SharedClaims, shared_store
    from hardy.workflows.shared.metrics import semantic_report
    from hardy.workflows.shared.promotion import PromotionStore
    from hardy.workflows.shared.realizations import RealizationStore

    sources = source_report(held)
    semantics = semantic_report(SharedClaims(shared_store(held.root)), LinkStore(held.root / "links"), RealizationStore(held.root / "realizations"),
                                PromotionStore(held.root / "promotions"))
    print(json.dumps({"sources": sources.model_dump(mode="json"), "semantics": semantics.model_dump(mode="json")}, indent=2, sort_keys=True))
    return 0
