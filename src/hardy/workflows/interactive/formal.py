"""Atomic checked saves and audit decisions for an interactive Lean tree.

A save builds and audits a shadow before committing, then publishes evidence.
Cross-capability authorship and documentation gates are explicit callbacks;
they cannot expose mutable session state to the formal workspace owner.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ... import audit
from ...lean import LeanTools
from ...models import ToolResult
from ...workspace import (LeanWorkspace, WorkspacePathError, ImportCycle, BuildFailure,
    safe_relative, module_name, dependents, declarations, assumptions,
    unreadable_assumptions, internal_imports)

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


