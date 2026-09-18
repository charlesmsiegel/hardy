"""Serve the real browser bundle over a scripted fake session.

A script, not a test: pytest collects `test_*.py` and this is not one. It
exists so `web/smoke.mjs` -- and a person with a browser -- can drive the
actual page without a model, a provider, or a Lean toolchain. The session is
`FakeSession` from `web_fakes`, whose scripted turn streams "hel", "lo" and
then a `reply` of "hello", so a smoke can assert the whole streaming path
end to end and finish in milliseconds.

Two lines are special. Sending `slow` runs the ordinary scripted turn with a
three-second pause before each event, so a person at a browser can click
something while a turn owns the session and see it refused -- `+ chat` during
a turn is the case that matters, since making the chat is plain file I/O and
opening it is not.

Sending `interleave` runs a turn that interrupts itself
with a session notice between two pieces of text, which is what a delegation
finishing mid-turn does. It exists because that interruption used to make the
page draw the turn twice -- the notice ended the streaming message, the text
after it opened a second one, and the final `reply` replaced only the second.
A page that draws `one two` once has the turn's identity right.

    uv run python tests/unit/web_smoke_server.py --port 8765

Everything it writes goes in a temporary directory that is removed on the way
out; nothing here touches a real project.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from web_fakes import FakeSession, make_config, make_problem, make_registry  # noqa: E402

from hardy.agents.contracts import TurnEvent  # noqa: E402
from hardy.app.web.host import WebHost  # noqa: E402
from hardy.app.web.server import serve  # noqa: E402

SLUG = "sylow"
#: A second registered problem, in a root of its own, so the page's project
#: menu has something to switch to and the registry routes have two roots
#: to move between.
OTHER = "burnside"
#: The line that runs the interrupted turn described in the module docstring.
INTERLEAVE = "interleave"
NOTICE = "a delegation finished while the turn was still streaming"
#: The line that holds a turn open long enough to click something during it.
#: Everything that refuses while a turn owns the session -- opening a chat
#: above all -- can only be seen refusing if there is a turn to refuse under,
#: and the scripted turn is otherwise over in microseconds.
SLOW = "slow"
SLOW_DELAY = 3.0
#: Long enough for the loop to drain what the worker has already posted.
PAUSE = 0.1


class ScriptedSession(FakeSession):
    """`FakeSession`, plus one turn that interrupts itself with a notice."""

    def stream(self, text: str):
        if text.strip() == SLOW:
            # Wrapped rather than set on `self.delay`: the delay is read inside
            # the generator, so assigning it would outlive this turn the way
            # the interleave script used to.
            events = super().stream(text)

            def slowly():
                for event in events:
                    time.sleep(SLOW_DELAY)
                    yield event

            return slowly()
        if text.strip() != INTERLEAVE:
            return super().stream(text)
        # Swapped for this turn and put back, not assigned. Assigning left the
        # session scripted for every turn after it, so a second `npm run smoke`
        # against the same server answered `hello` with "one two" and failed on
        # the fixture rather than on the page. Materialised while the swap is
        # in place, because `FakeSession.stream` reads `self.script` lazily.
        original, self.script = self.script, [
            TurnEvent("text", "one "), TurnEvent("text", "two"), TurnEvent("reply", "one two"),
        ]
        try:
            events = list(super().stream(text))
        finally:
            self.script = original

        def interleaved():
            for index, event in enumerate(events):
                yield event
                if index == 0 and self.on_notice is not None:
                    # The pause is what makes the order deterministic rather
                    # than lucky. A turn's events reach the stream through the
                    # loop's queue and a notice is emitted straight from this
                    # thread, so without it the notice overtakes the text
                    # already posted and lands before the turn instead of
                    # inside it -- which is not the case worth testing.
                    time.sleep(PAUSE)
                    self.on_notice(NOTICE)

        return interleaved()


def seed_workspace(problem: Path) -> None:
    """One Lean file, one TeX file, and one recorded correspondence.

    Without these the Files page can only be seen in its empty state, and the
    editor -- which is the whole of Shipment 3 -- has nothing to open. The
    Lean file deliberately declares two theorems with different fates: one
    that a stored audit grades, and one that nothing has ever audited, so the
    file's own verdict is the worse of the two rather than the better. That is
    the case the header is easy to get wrong.

    The recorded name is what `§ this declaration ↔ informal` reads for "where
    is this stated in tex/". It names `order_30_not_simple` only, so the other
    declaration shows the unmatched case beside it.
    """
    lean = problem / "lean"
    lean.mkdir(parents=True, exist_ok=True)
    (lean / "Sylow.lean").write_text(
        "import Mathlib\n"
        "\n"
        "/-- No group of order 30 is simple. -/\n"
        "theorem order_30_not_simple (G : Type) : True := trivial\n"
        "\n"
        "theorem order_56_not_simple (G : Type) : True := trivial\n",
        encoding="utf-8",
    )
    tex = problem / "tex"
    tex.mkdir(parents=True, exist_ok=True)
    (tex / "writeup.tex").write_text(
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "\\begin{theorem}\\label{thm:order30}No group of order 30 is simple.\\end{theorem}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    record = problem / "session.json"
    if record.is_file():
        state = json.loads(record.read_text(encoding="utf-8"))
        state.setdefault("names", []).append({
            "formal_name": "order_30_not_simple",
            "latex_name": "thm:order30",
            "description": "No group of order 30 is simple.",
        })
        record.write_text(json.dumps(state), encoding="utf-8")


def seed_ledger(problem: Path) -> None:
    """A small project ledger, so the graph panel has something to draw.

    Without it `/api/graph` answers two empty lists and the panel can only be
    seen in its empty state: no layout, no kind colours, no formal badge, no
    edge families, and above all no stale edge -- the one thing the graph says
    that nothing else in the page says. What is written here is chosen for what
    it makes the panel draw:

    * four items across three kind families (a theorem and a lemma in
      *results*, a question in *research*, a section in *documents*), so the
      fill colours can be told apart;
    * a theorem carrying `formal` evidence, for the "F" badge. It takes two
      transactions: a record cannot pin its own digest, since the digest would
      then have to include the reference that names it, and
      `validate_structure` refuses a reference to a version the snapshot does
      not hold. So the bare theorem is committed first and the evidenced
      revision pins that committed version;
    * four relations, `depends_on` and `formalizes` solid, `poses` and
      `documents` dashed (`panels/vocabulary.py`'s `_STYLE`,
      `panels/vocabulary.py:47-58`);
    * two stale edges, one on each of the two paths `graph()`'s own docstring
      distinguishes (`panels/record.py:84-92`). `rel-depends` (`depends_on`,
      theorem to lemma) is stale the *generic* way: `stale_artifacts()`
      (`ledger/views.py:158-166`) only reports on `documents`/`formalizes`
      relations, so a `depends_on` edge's `expected_version`/`current_version`
      are both `None` and the browser falls back to its generic sentence.
      `rel-formalizes` (`formalizes`, the same theorem to the same lemma) is
      stale the *real-versions* way: `formalizes` is one of the two kinds
      `stale_artifacts()` does track, so this edge carries the lemma's exact
      pinned and current version numbers and the browser names them. Both
      point at the one lemma revision below -- the lemma is revised once, in a
      second transaction, after both relations pinned the version it had --
      so one edit exercises both of the stale-sentence paths a browser-level
      check can reach; the previous, `depends_on`-only fixture left the
      real-versions path with no coverage above the Python unit tests.

    Imported inside the function, like `library_import` does, so a smoke server
    that is only serving the page does not pull the ledger package in.
    """
    from hardy.workflows.ledger.contracts import (
        ArtifactRef,
        EvidenceKind,
        EvidenceRef,
        ProjectItem,
        ProjectItemKind,
        ProjectOrigin,
        Relation,
        RelationKind,
    )
    from hardy.workflows.ledger.store import LedgerStore

    store = LedgerStore(problem)

    def append(records):
        return store.append(records, expected_revision=store.read().revision)

    lemma = ProjectItem(
        id="lemma-conjugacy", kind=ProjectItemKind.LEMMA, name="Conjugacy of Sylow subgroups",
        origin=ProjectOrigin.TARGET_PAPER,
        statement="Any two Sylow p-subgroups of a finite group are conjugate.",
    )
    bare = ProjectItem(
        id="thm-sylow-three", kind=ProjectItemKind.THEOREM, name="Sylow III",
        origin=ProjectOrigin.TARGET_PAPER,
        statement="The number of Sylow p-subgroups is congruent to 1 modulo p and divides the index.",
    )
    question = ProjectItem(
        id="q-how-many", kind=ProjectItemKind.QUESTION, name="How many Sylow subgroups?",
        origin=ProjectOrigin.HUMAN_AUTHORED,
        statement="For which groups is the Sylow count exactly one?",
    )
    section = ProjectItem(
        id="sec-counting", kind=ProjectItemKind.SECTION, name="Counting the subgroups",
        origin=ProjectOrigin.GENERATED_LOCAL,
    )
    append([lemma, bare, question, section])
    theorem = bare.model_copy(update={"evidence": (
        EvidenceRef(kind=EvidenceKind.FORMAL, subject=bare.ref, producer="web-smoke-fixture",
                    artifact=ArtifactRef(uri="lean/Sylow.lean", digest="0" * 64)),
    )})
    append([theorem])
    # Pinned after the theorem gained its evidence, so all four start fresh
    # and exactly two of them are made stale below.
    append([
        Relation(id="rel-depends", kind=RelationKind.DEPENDS_ON, source=theorem.ref, target=lemma.ref),
        Relation(id="rel-poses", kind=RelationKind.POSES, source=question.ref, target=theorem.ref),
        Relation(id="rel-documents", kind=RelationKind.DOCUMENTS, source=section.ref, target=theorem.ref),
        # Same endpoints as `rel-depends`, a different kind: `stale_artifacts()`
        # tracks `formalizes` but not `depends_on`, so revising the lemma below
        # makes this edge stale with real version numbers while `rel-depends`
        # stays on the generic, `expected_version: null` path -- see the
        # docstring above.
        Relation(id="rel-formalizes", kind=RelationKind.FORMALIZES, source=theorem.ref, target=lemma.ref),
    ])
    append([lemma.model_copy(update={"statement": "Any two Sylow p-subgroups are conjugate; see Lemma 2."})])


class FakeOpener:
    """The opener `test_web_server.py` uses: a new fake session per open."""

    def __init__(self, root: Path) -> None:
        self.root, self.session = root, None

    def __call__(self, slug, confirm, current, *, chat="main", root=None):
        moved = {"root": root} if root is not None else {}
        config = dataclasses.replace(current, project=slug, chat=chat, **moved)
        self.session = ScriptedSession(config.layout.problem, chat)
        return config, self.session

    def cancel(self) -> bool:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--static",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "src" / "hardy" / "app" / "web" / "static",
        help="the built bundle to serve; the packaged one by default",
    )
    parser.add_argument("--open", action="store_true", help="open a browser on the page")
    options = parser.parse_args(argv)

    if not (options.static / "index.html").is_file():
        parser.error(f"no bundle at {options.static}; run `npm run build` in web/ first")

    with tempfile.TemporaryDirectory(prefix="hardy-web-smoke-") as directory:
        root = Path(directory)
        problem = make_problem(root, SLUG)
        seed_ledger(problem)
        seed_workspace(problem)
        config = make_config(root, SLUG)
        # Two registered problems in two roots, the way a real registry
        # holds them; the launch opens the first, as `hardy web` opens the
        # last one used.
        registry = make_registry(root)
        registry.add(problem)
        registry.add(make_problem(root / "other", OTHER))
        registry.touch(problem)
        host = WebHost(
            config, FakeOpener(root), lambda confirm, cfg: ScriptedSession(cfg.layout.problem),
            projects=registry,
        )
        host.start()
        try:
            serve(host, port=options.port, static=options.static, open_browser=options.open)
        finally:
            host.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
