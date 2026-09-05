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


def _bundle(body: str = PAPER_SOURCE) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        encoded = body.encode("utf-8")
        info = tarfile.TarInfo("main.tex")
        info.size = len(encoded)
        tar.addfile(info, io.BytesIO(encoded))
    return buffer.getvalue()


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
    def unreachable(**kwargs):
        raise RuntimeError("the provider is down")

    sourced._review_assumption = unreachable

    result = _assume(sourced)

    assert not result.ok
    assert sourced.state["assumptions"] == []
    assert sourced.state["quarantine"]


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
