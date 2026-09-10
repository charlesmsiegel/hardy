"""Evidence gates for widening trust, independent of sessions and providers.

Theory: trust requests have explicit categories and exact scope identities; Lean
and source evidence can refuse them or expose gaps, never establish approval.
Adapters acquire evidence and own approval/publication. Probe algorithms live here
over named source operations; formal.refute remains the negation source/judge owner.
B2 can supply authenticated subject/scope/source references at this seam without
turning a local hypothesis or conjecture into global trust. This is a cheap filter,
not a decision procedure; imported source layout and diagnostic positions matter.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from typing import Any

from hardy.formal import refute
from hardy.formal.contracts import DeclaredAssumption
from hardy.formal.syntax import normalise_lean
from hardy.formal.verifier import declaration_violation
from hardy.formal.workspace import ANY_NAME, COMMAND, unreadable_assumptions
from hardy.literature import statements as paper_statements
from hardy.workflows.ledger.contracts import ArtifactRef, Scope, VersionRef


class TrustRequestKind(str, Enum):
    GLOBAL_ASSUMPTION = "global_assumption"
    PAPER_STATEMENT_ASSUMPTION = "paper_statement_assumption"
    PREAUTHORIZED_RUN_ASSUMPTION = "preauthorized_run_assumption"
    LOCAL_BINDER = "local_binder"
    LOCAL_HYPOTHESIS = "local_hypothesis"
    CONJECTURE = "conjecture"


@dataclass(frozen=True)
class AdmissionRequest:
    """Caller-owned routing and identity, never fields taken from model prose."""

    kind: TrustRequestKind
    subject: VersionRef | None = None
    scope: Scope | None = None


@dataclass(frozen=True)
class SourceEvidence:
    """Exact read source; callers authenticate bytes and any ledger linkage."""

    artifact: ArtifactRef
    statement_read: bool
    subject: VersionRef | None = None


@dataclass(frozen=True)
class ProbeOperations:
    """Complete-source elaboration and refutation operations, budgets caller-owned."""

    elaborate: Callable[[str], Any]
    refute: Callable[[str], Any]


@dataclass(frozen=True)
class CheckDecision:
    refusal: str | None = None
    checked: str = ""


class FaithfulnessDisposition(str, Enum):
    UNAVAILABLE = "unavailable"
    QUARANTINE = "quarantine"
    ACCEPT = "accept"


@dataclass
class SearchEvidence:
    """One request's search, consumed by its adapter after the search gate passes."""

    attempts: int = 0
    inspected: bool = False
    searched: list[str] = field(default_factory=list)

    def attempted_inspection(self) -> None:
        self.attempts += 1

    def consume(self) -> None:
        self.attempts = 0
        self.inspected = False
        self.searched = []

    def note_inspected(self, names: list[str], output: str) -> None:
        resolved: set[str] = set()
        try:
            payload = json.loads(output[output.index("{"):])
            resolved = {item["name"] for item in payload.get("resolved", [])}
        except (ValueError, KeyError, TypeError):
            pass
        self.searched.extend(f"{name} {'✓' if name in resolved else '✗'}" for name in names)
        self.inspected = True

    def refusal(self, *, available: bool, paper: bool = False) -> str | None:
        if not available or self.attempts:
            return None
        prefix = "no `inspect_declarations` has been run since the last assumption request. "
        if paper:
            return prefix + "Look for the result in Mathlib before assuming it from the paper."
        return prefix + ("Look for the result before assuming it: pass several "
                         "candidate spellings and let Lean say which exist.")

    def description(self) -> list[str]:
        if self.attempts and not self.inspected:
            return [f"{self.attempts} inspection(s) attempted since the last request, none finished"]
        if len(self.searched) > 20:
            return [f"{len(self.searched)} names inspected; last 20:"] + self.searched[-20:]
        return list(self.searched)


class AdmissionPolicy:
    """Staged decisions; effectful adapters retain each route's gate ordering."""

    def request_refusal(self, request: AdmissionRequest) -> str | None:
        if request.kind not in (
            TrustRequestKind.GLOBAL_ASSUMPTION,
            TrustRequestKind.PAPER_STATEMENT_ASSUMPTION,
            TrustRequestKind.PREAUTHORIZED_RUN_ASSUMPTION,
        ):
            return "Local context and conjectures cannot be admitted as global assumptions."
        if (request.subject is None) != (request.scope is None):
            return "Admission subject and scope must be supplied together."
        if request.scope is not None and request.subject is not None:
            for target in request.scope.must_prove:
                if target == request.subject:
                    return "This scope must prove the requested subject; it cannot assume itself."
                if target.id == request.subject.id:
                    return "Admission subject has a stale or mismatched must_prove scope revision."
        return None

    def preauthorized_declaration(
        self, request: AdmissionRequest, declaration: DeclaredAssumption
    ) -> str | None:
        refusal = self.request_refusal(request)
        if refusal:
            return refusal
        if request.kind != TrustRequestKind.PREAUTHORIZED_RUN_ASSUMPTION:
            return "Only caller-preauthorized run declarations use this structural admission route."
        return declaration_violation(declaration)

    def source_refusal(self, evidence: SourceEvidence | None) -> str | None:
        if evidence is None:
            return "The source is not held; read the paper's own statement before assuming it."
        if not evidence.statement_read:
            return "The named source statement has not been read."
        return None

    def faithfulness(self, *, reached: bool, agreed: bool) -> FaithfulnessDisposition:
        if not reached:
            return FaithfulnessDisposition.UNAVAILABLE
        return FaithfulnessDisposition.ACCEPT if agreed else FaithfulnessDisposition.QUARANTINE

    def refutation(self, verdict: refute.Verdict) -> CheckDecision:
        return CheckDecision(
            refusal=f"Lean proves the negation with `{verdict.tactic}`." if verdict.refuted else None,
            checked=verdict.caveat,
        )

    def check_global(self, name: str, statement: str, operations: ProbeOperations) -> CheckDecision:
        refusal = assumption_shape(name, statement)
        if refusal:
            return CheckDecision(refusal)
        refusal, caveat = assumption_probe(f"axiom {name} : {statement.strip()}", run_source=operations.elaborate)
        if refusal:
            return CheckDecision(refusal)
        if caveat:
            return CheckDecision(checked=caveat)
        warning = vacuity_probe(statement, run_source=operations.elaborate)
        elaborated = "Lean elaborated this statement and could not prove it."
        return CheckDecision(checked=f"{elaborated} {warning}" if warning else elaborated)

    def check_paper(
        self, name: str, qualified: str, statement: str, kind: str, operations: ProbeOperations
    ) -> CheckDecision:
        refusal = assumption_shape(name, statement)
        if refusal:
            return CheckDecision(refusal)
        if kind == "constant":
            return CheckDecision(checked=(
                "An opaque constant is not elaborated as a proposition, so nothing was "
                "proved or refuted about it. It asserts that something with this type "
                "exists, which is trust beyond assuming a statement."
            ))
        refusal, caveat = assumption_probe(f"axiom {qualified} : {statement}", run_source=operations.elaborate)
        if refusal:
            return CheckDecision(refusal)
        verdict = refutation_probe(statement, run_source=operations.refute)
        decision = self.refutation(verdict)
        if decision.refusal:
            return CheckDecision(refute.describe(verdict, statement))
        checked = " ".join((
            caveat or "Lean elaborated this statement and could not prove it.",
            f"The counterexample search was not conclusive: {decision.checked}." if decision.checked
            else "No counterexample was found by the cheap refutation probes.",
        ))
        return CheckDecision(checked=checked)

    def paper_statement(
        self, record: Any, reading: paper_statements.Survey | None, name: str
    ) -> tuple[paper_statements.Statement | None, SourceEvidence | None, str | None]:
        if record is None or reading is None:
            return None, None, self.source_refusal(None)
        wanted = paper_statements.find(reading.statements, name)
        if wanted is not None:
            # This identity is explicitly the inventoried excerpt, not a claim
            # that its digest authenticates the entire downloaded source archive.
            evidence = SourceEvidence(
                artifact=ArtifactRef(
                    uri=f"arxiv:{record.arxiv_id}/statement-excerpt",
                    digest=sha256(wanted.text.encode("utf-8")).hexdigest(),
                    locator=f"{wanted.file}#{wanted.ref}",
                ),
                statement_read=True,
            )
            return wanted, evidence, self.source_refusal(evidence)
        cut = (f" The reading stopped at the first {paper_statements.MAX_STATEMENTS} statements, "
               "so this may be one it did not reach.") if reading.truncated else ""
        return None, None, (f"{record.arxiv_id} makes no statement called {name!r}.{cut} "
                      f"list_statements names them: {[item.ref for item in reading.statements][:20]}")

    def paper_name_refusal(self, short: str, kind: str) -> str | None:
        if kind not in ("statement", "constant"):
            return f"kind must be 'statement' or 'constant', not {kind!r}"
        if "." in short or not re.fullmatch(ANY_NAME, short):
            return (f"formal_name must be a single Lean identifier, not {short!r}: the "
                    "namespace is the paper's cite key and Hardy writes it. Pass the axiom's "
                    "own name with no dots in it.")
        return None


# Tried in order, and the order is part of the message: `trivial` closing a
# statement is damning, while `exact?` closing it says the result was in
# Mathlib all along.
#
# What this catches is what standard automation closes, which is not the
# same as every logically weak statement, and the difference is worth
# stating. The graded appendix offered `exists a b : G, a * b = b * a` as the
# meaning of "abelian". It is true in every group -- `exact <1, 1, rfl>`
# closes it -- and *none* of these tactics find that witness, `exact?`
# included; only writing the term does. So this gate is a filter, not a
# decision procedure. It did catch `exists P : Sylow p G, True` on a live
# run, which is the same species of vacuity stated a little more carelessly.
PROBES = ("trivial", "simp", "tauto", "aesop", "exact?")

# Tried on the stripped statement when its conclusion is an existential.
# `exact?` and `aesop` do not synthesise a witness, and the bad axiom the
# failing run approved -- `∃ P : Subgroup G, P.Normal` -- is closed by the
# first of these.
WITNESSES = (
    "exact ⟨⊥, inferInstance⟩",
    "exact ⟨⊤, inferInstance⟩",
    "exact ⟨⊥, by simp⟩",
    "exact ⟨⊤, by simp⟩",
    "exact ⟨1, by simp⟩",
)


# Shown in `checked` when `_strip_hypotheses` refuses a statement that had
# hypotheses to strip. Distinct wording from every vacuity warning, so a
# reader -- and a test -- cannot mistake "the question was never asked" for
# "the question was asked and came back concerning".
VACUITY_STRIP_REFUSED = (
    "Hypothesis stripping was not attempted: the statement's binders could "
    "not be read, so the vacuity question was not asked."
)


def _probe_suggestion(result: Any, line: int) -> str:
    """What `exact?` offered on `line`, if anything.

    `exact?` reports its term as an informational diagnostic. Every other probe
    reports nothing at all when it succeeds, so the caller falls back to naming
    the tactic. The literal `Try this:` prefix is Lean's and is not pinned by
    any fixture here -- treated as a bonus, and nothing depends on it.
    """
    for diagnostic in getattr(result, "diagnostics", ()):
        if diagnostic.line == line and "Try this:" in diagnostic.message:
            return diagnostic.message.split("Try this:", 1)[1].strip()
    return ""


_BINDER = re.compile(r"\{[^{}]*\}|\[[^\[\]]*\]|\((?:[^()]|\([^()]*\))*\)")
_DATA_TYPE = re.compile(r"^(?:Type|Sort|Prop)\b|^(?:ℕ|ℤ|ℚ|ℝ|ℂ|Nat|Int|Rat|Real|Complex|Bool|String)$")


def _split_top(text: str, separator: str) -> list[str]:
    """`text` split on `separator` outside every bracket."""
    return _split_top_before(text, separator, len(text))


def _mentions(name: str, text: str) -> bool:
    """Whether `name` occurs as a whole word in `text`.

    Lean's dot notation glues a name to what follows with `.` (`H.index`), so
    a boundary of `.` or `'` must not disqualify a match the way an ordinary
    word character would.
    """
    return re.search(rf"(?<![\w.']){re.escape(name)}(?![\w'])", text) is not None


_TOP_LEVEL_QUANTIFIER_SYMBOLS = "∀∃Σλ"


def _first_top_level_quantifier(text: str) -> int:
    """Index in `text` of the first `∀`/`∃`/`∃!`/`Σ`/`λ`/`fun` outside every
    bracket, or `len(text)` if none occurs.

    Marks where an arrow premise chain has to stop. `∃ f : α → Prop, …`
    holds an arrow that belongs to the bound variable's own type, not a
    premise separator -- splitting on it is the bug behind finding #3's
    first and fourth rows. Nothing at or past the first top-level quantifier
    is a candidate premise boundary, whatever punctuation it contains.
    """
    depth = 0
    index = 0
    while index < len(text):
        character = text[index]
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
        elif depth == 0:
            if character in _TOP_LEVEL_QUANTIFIER_SYMBOLS:
                return index
            before_ok = index == 0 or not (text[index - 1].isalnum() or text[index - 1] == "_")
            after = index + 3
            after_ok = after >= len(text) or not (text[after].isalnum() or text[after] == "_")
            if before_ok and after_ok and text.startswith("fun", index):
                return index
        index += 1
    return len(text)


def _split_top_before(text: str, separator: str, limit: int) -> list[str]:
    """`text` split on `separator` outside every bracket, using only splits
    that start strictly before `limit`.

    Everything from `limit` on -- see `_first_top_level_quantifier` -- lands
    unsplit in the final part, even if it contains `separator` itself.
    """
    parts, depth, start = [], 0, 0
    index = 0
    while index < limit:
        character = text[index]
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
        elif depth == 0 and text.startswith(separator, index):
            parts.append(text[start:index])
            start = index + len(separator)
            index = start
            continue
        index += 1
    parts.append(text[start:])
    return parts


def _first_top_level(text: str, separator: str, limit: int) -> int:
    """Index of the first top-level `separator` in `text[:limit]`, or -1.

    Same bracket-depth tracking as `_split_top`/`_split_top_before`, kept as
    its own function because `_strip_hypotheses` needs the *position* of an
    arrow relative to an equivalence, not a split on it.
    """
    depth = 0
    index = 0
    while index < limit:
        character = text[index]
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
        elif depth == 0 and text.startswith(separator, index):
            return index
        index += 1
    return -1


# Returned by `_strip_hypotheses` when one of its fail-closed checks fires,
# so a caller can tell "Hardy could not read this safely" apart from `None`
# ("there was nothing here to strip"). The two used to be one value, and a
# bare `∀ n : ℕ, …` -- which has no hypotheses at all -- was reported to the
# human as unreadable exactly like a binder whose type really was lost.
UNREADABLE = object()


def _split_binder_colon(inner: str) -> tuple[str, str] | None:
    """A binder's inner text split on the first `:` that introduces its
    type, or None if it has none.

    Skips `:=` (a default value) and `::` (list cons, or a namespace
    separator) and any colon inside a further bracket, so `(hp:Nat.Prime 2)`
    -- no space around the colon -- is read as a hypothesis rather than,
    under a literal `" : "` search, as an untyped binder that is always
    kept.
    """
    depth = 0
    index = 0
    while index < len(inner):
        character = inner[index]
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
        elif character == ":" and depth == 0:
            before = inner[index - 1] if index > 0 else ""
            after = inner[index + 1] if index + 1 < len(inner) else ""
            if after == ":":
                index += 2
                continue
            if before == ":" or after == "=":
                index += 1
                continue
            return inner[:index].strip(), inner[index + 1:].strip()
        index += 1
    return None


def _strip_hypotheses(statement: str) -> Any:
    """`statement` with its hypotheses removed; `None` if it has none; or
    `UNREADABLE` if reading its binders or its premise chain could not be
    trusted.

    What the vacuity probe elaborates, so a wrong answer here is not a
    missing warning but a false one shown to a human relying on it -- this
    fails closed rather than guess. Whitespace is collapsed with
    `normalise_lean` rather than a bare `split`/`join`, because the latter
    collapses whitespace inside string literals and `«…»` names too, turning
    `"a  b"` into `"a b"` -- a different Lean string reported as if it were
    the one the statement actually has. Returns `UNREADABLE` outright when a
    `"` or `«` survives that collapse, because every split below (on `, `,
    ` → `, ` ↔ `) has no notion of a literal and cannot tell a separator
    sitting inside one from one that actually separates binders or premises.
    Also returns `UNREADABLE` when a
    strict-implicit binder (`⦃…⦄`) appears anywhere, since `_BINDER` has no
    alternative for that bracket and would silently drop it and its name;
    when a top-level `↔` precedes the first top-level arrow in the body,
    since `↔` binds looser than `→` and that arrow is not a premise
    separator at all but sits inside the equivalence's own right-hand side
    (`A ↔ B → C` is `A ↔ (B → C)`) -- splitting on it anyway reports a
    hypothesis the statement never had; and when `binders` holds text
    `_BINDER` did not consume *and* the binder list was written with at
    least one wrapping bracket (`(…)`/`{…}`/`[…]`), because a nested paren
    one level deeper than `_BINDER` handles is losing real hypothesis text.
    A binder list with no wrapping bracket at all (`∀ n : ℕ, …`, `∀ x ∈ s,
    …`) has never been something this function could parse, and if there is
    also no top-level premise arrow behind it, nothing was going to be
    stripped even had it parsed -- that case returns `None`, not
    `UNREADABLE`, so an entirely ordinary quantifier is not reported to the
    human as unreadable. A binder is a hypothesis unless it is an instance,
    its type is a universe or a known data type, its type is exactly a name
    bound earlier in the same statement, or it is depended on -- named in
    the conclusion, or in the type of another binder that is kept. An arrow
    premise before the statement's first top-level quantifier is always a
    hypothesis; an arrow at or after that quantifier is left untouched,
    because it sits inside the quantifier's own binder type rather than
    separating premises. Returns `None` when there is nothing to strip -- no
    leading `∀`/`forall`, or one with nothing after a top-level comma -- so
    the caller probes the statement whole, exactly as it was given.
    """
    text = normalise_lean(statement).strip()
    if "⦃" in text or "⦄" in text:
        return UNREADABLE
    if '"' in text or "«" in text:
        # `normalise_lean` collapses whitespace literal-safely, but the
        # splits below (`_split_top` on `, `, `_first_top_level` on ` → `
        # and ` ↔ `) have no notion of a literal at all -- a separator
        # sitting inside a string or a guillemet-quoted name looks exactly
        # like one that actually separates binders or premises. Rather than
        # split blind and risk reporting a hypothesis the statement never
        # had (or hiding one it did), this fails closed.
        return UNREADABLE
    binders, body = "", text
    for keyword in ("∀ ", "forall "):
        if text.startswith(keyword):
            head = text[len(keyword):]
            parts = _split_top(head, ", ")
            if len(parts) < 2:
                return None
            binders, body = parts[0], ", ".join(parts[1:])
            break
    quantifier_index = _first_top_level_quantifier(body)
    arrow_index = _first_top_level(body, " → ", quantifier_index)
    iff_index = _first_top_level(body, " ↔ ", quantifier_index)
    # `↔` only corrupts the split when it precedes a top-level arrow; with no
    # such arrow there is no premise chain for it to mis-split, so an
    # arrow-free equivalence -- however many binders it carries, including
    # none -- is safe to strip and probe like any other statement.
    if arrow_index != -1 and iff_index != -1 and iff_index < arrow_index:
        return UNREADABLE
    premises = _split_top_before(body, " → ", quantifier_index)
    conclusion = premises[-1].strip()
    consumed = "".join(_BINDER.findall(binders))
    if "".join(consumed.split()) != "".join(binders.split()):
        bracket_led = bool(binders) and binders.lstrip()[:1] in "({[⦃"
        if not bracket_led and len(premises) == 1:
            return None
        return UNREADABLE
    if not binders and len(premises) == 1:
        return None

    # Each binder as [its group text, its names, its type text, whether kept].
    parsed: list[list] = []
    bound = set()
    for group in _BINDER.findall(binders):
        inner = group[1:-1]
        split = _split_binder_colon(inner)
        names, typ = (inner, "") if split is None else split
        keep = group[0] == "[" or split is None or bool(_DATA_TYPE.match(typ)) or typ in bound
        parsed.append([group, names.split(), typ, keep])
        if keep:
            bound.update(names.split())

    # A binder none of the rules above keep is still data if something kept
    # depends on it -- the conclusion, or the type of another kept binder.
    # Loop to a fixed point: keeping one binder is sometimes what makes an
    # earlier one, referenced only from that one's type, worth keeping too.
    changed = True
    while changed:
        changed = False
        kept_text = conclusion + " " + " ".join(entry[2] for entry in parsed if entry[3])
        for entry in parsed:
            if not entry[3] and any(_mentions(name, kept_text) for name in entry[1]):
                entry[3] = True
                changed = True

    kept = [entry[0] for entry in parsed if entry[3]]
    if kept:
        return f"∀ {' '.join(kept)}, {conclusion}"
    return conclusion


def _vacuity_source(stripped: str, *, include_probes: bool = True) -> tuple[str, list[str]]:
    """Build the vacuity probe's Lean source and tactics for this stripped statement.

    The integration test elaborates the same file, so there is one place the layout lives.

    `include_probes` is False when `stripped` strips nothing off the original
    statement: `_assumption_probe` has just run `PROBES` against that exact
    text and failed, so running them again here would ask Lean the same
    question twice and, if it happened to close, describe an unstripped
    statement as proved "with every hypothesis removed".
    """
    # Start with PROBES, add WITNESSES only if the conclusion is a plain ∃.
    tactics = list(PROBES) if include_probes else []

    # Extract the conclusion (after leading binders' top-level comma).
    if stripped.startswith("∀ "):
        conclusion = ", ".join(_split_top(stripped[2:], ", ")[1:])
    else:
        conclusion = stripped

    # `∃!` is unique existence: a bare witness (`⊥`, `⊤`) proves something
    # exists, never that it is the only one, so trying `WITNESSES` against a
    # `∃!` conclusion can only ever fail and is not worth the elaboration.
    stripped_conclusion = conclusion.lstrip()
    if stripped_conclusion.startswith("∃") and not stripped_conclusion.startswith("∃!"):
        tactics.extend(WITNESSES)

    # Build the Lean source.
    examples = "\n".join(f"example : {stripped} := by {tactic}" for tactic in tactics)
    source = f"import Mathlib\n\n{examples}\n"

    return source, tactics


def assumption_shape( formal_name: str, lean_statement: str) -> str | None:
    """Why this could never be declared, or None.

    `request_assumption` used to accept anything and wrap it in
    `axiom NAME : ...`, so it could approve text `save_lean` would refuse
    forever. That is not hypothetical: a session was told to declare
    `axiom cyclic_of_prime_order : axiom cyclic_of_prime_order (G : Type*)
    ... : ...` -- a double header nothing can parse -- and spent ten turns
    discovering there was no spelling that satisfied both ends. Matching the
    approval required binders the parser refuses; satisfying the parser
    produced a statement that no longer matched the approval.

    So both ends now ask the same code about the same string. `COMMAND` is
    what recognises a line opening a declaration, and an axiom's statement
    is a type, never a command. `unreadable_assumptions` is what `save_lean`
    itself calls.

    A statement is also one line. `True\\naxiom extra : False` is two
    declarations and `ASSUMPTION` reads both happily, so without this the
    request round-trips and an approval granted for the first carries the
    second. Approved statements are stored whitespace-collapsed anyway, so
    refusing a newline costs nothing a caller needed.

    Not sufficient, and not meant to be. A binder-only statement --
    `(G : Type*) : True` -- matches neither check, because
    `axiom f : (G : Type*) : True` parses by taking everything after the
    first colon. It is not valid Lean and only elaboration can say so, which
    is what `_assumption_probe` is for. `opaque`, and any declaration
    keyword `COMMAND` does not list, land there too.
    """
    statement = lean_statement.strip()
    if "\n" in statement or "\r" in statement:
        return (
            "a statement is one line and one type. More than one line can carry a "
            "second declaration, which an approval of the first would not cover. "
            "Collapse it to one line."
        )
    if COMMAND.match(statement):
        return (
            f"a statement may not itself be a declaration, and `{statement[:60]}` "
            f"opens one. Pass only the statement -- the type after the colon -- and "
            f"Hardy writes `axiom {formal_name} :` in front of it. Binders belong "
            f"inside the statement as `forall`, not before the colon."
        )
    declaration = f"axiom {formal_name} : {statement}"
    if unreadable_assumptions(declaration):
        return (
            f"`{declaration[:80]}` cannot be read as `axiom NAME : STATEMENT`, so "
            f"save_lean could never accept it. An assumption carries no binders and "
            f"no universe parameters."
        )
    return None


def assumption_probe(declaration: str, *, run_source: Callable[[str], Any]) -> tuple[str | None, str]:
    r"""Ask Lean about a proposed axiom before any human is asked.

    Two questions in one elaboration, because Lean reports diagnostics per
    declaration and a second process buys nothing: does this elaborate at
    all, and can any of `PROBES` close it.

    A statement Lean proves is not an assumption -- it is a theorem nobody
    has saved yet. See `PROBES` for what that does and does not reach: it is
    a filter over what standard automation closes, not a decision procedure,
    and the graded appendix's own error is outside it.

    `import Mathlib` rather than the workspace's own imports. An assumption
    may mention anything, and a narrower import set turns "that name does
    not exist" into "I did not import that name", which is a different
    sentence and a misleading one.

    Returns a refusal or None, and a caveat that is empty unless the probe
    could not be run. A machine whose Lean will not start must not be one
    where every axiom is approved unchecked, nor one where none can be: the
    caveat carries the uncertainty to the human, who is the one deciding.
    """
    head, _, tail = declaration.partition(":")
    # Collapsed to one line before anything else. Which tactic closed the
    # goal is read from `LeanDiagnostic.line`, and Hardy keeps only a
    # diagnostic's start line -- Lean's `endPos` is discarded -- so a
    # two-line statement would attribute an error to the wrong tactic and
    # could report a probe as succeeding when it failed.
    statement = normalise_lean(tail).strip()
    examples = "\n".join(f"example : {statement} := by {tactic}" for tactic in PROBES)
    # The probes come FIRST and the axiom LAST, which is the whole design of
    # this file rather than a formatting choice. With the axiom declared
    # above them it is in scope, and `exact?` closes every statement by
    # citing it:
    #
    #     theorem sylow_first : ... := exact fun {G} ... => sylow_first p a
    #
    # A live run refused seven honest requests that way, Sylow's theorems
    # among them, each "proved" from itself. Lean resolves names in order, so
    # putting the axiom after the probes is what makes the question real.
    #
    # Exactly this layout, and `test_assumption_gates` asserts it: the import
    # on line 1, blank, one example per line from line 3, blank, then the
    # declaration last. The arithmetic below is that layout.
    source = f"import Mathlib\n\n{examples}\n\n{head.strip()} : {statement}\n"
    try:
        # The adapter selects the budget: a cold Mathlib import can take
        # minutes, and default turn timeouts would make the first request
        # routinely degrade to "could not be checked".
        result = run_source(source)
    except Exception as error:  # noqa: BLE001 - an unrunnable probe is a caveat, never a crash
        return None, f"Lean could not be checked ({error})."
    if getattr(result, "timed_out", False) or getattr(result, "interrupted", False):
        return None, "Lean could not be checked (the elaboration did not finish)."
    errors = [item for item in result.diagnostics if item.severity == "error"]
    if not result.ok and not errors:
        # Lean failed and said nothing this can read. Every conclusion below
        # is drawn from *which line* an error landed on, so with no errors
        # to place, "no error on line 5" would read as "`trivial` closed the
        # goal" -- turning an unusable answer into a confident refusal.
        return None, "Lean could not be checked (it failed without diagnostics Hardy could read)."
    placed = {item.line for item in errors if item.line is not None}
    # An error Lean could not place counts against the declaration and never
    # in a probe's favour: "no error on that line" must mean the tactic
    # closed the goal, not that Hardy could not tell where the error was.
    unplaced = any(item.line is None for item in errors)
    first_probe = 3
    declaration_line = first_probe + len(PROBES) + 1
    # An error before the probes even started -- `import Mathlib` failing
    # on line 1, or landing on line 2's blank line -- or after the
    # declaration is not about any probe tactic closing the goal. Reading
    # the *absence* of an error on a probe's own line as "that tactic
    # closed the goal" is only sound once Lean actually reached the
    # probes; an error here means it did not, and every probe line then
    # looks clean for the same reason a killed process would.
    stray = any(
        item.line is not None and (item.line < first_probe or item.line > declaration_line)
        for item in errors
    )
    # The declaration is read first, and an error Lean could not place is
    # read against it. A statement Lean will not accept fails on every probe
    # line too, and "every tactic failed" would otherwise be reported back as
    # a clean assumption.
    if unplaced or stray or declaration_line in placed:
        return (
            f"Lean does not accept this statement, so nothing can be built on it:\n"
            f"{result.output}\n"
            f"Fix the statement and request it again.",
            "",
        )
    for index, tactic in enumerate(PROBES):
        if first_probe + index in placed:
            continue
        proof = _probe_suggestion(result, first_probe + index) or f"by {tactic}"
        return (
            f"Lean proves this outright, so it is a theorem, not an assumption:\n"
            f"  theorem {head.strip().removeprefix('axiom').strip()} : "
            f"{statement} := {proof}\n"
            f"Save it with save_lean instead of assuming it.",
            "",
        )
    return None, ""


def vacuity_probe(statement: str, *, run_source: Callable[[str], Any]) -> str:
    """Whether the conclusion holds with the hypotheses gone. A warning or "".

    Run only after `_assumption_probe` returned no refusal, as its own
    elaboration: nothing here needs the axiom in scope, and the first
    file's layout is pinned by its tests. A statement `_strip_hypotheses`
    reports `UNREADABLE` is not probed, and says so -- that sentinel
    means there was something to say no to, unlike `None`, which means
    the statement never had hypotheses in the first place (a bare
    statement such as `True`, or an ordinary quantifier `_strip_hypotheses`
    was never going to strip anything from) and stays silent exactly as
    it always has. When every binder turns out to be data -- nothing was
    actually stripped -- `PROBES` is left out of the file
    `_vacuity_source` builds: `_assumption_probe` already ran them
    against this exact text and failed, so running them again would only
    risk describing that same, unstripped statement as proved "with every
    hypothesis removed".

    Reads each `example` line's diagnostic by its line number alone, which
    assumes Lean reached every `example` in the file: a parse-level error
    that aborts elaboration before the first one would leave every later
    line with no diagnostic of its own, and this would read that silence
    as every tactic having closed its goal. The same exposure
    `_assumption_probe` carries, for the same reason.
    """
    normalised = normalise_lean(statement).strip()
    stripped = _strip_hypotheses(normalised)
    if stripped is UNREADABLE:
        return VACUITY_STRIP_REFUSED
    if stripped is None:
        return ""
    hypotheses_removed = stripped != normalised
    source, tactics = _vacuity_source(stripped, include_probes=hypotheses_removed)
    if not tactics:
        # Nothing was stripped and the conclusion is not existential: there
        # is nothing left worth asking Lean that `_assumption_probe` has
        # not already asked.
        return ""
    try:
        result = run_source(source)
    except Exception as error:  # noqa: BLE001 - a warning that cannot be computed is itself reported
        return f"The vacuity probe could not be run ({error})."
    if getattr(result, "timed_out", False) or getattr(result, "interrupted", False):
        return "The vacuity probe could not be run (the elaboration did not finish)."
    errors = [item for item in result.diagnostics if item.severity == "error"]
    if not result.ok and not errors:
        return "The vacuity probe could not be run (Lean failed without diagnostics)."
    # An error outside the `example` lines -- unplaced, or on `import
    # Mathlib`'s line 1 before the probes even ran -- is not a probe
    # having closed the goal. Reading the *absence* of an error on a
    # probe's own line as success is only sound once Lean actually
    # reached the probes; this is the same exposure `_assumption_probe`
    # carries, and a warning built on it would misreport a Lean failure
    # as the assumption being vacuous.
    tactic_lines = range(3, 3 + len(tactics))
    if any(item.line is None or item.line not in tactic_lines for item in errors):
        return "The vacuity probe could not be run (Lean failed before reaching the probes)."
    placed = {item.line for item in errors}
    for index, tactic in enumerate(tactics):
        line = 3 + index
        if line in placed:
            continue
        proof = _probe_suggestion(result, line) or f"by {tactic}"
        if hypotheses_removed:
            return (
                "Lean elaborated this statement and could not prove it as stated — but "
                f"proves it with every hypothesis removed (`{proof}`): the conclusion "
                f"`{stripped}` holds without the hypotheses. This assumption may be vacuous."
            )
        return (
            "Lean elaborated this statement and could not prove it with standard "
            f"automation — but a direct witness closes it (`{proof}`). It is a "
            "theorem, not an assumption."
        )
    return ""


def refutation_probe(statement: str, *, run_source: Callable[[str], Any], imported: bool = True) -> refute.Verdict:
    """Ask Lean whether the negation is provable, and never crash doing it.

    A machine whose Lean cannot start must not be one where every axiom is
    admitted unchecked, nor one where none can be: the caveat carries the
    uncertainty to the human, who is the one deciding.
    """
    try:
        # Built inside the guard, as `workflow._refute` builds it: the
        # shape gate here rejects `\n` and `\r`, and `probe_source`
        # rejects six line terminators, so a separator surviving
        # `normalise_lean` inside a string literal raised out of the tool
        # -- no `assumption_prompt` recorded, and the search evidence
        # spent by the caller's `finally`. A probe that will not run is a
        # caveat however early it declines.
        source, tactics = refute.probe_source(statement, imported=imported)
        result = run_source(source)
    except Exception as error:  # noqa: BLE001 - an unrunnable probe is a caveat, never a crash
        return refute.Verdict(False, caveat=f"the refutation probe could not run ({error})")
    return refute.judge(result, tactics)
