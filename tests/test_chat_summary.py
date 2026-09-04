"""What a real session says about itself, from the artifacts (#100, #105)."""

from __future__ import annotations

from pathlib import Path

from test_chat import FakeChatRuntime, call, session

from hardy import export

BASIC = "import Mathlib\ntheorem hardyBasic : True := by exact True.intro\n"
BROKEN = "import Mathlib\ntheorem hardyBroken : True := by exact\n"


def built(tmp_path: Path) -> object:
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "Basic.lean", "source": BASIC}),
        {"role": "assistant", "content": "Saved."},
    ])
    chat = session(tmp_path, runtime, registered=("hardyBasic",))
    chat.set_goal("Establish the basics")
    chat.send("Save something.")
    return chat


def test_the_summary_names_the_goal_and_the_saved_theorem(tmp_path: Path):
    text = built(tmp_path).summary().text()
    assert "Establish the basics" in text
    assert "Modules" in text and "Basic" in text
    # Under `Proved`, with its own verdict -- not merely somewhere in the page.
    proved = text.split("Proved\n", 1)[1].split("\nOpen", 1)[0].split("\nFailed", 1)[0]
    assert "hardyBasic" in proved


def test_the_summary_reports_a_refused_save_from_the_transcript(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "Broken.lean", "source": BROKEN}),
        {"role": "assistant", "content": "That did not work."},
    ])
    chat = session(tmp_path, runtime, registered=("hardyBroken",))
    chat.send("Save something broken.")
    text = chat.summary().text()
    assert "Failed attempts" in text
    assert "save_lean Broken.lean" in text


def test_an_untouched_workspace_summarises_as_having_nothing(tmp_path: Path):
    text = session(tmp_path, FakeChatRuntime([])).summary().text()
    assert "not set (/goal)" in text
    assert "nothing here is reportable" in text
    assert "no Lean module is saved." in text


def test_the_summary_carries_no_spend(tmp_path: Path):
    text = built(tmp_path).summary().text().lower()
    assert "usd" not in text and "spent" not in text


def test_the_export_material_holds_everything_the_page_needs(tmp_path: Path):
    material = built(tmp_path).export_material()
    assert material["goal"] == "Establish the basics"
    assert "hardyBasic" in material["theorems"]
    assert any("hardyBasic" in source for source in material["lean"].values())
    assert material["provenance"]["model"] == "chat-model@test"
    assert any(event.get("type") == "user" for event in material["transcript"])


def test_a_real_session_exports_a_page_that_states_its_own_verdicts(tmp_path: Path):
    page = export.build(export.prepare(built(tmp_path).export_material()))
    assert page.startswith("<!doctype html>")
    assert "hardyBasic" in page
    assert "Establish the basics" in page
    # And the conversation, under the heading that says it proves nothing.
    assert "Save something." in page
    assert "None of it is evidence" in page


def test_a_verdict_the_session_has_expired_is_handed_out_as_expired(tmp_path: Path):
    """`/status --full` and `/export` must not read `state["audit"]` raw: the
    session already expires a verdict whose toolchain, source or dependencies
    moved, and a page that ignored that would call a theorem kernel-verified
    beside its own "no longer established"."""
    chat = built(tmp_path)
    assert "hardyBasic: kernel-verified" in chat.summary().text()

    # What `_still_current` is for: the build signature no longer matches.
    for record in chat.state["audit"].values():
        record["signature"] = "moved"
    chat._save_state()

    text = chat.summary().text()
    assert "kernel-verified" not in text
    assert "hardyBasic: no longer established" in text
    assert all(
        record.get("stale") for record in chat.export_material()["audit"].values()
    )


def test_the_summary_and_the_export_gather_under_the_session_gate(tmp_path: Path):
    """`/status --full` is safe in flight, so a `save_lean` on another thread
    can land between two of the reads. The pair that must not straddle one is
    the audit and the sources: an old verdict beside a new statement says
    "kernel-verified" about content Lean never saw."""
    chat = built(tmp_path)
    held: list[str] = []

    class Watched:
        """Stands in for the gate, recording that it was held throughout."""

        def __init__(self):
            self.depth = 0

        def __enter__(self):
            self.depth += 1
            held.append("enter")
            return self

        def __exit__(self, *exception):
            held.append("exit")
            self.depth -= 1
            return False

    watched = Watched()
    chat._gate = watched
    # Read inside the gathering, so a witness can say the gate was open then.
    original = chat._theorem_statements

    def watching(sources=None):
        assert watched.depth == 1, "the sources were read outside the gate"
        return original(sources)

    chat._theorem_statements = watching
    chat.summary()
    chat.export_material()
    assert held == ["enter", "exit", "enter", "exit"]


def test_a_linked_writeup_is_not_reported_as_a_compiled_document(tmp_path: Path):
    """`is_file` and `stat` both follow a link, so a checked-out
    `writeup.pdf -> <any file>` had the page state that Hardy compiled a
    document and report that file's size. Hardy compiled nothing.

    And it says which of the two happened. Reporting the refusal as "no
    compiled document was found" is a different false statement in the other
    direction: the file is there and Hardy declined to read it, which a reader
    deciding whether a writeup exists needs to be able to tell apart.
    """
    chat = built(tmp_path)
    elsewhere = tmp_path / "not-ours.pdf"
    elsewhere.write_bytes(b"%PDF-not-hardys")
    (Path(chat.workspace) / "writeup.pdf").symlink_to(elsewhere)

    said = chat.export_material()["document"]
    assert "was compiled" not in said
    assert "symlink" in said
    assert "not a finding that none exists" in said


def test_an_absent_writeup_is_reported_as_absent(tmp_path: Path):
    """The other half of the pair: nothing there really is nothing there."""
    chat = built(tmp_path)
    assert "No compiled document was found" in chat.export_material()["document"]


def test_the_audit_and_the_statement_come_from_one_read_of_the_tree(tmp_path: Path):
    """The gate serializes Hardy's own tool calls and nothing else; editing a
    `.lean` file behind Hardy is supported. Two reads could straddle such an
    edit and pair a still-current verdict with a statement it was never about.

    The obligations are deliberately not part of this: they are the same
    independent computation `/status` and `report_result` use, and a later read
    can only make them ask for more.
    """
    chat = built(tmp_path)
    read = chat.lean_workspace.sources
    calls: list[int] = []
    edited = BASIC.replace("True := by exact True.intro", "1 = 1 := by rfl")

    def moving():
        calls.append(1)
        found = read()
        # An editor changes the file between one read of the tree and the next.
        return found if len(calls) == 1 else dict.fromkeys(found, edited)

    chat.lean_workspace.sources = moving
    material = chat.export_material()

    assert "1 = 1" not in material["theorems"]["hardyBasic"], (
        "a verdict was paired with a statement it never graded"
    )
    assert "1 = 1" not in "".join(material["lean"].values()), (
        "the page prints a source the verdict beside it is not about"
    )


def test_the_export_carries_what_arrived_from_outside(tmp_path: Path):
    """An imported module is indistinguishable from an authored one in the
    sources, so the origin and arriving digest have to travel with it."""
    chat = built(tmp_path)
    chat.state.setdefault("imported", []).append(
        {
            "kind": "lean",
            "path": "Sylow.lean",
            "origin": "/elsewhere/Sylow.lean",
            "sha256": "d" * 64,
        }
    )

    material = chat.export_material()

    assert material["imported"][0]["origin"] == "/elsewhere/Sylow.lean"


def test_a_lemma_elsewhere_cannot_lend_its_verdict_to_a_theorem(tmp_path: Path):
    """The audit records every declaration and a verdict is looked up by name,
    so an audited `lemma result` answers for an unaudited `theorem result`."""
    chat = built(tmp_path)
    read = chat.lean_workspace.sources

    def two_modules():
        found = dict(read())
        found["Other"] = "import Mathlib\nlemma hardyBasic : True := by exact True.intro\n"
        return found

    chat.lean_workspace.sources = two_modules

    assert "hardyBasic" in chat.export_material()["shared"]


def test_the_obligations_describe_the_same_tree_the_results_do(tmp_path: Path):
    """A later read can ask for LESS, not only for more: an edit that removes
    an undocumented theorem between the reads left the page showing that
    theorem's statement and its verdict beside "Nothing outstanding"."""
    chat = built(tmp_path)
    read = chat.lean_workspace.sources
    calls: list[int] = []

    def vanishing():
        calls.append(1)
        found = read()
        # The editor deleted the module after the results were captured.
        return found if len(calls) == 1 else {}

    chat.lean_workspace.sources = vanishing
    material = chat.export_material()

    assert material["theorems"], "the fixture saved no theorem"
    assert material["obligations"], "the page shows a result and owes nothing for it"


def test_a_private_helper_in_two_modules_is_not_an_ambiguous_name(tmp_path: Path):
    """Lean mangles a private name, so two modules spelling a helper
    `private lemma step` do not collide -- and the obligation asking the model
    to namespace one of them could not be satisfied."""
    chat = built(tmp_path)
    read = chat.lean_workspace.sources

    def two_modules():
        found = dict(read())
        helper = "import Mathlib\nprivate lemma step : True := by exact True.intro\n"
        found["First"] = helper
        found["Second"] = helper
        return found

    chat.lean_workspace.sources = two_modules

    assert "step" not in chat.export_material()["shared"]


def test_the_shared_identity_is_refreshed_before_a_verdict_is_graded(tmp_path: Path):
    """A shared source edited in the user's own editor has already invalidated
    every stored verdict; an identity fixed at startup lets the signature go on
    matching a dependency that has moved."""
    chat = built(tmp_path)
    refreshed: list[int] = []
    original = chat._refresh_shared_identity

    def counting():
        refreshed.append(1)
        return original()

    chat._refresh_shared_identity = counting
    chat.summary()
    chat.export_material()

    assert len(refreshed) == 2, "a gatherer graded verdicts against a stale identity"


def test_a_private_theorem_cannot_answer_for_a_public_one_of_the_same_name(tmp_path: Path):
    """The statements map is keyed by name, so a later `private theorem result`
    overwrote the public one -- and a private declaration is not in the axiom
    audit, so the page showed the private statement under the public verdict."""
    chat = built(tmp_path)
    read = chat.lean_workspace.sources

    def two_modules():
        found = dict(read())
        found["Later"] = (
            "import Mathlib\nprivate theorem hardyBasic : 1 = 1 := by rfl\n"
        )
        return found

    chat.lean_workspace.sources = two_modules

    assert "1 = 1" not in chat.export_material()["theorems"]["hardyBasic"]


def test_two_modules_sharing_only_a_lemma_name_owe_nothing(tmp_path: Path):
    """A lemma is scaffolding: two disconnected modules both spelling one
    `step` collide with nothing a report, a registry entry or a label can name,
    and the obligation said they "each declare a theorem"."""
    chat = built(tmp_path)
    read = chat.lean_workspace.sources

    def two_modules():
        found = dict(read())
        helper = "import Mathlib\nlemma step : True := by exact True.intro\n"
        found["First"] = helper
        found["Second"] = helper
        return found

    chat.lean_workspace.sources = two_modules

    assert "step" not in chat.export_material()["shared"]


def test_a_lemma_that_shares_a_theorems_name_still_collides(tmp_path: Path):
    """The half that must survive: the audit records both and a verdict is
    looked up by name, so an audited lemma answers for an unaudited theorem."""
    chat = built(tmp_path)
    read = chat.lean_workspace.sources

    def two_modules():
        found = dict(read())
        found["Other"] = "import Mathlib\nlemma hardyBasic : True := by exact True.intro\n"
        return found

    chat.lean_workspace.sources = two_modules

    assert "hardyBasic" in chat.export_material()["shared"]


def test_the_automation_disclosure_reads_the_same_tree_the_results_do(tmp_path: Path):
    """`_automation_closed` compares a stored statement against the tree; read
    from a later tree it can attach a warning to a statement that is not the
    one shown, or drop one that belongs to it."""
    chat = built(tmp_path)
    seen: list[object] = []
    original = chat._automation_closed

    def watching(sources=None):
        seen.append(sources)
        return original(sources)

    chat._automation_closed = watching
    material = chat.export_material()

    assert seen and seen[0] is not None, "the flags were read from their own tree"
    assert material["automation"] == {}


def test_a_private_theorem_owes_no_audit_it_cannot_have(tmp_path: Path):
    """`_audit_gaps` was asked about a name Lean mangles, so the obligation
    asked for an audit that cannot be established."""
    chat = built(tmp_path)
    read = chat.lean_workspace.sources

    def with_private():
        found = dict(read())
        found["Hidden"] = "import Mathlib\nprivate theorem hidden : True := by exact True.intro\n"
        return found

    chat.lean_workspace.sources = with_private
    owed = " ".join(chat.export_material()["obligations"])

    assert "hidden" not in owed


def test_a_writeup_hardy_never_compiled_is_not_credited_to_hardy(tmp_path: Path):
    """A regular file is not evidence that Hardy made it.

    A clone carries whatever `writeup.pdf` was committed and a user may drop
    one in. "was compiled" then credited Hardy with a document it never
    produced -- beside an outstanding section that may still be asking for the
    compile. The digest of what Hardy's own compile produced is stamped only
    after a compile Hardy ran, so a file it never made cannot match it.
    """
    chat = built(tmp_path)
    (Path(chat.workspace) / "writeup.pdf").write_bytes(b"%PDF-someone-elses")

    said = chat.export_material()["document"]
    assert "was compiled by Hardy" not in said
    assert "not bytes Hardy is recorded as having produced" in said


def test_a_writeup_hardy_did_compile_is_reported_as_compiled(tmp_path: Path):
    """The other half: the claim is available when the record supports it."""
    import hashlib

    chat = built(tmp_path)
    document = Path(chat.workspace) / "writeup.pdf"
    document.write_bytes(b"%PDF-hardys")
    chat.state["tex_signature"] = "whatever this compile was made against"
    chat.state["writeup_sha256"] = hashlib.sha256(b"%PDF-hardys").hexdigest()

    assert "was compiled by Hardy" in chat.export_material()["document"]


def test_a_writeup_replaced_after_the_compile_is_not_attributed_to_hardy(tmp_path: Path):
    """A signature says Hardy compiled *something* here once.

    It says nothing about the bytes now on disk. A user who compiles and then
    drops another PDF over the result leaves the signature truthy, and reading
    it alone credited Hardy with a document it never made. The digest stamped
    at compile time is what answers the question actually being asked.
    """
    import hashlib

    chat = built(tmp_path)
    document = Path(chat.workspace) / "writeup.pdf"
    chat.state["tex_signature"] = "whatever this compile was made against"
    chat.state["writeup_sha256"] = hashlib.sha256(b"%PDF-hardys").hexdigest()
    document.write_bytes(b"%PDF-somebody-elses")

    said = chat.export_material()["document"]
    assert "was compiled by Hardy" not in said
    assert "not bytes Hardy is recorded as having produced" in said


def test_a_workspace_stamped_before_digests_does_not_claim_authorship(tmp_path: Path):
    """The signature without a digest reads as "not established", not as
    Hardy's: the page may say what it does not know and must not guess towards
    the stronger claim."""
    chat = built(tmp_path)
    (Path(chat.workspace) / "writeup.pdf").write_bytes(b"%PDF-older-workspace")
    chat.state["tex_signature"] = "stamped before the digest existed"

    assert "was compiled by Hardy" not in chat.export_material()["document"]


def test_the_export_carries_the_settings_that_shaped_what_was_found(tmp_path: Path):
    """Gathered from the live session, not from a config file read separately.

    The Lean timeout decides whether an audit came back at all, and a missing
    kernel or search backend removes a whole class of observation. Two runs
    that differ only in these are different experiments, and an artifact that
    cannot show it cannot be used to compare them.
    """
    chat = built(tmp_path)

    settings = chat.export_material()["settings"]

    assert "s per call" in settings["Lean timeout"]
    assert settings["Computer algebra"]
    assert settings["Literature search"]


def test_a_session_with_no_kernel_says_none_rather_than_staying_silent(tmp_path: Path):
    chat = built(tmp_path)
    chat.cas = None
    chat.cas_detail = ""

    assert "none" in chat.export_material()["settings"]["Computer algebra"]


def _with_shared(tmp_path: Path, saved: str):
    """A session whose saved Lean is `saved`, with a shared tree beside it.

    The tree has to exist before the session opens: `_discover_shared` runs at
    construction.
    """
    workspace = tmp_path / "problem"
    shared = tmp_path / ".hardy" / "lean"
    shared.mkdir(parents=True)
    (shared / "Helper.lean").write_text("theorem helper : True := trivial\n", encoding="utf-8")
    (shared / "Unrelated.lean").write_text(
        'def token : String := "Bearer abc123def456"\n', encoding="utf-8"
    )
    runtime = FakeChatRuntime([{"role": "assistant", "content": "Nothing to do."}])
    chat = session(workspace, runtime, registered=("hardyBasic",))
    chat.set_goal("Establish the basics")
    # Written into the tree rather than saved through the tool: the fake Lean
    # refuses an import it cannot resolve, and what this is about is which
    # shared modules the gatherer follows from a source that imports them --
    # not whether the fixture's Lean will accept the import.
    lean = Path(chat.workspace) / "lean"
    lean.mkdir(parents=True, exist_ok=True)
    (lean / "Basic.lean").write_text(saved, encoding="utf-8")
    return chat


def test_the_export_carries_the_shared_modules_the_workspace_imports(tmp_path: Path):
    """Keyed by module name -- which is how a saved theorem refers to one and
    what a recipient matches their own copy against."""
    chat = _with_shared(tmp_path, "import Mathlib\nimport Helper\n" + BASIC.split("\n", 1)[1])
    assert chat.shared_roots, "the fixture did not place a shared tree the session can see"

    carried = chat.export_material()["shared_sources"]
    assert "Helper" in carried
    assert "theorem helper : True := trivial" in carried["Helper"]


def test_a_shared_module_nothing_imports_is_not_published(tmp_path: Path):
    """`~/.hardy/lean` is a personal library shared by every project on the
    machine. Copying all of it into a shareable report would disclose an
    unrelated body of work -- rendered verbatim, with the credential filter
    deliberately off, because these are audited sources. Exporting one project
    must not publish another.
    """
    chat = _with_shared(tmp_path, "import Mathlib\nimport Helper\n" + BASIC.split("\n", 1)[1])

    carried = chat.export_material()["shared_sources"]
    assert "Unrelated" not in carried
    assert not any("Bearer abc123def456" in text for text in carried.values())


def test_the_project_copy_wins_when_two_shared_trees_define_a_module(tmp_path: Path):
    """`shared_roots` is project-first and Lean resolves to the first match.

    Taking the later one would put a different source in the page from the one
    the kernel elaborated and the audit graded -- which is the exact failure
    carrying the shared sources at all is meant to prevent.

    Asserted against `_shared_sources` rather than through `export_material`:
    that path calls `_refresh_shared_identity` first, which rediscovers
    `shared_roots` from the layout and would throw away a second root a test
    appended -- so the collision would never be exercised at all.
    """
    workspace = tmp_path / "problem"
    project = tmp_path / ".hardy" / "lean"
    project.mkdir(parents=True)
    (project / "Helper.lean").write_text("theorem helper : True := trivial\n", encoding="utf-8")

    runtime = FakeChatRuntime([{"role": "assistant", "content": "Nothing to do."}])
    chat = session(workspace, runtime)

    personal = tmp_path / "personal"
    personal.mkdir()
    (personal / "Helper.lean").write_text("theorem helper : False := sorry\n", encoding="utf-8")
    chat.shared_roots = (*chat.shared_roots, (personal, personal))

    carried = chat._shared_sources({"Basic": "import Helper\n"})
    assert carried["Helper"] == "theorem helper : True := trivial\n"


def test_a_shared_module_the_workspace_shadows_is_not_published(tmp_path: Path):
    """Lean resolves the problem's own module, so the shared one is not what
    the theorem rests on -- and publishing it would put an unrelated file on
    the page under the name of one the page already carries."""
    workspace = tmp_path / "problem"
    shared = tmp_path / ".hardy" / "lean"
    shared.mkdir(parents=True)
    (shared / "Helper.lean").write_text(
        'def secret : String := "not this one"\n', encoding="utf-8"
    )

    runtime = FakeChatRuntime([{"role": "assistant", "content": "Nothing to do."}])
    chat = session(workspace, runtime)
    lean = Path(chat.workspace) / "lean"
    lean.mkdir(parents=True, exist_ok=True)
    # The workspace declares `Helper` itself, so the import resolves here.
    (lean / "Helper.lean").write_text("theorem helper : True := trivial\n", encoding="utf-8")
    (lean / "Basic.lean").write_text("import Helper\n", encoding="utf-8")

    carried = chat.export_material()["shared_sources"]
    assert "Helper" not in carried
    assert not any("not this one" in text for text in carried.values())


def test_a_personal_library_resolves_its_own_imports_not_the_projects(tmp_path: Path):
    """`_compile_path`'s rule: a shared library sees only the libraries further
    out than itself. A personal module importing `B` is compiled against the
    personal `B`, so the page has to carry that one."""
    workspace = tmp_path / "problem"
    project = tmp_path / ".hardy" / "lean"
    project.mkdir(parents=True)
    (project / "B.lean").write_text("theorem b : True := trivial  -- project\n", encoding="utf-8")

    runtime = FakeChatRuntime([{"role": "assistant", "content": "Nothing to do."}])
    chat = session(workspace, runtime)

    personal = tmp_path / "personal"
    personal.mkdir()
    (personal / "A.lean").write_text("import B\n", encoding="utf-8")
    (personal / "B.lean").write_text("theorem b : True := trivial  -- personal\n", encoding="utf-8")
    chat.shared_roots = (*chat.shared_roots, (personal, personal))

    carried = chat._shared_sources({"Basic": "import A\n"})

    assert "A" in carried
    # `A` lives in the personal tree, so its own `import B` resolves there.
    assert "personal" in carried["B"]


def test_the_settings_carry_the_limits_that_bound_what_could_be_observed(tmp_path: Path):
    """A cell that timed out, a session budget that ran out, and an observation
    the model saw only a summary of are three different reasons a computation
    is missing from the record. A reader comparing two exports has to be able
    to tell a different question from a different budget.

    Read off the runtime that enforces them, so what the page reports is what
    was actually in force rather than what a config file still says.
    """
    from hardy import cas_tools
    from hardy.domain import RunLimits

    chat = built(tmp_path)

    class Session:
        limits = RunLimits()

    chat.cas = cas_tools.CasToolRuntime.__new__(cas_tools.CasToolRuntime)
    chat.cas.session = Session()
    chat.cas.observation_bytes = 32 * 1024

    settings = chat.export_material()["settings"]
    assert "per cell" in settings["Computer algebra limits"]
    assert "per session" in settings["Computer algebra limits"]
    assert "bytes per tool result" in settings["Observed by the model"]
    assert "wall clock" in settings["Literature search budget"]
