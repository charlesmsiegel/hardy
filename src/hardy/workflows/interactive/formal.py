"""Atomic checked saves and audit decisions for an interactive Lean tree.

A save builds and audits a shadow before committing, then publishes evidence.
Cross-capability authorship and documentation gates are explicit callbacks;
they cannot expose mutable session state to the formal workspace owner.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hardy import audit
from hardy.foundation.values import ToolResult
from hardy.lean import LeanTools
from hardy.workspace import (
    IDENTIFIER,
    QUALIFIED_NAME,
    BuildFailure,
    ImportCycle,
    LeanWorkspace,
    WorkspacePathError,
    assumptions,
    declarations,
    dependents,
    internal_imports,
    module_name,
    safe_relative,
    unreadable_assumptions,
)

# The head of a saved theorem's statement as `statements` reports it: the
# keyword, then the declared name, then the signature an anonymous `example`
# can carry verbatim. The name is `QUALIFIED_NAME` -- the same alphabet the
# declaration scan reads, where any component may be a `«...»` quotation
# carrying whitespace -- because a whitespace split read `«obvious` as the
# name of `theorem «obvious result» : True`, and a guillemet-only alternative
# still misread the qualified `theorem Foo.«obvious result» : True`. An
# explicit universe binder (`theorem vacuous.{u} ...`) is captured apart from
# both: it belongs to neither the name nor the signature -- `example` cannot
# carry one, so the probe redeclares its names with a `universe` command.
THEOREM_HEAD = re.compile(rf"^theorem\s+({QUALIFIED_NAME})(\.\{{[^}}]*\}})?\s*(.*)$")
# What may name a universe in that binder: `IDENTIFIER`, exactly. A binder
# this cannot read would put an unparseable `universe` command on the probe
# file's own lines, and a parse error there takes every verdict with it.
UNIVERSE_NAME = re.compile(rf"^{IDENTIFIER}$")

@dataclass(frozen=True)
class SavePolicy:
    generated_refusal: Callable[[Any], str | None]
    result_gate: Callable[[str], str | None]
    documentation_gate: Callable[[str], str | None]
    final_gates: Callable[[str], ToolResult | None]
    compile_path: Callable[[Path], str]
    build_shared: Callable[[], None]
    missing_names: Callable[[dict[str, str], dict[str, str]], list[str]]
    audit_tree: Callable[[LeanWorkspace, Sequence[str]], Any]
    closes_and_adds: Callable[[str, Sequence[str], dict[str, Any]], str | None]
    publish_audit: Callable[[dict[str, Any], dict[str, str]], None]
    refresh_automation: Callable[[], str]
    persist: Callable[[], None]
    owed_note: Callable[[], str]

class FormalWorkspaceService:
    SAVE_STREAK_LIMIT = 3

    def __init__(self, lean: LeanTools, workspace: LeanWorkspace):
        self.lean = lean
        self.lean_workspace = workspace
        self._save_streak: dict[str, int] = {}
        self._checked_green: dict[str, set[str]] = {}

    def begin_turn(self) -> None:
        self._save_streak.clear()
        self._checked_green.clear()

    def checked(self, path: str, source: str) -> None:
        self._checked_green.setdefault(self._streak_key(path), set()).add(self._save_digest(source))

    def _streak_key(self, path: str) -> str:
        """The `_save_streak` key `path` counts against.

        Its safe-relative form, so `Main.lean` and `./Main.lean` share one
        streak instead of two half-sized ones nothing ever brakes. Falls back
        to `path` itself when `safe_relative` refuses it: the unbraked save
        refuses the same path for the same reason, so a streak keyed on a
        spelling Hardy will never accept costs nothing.
        """
        try:
            return str(safe_relative(path))
        except WorkspacePathError:
            return path


    @staticmethod
    def _save_digest(source: str) -> str:
        """The identity a green `check_lean` vouches for and a save spends.

        Hashed over `source.rstrip() + "\\n"` -- exactly what
        `_save_lean_unbraked` writes to disk -- rather than over `source`
        verbatim, so `check_lean(X)` vouches for `save_lean(X)` *and* for
        `save_lean(X + "\\n")`: the two calls write identical bytes to the
        workspace, and finding #4 of the second brutal review was this
        digest treating them as different sources and braking the second.
        """
        return hashlib.sha256((source.rstrip() + "\n").encode("utf-8")).hexdigest()


    def _streak_refusal(self, path: str, source: str) -> ToolResult | None:
        key = self._streak_key(path)
        if self._save_streak.get(key, 0) < self.SAVE_STREAK_LIMIT:
            return None
        # The brake promises "until `check_lean` passes on the exact source
        # you intend to save" -- so it is lifted only by a green check of
        # this exact source, not by any `check_lean` call that happens to
        # land on the same path. A save of something else has not been shown
        # to fix anything.
        digest = self._save_digest(source)
        green = self._checked_green.get(key)
        if green is not None and digest in green:
            # Spend the vouch: one green check admits one save, not every
            # save of that source for the rest of the turn. Finding #3 of
            # the second brutal review left this exemption permanent, so a
            # single `check_lean` on a byte string that then failed
            # `save_lean`'s stricter gates (result/documentation/shadow
            # build) bought an unbounded run of refused saves the brake
            # never fired on again.
            green.discard(digest)
            return None
        return ToolResult(
            False,
            f"{self.SAVE_STREAK_LIMIT} consecutive saves of `{path}` have been refused. "
            "Hardy will not elaborate another until `check_lean` passes on the exact "
            "source you intend to save on this path. Check a smaller piece — split "
            "the file, or reduce it to what already compiles — then save that "
            "checked source.",
        )


    def _save_lean(self, path: str, source: str, save: Callable[[str, str], ToolResult]) -> ToolResult:
        refusal = self._streak_refusal(path, source)
        if refusal is not None:
            return refusal
        result = save(path, source)
        key = self._streak_key(path)
        if result.ok:
            self._save_streak.pop(key, None)
        else:
            self._save_streak[key] = self._save_streak.get(key, 0) + 1
        return result


    def _save_lean_unbraked(
        self, path: str, source: str, *, policy: SavePolicy, ratchet: bool = True, generated: bool = False
    ) -> ToolResult:
        try:
            relative = safe_relative(path)
        except WorkspacePathError as error:
            return ToolResult(False, str(error), source)
        # `ratchet=False` is how an imported file enters (#112). The two gates
        # it skips are authorship steering -- `theorem` reserved to registered
        # results, the writeup catch-up -- rules about how a model writes new
        # work, which an imported file has already been written without. The
        # verification gates all still run: assumption approval, the shadow
        # build, registered-name preservation, and the axiom audit are what
        # "no weaker a check than one Hardy wrote" means, and the writeup debt
        # an imported theorem brings is not waived either -- it lands in the
        # obligations like any other saved theorem's.
        # `Papers/` is Hardy's own writing, minted from an approved paper
        # statement and regenerated whole. A model editing it by hand could
        # put an axiom nobody approved under a name the audit already trusts,
        # which is the one thing the approval flow exists to prevent -- so the
        # tree is refused to everything but `assume_statement`.
        if not generated:
            owned = policy.generated_refusal(relative)
            if owned is not None:
                return ToolResult(False, owned, source)
        gate = (policy.result_gate(source) or policy.documentation_gate(source)) if ratchet else None
        if gate is not None:
            return ToolResult(False, gate, source)
        refusal = policy.final_gates(source)
        if refusal is not None:
            return refusal
        text = source.rstrip() + "\n"
        # The shadow build elaborates this file itself, so there is no
        # pre-check run: with Mathlib imported each elaboration costs tens of
        # seconds, and checking the same source twice per save doubled the
        # expensive half of the operation. What Lean said is captured here
        # because the build reports only which module failed.
        seen: dict[str, ToolResult] = {}

        def capturing(module: str, source_root: Path, build_root: Path, source_file: Path) -> tuple[bool, str]:
            result = self.lean.compile_module(
                source_root, build_root, source_file, lean_path=policy.compile_path(build_root)
            )
            seen[module] = result
            return result.ok, result.output

        # Before the shadow is staged, so the staged build is keyed on the
        # identity the shared sources currently have. Staging first would copy
        # `_environment` into the shadow while it still named the old shared
        # text, and the save would be committed under a signature that was
        # already false when it was computed.
        policy.build_shared()
        # Before staging: what the tree holds now is what a registered name may
        # be judged to have vanished *from*.
        committed = self.lean_workspace.sources()
        shadow, commit = self.lean_workspace.stage(relative, text, capturing)
        try:
            module = module_name(relative)
            try:
                affected = [module, *sorted(dependents(shadow.sources(), module))]
                failure = shadow.build_modules(affected)
            except ImportCycle as error:
                return ToolResult(False, f"{error}; nothing was written", source)
            if failure is not None:
                return ToolResult(False, f"this save breaks {failure.module}, so nothing was written:\n{failure.output}", source)
            # The registry and the Lean must stay in step. This was a per-file
            # check when the workspace was one file; a registered name now has
            # to survive somewhere in the tree, not in whichever file is being
            # saved -- but it must not be allowed to vanish from all of them.
            lost = policy.missing_names(shadow.sources(), committed)
            if lost:
                return ToolResult(False, f"this save would drop registered names from the workspace: {lost}", source)
            # Last, because it is the only gate that costs another Lean run,
            # and still before `commit`: a refused audit must leave the
            # workspace exactly as it was.
            audited = policy.audit_tree(shadow, affected)
            if isinstance(audited, ToolResult):
                return audited
            records, note = audited
            stale = policy.closes_and_adds(source, affected, records)
            if stale is not None:
                return ToolResult(False, stale, source)
            commit()
        finally:
            LeanWorkspace.discard(shadow)
        # Published after the write, and not before: a verdict stored first
        # would survive a failed commit and describe a tree that never existed.
        # Stamped with what the module's build inputs hashed to, not merely with
        # the toolchain: the same signature the build cache is keyed on, which
        # already folds in the environment, the module's source, everything it
        # imports inside the workspace, and the olean behind every import
        # outside it. A verdict is an answer about those inputs and expires with
        # them.
        signatures = self.lean_workspace.current_signatures()
        policy.publish_audit(records, signatures)
        # After the commit -- the answer is a disclosure about a saved theorem,
        # never a gate on saving one -- and before `_save_state`, so the
        # verdicts persist in the same write the audit records do.
        automation = policy.refresh_automation()
        policy.persist()
        # Absent from `seen` when the source was byte-identical to what was
        # already built, so the cache skipped it. Nothing was wrong with it.
        result = seen.get(module, ToolResult(True, "unchanged; already built", source))
        return ToolResult(
            result.ok,
            f"{result.output}\n\naxiom audit: {note}{automation}{policy.owed_note()}",
            result.source,
        )


    def _final_gates(self, source: str, state: Mapping[str, Any]) -> ToolResult | None:
        """What disqualifies a source from being saved, before Lean is asked.

        All of it is textual, so it costs nothing and runs first: there is no
        point spending a minute elaborating a file that an unapproved axiom
        already rules out.

        A hole is not here. `sorry` is how a proof of any size gets built, and
        refusing it meant the unfinished part of a development could never
        reach disk -- so a thousand-line proof lived in the model's context and
        was re-sent in full on every check. What a hole costs is charged where
        a claim is made instead: the audit records it, the obligations name it,
        and `report_result` grades it partial.
        """
        found = declarations(source)
        # The audit asks `#print axioms` about theorems and lemmas, and about
        # nothing else -- so those are the only declarations a hole can be
        # *reported* through. A file that declares neither and carries one
        # would put a hole in the workspace that `/status`, the end-of-turn
        # notice and the banner all stay silent about, which is the one thing
        # keeping holes was not allowed to cost.
        if self.lean.has_holes(source) and not (found["theorem"] or found["lemma"]):
            return ToolResult(
                False,
                "this file has a hole in it and declares no theorem or lemma, so nothing "
                "here can report the hole as open: Hardy tracks one by asking Lean what "
                "each saved theorem and lemma rests on. State the work as a `lemma` -- a "
                "lemma may carry a hole and is free to save -- and the hole is then "
                "reported until you close it.",
                source,
            )
        # A `theorem` is what this workspace reports as a result, and a private
        # one can be neither audited nor cited: Lean mangles the name out of
        # reach of any other module, including the file the audit elaborates.
        # Refused rather than skipped, or a documented result would sit behind
        # a gate that never ran on it. `private lemma` stays free.
        hidden = [name for name in found["theorem"] if name in found["private"]]
        if hidden:
            return ToolResult(
                False,
                f"a private theorem cannot be audited or written up, because no other module can "
                f"name it: {hidden}. Drop `private`, or state it as a `private lemma` if it is "
                "scaffolding rather than a result.",
                source,
            )
        # A quarantined name is refused before approval is consulted at all:
        # the reader said this Lean does not say what the paper says, and
        # declaring it under that name anyway would let Hardy derive claims
        # the paper never made under the paper's name.
        #
        # What this does *not* do is close the statement off entirely. The
        # same Lean, re-requested through `request_assumption` under a
        # different name, is an ordinary approval a human may grant -- and
        # should be able to, since the reader's verdict is one reading and
        # the human is the one deciding. The rule is about the paper's name,
        # not about the text. Matched on the
        # qualified name and nothing else, because that is the declaration
        # the reader refused: `assumptions()` qualifies by the namespace in
        # force, so a minted axiom always comes back as `Papers.<key>.<leaf>`,
        # and a top-level `axiom <leaf>` is a different declaration Lean will
        # not confuse with it. Matching a shorter spelling therefore only ever
        # fired on an unrelated name -- refusing another paper's approved
        # `main`, or a bare one `request_assumption` approved, and blaming a
        # reader that never saw it. The approval gate below is what refuses a
        # bare name nobody approved, and it says the true thing about why.
        quarantined = {str(item["formal_name"]) for item in state.get("quarantine", ())}
        for name, _ in assumptions(source):
            if name in quarantined:
                return ToolResult(
                    False,
                    f"`{name}` is quarantined: an independent reader found that this Lean "
                    "does not say what the paper says, so it is recorded and not importable. "
                    "Restate it through assume_statement; it cannot be declared by hand.",
                    source,
                )
        approved = {item["formal_name"]: " ".join(item["lean_statement"].split()) for item in state["assumptions"]}
        # Qualified by the namespace they sit in, so this gate and the audit
        # ask about the same name. A flat scan called it `bar` while Lean
        # reported `Foo.bar`, and no single approval could satisfy both.
        for name, statement in assumptions(source):
            if approved.get(name) != " ".join(statement.split()):
                return ToolResult(False, f"unapproved or altered assumption `{name}`; use request_assumption first", source)
        # An axiom the scan could not read is refused rather than skipped. It
        # cannot be compared against an approval -- the type Lean gives
        # `axiom Sneaky (P : Prop) : P` is `∀ P : Prop, P`, which is not the
        # text after the colon -- and skipping it let one pass unremarked.
        # `request_assumption` produces neither binders nor universe
        # parameters, so this refuses only shapes the approval flow cannot
        # reach.
        unreadable = unreadable_assumptions(source)
        if unreadable:
            return ToolResult(False, f"could not read `{unreadable[0]}` as `axiom NAME : STATEMENT`; an assumption must be approved by request_assumption and then declared in exactly that shape, without binders or universe parameters", source)
        return None


    def _audit_tree(
        self, space: LeanWorkspace, modules: Sequence[str], *, approved: set[str], probe_groups: Callable[..., Any]
    ) -> ToolResult | tuple[dict[str, dict[str, Any]], str]:
        """What the built modules actually rest on: a record each, or a refusal.

        The textual gate in `_final_gates` sees an `axiom` written into the
        source in front of it and nothing else. An axiom reached through an
        import is invisible to it, and that is the case a saved artifact can
        be wrong about while looking right, so Lean is asked directly.

        Asked over the staged tree, before anything is committed, and over
        every module the save rebuilt rather than only the one it edited: a
        dependent inherits whatever the edit brought in, so its own claim
        changed too even though its source did not. Which is also why a module
        outside that set keeps its earlier record rather than being dropped --
        nothing it depends on moved.
        """
        sources = space.sources()
        # `declarations` strips comments and rescans the whole file, so it is
        # asked once per module rather than once per kind.
        found_in = {module: declarations(sources[module]) for module in modules}
        # Private declarations are left out because Lean will not let this probe
        # name one: it elaborates a file that *imports* the module, and a private
        # name is mangled out of reach from there. Asking anyway is an unknown
        # identifier, which would refuse every save of a file using the ordinary
        # `private lemma` idiom. Nothing is lost -- an exported declaration that
        # uses a private helper reports the helper's axioms as its own.
        declared = {
            module: tuple(
                name
                for name in found["theorem"] + found["lemma"]
                if name not in found["private"]
            )
            for module, found in found_in.items()
        }
        names = list(dict.fromkeys(name for module in modules for name in declared[module]))
        empty = {
            module: audit.unestablished(f"no theorem or lemma is declared in {module}")
            for module in modules
            if not declared[module]
        }
        if not names:
            # Nothing here claims to be a result, so there is nothing to grade.
            # Recorded as an audit that did not run rather than as a clean one.
            return empty, f"not established -- no theorem or lemma is declared in {list(modules)}"
        # Modules that never import each other may each declare a root-level
        # `step`, or a `def helper`, or anything else at the same name, and both
        # build. One probe importing all of them brings those together, and Lean
        # will not resolve a name that now means two things -- so a save Lean
        # accepts would be refused.
        #
        # Which names collide is not knowable from the audit targets: the clash
        # can be in a `def`, a `structure`, an `instance`, anything a module
        # exports. Rather than enumerate the kinds and still miss one, the cheap
        # probe is tried first and a failure is retried per module. That is
        # correct for every collision without naming any of them, and costs the
        # extra elaborations only on the trees that need them. Nothing is
        # loosened: each retry still asks about every declaration and still
        # requires a clean report, so a tree that is genuinely broken refuses
        # either way.
        attempts: list[list[list[str]]] = [[list(modules)]]
        if len(modules) > 1:
            attempts.append([[module] for module in modules])
        for index, groups in enumerate(attempts):
            outcome = probe_groups(space, groups, declared)
            if not isinstance(outcome, ToolResult):
                reports, covering = outcome
                break
            if index == len(attempts) - 1:
                return outcome
        else:  # pragma: no cover - `attempts` is never empty
            return ToolResult(False, "the axiom audit had nothing to run")
        verdict = audit.classify(reports, approved)
        # On the status, not on the presence of a finding. A hole grades `open`
        # and is kept: it is an unfinished proof, not an unacceptable one, and
        # the refusal for it happens where a claim is made. An unapproved axiom
        # still rejects, and a save carrying both is refused for the axiom --
        # the half the model can do something about.
        if verdict.status == "rejected":
            if verdict.unapproved:
                needed = {
                    axiom: list(audit.dependents(reports, axiom)) for axiom in verdict.unapproved
                }
                return ToolResult(
                    False,
                    f"the axiom audit refused this save: {audit.describe(verdict)}. "
                    f"These assumptions reached through imports have not been approved: {needed}. "
                    "Call request_assumption for each before saving work that rests on it.",
                )
            # Reached only for a forbidden axiom that is not a hole. `FORBIDDEN`
            # holds exactly `sorryAx` today, so `classify` never produces one --
            # but deleting the branch would lose the message the moment it grows,
            # and leave `verdict.forbidden[0]` read off a branch nobody wrote.
            return ToolResult(
                False,
                f"the axiom audit refused this save: {audit.describe(verdict)}. "
                f"{list(audit.dependents(reports, verdict.forbidden[0]))} depend on "
                "something no human may approve.",
            )
        # A record per module rather than one for the save, so a later save
        # elsewhere in the tree cannot overwrite what this one established.
        records = dict(empty)
        for module, reported in covering.items():
            records[module] = audit.classify(reported, approved).as_dict()
        return records, audit.describe(verdict)


    def _build_imports(self, space: LeanWorkspace, source: str) -> BuildFailure | ToolResult | None:
        """Make the workspace modules a candidate imports importable."""
        try:
            needed = internal_imports(source, space.sources())
            return space.build_modules(needed) if needed else None
        except ImportCycle as error:
            return ToolResult(False, str(error), source)


    def _check_lean(self, path: str, source: str, *, build_shared: Callable[[], None], run_source: Callable[[str], ToolResult]) -> ToolResult:
        try:
            safe_relative(path)
        except WorkspacePathError as error:
            return ToolResult(False, str(error), source)
        # Before the first Lean call of this check: an `import CommAlg` that
        # names a shared library resolves against an olean, and nothing builds
        # that olean but this.
        build_shared()
        failure = self._build_imports(self.lean_workspace, source)
        if isinstance(failure, ToolResult):
            return failure
        if failure is not None:
            return ToolResult(False, f"a workspace file this one imports does not build: {failure.module}\n{failure.output}", source)
        return run_source(source)


    def _automation_probe(self, proposed: Mapping[str, str], *, probes: tuple[str, ...], probe_seconds: float, run_source: Callable[..., ToolResult]) -> dict[str, str] | None:
        """Which of these saved statements one `PROBES` tactic closes outright.

        The same ladder `_assumption_probe` runs against a proposed axiom,
        asked of theorems being saved -- because the handwave migrates: a live
        run, refused an axiom for Sylow III, saved

            theorem sylow_count_congruence ... :
                ∃ (n_p : ℕ), n_p ∣ Nat.card G ∧ n_p ≡ 1 [MOD p] := by aesop

        `n_p = 1` satisfies both conjuncts, the comment claimed Sylow, and the
        banner counted it machine-checked without a word. The answer here is a
        *disclosure*, never a refusal: plenty of legitimate scaffolding is
        `simp`-closable, and a lemma that falls to one tactic is still a
        lemma. What must not happen is the provenance banner counting it on
        the same terms as a theorem with content, silently.

        `proposed` maps each theorem's name to its statement as `statements`
        reports it -- `theorem NAME binders : type`, whitespace-normalised to
        one line, which is what keeps the line arithmetic below sound. Each
        becomes one `example` per tactic, rewritten to carry no name so the
        goal is real, plus one `sorry` sentinel: `sorry` closes any goal a
        statement that elaborates can pose (a warning, never an error), so an
        error on the sentinel line means the *statement* does not elaborate
        here -- section `variable`s left behind, a workspace-local definition
        -- and the five probe errors above it are about the statement, not the
        tactics. Without the sentinel that shape was recorded as "closed by
        nothing", which is a clean bill of health the probe never issued.

        `import Mathlib` alone, exactly as the assumption probe imports: the
        workspace's own modules are deliberately absent, because the theorem
        under question is already declared in one of them and `exact?` would
        close every statement by citing it -- the same self-citation
        `_assumption_probe` dodges by declaring the axiom last, which no
        ordering can dodge once the declaration lives in an import. The cost
        is stated rather than hidden: a statement that does not elaborate
        outside its workspace cannot be probed at all, and is reported as
        exactly that. A filter, not a decision procedure, in the sense
        `PROBES` documents.

        Returns each name mapped to the tactic that closed it, "" when every
        tactic was tried and failed, or None when the statement did not
        elaborate here; the whole answer is None when Lean could not be asked
        at all, and then nothing is stored, so the next save asks again.
        Every conclusion is drawn from which line an error landed on, so the
        reading rules are `_assumption_probe`'s -- an unplaced error, or one
        outside the `example` lines, means Lean never reached the probes --
        plus one of this probe's own: output that overflowed the process
        limit was cut before the later lines' diagnostics were written, and
        the silence of a line nobody heard from is not a tactic succeeding.
        """
        ordered = sorted(proposed)
        block = (*probes, "sorry")
        lines: list[str] = []
        signed: list[str] = []
        verdicts: dict[str, str | None] = {}
        universes: set[str] = set()
        for name in ordered:
            if "\n" in proposed[name] or "\r" in proposed[name]:
                # `normalise_lean` preserves a newline inside a string
                # literal, and every conclusion below is drawn from which
                # line an error landed on -- an example spanning several
                # physical lines would attribute its neighbours' errors to
                # the wrong tactic. Recorded as unanswered rather than
                # guessed at.
                verdicts[name] = None
                continue
            found = THEOREM_HEAD.match(proposed[name])
            if found is None or not found.group(3).strip():
                # No proposition to probe. A tree holding it could not have
                # built, so nothing real is lost by leaving it unanswered.
                continue
            binder = found.group(2)
            bound = [part.strip() for part in binder[2:-1].split(",")] if binder else []
            if bound and not all(UNIVERSE_NAME.match(part) for part in bound):
                # A binder the `universe` command below could not redeclare.
                # Emitting it anyway puts a parse error on the probe file's
                # own lines, which takes every statement's verdict with it.
                verdicts[name] = None
                continue
            universes.update(bound)
            signed.append(name)
            lines.extend(f"example {found.group(3).strip()} := by {tactic}" for tactic in block)
        if not lines:
            return verdicts
        preamble = "import Mathlib\n\n"
        first = 3
        if universes:
            # One command redeclares every statement's universe names:
            # `example` cannot carry a `.{u}` binder of its own, and without
            # this a universe-polymorphic theorem's examples referenced names
            # nothing bound. File-global on purpose -- universes have no
            # scope to collide in -- and it costs the line arithmetic exactly
            # one line, accounted for in `first`.
            preamble += f"universe {' '.join(sorted(universes))}\n"
            first = 4
        source = preamble + "\n".join(lines) + "\n"
        try:
            result = run_source(source, timeout=max(self.lean.timeout, probe_seconds))
        except Exception:  # noqa: BLE001 - an unrunnable probe withholds a disclosure, never a save
            return None
        if (
            getattr(result, "timed_out", False)
            or getattr(result, "interrupted", False)
            or getattr(result, "output_overflow", False)
        ):
            return None
        errors = [item for item in result.diagnostics if item.severity == "error"]
        if not result.ok and not errors:
            return None
        if any(
            item.line is None or item.line < first or item.line >= first + len(lines)
            for item in errors
        ):
            return None
        placed = {item.line for item in errors}
        for position, name in enumerate(signed):
            start = first + position * len(block)
            if start + len(block) - 1 in placed:
                # The sentinel errored: the statement itself does not
                # elaborate here, and the probe lines above it failed for
                # that reason rather than because any tactic was tried.
                verdicts[name] = None
                continue
            verdicts[name] = next(
                # The lines for one statement are in `PROBES` order, so the
                # first clean line is the earliest tactic -- and the order is
                # part of the message, exactly as it is for an axiom.
                (
                    tactic
                    for offset, tactic in enumerate(probes)
                    if start + offset not in placed
                ),
                "",
            )
        return verdicts


    def _refresh_automation(self, *, current: dict[str, str], stored: dict[str, Any], environment: str, probe: Callable[[Mapping[str, str]], dict[str, str] | None], publish: Callable[[dict[str, Any]], None]) -> str:
        """Probe every saved theorem whose verdict is missing or expired, and
        record what came back. A note for the save's result, or "".

        Called after a save commits and before its state is written, so the
        verdicts land in the same `_save_state` the audit records do. Keyed by
        theorem name with the exact statement the verdict was established
        against and the toolchain it was established under, because those are
        what expire it: `_automation_closed` ignores a record either has moved
        out from under, and an expired record lands back in `needed` here. A
        record still current is not re-asked -- the answer depends on nothing
        but the statement and the environment, and `import Mathlib` costs the
        same tens of seconds every time.

        Over the whole tree rather than only the file just saved, for the
        price of the same single elaboration: a statement can move without its
        file being saved -- edited on disk, or its name taken over by another
        module's declaration while a shared-name obligation stands -- and
        probing only the saved file left that record expired until its own
        file happened to be saved again.

        A verdict of None -- the statement does not elaborate outside its
        workspace -- is stored as `"tactic": None`, which is a different fact
        from "": nothing closed it because nothing could be tried. It is
        named in the note once, and not re-asked while the statement stands,
        because the answer will not change until the statement does.

        The note is appended to the save's own result: the model that just
        saved a flagged theorem is the one that can still strengthen the
        statement, and telling it only through the banner tells it a compile
        too late.
        """
        for name in [found for found in stored if found not in current]:
            del stored[name]
        publish(stored)
        needed = {
            name: text
            for name, text in current.items()
            if stored.get(name, {}).get("statement") != text
            or stored.get(name, {}).get("environment") != environment
        }
        if not needed:
            return ""
        probed = probe(needed)
        if probed is None:
            return (
                "\n\nautomation probe: Lean could not be asked whether a single tactic "
                "closes these statements outright; nothing was recorded, and the next "
                "save will ask again."
            )
        for name, tactic in probed.items():
            stored[name] = {
                "statement": needed[name],
                "tactic": tactic,
                "environment": environment,
            }
        publish(stored)
        notes = []
        flagged = {name: tactic for name, tactic in probed.items() if tactic}
        if flagged:
            listed = ", ".join(
                f"`{name}` (by `{tactic}`)" for name, tactic in sorted(flagged.items())
            )
            notes.append(
                f"automation probe: a single automation call closes {listed} outright. "
                "Saved all the same -- this is a disclosure, not a refusal -- but the "
                "writeup banner, /status and read_workspace will all say so, because a "
                "statement one tactic closes may assert far less than its name or the "
                "prose around it suggests. If that is not what you meant to prove, "
                "strengthen the statement."
            )
        unreached = sorted(name for name, tactic in probed.items() if tactic is None)
        if unreached:
            names = ", ".join(f"`{name}`" for name in unreached)
            notes.append(
                f"automation probe: {names} could not be probed in isolation "
                "(section variables, a local definition, or a multi-line string "
                "literal), so whether one tactic closes it was not established in "
                "either direction."
            )
        return "".join(f"\n\n{note}" for note in notes)


    def _automation_closed(self, *, stored: Mapping[str, Any], current: Callable[[], dict[str, str]], environment: Callable[[], str]) -> dict[str, str]:
        """Saved theorems one automation call closes: name to the tactic.

        Read from the recorded probe verdicts, and only while the statement a
        verdict was established against is still the statement saved and the
        toolchain is still the one it was asked under -- a record that
        outlives its inputs is the exact failure `_obligations`' "never
        stored" rule exists to prevent, so the expiry is checked here on every
        read rather than trusted to cleanup. The environment check is the
        audit's rule: what standard automation closes moves with Mathlib and
        the toolchain, and a verdict from another environment is not current.
        A theorem no probe has covered yet is simply absent, the same terms
        `state["audit"]` gives a module no save has covered.
        """
        if not stored:
            return {}
        current = current()
        environment = environment()
        return {
            name: str(record.get("tactic"))
            for name, record in stored.items()
            if record.get("tactic")
            and current.get(name) == record.get("statement")
            and record.get("environment") == environment
        }


    def _still_current(
        self, module: str, record: dict[str, Any], signatures: dict[str, str]
    ) -> dict[str, Any]:
        """An audit verdict as it stands against the tree in front of us.

        The axioms a declaration rests on are a fact about everything that went
        into building it: the toolchain and project, the module's own source,
        the workspace modules it imports, and the oleans behind the imports it
        takes from outside. Any of those can move without a save -- a file
        edited on disk, a local Lake project rebuilt, a different Lean -- and a
        stored verdict would otherwise sit in `session.json` and be handed to
        the model as the module's current audit until some later save happened
        to cover it again.

        So the check is the build signature, not the toolchain alone: it already
        folds in all of that, and it is recursive, so a change beneath a module
        expires the verdict above it too. A verdict written before verdicts
        carried a signature has none to match, and is treated the same way:
        unknown is not current. What it said is kept for reference rather than
        deleted -- it is the *status* that must not read as a pass.
        """
        if record.get("signature") and record["signature"] == signatures.get(module):
            return record
        return {
            **record,
            "status": "not established",
            "reason": "the module's Lean toolchain, source, or dependencies have changed since this was established; save it again",
            "stale": True,
        }


    def _current_audit(
        self, sources: dict[str, str] | None = None, *, stored: dict[str, Any]
    ) -> dict[str, dict[str, Any]]:
        """The stored verdicts, each measured against the tree in front of us.

        `session.json` keeps a verdict for reference after the module beneath
        it has moved; `_still_current` is what says whether it still describes
        anything, and every existing reader of the audit -- `_open_theorems`,
        `_settled_declarations`, `_audit_gaps` -- goes through it. `/status
        --full` and `/export` must too, and the first version of both did not:
        they read `state["audit"]` raw, so a theorem whose toolchain had moved
        was rendered "kernel-verified" on the same page that reports its audit
        as no longer established. A disclosure that contradicts the obligation
        beside it is worse than none.

        A signature that cannot be computed -- a tree that does not order --
        expires everything rather than passing it through. `_audit_gaps`
        reports the cycle; nothing here may grade a workspace it cannot read.
        """
        try:
            signatures = self.lean_workspace.current_signatures(sources)
        except ImportCycle as error:
            return {
                module: {
                    **record,
                    "status": "not established",
                    "reason": f"the workspace does not order: {error}",
                    "stale": True,
                }
                for module, record in stored.items()
            }
        return {
            module: self._still_current(module, record, signatures)
            for module, record in stored.items()
        }


