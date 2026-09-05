r"""Assuming a paper's statement: inventoried eagerly, minted one at a time.

The two properties worth holding on to are opposite in direction. Nothing is
minted that nobody asked for -- reading a paper must not put fifty axioms in
the trust base -- and nothing is minted that was not read: an axiom whose Lean
does not say what the paper says is worse than no axiom, because it lets Hardy
prove things the paper never claimed while naming the paper as the source.
"""

from __future__ import annotations

import io
import json
import sys
import tarfile
from pathlib import Path

import pytest
from test_chat import FakeChatRuntime, factory

from hardy import arxiv
from hardy import assume as assume_module
from hardy.chat import CHAT_TOOLS, MathematicsSession

FEED = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">'
    b"<entry><id>http://arxiv.org/abs/math.DG/0211159v1</id>"
    b"<published>2002-11-11T18:00:00Z</published><updated>2002-11-11T18:00:00Z</updated>"
    b"<title>The entropy formula for the Ricci flow</title>"
    b"<summary>A monotonic expression for the Ricci flow.</summary>"
    b"<author><name>Grigori Perelman</name></author>"
    b'<category term="math.DG"/></entry></feed>'
)

PAPER_SOURCE = r"""
\documentclass{article}
\newtheorem{theorem}{Theorem}
\begin{document}
\begin{theorem}[No local collapsing]\label{thm:collapse}
Every finite-time solution has bounded geometry.
\end{theorem}

\begin{lemma}\label{lem:aux}
The entropy is monotonic.
\end{lemma}
\end{document}
"""

PAPER = "math.DG/0211159v1"


def _bundle(
    body: str = PAPER_SOURCE, second: tuple[str, str] | None = None
) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, text in (("main.tex", body), *( (second,) if second else () )):
            encoded = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(encoded)
            tar.addfile(info, io.BytesIO(encoded))
    return buffer.getvalue()


DECOY_SOURCE = r"""
\documentclass{article}
egin{document}
egin{theorem}\label{decoy}Decoy claim nobody published.\end{theorem}
\end{document}
"""


@pytest.fixture
def session(tmp_path: Path) -> MathematicsSession:
    workspace = tmp_path / "problem"
    workspace.mkdir()
    runtime = FakeChatRuntime([])
    built = MathematicsSession(
        workspace,
        factory(type(runtime), runtime.script),
        (sys.executable, str(Path(__file__).with_name("fake_lean.py"))),
        (sys.executable, str(Path(__file__).with_name("fake_latex.py"))),
        lambda proposal: True,
    )
    bundle = _bundle()

    def transport(url: str, timeout: float, limit: int | None = None) -> bytes:
        return bundle if "e-print" in url else FEED

    built.papers.client = arxiv.ArxivClient(
        built.papers.library,
        transport=transport,
        clock=lambda: 1_000_000.0,
        sleep=lambda seconds: None,
    )
    # An independent reader that agrees, and an inspection gate already
    # satisfied: both are exercised on their own below.
    built._review_assumption = lambda **kwargs: (True, ())
    built._inspect_attempts_since_request = 1
    return built


@pytest.fixture
def sourced(session: MathematicsSession) -> MathematicsSession:
    session._tool("fetch_paper", {"paper_id": PAPER})
    session._tool("fetch_source", {"paper_id": PAPER})
    return session


def _assume(session: MathematicsSession, **overrides):
    arguments = {
        "paper_id": PAPER,
        "statement": "thm:collapse",
        "formal_name": "no_local_collapsing",
        "lean_statement": "True",
        "informal_statement": "Every finite-time solution has bounded geometry.",
        "reason": "Mathlib has no Ricci flow theory.",
    }
    arguments.update(overrides)
    session._inspect_attempts_since_request = 1
    return session._tool("assume_statement", arguments)


# --- Listing ---------------------------------------------------------------------


def test_the_assume_verbs_are_offered() -> None:
    offered = {spec["function"]["name"] for spec in CHAT_TOOLS}
    assert {"list_statements", "assume_statement"} <= offered


def test_listing_a_paper_with_no_source_says_how_to_get_one(session) -> None:
    session._tool("fetch_paper", {"paper_id": PAPER})

    result = session._tool("list_statements", {"paper_id": PAPER})

    assert not result.ok
    assert "fetch_source" in result.output


def test_every_statement_the_paper_makes_is_listed(sourced) -> None:
    result = sourced._tool("list_statements", {"paper_id": PAPER})

    assert result.ok, result.output
    payload = json.loads(result.output)
    assert [item["ref"] for item in payload["statements"]] == ["thm:collapse", "lem:aux"]
    assert payload["statements"][0]["heading"] == "No local collapsing"


def test_listing_mints_nothing(sourced) -> None:
    """Eager inventory, lazy minting: reading a paper must not widen the
    trust base by a single axiom."""
    sourced._tool("list_statements", {"paper_id": PAPER})

    assert sourced.state["assumptions"] == []
    assert not (sourced.workspace / "lean" / "Papers").exists()


# --- Minting ------------------------------------------------------------------------


def test_an_approved_statement_becomes_an_axiom_in_the_paper_namespace(sourced) -> None:
    result = _assume(sourced)

    assert result.ok, result.output
    module = next((sourced.workspace / "lean" / "Papers").glob("*.lean"))
    source = module.read_text(encoding="utf-8")
    assert "namespace Papers." in source
    assert "axiom no_local_collapsing : True" in source
    assert "thm:collapse" in source
    assert PAPER in source


def test_the_axiom_is_recorded_with_the_paper_it_came_from(sourced) -> None:
    _assume(sourced)

    entry = sourced.state["assumptions"][0]
    assert entry["paper"]["arxiv_id"] == PAPER
    assert entry["paper"]["ref"] == "thm:collapse"
    assert entry["paper"]["cite_key"]
    assert entry["formal_name"].endswith("no_local_collapsing")


def test_the_paper_is_cited_so_the_docstring_key_resolves(sourced) -> None:
    """A docstring naming a bibliography key nothing defines sends a reader
    looking for an entry that is not there."""
    _assume(sourced)

    entry = sourced.state["assumptions"][0]
    assert sourced.papers.bibliography.find(f"arxiv:{PAPER}") is not None
    generated = (sourced.workspace / "tex" / "references.tex").read_text(encoding="utf-8")
    assert entry["paper"]["cite_key"] in generated


def test_the_approved_name_is_the_qualified_one_lean_will_report(sourced) -> None:
    """An axiom inside `namespace Papers.x` is `Papers.x.foo` to Lean, and an
    approval recorded under the short name would be refused by the save gate
    that compares them."""
    _assume(sourced)

    assert sourced.state["assumptions"][0]["formal_name"].startswith("Papers.")


def test_a_statement_the_paper_does_not_make_is_refused(sourced) -> None:
    result = _assume(sourced, statement="thm:invented")

    assert not result.ok
    assert "thm:collapse" in result.output
    assert sourced.state["assumptions"] == []


def test_a_paper_with_no_source_cannot_be_assumed_from(session) -> None:
    session._tool("fetch_paper", {"paper_id": PAPER})

    result = _assume(session)

    assert not result.ok
    assert "fetch_source" in result.output


def test_a_statement_lean_proves_outright_is_refused_as_a_theorem(sourced, monkeypatch) -> None:
    monkeypatch.setattr(
        sourced, "_assumption_probe", lambda declaration: ("Lean proves this outright", "")
    )

    result = _assume(sourced)

    assert not result.ok
    assert "proves this outright" in result.output
    assert sourced.state["assumptions"] == []


def test_a_statement_whose_negation_lean_proves_is_refused(sourced, monkeypatch) -> None:
    from hardy import refute

    monkeypatch.setattr(
        sourced, "_refutation_probe", lambda statement: refute.Verdict(True, tactic="decide")
    )

    result = _assume(sourced)

    assert not result.ok
    assert "decide" in result.output
    assert sourced.state["assumptions"] == []
    assert not (sourced.workspace / "lean" / "Papers").exists()


def test_a_refutation_that_could_not_be_run_does_not_block_the_request(
    sourced, monkeypatch
) -> None:
    """A machine whose Lean will not start must not be one where nothing can
    be assumed. The caveat travels to the human instead."""
    from hardy import refute

    shown: list[dict] = []
    sourced.confirm = lambda proposal: shown.append(dict(proposal)) or True
    monkeypatch.setattr(
        sourced,
        "_refutation_probe",
        lambda statement: refute.Verdict(False, caveat="the refutation probe did not finish"),
    )

    result = _assume(sourced)

    assert result.ok, result.output
    assert "did not finish" in shown[0]["checked"]


def test_the_human_is_shown_the_paper_s_own_words(sourced) -> None:
    shown: list[dict] = []
    sourced.confirm = lambda proposal: shown.append(dict(proposal)) or True

    _assume(sourced)

    assert "bounded geometry" in shown[0]["paper_text"]
    assert shown[0]["source"].startswith("arXiv:")


def test_a_declined_statement_mints_nothing(sourced) -> None:
    sourced.confirm = lambda proposal: False

    result = _assume(sourced)

    assert not result.ok
    assert sourced.state["assumptions"] == []
    assert not (sourced.workspace / "lean" / "Papers").exists()


# --- Faithfulness and quarantine -------------------------------------------------------


def test_a_statement_the_reader_disputes_is_quarantined_rather_than_minted(sourced) -> None:
    sourced._review_assumption = lambda **kwargs: (False, ("the Lean drops the hypothesis",))

    result = _assume(sourced)

    assert not result.ok
    assert "quarantin" in result.output.lower()
    assert sourced.state["assumptions"] == []
    assert not (sourced.workspace / "lean" / "Papers").exists()
    quarantined = sourced.state["quarantine"]
    assert quarantined[0]["formal_name"].endswith("no_local_collapsing")
    assert quarantined[0]["divergences"] == ["the Lean drops the hypothesis"]


def test_a_reader_that_cannot_be_reached_is_not_an_agreement(sourced) -> None:
    """Nothing is minted -- and nothing is recorded against the name either,
    because Hardy established nothing about this translation. See
    `test_a_reader_that_could_not_be_reached_is_not_recorded_as_a_finding`."""
    def unreachable(**kwargs):
        raise RuntimeError("the provider is down")

    sourced._review_assumption = unreachable

    result = _assume(sourced)

    assert not result.ok
    assert sourced.state["assumptions"] == []
    assert not sourced.state.get("quarantine")


def test_the_review_happens_before_the_human_is_asked(sourced) -> None:
    """Nobody should be asked to approve a statement Hardy has established
    does not say what the paper says."""
    shown: list[dict] = []
    sourced.confirm = lambda proposal: shown.append(dict(proposal)) or True
    sourced._review_assumption = lambda **kwargs: (False, ("wrong quantifier",))

    _assume(sourced)

    assert shown == []


def test_a_quarantined_name_cannot_be_declared_by_hand(sourced) -> None:
    """Quarantine is a rule, not a warning: the name is not importable."""
    sourced._review_assumption = lambda **kwargs: (False, ("wrong quantifier",))
    _assume(sourced)
    quarantined = sourced.state["quarantine"][0]["formal_name"]

    refusal = sourced._final_gates(f"axiom {quarantined} : True\n")

    assert refusal is not None
    assert "quarantin" in refusal.output.lower()


def test_a_quarantined_statement_is_reported_in_the_workspace(sourced) -> None:
    sourced._review_assumption = lambda **kwargs: (False, ("wrong quantifier",))
    _assume(sourced)

    listing = json.loads(sourced._tool("read_workspace", {}).output)

    assert listing["quarantine"][0]["divergences"] == ["wrong quantifier"]


def test_an_opaque_constant_is_recorded_as_added_trust(sourced) -> None:
    result = _assume(
        sourced,
        kind="constant",
        formal_name="RicciFlow",
        lean_statement="Type",
        statement="lem:aux",
    )

    assert result.ok, result.output
    module = next((sourced.workspace / "lean" / "Papers").glob("*.lean"))
    assert "opaque RicciFlow : Type" in module.read_text(encoding="utf-8")
    assert sourced.state["assumptions"][0]["kind"] == "constant"


# --- The generated module is Hardy's ------------------------------------------------


def test_the_papers_module_may_not_be_written_by_hand(sourced) -> None:
    _assume(sourced)
    module = next((sourced.workspace / "lean" / "Papers").glob("*.lean"))
    relative = f"Papers/{module.name}"

    result = sourced._tool(
        "save_lean", {"path": relative, "source": "import Mathlib\naxiom sneaky : False\n"}
    )

    assert not result.ok
    assert "assume_statement" in result.output
    assert "sneaky" not in module.read_text(encoding="utf-8")


def test_the_papers_module_may_not_be_deleted_by_hand(sourced) -> None:
    _assume(sourced)
    module = next((sourced.workspace / "lean" / "Papers").glob("*.lean"))

    result = sourced._tool("delete_file", {"path": f"Papers/{module.name}"})

    assert not result.ok
    assert module.exists()


def test_a_second_assumption_joins_the_same_module(sourced) -> None:
    _assume(sourced)
    _assume(
        sourced,
        statement="lem:aux",
        formal_name="entropy_monotonic",
        informal_statement="The entropy is monotonic.",
    )

    modules = list((sourced.workspace / "lean" / "Papers").glob("*.lean"))
    assert len(modules) == 1
    source = modules[0].read_text(encoding="utf-8")
    assert "axiom no_local_collapsing" in source
    assert "axiom entropy_monotonic" in source
    assert len(sourced.state["assumptions"]) == 2


def test_the_reader_is_given_no_tools_and_no_conversation(session, tmp_path: Path) -> None:
    """Independence of context, not only of weights. A reader handed the
    session's own thread would be reading the translation through the
    conversation that produced it, which is the bias the check exists to
    avoid."""
    built: list[dict] = []

    class Reader:
        model = "reader@test"

        def __init__(self, **context):
            built.append(context)

        def stream(self, text):
            return iter(())

        def ask(self, text):
            return '{"agrees": true, "divergences": []}'

        def cancel(self):
            pass

    # The fixture stubs the reader out; these two tests are about the real one.
    del session._review_assumption
    session._make_runtime = lambda **context: Reader(**context)

    agreed, divergences = session._review_assumption(
        paper="arXiv:x -- T",
        reference="thm:1",
        paper_text="The paper says something.",
        formal_name="Papers.k.foo",
        lean_statement="True",
        informal_statement="Something.",
    )

    assert agreed and divergences == ()
    assert built[0]["specs"] == []
    assert built[0]["session_id"] is None


def test_a_reader_that_answers_with_prose_is_not_an_agreement(session) -> None:
    class Reader:
        model = "reader@test"

        def __init__(self, **context):
            pass

        def stream(self, text):
            return iter(())

        def ask(self, text):
            return "Looks fine to me."

        def cancel(self):
            pass

    # The fixture stubs the reader out; these two tests are about the real one.
    del session._review_assumption
    session._make_runtime = lambda **context: Reader(**context)

    agreed, divergences = session._review_assumption(
        paper="arXiv:x -- T",
        reference="thm:1",
        paper_text="The paper says something.",
        formal_name="Papers.k.foo",
        lean_statement="True",
        informal_statement="Something.",
    )

    assert not agreed
    assert divergences


def test_a_quarantined_constant_cannot_be_declared_as_opaque(sourced) -> None:
    r"""`render_module` writes `opaque` for `kind="constant"`, so `opaque` is
    the spelling this feature mints -- and the gate scanned for `axiom` and
    `constant` only. A name the independent reader refused could be declared
    by hand under the one keyword the quarantine could not see."""
    sourced._review_assumption = lambda **kwargs: (False, ("wrong object",))
    _assume(sourced, kind="constant", formal_name="RicciFlow", lean_statement="Type")
    quarantined = sourced.state["quarantine"][0]["formal_name"]

    refusal = sourced._final_gates(f"opaque {quarantined} : Type\n")

    assert refusal is not None
    assert "quarantin" in refusal.output.lower()


def test_an_unapproved_opaque_constant_is_refused_like_an_axiom(sourced) -> None:
    """An `opaque` is a declaration with no proof, exactly as an `axiom` is:
    it asserts that something of that type exists. Approval is what makes one
    admissible, whichever keyword it is spelled with."""
    refusal = sourced._final_gates("opaque cheat : Nat\n")

    assert refusal is not None
    assert "unapproved" in refusal.output.lower() or "quarantin" in refusal.output.lower()


def test_an_approved_constant_still_saves(sourced) -> None:
    """The gate has to admit what the mint writes, or nothing can be assumed."""
    result = _assume(
        sourced, kind="constant", formal_name="RicciFlow", lean_statement="Type",
        statement="lem:aux",
    )

    assert result.ok, result.output


def test_assuming_a_name_already_recorded_is_refused_before_anyone_is_asked(sourced) -> None:
    """The second mint rendered the module from the record *and* appended the
    new statement, so the same axiom was declared twice and the save failed --
    after a human had approved it, and blaming the approval flow for a
    rendering bug. There is no path to revise a minted assumption in place
    (`Papers/` is refused to save_lean and delete_file), so the honest answer
    is to refuse before the prompt."""
    assert _assume(sourced).ok
    shown: list[dict] = []
    sourced.confirm = lambda proposal: shown.append(dict(proposal)) or True

    again = _assume(sourced, lean_statement="2 = 2")

    assert not again.ok
    assert "already" in again.output.lower()
    assert shown == [], "nobody should be asked to approve a name that is already minted"
    module = next((sourced.workspace / "lean" / "Papers").glob("*.lean"))
    source = module.read_text(encoding="utf-8")
    assert source.count("axiom no_local_collapsing") == 1
    assert len(sourced.state["assumptions"]) == 1


def test_a_constant_statement_is_shape_checked_like_an_axiom(sourced) -> None:
    """`kind="constant"` skipped every pre-approval gate, so a statement
    carrying its own declaration reached the human and the generated file."""
    shown: list[dict] = []
    sourced.confirm = lambda proposal: shown.append(dict(proposal)) or True

    result = _assume(
        sourced,
        kind="constant",
        formal_name="Widget",
        lean_statement="Type\naxiom sneaky : False",
        statement="lem:aux",
    )

    assert not result.ok
    assert sourced.state["assumptions"] == []
    assert shown == [], "the shape gate must run before anyone is asked to approve"


def test_a_paper_whose_inclusions_nest_deeply_is_refused_not_crashed(sourced) -> None:
    """A bundle of 1200 files each `\\input`ing the next is well inside every
    quota. The walk recursed per inclusion and raised `RecursionError`, which
    is a `RuntimeError` and escaped the tool dispatcher entirely."""
    from hardy import assume as assume_module

    files = {"main.tex": "\\documentclass{article}\\begin{document}\\input{f0}\\end{document}"}
    for index in range(1200):
        files[f"f{index}.tex"] = f"\\input{{f{index + 1}}}"
    files["f1200.tex"] = "\\begin{theorem}\\label{t}Deep.\\end{theorem}"

    found = assume_module.inventory(files)

    assert isinstance(found, tuple)


def test_the_papers_tree_is_hardys_whatever_the_case(sourced) -> None:
    """`papers/` and `Papers/` are one file on macOS and Windows."""
    _assume(sourced)
    module = next((sourced.workspace / "lean" / "Papers").glob("*.lean"))

    result = sourced._tool(
        "save_lean",
        {"path": f"papers/{module.name}", "source": "import Mathlib\naxiom sneaky : False\n"},
    )

    assert not result.ok
    assert "assume_statement" in result.output


def test_an_assumption_whose_statement_hides_a_hole_is_not_reported_clean(sourced) -> None:
    """A `sorry` inside an axiom's type is a hole, and the stand-in used to
    accept the whole file before it looked for one."""
    _assume(sourced)
    module = next((sourced.workspace / "lean" / "Papers").glob("*.lean"))
    holed = module.read_text(encoding="utf-8").replace(
        "axiom no_local_collapsing : True", "axiom no_local_collapsing : (sorry : Prop)"
    )

    result = sourced._tool("check_lean", {"path": "Holed.lean", "source": holed})

    assert "sorry" in result.output.lower() or not result.ok


def test_a_quarantine_does_not_brick_the_same_leaf_in_another_paper(sourced) -> None:
    """The leaf fallback matched across papers, and `Papers/<A>.lean` is
    regenerated whole on every mint -- so one paper's rejected `main` refused
    another paper's already-approved `main` forever, and the message accused
    the innocent axiom of being the quarantined one."""
    sourced._review_assumption = lambda **kwargs: (False, ("wrong quantifier",))
    _assume(sourced)
    quarantined = sourced.state["quarantine"][0]["formal_name"]
    leaf = quarantined.rsplit(".", 1)[-1]
    elsewhere = f"Papers.someone_else_0000000000.{leaf}"

    refusal = sourced._final_gates(f"axiom {elsewhere} : True\n")

    assert refusal is None or "quarantin" not in refusal.output.lower()


def test_an_unrelated_approval_sharing_a_leaf_is_not_refused_as_quarantined(sourced) -> None:
    """A top-level `axiom key_bound` is a different Lean declaration from
    `Papers.<key>.key_bound`, and it is the shape `request_assumption`
    approves. Matching quarantine on a shorter spelling only ever fired on
    that case -- and the approval gate below already refuses an unapproved
    bare name, so the branch bought nothing and cost a human's approval."""
    sourced._review_assumption = lambda **kwargs: (False, ("wrong quantifier",))
    _assume(sourced, formal_name="key_bound")
    leaf = sourced.state["quarantine"][0]["formal_name"].rsplit(".", 1)[-1]
    sourced.state["assumptions"].append(
        {"formal_name": leaf, "lean_statement": "True", "informal_statement": "x"}
    )

    assert sourced._final_gates(f"axiom {leaf} : True\n") is None


def test_a_bare_name_nobody_approved_is_still_refused(sourced) -> None:
    """What the dropped quarantine branch was standing in for: the approval
    gate catches it, and says the true thing about why."""
    sourced._review_assumption = lambda **kwargs: (False, ("wrong quantifier",))
    _assume(sourced, formal_name="key_bound")

    refusal = sourced._final_gates("axiom key_bound : True\n")

    assert refusal is not None
    assert "unapproved" in refusal.output.lower()


def test_the_generated_docstring_quotes_the_paper_s_own_sentence(sourced) -> None:
    """`assume.py`'s promise is that a reader who wants to check an assumption
    can find the sentence it was made from. The module is regenerated from the
    durable record rather than from the object just minted, and the record
    carried no paper text -- so no generated module ever quoted the paper."""
    result = _assume(sourced)

    assert result.ok, result.output
    source = next((sourced.workspace / "lean" / "Papers").glob("*.lean")).read_text(
        encoding="utf-8"
    )

    assert "The paper states:" in source
    assert "bounded geometry" in source


def test_a_refusal_past_the_search_gate_still_spends_the_inspection(sourced) -> None:
    """`request_assumption` spends the evidence in a `finally` around
    everything after the gate, and says why: letting it sit unconsumed let a
    next request under a different name walk through the gate on evidence
    that was never about it. Here the `finally` sat around the mint alone, so
    every refusal between the gate and the mint returned with it intact."""
    sourced.search = object()
    assert _assume(sourced).ok

    sourced._inspect_attempts_since_request = 1
    refused = _assume(sourced)

    assert not refused.ok
    assert "already an approved assumption" in refused.output
    assert sourced._inspect_attempts_since_request == 0


def test_the_workspace_listing_of_refusals_is_bounded(sourced) -> None:
    """The record keeps every refusal -- it is what the gate refuses from --
    but the listing is re-sent whole on every `read_workspace`, so a model
    that keeps proposing bad Lean was growing its own context with its own
    rejected statements until the turn died."""
    sourced.state["quarantine"] = [
        {
            "formal_name": f"Papers.k.n{index}",
            "lean_statement": "x " * 5_000,
            "informal_statement": "y " * 5_000,
            "kind": "statement",
            "divergences": ["z " * 5_000],
            "paper": {"arxiv_id": PAPER, "cite_key": "k", "ref": "thm:collapse"},
        }
        for index in range(200)
    ]

    listing = json.loads(sourced._tool("read_workspace", {}).output)

    assert listing["quarantine_count"] == 200
    assert len(listing["quarantine"]) <= 20
    assert len(json.dumps(listing["quarantine"])) < 40_000
    # The most recent, because that is what the model just tried.
    assert listing["quarantine"][-1]["formal_name"] == "Papers.k.n199"


def test_a_listing_says_when_it_stopped_short_of_the_paper(sourced, monkeypatch) -> None:
    """A listing cut at the bound looked exactly like a paper that stops
    there, and `assume_statement` then answered "the paper makes no statement
    called that" about a statement the paper does make."""
    monkeypatch.setattr(assume_module, "MAX_STATEMENTS", 1)

    listing = json.loads(sourced._tool("list_statements", {"paper_id": PAPER}).output)

    assert listing["truncated"] is True
    refusal = _assume(sourced, statement="lem:aux")
    assert not refusal.ok
    assert "stopped" in refusal.output or "truncat" in refusal.output


def test_a_listing_names_the_documents_it_did_not_read(session) -> None:
    """One root is read and the others are not, decided by a filename. A
    reader weighing an assumption is owed the fact that another document in
    the bundle was never looked at -- a decoy carrying a real preamble is
    otherwise invisible."""
    session._tool("fetch_paper", {"paper_id": PAPER})
    session.papers.library.admit_source(
        session.papers._held(PAPER).identifier,
        _bundle(PAPER_SOURCE, second=("decoy.tex", DECOY_SOURCE)),
        source_url="u",
        fetched_at="t",
    )

    listing = json.loads(session._tool("list_statements", {"paper_id": PAPER}).output)

    assert listing["unread_documents"] == ["decoy.tex"]
    assert all(item["file"] != "decoy.tex" for item in listing["statements"])


def test_a_reader_that_could_not_be_reached_is_not_recorded_as_a_finding(sourced) -> None:
    """Refusing to mint is right -- an unreachable reader is not an
    agreement. Recording it as a *divergence* is not: quarantine is durable,
    there is no path to clear an entry, and one provider 503 was blacklisting
    a name for the life of the project under a verdict nobody reached."""
    def unreachable(**kwargs):
        raise ConnectionError("provider 503")

    sourced._review_assumption = unreachable

    result = _assume(sourced)

    assert not result.ok
    assert sourced.state["assumptions"] == []
    assert sourced.state.get("quarantine", []) == [], "no reader said this was unfaithful"
    assert "could not be reached" in result.output
    assert "try again" in result.output.lower()


def test_the_name_stays_available_after_a_reader_could_not_be_reached(sourced) -> None:
    """The retry is the whole point of not quarantining: a second call must
    reach the human rather than meet a permanent refusal."""
    calls = []

    def flaky(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise ConnectionError("provider 503")
        return True, ()

    sourced._review_assumption = flaky

    assert not _assume(sourced).ok
    second = _assume(sourced)

    assert second.ok, second.output
    assert len(sourced.state["assumptions"]) == 1


def test_a_reader_that_disputes_the_lean_is_still_quarantined(sourced) -> None:
    """The distinction is between a verdict and a failure to get one."""
    sourced._review_assumption = lambda **kwargs: (False, ("the Lean drops the hypothesis",))

    result = _assume(sourced)

    assert not result.ok
    assert sourced.state["quarantine"][0]["divergences"] == ["the Lean drops the hypothesis"]


def test_a_dotted_formal_name_is_refused_before_the_human_is_asked(sourced) -> None:
    """The module is regenerated with only the leaf of the recorded name, so
    a dotted name always failed the save gate afterwards -- spending a human
    approval, naming a Lean name nobody proposed, and advising a tool that
    cannot write `Papers/` at all. Refused up front instead."""
    shown: list[dict] = []
    sourced.confirm = lambda proposal: shown.append(dict(proposal)) or True

    result = _assume(sourced, formal_name="Ricci.no_collapse")

    assert not result.ok
    assert shown == [], "nobody should be asked to approve a name that cannot be minted"
    assert "." in result.output
    assert sourced.state["assumptions"] == []


def test_a_minted_axiom_records_when_it_was_approved_and_against_what(sourced) -> None:
    """`request_assumption` writes both, and every renderer reads them. The
    mint path wrote neither, so the export said "this approval predates the
    field" of an approval seconds old -- and that the goal shown may not be
    the one it was given for, when `_mint` had just shown that very goal."""
    sourced.state["goal"] = "Prove the Poincare conjecture."

    assert _assume(sourced).ok

    record = sourced.state["assumptions"][0]
    assert record["approved_at"], "when a human said yes"
    assert record["goal_at_approval"] == "Prove the Poincare conjecture."


def test_an_assumed_constant_is_exported_as_the_opaque_it_is(sourced) -> None:
    """`kind='constant'` is written into the module as `opaque`, and the two
    are not the same trust. The export printed `axiom` under a comment
    reading "the declaration the results above rest on, exactly"."""
    from hardy import export

    assert _assume(
        sourced, kind="constant", formal_name="RicciFlow", lean_statement="Type"
    ).ok

    page = export._assumptions(sourced.state["assumptions"])

    assert "opaque Papers." in page
    assert "axiom Papers." not in page


def test_a_reader_that_answers_the_string_false_has_not_agreed(sourced) -> None:
    """`bool("false")` is `True`. Every non-empty string the reader could put
    in `agrees` -- "false", "no", "disagree" -- read as agreement, so a
    reader that refused and spelled out the divergence had its axiom minted
    anyway and its findings discarded."""
    def refusing(**kwargs):
        return sourced._read_review(
            '{"agrees": "false", "divergences": ["the Lean asserts the converse"]}'
        )

    sourced._review_assumption = refusing

    result = _assume(sourced)

    assert not result.ok
    assert sourced.state["assumptions"] == []


def test_an_answer_with_no_verdict_field_is_not_a_verdict(sourced) -> None:
    """A model that paraphrases its schema answers with a JSON object that
    has no `agrees` key. That is not a review, so it must refuse and record
    nothing -- quarantining it under an empty divergence list is the durable
    blacklisting the unreachable-reader split exists to prevent, arriving by
    the schema rather than by the transport."""
    def paraphrasing(**kwargs):
        return sourced._read_review('{"verdict": "agrees", "notes": []}')

    sourced._review_assumption = paraphrasing

    result = _assume(sourced)

    assert not result.ok
    assert sourced.state["assumptions"] == []
    assert not sourced.state.get("quarantine")
    assert "could not be reached" in result.output


def test_a_reader_that_answers_true_agrees(sourced) -> None:
    def agreeing(**kwargs):
        return sourced._read_review('{"agrees": true, "divergences": []}')

    sourced._review_assumption = agreeing

    assert _assume(sourced).ok


def test_an_empty_listing_names_the_documents_it_did_not_read(session) -> None:
    """"The paper states nothing Hardy recognises" is a claim about the
    paper. When another document in the bundle went unread, the honest
    answer says so -- the empty-listing branch returned before the payload
    that carries `unread_documents` was ever built."""
    session._tool("fetch_paper", {"paper_id": PAPER})
    session.papers.library.admit_source(
        session.papers._held(PAPER).identifier,
        _bundle(
            "\\documentclass{article}\n\\begin{document}\nNo results here.\n\\end{document}\n",
            second=("zother.tex", DECOY_SOURCE),
        ),
        source_url="u",
        fetched_at="t",
    )

    result = session._tool("list_statements", {"paper_id": PAPER})

    assert not result.ok
    assert "zother.tex" in result.output


def test_the_appendix_owes_the_declaration_lean_was_actually_given(sourced) -> None:
    """The generated module writes `opaque`, and the completion gate demanded
    a verbatim `axiom` line that exists nowhere in the tree. The only
    reachable finished state was one whose published appendix misstated what
    the work rests on -- understating the trust base, since an opaque
    constant is the stronger thing to have asserted."""
    from hardy import completion

    assert _assume(
        sourced, kind="constant", formal_name="widget", lean_statement="Type"
    ).ok
    name = sourced.state["assumptions"][0]["formal_name"]

    owed = completion._assumption_obligations(
        sourced.state["assumptions"],
        {name},
        (),
        completion.displayed("\\appendix\nNothing quoted here.\n"),
    )

    quoted = " ".join(item.detail for item in owed)
    assert f"opaque {name} : Type" in quoted
    assert f"axiom {name} : Type" not in quoted


def test_the_human_is_shown_the_keyword_hardy_will_write(sourced) -> None:
    """The one point where a person decides. It printed `Lean: axiom ...`
    for a declaration Hardy writes as `opaque ...`."""
    shown: list[dict] = []
    sourced.confirm = lambda proposal: shown.append(dict(proposal)) or True

    _assume(sourced, kind="constant", formal_name="widget", lean_statement="Type")

    assert shown[0]["kind"] == "constant"
    assert shown[0]["keyword"] == "opaque"


def test_a_guillemet_name_carrying_a_dot_is_refused_before_the_human_is_asked(
    sourced,
) -> None:
    """`ANY_NAME` admits `«a.b»`, and the module is regenerated with
    `rsplit(".", 1)[-1]` -- giving the leaf `b»`. The human approved
    `Papers.<key>.«a.b»` and the file was written with `axiom b»`. The gate
    has to be "one component", not "matches a Lean name"."""
    shown: list[dict] = []
    sourced.confirm = lambda proposal: shown.append(dict(proposal)) or True

    result = _assume(sourced, formal_name="«a.b»")

    assert not result.ok
    assert shown == []
    assert sourced.state["assumptions"] == []


def test_a_guillemet_name_without_a_dot_is_still_allowed(sourced) -> None:
    """The escape exists so a name Lean needs quoting for can be used."""
    assert _assume(sourced, formal_name="«a b»").ok


def test_a_statement_the_probe_cannot_collapse_is_a_refusal_not_a_crash(sourced) -> None:
    """`_assumption_shape` rejects `\\n` and `\\r`; `probe_source` rejects six
    line terminators. A separator that survives `normalise_lean` inside a
    string literal raised `ValueError` out of the tool -- no
    `assumption_prompt` recorded, and the search evidence spent by the
    `finally`. `_refutation_probe`'s docstring opens "and never crash"."""
    result = _assume(sourced, lean_statement='True ∨ ("a b" = "a b")')

    assert not result.ok
    assert sourced.state["assumptions"] == []
