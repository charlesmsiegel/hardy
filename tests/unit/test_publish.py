"""Publication assembly preserves source prose and uses the real document gate."""
import importlib
import json
import sys

import pytest

from hardy.documents.latex import LatexTools
from hardy.documents.writeup import escape_tex_text
from hardy.workflows.context import BindingSpec, ContextManager, DeclarationSpec
from hardy.workflows.ledger.contracts import (
    ArtifactRef,
    CitationContract,
    ProjectItem,
    Relation,
    Scope,
)
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.publication import PublicationPlanner, PublicationRequest


@pytest.fixture
def publish():
    try:
        return importlib.import_module("hardy.workflows.publish")
    except ModuleNotFoundError:
        pytest.fail("D6 publication assembly is not implemented")


@pytest.fixture
def project(tmp_path):
    store = LedgerStore(tmp_path / "project")
    theorem = ProjectItem(id="Main", name="Main theorem", kind="theorem", origin="human_authored",
                          statement="Every X has P", publication_visibility="public")
    prose = ProjectItem(id="P", name="Author's paragraph", kind="exposition", origin="human_authored",
                        statement="Keep 100% of this & this.\n\\input{private} is literal text.",
                        publication_visibility="public")
    scope = Scope(id="scope", must_prove=(theorem.ref,))
    relation = Relation(id="doc", kind="documents", source=prose.ref, target=theorem.ref)
    store.append((theorem, prose, scope, relation), expected_revision=0)
    request = PublicationRequest(roots=(theorem.ref,), scope=scope.ref)
    return store, theorem, prose, scope, PublicationPlanner(store).plan(request)


def compiler(tmp_path, *, succeeds=True):
    # The external compiler is the test substitute; LatexTools itself is real.
    script = tmp_path / "compiler.py"
    script.write_text(
        "from pathlib import Path\nimport sys\n"
        + ("Path('writeup.pdf').write_bytes(b'test compiler PDF')\n"
           "Path('writeup.aux').write_text('')\n"
           if succeeds else "print('deliberate compiler failure')\nsys.exit(1)\n"),
        encoding="utf-8",
    )
    return LatexTools((sys.executable, str(script)))


def test_assembly_preserves_author_prose_as_text_and_records_unverified_math(publish, project):
    store, theorem, prose, _, plan = project
    before = store.read()
    draft = publish.assemble_publication(plan, title="Selected theorem")
    assert escape_tex_text(prose.statement) in draft.source
    assert "\\input{private}" not in draft.source
    assert theorem.digest in draft.source
    assert any("unestablished" in gap.lower() and "Main" in gap for gap in draft.gaps)
    assert not draft.ready
    assert store.read() == before


def test_stale_prose_is_flagged_and_never_reused_as_current_text(publish, project):
    store, old, prose, scope, _ = project
    new = old.model_copy(update={"statement": "A stronger statement"})
    store.append((new,), expected_revision=1)
    plan = PublicationPlanner(store).plan(PublicationRequest(roots=(new.ref,), scope=scope.ref))
    draft = publish.assemble_publication(plan, title="New claim")
    assert escape_tex_text(prose.statement) not in draft.source
    assert any("stale" in gap.lower() and old.digest in gap and new.digest in gap for gap in draft.gaps)
    assert any("missing exposition" in gap.lower() for gap in draft.gaps)
    assert store.read().head("P") == prose


def test_artifact_only_exposition_is_reported_unavailable_without_reading_uri(publish, project):
    store, theorem, prose, scope, _ = project
    artifact = ArtifactRef(uri="file:///private/notes.tex", digest="a" * 64)
    revised = prose.model_copy(update={"statement": None, "artifacts": (artifact,)})
    relation = Relation(id="doc", kind="documents", source=revised.ref, target=theorem.ref)
    store.append((revised, relation), expected_revision=1)
    plan = PublicationPlanner(store).plan(PublicationRequest(roots=(theorem.ref,), scope=scope.ref))
    draft = publish.assemble_publication(plan)
    assert any("unavailable" in gap.lower() and artifact.uri in gap for gap in draft.gaps)
    assert not draft.ready


@pytest.mark.parametrize("succeeds", [False, True])
def test_real_document_gate_persists_frozen_input_and_reports_compile_separately(publish, project, tmp_path, succeeds):
    store, _, _, _, plan = project
    output = tmp_path / "publication"
    result = publish.PublishWorkflow(compiler(tmp_path, succeeds=succeeds)).publish(plan, output=output)
    assert result.compilation.ok is succeeds
    assert not result.draft.ready
    assert (output / "writeup.pdf").exists() is succeeds
    assert (output / "writeup.tex").read_text(encoding="utf-8") == result.draft.source
    saved = json.loads((output / "publication.json").read_text(encoding="utf-8"))
    assert saved["plan"] == plan.model_dump(mode="json")
    assert saved["draft"]["plan_digest"] == result.draft.plan_digest
    assert (output / "compile.log").read_text(encoding="utf-8") == result.compilation.output
    assert store.read().revision == plan.revision


def test_existing_human_output_is_never_overwritten(publish, project, tmp_path):
    output = tmp_path / "publication"
    output.mkdir()
    source = output / "writeup.tex"
    source.write_text("My edited manuscript", encoding="utf-8")
    workflow = publish.PublishWorkflow(compiler(tmp_path))
    with pytest.raises(FileExistsError):
        workflow.publish(project[-1], output=output)
    assert source.read_text(encoding="utf-8") == "My edited manuscript"
    assert not (output / "writeup.pdf").exists()


def test_book_uses_same_document_gate_and_persists_exact_structure(publish, project, tmp_path):
    store, theorem, prose, scope, _ = project
    book = ProjectItem(id="Book", name="Collected results", kind="book", origin="human_authored",
                       publication_visibility="public")
    chapter = ProjectItem(id="Chapter", name="First chapter", kind="chapter", origin="human_authored",
                          publication_visibility="public")
    contains = (Relation(id="chapter", kind="contains", source=book.ref, target=chapter.ref),
                Relation(id="theorem", kind="contains", source=chapter.ref, target=theorem.ref))
    store.append((book, chapter, *contains), expected_revision=1)
    plan = PublicationPlanner(store).plan(PublicationRequest(roots=(book.ref,), scope=scope.ref))
    result = publish.PublishWorkflow(compiler(tmp_path)).publish(plan, output=tmp_path / "book")
    assert result.compilation.ok and not result.draft.ready
    assert r"\documentclass{book}" in result.draft.source
    assert r"\part*{Book: Collected results}" in result.draft.source
    assert r"\chapter*{Chapter: First chapter}" in result.draft.source
    assert escape_tex_text(prose.statement) in result.draft.source
    saved = json.loads((result.output / "publication.json").read_text(encoding="utf-8"))
    assert saved["plan"]["containment"] == [r.model_dump(mode="json") for r in contains]
    assert saved["plan"]["structure"][-1]["containers"] == [book.ref.model_dump(), chapter.ref.model_dump()]


def test_nested_section_and_later_chapter_sibling_keep_their_document_parents(publish, project):
    store, theorem, _, scope, _ = project
    chapter = ProjectItem(id="Chapter", name="Chapter", kind="chapter", origin="human_authored",
                          publication_visibility="public")
    outer = ProjectItem(id="Outer", name="Outer", kind="section", origin="human_authored",
                        publication_visibility="public")
    inner = outer.model_copy(update={"id": "Inner", "name": "Inner"})
    sibling = theorem.model_copy(update={"id": "Sibling", "name": "Sibling", "statement": "Sibling claim"})
    store.append((chapter, outer, inner, sibling,
        Relation(id="outer", kind="contains", source=chapter.ref, target=outer.ref),
        Relation(id="inner", kind="contains", source=outer.ref, target=inner.ref),
        Relation(id="nested-theorem", kind="contains", source=inner.ref, target=theorem.ref),
        Relation(id="sibling", kind="contains", source=chapter.ref, target=sibling.ref)), expected_revision=1)
    plan = PublicationPlanner(store).plan(PublicationRequest(roots=(chapter.ref,), scope=scope.ref))
    source = publish.assemble_publication(plan).source
    headings = [line for line in source.splitlines() if line.startswith((
        r"\chapter*", r"\section*", r"\subsection*", r"\subsubsection*"))]
    assert headings == [
        r"\section*{Status and remaining work}",
        r"\chapter*{Chapter: Chapter}",
        r"\section*{Section: Outer}",
        r"\subsection*{Section: Inner}",
        r"\subsubsection*{Theorem: Main theorem}",
        r"\section*{Theorem: Sibling}",
    ]
    # Local prose labels must not pop the theorem out of its nested section.
    assert r"\textbf{Exposition}" in source
    for item in plan.items:
        assert source.count(f"Source identity: {item.id}@{item.digest}") == 1
    sibling_text = source.split(r"\section*{Theorem: Sibling}")[1]
    assert f"Document containers: Chapter [{chapter.id}@{chapter.digest}]" in sibling_text
    assert "Outer [" not in sibling_text and "Inner [" not in sibling_text


def test_deeper_than_latex_headings_keeps_explicit_exact_container_path(publish, project):
    store, theorem, _, scope, _ = project
    sections = tuple(ProjectItem(id=f"S{i}", name=f"Section {i}", kind="section", origin="human_authored",
                                 publication_visibility="public") for i in range(8))
    children = (*sections[1:], theorem)
    links = tuple(Relation(id=f"member-{i}", kind="contains", source=parent.ref, target=child.ref)
                  for i, (parent, child) in enumerate(zip(sections, children, strict=True)))
    store.append((*sections, *links), expected_revision=1)
    plan = PublicationPlanner(store).plan(PublicationRequest(roots=(sections[0].ref,), scope=scope.ref))
    source = publish.assemble_publication(plan).source
    assert r"\textbf{Theorem: Main theorem}" in source
    theorem_text = source.split(r"\textbf{Theorem: Main theorem}")[1]
    for section in sections:
        assert f"{section.name} [{section.id}@{section.digest}]" in theorem_text
    assert "\\subsubparagraph" not in source
    assert source.count(theorem.statement) == 1


def test_plan_survives_later_ledger_changes_without_document_owner_reading_them(publish, project, tmp_path):
    store, theorem, _, _, plan = project
    store.append((theorem.model_copy(update={"statement": "Later mathematics"}),), expected_revision=1)
    result = publish.PublishWorkflow(compiler(tmp_path)).publish(plan, output=tmp_path / "frozen")
    assert theorem.statement in result.draft.source
    assert "Later mathematics" not in result.draft.source
    assert result.compilation.ok


def test_adapter_carries_minimal_hypotheses_notation_and_exact_citation_contract(publish, project):
    store, _, _, scope, _ = project
    manager = ContextManager(store)
    root = manager.create_root(id="C0", label="arbitrary setup")
    setup = manager.extend(root.ref, id="C1", label="compact manifold", declarations=(
        DeclarationSpec(id="X", symbol="X", semantic_type="smooth manifold"),
        DeclarationSpec(id="irrelevant", symbol="Y", semantic_type="irrelevant ambient variety"),
        DeclarationSpec(id="H", symbol="h", semantic_type="compact X", role="local_hypothesis"),
    ), bindings=(BindingSpec(id="convention", kind="convention", symbol="curve", meaning="smooth projective curve"),))
    theorem = ProjectItem(id="CompactMain", name="Compact case", kind="theorem", origin="human_authored",
                          statement="The compact result", context=setup.ref, publication_visibility="public")
    external = ProjectItem(id="External", name="Classical theorem", kind="external_result", origin="background_paper",
                           statement="The cited conclusion", publication_visibility="public")
    citation = CitationContract(id="source", use_site=theorem.ref, required_claim=external.ref,
        paper_id="Author2026", paper_version="v3", conclusion="The cited conclusion",
        source_hypotheses=("X compact",),
        source_statement=ArtifactRef(uri="paper.txt", digest="c" * 64, locator="Theorem 4"))
    refs = (store.read().head("X").ref, store.read().head("H").ref,
            store.read().head("convention").ref, external.ref)
    store.append((theorem, external, citation, *(Relation(id=f"requires-{i}", kind="uses",
                  source=theorem.ref, target=ref) for i, ref in enumerate(refs))), expected_revision=store.read().revision)
    plan = PublicationPlanner(store).plan(PublicationRequest(roots=(theorem.ref,), scope=scope.ref))
    draft = publish.assemble_publication(plan)
    assert "Local hypothesis h: compact X" in draft.source
    assert "smooth projective curve" in draft.source
    assert "irrelevant ambient variety" not in draft.source
    assert "Author2026, version v3" in draft.source and "Theorem 4" in draft.source
    assert citation.source_statement.digest in draft.source
    assert "Source hypothesis: X compact" in draft.source
    assert any("Unchecked citation" in gap for gap in draft.gaps)
