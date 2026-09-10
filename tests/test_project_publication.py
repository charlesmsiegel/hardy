"""E3 composes exact ledger selection with the session's real document gate."""
import json
import sys

import pytest
from test_chat import FakeChatRuntime, session

from hardy.documents.latex import LatexTools
from hardy.workflows.ledger.contracts import (
    ProjectItem,
    PublicationRole,
    PublicationVisibility,
    Relation,
    Scope,
)
from hardy.workflows.ledger.policy import LedgerPolicy, ScopeChangeDecision
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.publication import PublicationPlanner, PublicationRequest
from hardy.workflows.publish import assemble_publication


def populate(path, *, main_visibility="public"):
    store = LedgerStore(path)
    def item(id, kind, statement):
        return ProjectItem(id=id, name=id, kind=kind, statement=statement,
                           origin="human_authored", publication_visibility="public")
    main = item("Main", "theorem", "The exact main statement")
    main = main.model_copy(update={"publication_visibility": PublicationVisibility(main_visibility)})
    helper = item("Helper", "lemma", "The exact helper statement")
    example = item("Example", "example", "An illustrative example")
    prose = item("Paragraph", "exposition", "Keep this author's exact paragraph.")
    scope = Scope(id="scope", must_prove=(main.ref,))
    dependency = Relation(id="dependency", kind="uses", source=main.ref, target=helper.ref)
    store.append((main, helper, example, prose, scope, dependency), expected_revision=0)
    return store, main, helper, example, prose, scope


def test_metadata_visibility_hides_linked_helper_without_floating_audit_refs(tmp_path):
    store, main, helper, _, _, scope = populate(tmp_path)
    hidden = helper.model_copy(update={"publication_visibility": PublicationVisibility.INTERNAL})
    store.append((hidden,), expected_revision=1)
    plan = PublicationPlanner(store).plan(PublicationRequest(roots=(main.ref,), scope=scope.ref))
    assert helper.ref in plan.closure and hidden.ref not in plan.closure
    assert helper.ref in plan.unestablished
    assert "Helper" not in {item.id for item in plan.items}
    assert [(revision.item, revision.presentation) for revision in plan.presentation_revisions] == [(helper.ref, hidden.ref)]
    assert store.read().head("scope") == scope


def test_presentation_does_not_float_to_changed_mathematics(tmp_path):
    store, main, helper, _, _, scope = populate(tmp_path)
    changed = helper.model_copy(update={"statement": "Different mathematics", "publication_visibility": "omitted"})
    store.append((changed,), expected_revision=1)
    plan = PublicationPlanner(store).plan(PublicationRequest(roots=(main.ref,), scope=scope.ref))
    assert helper in plan.items
    assert not plan.presentation_revisions


def test_presentation_role_is_rendered_without_replacing_the_audited_item(tmp_path):
    store = LedgerStore(tmp_path)
    original = ProjectItem(id="T", name="T", kind="theorem", statement="True", origin="human_authored",
                           publication_visibility="public", publication_role="supporting")
    scope = Scope(id="scope", must_prove=(original.ref,))
    store.append((original, scope), expected_revision=0)
    revised = original.model_copy(update={"publication_role": PublicationRole.MAIN})
    store.append((revised,), expected_revision=1)
    plan = PublicationPlanner(store).plan(PublicationRequest(roots=(original.ref,), scope=scope.ref))
    draft = assemble_publication(plan)
    assert "Publication role: main" in draft.source
    assert "Publication role: supporting" not in draft.source
    assert plan.items == (original,)
    assert original.ref in plan.unestablished
    assert revised.ref not in plan.closure


def test_session_links_marks_publishes_and_reopens_exact_draft(tmp_path):
    chat = session(tmp_path, FakeChatRuntime([]))
    store, main, helper, example, prose, scope = populate(tmp_path)
    illustration = chat.project_link("Example", "illustrates", "Main")
    documentation = chat.project_link("Paragraph", "documents", "Main")
    assert illustration.source == example.ref and illustration.target == main.ref
    assert documentation.source == prose.ref and documentation.target == main.ref
    hidden = chat.project_mark("Helper", "internal")
    before = store.read()
    result = chat.project_publish("Main", scope="scope", output="first-draft")
    assert result.compilation.ok
    assert not result.draft.ready
    assert result.output == tmp_path / "publications" / "first-draft"
    frozen = json.loads((result.output / "publication.json").read_text())
    assert {item["id"] for item in frozen["plan"]["items"]} == {"Main", "Example"}
    assert helper.ref.model_dump() in frozen["plan"]["closure"]
    assert frozen["plan"]["presentation_revisions"] == [{"item": helper.ref.model_dump(), "presentation": hidden.ref.model_dump(),
                                                        "visibility": "internal", "role": None}]
    assert prose.statement in result.draft.source
    assert helper.statement not in result.draft.source
    assert any(main.digest in gap for gap in result.draft.gaps)
    assert store.read() == before
    reopened = session(tmp_path, FakeChatRuntime([]))
    again = reopened.project_publish(f"Main@{main.digest}", scope=f"scope@{scope.digest}", output="second-draft")
    assert again.draft == result.draft
    with pytest.raises(FileExistsError):
        reopened.project_publish("Main", scope="scope", output="first-draft")
    assert (result.output / "writeup.tex").read_text() == result.draft.source


def test_marked_scope_target_keeps_scope_exact_dependency_and_prose_refs(tmp_path):
    chat = session(tmp_path, FakeChatRuntime([]))
    store, main, helper, _, prose, scope = populate(tmp_path)
    chat.project_link("Paragraph", "documents", "Main")
    changed = chat.project_mark("Main", "internal")
    result = chat.project_publish("Main", scope="scope", output="draft")
    plan = json.loads((result.output / "publication.json").read_text())["plan"]
    assert plan["request"]["roots"] == [main.ref.model_dump()]
    assert helper.ref.model_dump() in plan["closure"]
    assert prose.statement in result.draft.source
    assert plan["presentation_revisions"] == [{"item": main.ref.model_dump(), "presentation": changed.ref.model_dump(),
                                               "visibility": "internal", "role": None}]
    assert store.read().head("scope") == scope


@pytest.mark.parametrize("output", ["../escape", "a/b", "a\\b", ".", "C:\\outside", "NUL"])
def test_invalid_bundle_paths_refuse_before_writing(tmp_path, output):
    chat = session(tmp_path, FakeChatRuntime([]))
    populate(tmp_path)
    with pytest.raises(ValueError):
        chat.project_publish("Main", scope="scope", output=output)
    assert not (tmp_path / "publications").exists()


def test_compile_failure_retains_draft_and_diagnostics(tmp_path):
    chat = session(tmp_path, FakeChatRuntime([]))
    populate(tmp_path)
    compiler = tmp_path / "fail.py"
    compiler.write_text("import sys\nprint('intentional compile failure')\nsys.exit(1)\n")
    chat.latex = LatexTools((sys.executable, str(compiler)))
    result = chat.project_publish("Main", scope="scope", output="failed-draft")
    assert not result.compilation.ok
    assert (result.output / "publication.json").is_file()
    assert (result.output / "writeup.tex").read_text() == result.draft.source
    assert "intentional compile failure" in (result.output / "compile.log").read_text()


def test_exact_selection_invalid_links_and_stale_marks_refuse_without_mutation(tmp_path):
    chat = session(tmp_path, FakeChatRuntime([]))
    store, main, _, _, _, _ = populate(tmp_path)
    changed = main.model_copy(update={"statement": "A changed statement"})
    store.append((changed,), expected_revision=1)
    before = store.read()
    with pytest.raises(ValueError, match="stale"):
        chat.project_mark(f"Main@{main.digest}", "internal")
    with pytest.raises(ValueError):
        chat.project_link("Paragraph", "uses", "Main")
    with pytest.raises(ValueError):
        chat.project_link("Main", "documents", "Helper")
    with pytest.raises(ValueError):
        chat.project_publish("Main@abc", scope="scope", output="bad")
    with pytest.raises(ValueError):
        chat.project_publish("The exact main statement", scope="scope", output="bad")
    assert store.read() == before


def test_project_operations_refuse_reserved_conversation_turn(tmp_path):
    chat = session(tmp_path, FakeChatRuntime([]))
    store, *_ = populate(tmp_path)
    before = store.read()
    turn = chat.stream("reserved turn")
    try:
        with pytest.raises(ValueError, match="turn"):
            chat.project_mark("Main", "internal")
        with pytest.raises(ValueError, match="turn"):
            chat.project_link("Paragraph", "documents", "Main")
        with pytest.raises(ValueError, match="turn"):
            chat.project_publish("Main", scope="scope", output="busy")
        assert store.read() == before
    finally:
        turn.close()


def test_mark_refuses_trust_migration_and_identical_mark_is_a_noop(tmp_path):
    chat = session(tmp_path, FakeChatRuntime([]))
    store, _, helper, *_ = populate(tmp_path)
    admitted = Scope(id="trusted", allowed_background=(helper.ref,))
    policy = LedgerPolicy(read_scope_change=lambda old, new: ScopeChangeDecision(
        old.ref if old else None, new.ref, policy.digest))
    store.append((admitted,), expected_revision=1, validate=policy.validate)
    before = store.read()
    assert chat.project_mark("Helper", "public") == helper
    with pytest.raises(ValueError, match="admitted"):
        chat.project_mark("Helper", "internal")
    assert store.read() == before
    assert policy.premise_allowed(store.read(), helper.ref, scope=admitted, context=None)


def test_repeated_link_is_a_noop_and_new_statement_keeps_old_prose_stale(tmp_path):
    chat = session(tmp_path, FakeChatRuntime([]))
    store, main, _, _, prose, _ = populate(tmp_path)
    linked = chat.project_link("Paragraph", "documents", "Main")
    revision = store.read().revision
    assert chat.project_link("Paragraph", "documents", "Main") == linked
    assert store.read().revision == revision
    changed = main.model_copy(update={"statement": "Different mathematics"})
    store.append((changed,), expected_revision=revision)
    result = chat.project_publish("Main", scope="scope", output="changed")
    frozen = json.loads((result.output / "publication.json").read_text())["plan"]
    assert frozen["request"]["roots"] == [changed.ref.model_dump()]
    assert prose.statement not in result.draft.source
    assert any("Stale exposition" in gap and main.digest in gap for gap in result.draft.gaps)


def test_repeating_links_after_metadata_mark_does_not_duplicate_exposition(tmp_path):
    chat = session(tmp_path, FakeChatRuntime([]))
    store, _, _, _, prose, _ = populate(tmp_path)
    first = chat.project_link("Paragraph", "documents", "Main")
    chat.project_mark("Main", "internal")
    before = store.read()
    assert chat.project_link("Paragraph", "documents", "Main") == first
    assert store.read() == before
    result = chat.project_publish("Main", scope="scope", output="single-paragraph")
    assert result.draft.source.count(prose.statement) == 1


def test_repeating_link_after_source_metadata_mark_preserves_one_exact_paragraph(tmp_path):
    chat = session(tmp_path, FakeChatRuntime([]))
    store, _, _, _, prose, _ = populate(tmp_path)
    hidden = chat.project_mark("Paragraph", "internal")
    first = chat.project_link("Paragraph", "documents", "Main")
    assert first.source == hidden.ref
    chat.project_mark("Paragraph", "public")
    before = store.read()
    assert chat.project_link("Paragraph", "documents", "Main") == first
    assert store.read() == before
    result = chat.project_publish("Main", scope="scope", output="single-source")
    assert result.draft.source.count(prose.statement) == 1
    frozen = json.loads((result.output / "publication.json").read_text())["plan"]
    assert frozen["exposition"][0]["prose"]["publication_visibility"] == "internal"
    assert frozen["attachments"][0]["source"] == hidden.ref.model_dump()


def test_changed_prose_source_is_not_collapsed_as_a_presentation_edit(tmp_path):
    chat = session(tmp_path, FakeChatRuntime([]))
    store, _, _, _, prose, _ = populate(tmp_path)
    first = chat.project_link("Paragraph", "documents", "Main")
    changed = prose.model_copy(update={"statement": "A different author paragraph."})
    store.append((changed,), expected_revision=store.read().revision)
    second = chat.project_link("Paragraph", "documents", "Main")
    assert second.ref != first.ref
    assert second.source == changed.ref
    assert store.read().get(first.ref) == first


@pytest.mark.parametrize("container", [False, True])
def test_links_after_metadata_mark_reach_exact_scope_target_and_audit_example_dependencies(tmp_path, container):
    chat = session(tmp_path, FakeChatRuntime([]))
    store, main, _, example, prose, _ = populate(tmp_path, main_visibility="internal")
    if container:
        book = ProjectItem(id="Book", name="Book", kind="book", origin="human_authored",
                           publication_visibility="public")
        store.append((book, Relation(id="contains-main", kind="contains", source=book.ref, target=main.ref)),
                     expected_revision=store.read().revision)
    marked = chat.project_mark("Main", "public")
    documented = chat.project_link("Paragraph", "documents", "Main")
    illustrated = chat.project_link("Example", "illustrates", "Main")
    assert documented.target == marked.ref and illustrated.target == marked.ref
    extra = ProjectItem(id="Extra", name="Extra", kind="lemma", origin="human_authored",
                        statement="The example needs this fact", publication_visibility="internal")
    store.append((extra, Relation(id="example-extra", kind="uses", source=example.ref, target=extra.ref)),
                 expected_revision=store.read().revision)
    result = chat.project_publish("Book" if container else "Main", scope="scope", output="linked-later")
    plan = json.loads((result.output / "publication.json").read_text())["plan"]
    assert prose.statement in result.draft.source
    assert example.statement in result.draft.source
    assert extra.ref.model_dump() in plan["unestablished"]
    assert main.ref.model_dump() in plan["closure"]
    assert marked.ref.model_dump() not in plan["closure"]
    assert plan["exposition"][0]["documented"] == marked.ref.model_dump()
    assert plan["exposition"][0]["target"] == main.ref.model_dump()
    assert {link["id"] for link in plan["attachments"]} == {documented.id, illustrated.id}
