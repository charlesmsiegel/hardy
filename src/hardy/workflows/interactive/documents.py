"""Guarded document sources, compile publication, and completion obligations.

A successful compiler is only half a save: source publication must succeed
before its PDF and labels are published. Bibliography authority arrives as
keys and named operations; this owner never receives a paper client or session.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ... import completion
from ...bibliography import (BibliographyError, STORE as STORE_BIBLIOGRAPHY,
    hand_written_bibliography, is_generated as is_generated_bibliography)
from ...latex import (LatexTools, ROOT_DOCUMENT, ARTIFACTS as LATEX_ARTIFACTS,
    OUTPUTS as LATEX_OUTPUTS, compiles_document, unreached_fragments)
from ...layout import files_under, guard_for, read_text, read_bytes, LayoutError
from ...models import ToolResult
from ...storage import LockTimeout

BUILD_DIR_TEX = ".build/tex"
NEWLABEL = re.compile(r"\\newlabel\{([^}]*)\}")

class WriteupNotSaved(ValueError):
    """The compiler accepted a source which guarded publication could not save."""

@dataclass(frozen=True)
class DocumentPolicy:
    bibliography_refusal: Callable[[str, str], str]
    stamp: Callable[[], str]
    vouch: Callable[[tuple[str, ...]], str]
    stamp_writeup: Callable[[str | None, str | None], None]
    registry: Callable[[], list[dict[str, str]]]
    owed_note: Callable[[], str]

@dataclass(frozen=True)
class FormalDocumentFacts:
    statements: dict[str, str]
    saved_statements: dict[str, str]
    used_assumptions: set[str]
    shared_names: dict[str, list[str]]
    open_theorems: set[str]
    audit_gaps: tuple[completion.Obligation, ...]

class DocumentService:
    def __init__(self, workspace: Path, latex: LatexTools):
        self.workspace = workspace
        self.tex_root = workspace / "tex"
        self.latex = latex

    def _check_latex(self, path: str, source: str, *, policy: DocumentPolicy) -> ToolResult:
        resolved = self._tex_path(path)
        if isinstance(resolved, ToolResult):
            return resolved
        relative, _ = resolved
        # Refused at the check as well as at the save. A check that accepted a
        # hand-written bibliography and a save that refused it would teach the
        # model that the document is sound and then refuse to keep it, which
        # is a worse conversation than one refusal in the right place.
        refusal = policy.bibliography_refusal(relative, source)
        if refusal:
            return ToolResult(False, refusal, source)
        return self.latex.check(
            source,
            path=relative,
            tree=self.tex_root,
            stamp=policy.stamp(),
            vouched=policy.vouch,
        )


    def _save_latex(self, path: str, source: str, *, policy: DocumentPolicy) -> ToolResult:
        resolved = self._tex_path(path)
        if isinstance(resolved, ToolResult):
            return resolved
        # The normalised path, not the argument: `sections\one.tex` names one
        # file to `_tex_target` and, on a platform where a backslash is an
        # ordinary character, a different one to the compiler -- so the root
        # would be checked against the old fragment and then overwritten by a
        # candidate nothing had compiled.
        relative, _ = resolved
        # Before the compile, not after: a `\bibitem` the model wrote itself
        # resolves perfectly well, so the reference check would pass it and
        # the document would be published carrying a reference nothing
        # fetched. `cite_paper` is the only way a reference reaches a reader.
        refusal = policy.bibliography_refusal(relative, source)
        if refusal:
            return ToolResult(False, refusal, source)
        # Read before the compiler is handed the tree, and checked again at
        # the stamp: see `_stamp_writeup`.
        compiled_against = self._bibliography_identity()
        # The rest of the tree as the compiler will get it, for the same
        # reason and checked in the same place. Excludes the candidate, which
        # is the one file this save is about to change.
        compiled_tree = self._tex_tree_digest(relative)

        def _write() -> None:
            # `guard_for`, not a bare write to `target`. `_tex_path` proves the
            # NAME is a relative, dot-free, colon-free path; it does not and
            # cannot say where the directories of that name lead.
            # `tex/sections -> $HOME` passed every one of its checks, and
            # `save_latex("sections/one.tex")` then wrote a file of the model's
            # choosing into the user's home directory. The guard proves each
            # component against the one above it, at the moment of the write,
            # and refuses a symlinked leaf outright -- which is also what stops
            # `writeup.tex -> ~/.bashrc`.
            try:
                guard, name = guard_for(self.tex_root, relative, create=True)
                with guard.open(name, "w", encoding="utf-8") as handle:
                    handle.write(source.rstrip() + "\n")
            except OSError as error:
                # Raised on, never swallowed: the whole point of running here
                # is that `check` publishes nothing when this fails. Wrapped
                # so the answer names the save -- a directory sitting where
                # the file should be, a full disk -- instead of reading as a
                # compiler failure. A `LayoutError` needs no wrapper: it
                # already says which path it refused and why.
                raise WriteupNotSaved(f"{relative} could not be saved: {error}") from None

        # Handed to `check` rather than run after it. `check` publishes
        # `writeup.pdf` and `.build/tex/writeup.aux` from the candidate, and
        # doing that first meant a write the guard refused left a committed PDF
        # and a set of labels describing source that is not on disk, while the
        # unchanged `tex_signature` reported the writeup as freshly compiled.
        # The save is now the last thing that can fail before anything is
        # published, so a failure leaves the workspace as it was.
        try:
            result = self.latex.check(
                source,
                path=relative,
                tree=self.tex_root,
                output_dir=self.workspace,
                aux_dir=self.workspace / BUILD_DIR_TEX,
                commit=_write,
                stamp=policy.stamp(),
                vouched=policy.vouch,
            )
        except WriteupNotSaved as error:
            return ToolResult(False, str(error), source)
        if not result.ok:
            return result
        # Stamped after the write, on a compile that succeeded, and only when
        # what was compiled is the writeup itself. Saving a fragment the root
        # does not include yet is checked through a probe document, which says
        # the fragment is sound and nothing about the writeup -- stamping that
        # would mark the tree established on the strength of a document nobody
        # will read.
        if compiles_document(self._tex_sources(), relative) and self._tex_tree_digest(
            relative
        ) == compiled_tree:
            # Taken here, with the candidate now written: what the signature
            # about to be hashed should describe. `_stamp_writeup` checks it
            # again on the far side of that hash.
            settled = self._tex_tree_digest()
            # And only when the tree the compiler READ is still the tree on
            # disk. `check` copies the writeup into a scratch directory and
            # compiles that; the signature stamped below is taken from the
            # live files. Another session saving in between -- or an editor --
            # left the stamp describing source the published PDF was not built
            # from, and `report_result` accepts a stamped writeup. The
            # candidate is excluded because it is the one file this save is
            # entitled to have changed.
            #
            # Not stamping is the failure mode, which is the safe one: the
            # writeup reads stale, which is what it is, and the next compile
            # settles it.
            policy.stamp_writeup(compiled_against, settled)
        # Advisory rather than a refusal. With the save_lean ratchet in place a
        # hard gate here would deadlock: Lean blocked for want of a writeup,
        # and the writeup blocked for not yet covering everything registered.
        # Which is also why the writeup tree is the one place a save is never
        # refused for what it does not yet contain -- it is where every
        # obligation is settled.
        #
        # Two notes, not one. The obligations are about the *work*: what a
        # saved theorem still owes before anyone may report it. This one is
        # about the *registry*: a name recorded for something the document has
        # not labelled yet, which is a promise made and not yet kept even when
        # no theorem is waiting on it.
        missing = [item["latex_name"] for item in policy.registry() if item["latex_name"] not in self._labels()]
        note = policy.owed_note()
        if missing:
            note = f"\n\nStill missing labels for registered names: {missing}{note}"
        # Hardy first, pdfTeX second. The graded run's last save returned 4,879
        # bytes, of which the part that mattered -- "Still missing labels for
        # registered names" -- was the last line, under a wall of font paths.
        #
        # The log is kept whole. Filtering it on success was tried and withdrawn:
        # a filter cannot know which of pdfTeX's lines a caller needed, and it
        # loses the continuation lines of a multi-line warning, `Overfull` boxes,
        # `No file ...` notices, rerun instructions, and any `\typeout` a model
        # wrote to ask the engine a question. Reordering costs nothing and fixes
        # the same problem -- and `check` tail-truncates its output, so with the
        # note appended a long enough log could push it out entirely.
        if note:
            return ToolResult(True, f"Saved.{note}\n\n{result.output}", source)
        return result


    def _tex_root_source(self) -> str:
        """The saved root document's text, or empty when there is none.

        Through the guard, because `writeup.tex` is versioned and a clone may
        ship it as a link: read with `Path.read_text` it was a host file whose
        `\\input` lines decided whether a save counted as compiling the writeup,
        and whose whole text was then handed to LaTeX as this project's root.
        """
        root = self.tex_root / ROOT_DOCUMENT
        if not root.is_file():
            return ""
        return read_text(self.tex_root, ROOT_DOCUMENT)


    def _vouched_references(self, keys: tuple[str, ...], known: frozenset[str]) -> str:
        r"""Why the reference list the compiler built may not stand, or "".

        The authority, with `hand_written_bibliography` in front of it as a
        courtesy. Reading the source for `\bibitem` catches the ordinary
        mistake and says something useful about it, but it is lexical, and TeX
        is a macro language: `\csname bibitem\endcsname`, a `\newcommand`
        wrapping it, or any other expansion produces a reference list that no
        reader of the text would recognise as one. What every spelling has in
        common is the `\bibcite` the compiler writes into the `.aux` -- so
        that is what is checked, and every key in it has to be one
        `cite_paper` put in the store.

        The same move `completion.py` makes for labels: ask the compiler what
        it did, not the document what it says.
        """
        invented = [key for key in keys if key not in known]
        if not invented:
            return ""
        return (
            f"the compiled document defines {len(invented)} bibliography entry(s) Hardy "
            f"cannot vouch for: {', '.join(invented)}. Every reference a reader sees must "
            "come from cite_paper, which can only cite a paper fetch_paper actually "
            "stored; \\input{references} is the only reference list this document may "
            "carry."
        )


    def _bibliography_refusal(self, relative: str, source: str, regenerate: Callable[[], Any]) -> str:
        r"""Why this compile may not proceed on bibliography grounds, or "".

        The candidate AND the tree it is compiled with. Checking the candidate
        alone was half a rule: `LatexTools.check` compiles the whole saved
        `tex/` tree, so a `\bibitem{invented}` in a fragment saved before this
        gate existed, edited outside Hardy, or brought in from somewhere else
        would be pulled into a clean root and published with it -- the
        refusal has to cover every file the compiler will read, not only the
        one being handed over now.

        Hardy's own generated file is the one exemption, and a file that
        cannot be read is skipped rather than guessed at: one unreadable
        fragment must not stop every save in a workspace.
        """
        # First, because the compile below reads this file. The key check
        # asks whether every key the compiler used was recorded; it says
        # nothing about the authors or the title printed under one, so a
        # `references.tex` that arrived stale from a clone or was edited past
        # the refusal could publish fabricated metadata under a vouched key.
        # Rewritten from the store rather than refused: see
        # `Bibliography.regenerate` for why a refusal here would leave the
        # workspace with no move.
        try:
            regenerate()
        except (BibliographyError, LockTimeout, OSError, LayoutError) as error:
            return f"the generated reference list could not be brought up to date: {error}"
        refusal = hand_written_bibliography(relative, source)
        if refusal:
            return refusal
        for path in self._compilable_paths():
            if path == relative or is_generated_bibliography(path):
                continue
            try:
                text = read_text(self.tex_root, path, errors="replace")
            except (OSError, ValueError):
                continue
            if "\x00" in text:
                # Not something TeX reads as text. A binary that happens to
                # contain the bytes of a command is not a command.
                continue
            found = hand_written_bibliography(path, text)
            if found:
                return f"{path} is part of this document, and {found}"
        return ""


    def _compilable_paths(self) -> list[str]:
        r"""Every file under `tex/`, not only the `.tex` ones.

        `_copy_tree` hands the compiler the whole tree, and TeX will `\input`
        a file of any name -- so a `tex/fake.bbl` pulled in by the root was
        copied for the compiler and never shown to the bibliography check. If
        it defined a fabricated entry under a key `cite_paper` had already
        recorded, the auxiliary-file check saw only that legitimate key and
        the document was published. What the compiler may read is what has to
        be read here.
        """
        if not self.tex_root.is_dir():
            return []
        try:
            found = files_under(self.tex_root, "")
        except (OSError, ValueError):
            return self._tex_paths()
        # The same exclusions `_copy_tree` makes, so that this list is exactly
        # what the compiler is handed: an auxiliary file is the compiler's
        # output read back, and `writeup.pdf`/`writeup.log` are its outputs by
        # name. Neither is an input, so neither is scanned for commands nor
        # hashed into what the writeup was compiled from.
        return [
            relative.as_posix()
            for relative in found
            if relative.suffix not in LATEX_ARTIFACTS
            and relative.as_posix() not in LATEX_OUTPUTS
        ]


    def _tex_path(self, path: str) -> tuple[str, Path] | ToolResult:
        """The workspace-relative writeup path, and where it lives on disk."""
        cleaned = str(path).replace("\\", "/")
        if not cleaned.endswith(".tex"):
            return ToolResult(False, f"not a workspace LaTeX path: {path!r}")
        candidate = PurePosixPath(cleaned)
        # A colon is refused because `PurePosixPath("C:/out.tex").is_absolute()`
        # is False, while joining that to a Windows root discards the root and
        # yields `C:\out.tex` -- an escape that would let a tool read, overwrite,
        # or delete a file anywhere on the machine.
        if (
            candidate.is_absolute()
            or any(part in {"..", "."} for part in candidate.parts)
            or any(":" in part for part in candidate.parts)
        ):
            return ToolResult(False, f"path escapes the workspace: {path!r}")
        return str(candidate), self.tex_root / candidate


    def _tex_target(self, path: str) -> Path | ToolResult:
        resolved = self._tex_path(path)
        return resolved if isinstance(resolved, ToolResult) else resolved[1]


    def _tex_sources(self) -> dict[str, str]:
        """The writeup tree as text, keyed by workspace-relative path.

        Discovered and read through the layout guard, exactly as
        `LeanWorkspace.sources` is. `rglob` reports a symlinked `tex/leak.tex`
        as an ordinary fragment and `read_text` follows it without a word, so
        a repository could put a host file into the writeup: its text answered
        the statement obligations, it was hashed into `tex_signature` as the
        project's own, and `read_file` handed it back verbatim. A symlink under
        `tex/` is refused rather than skipped, for `files_under`'s reason --
        leaving it out silently would make the tree Hardy judges differ from
        the tree a reader sees.
        """
        if not self.tex_root.is_dir():
            return {}
        found: dict[str, str] = {}
        for relative in files_under(self.tex_root, ".tex"):
            try:
                found[relative.as_posix()] = read_text(
                    self.tex_root, relative, errors="replace"
                )
            except OSError:
                # Unreadable is not empty, but a listing that raises here would
                # take the turn with it. The obligation it leaves standing is
                # the safe direction: the file cannot be shown to say anything.
                # A `LayoutError` is deliberately NOT swallowed here: that one
                # is a refusal about what the tree is, not a file that happens
                # to be unreadable.
                continue
        return found


    def _tex_paths(self) -> list[str]:
        """Workspace-relative paths of every `.tex` file under the writeup root.

        An absent `tex/` is not an error -- a workspace that has not written
        any LaTeX yet is the ordinary starting state -- so this returns an
        empty list rather than raising, and both `_unreached_tex` and the
        steering block's omission check share this one place that decides it.
        Caught as `ValueError` rather than naming `WorkspacePathError`
        specifically: `files_under` raises the sibling `LayoutError` for a
        symlink anywhere under `tex/`, and both are `ValueError` subclasses,
        so one broad clause catches whichever this workspace produces.
        """
        if not self.tex_root.is_dir():
            return []
        try:
            return [relative.as_posix() for relative in files_under(self.tex_root, ".tex")]
        except (OSError, ValueError):
            return []


    def _unreached_tex(self) -> list[str]:
        """Writeup files no `\\input` chain from the root reaches.

        Read with `errors="replace"`, as `_tex_sources` is, so a `.tex` file
        that is not valid UTF-8 degrades to a fragment with replacement
        characters rather than raising. Each file is read inside its own
        `try`: one that cannot be read at all -- an `OSError`, or a
        `LayoutError`/`WorkspacePathError` from a symlink met between
        `_tex_paths`'s listing and this read -- is left out of `sources`
        rather than aborting the whole method, so one bad file does not hide
        every other file's orphan status.
        """
        paths = self._tex_paths()
        if not paths:
            return []
        sources: dict[str, str] = {}
        for path in paths:
            try:
                sources[path] = read_text(self.tex_root, path, errors="replace")
            except (OSError, ValueError):
                continue
        return unreached_fragments(sources)


    def _labels(self) -> set[str]:
        r"""Every label LaTeX actually created, from the last saved compile.

        Read from the compiler's own `.aux`, not from the source text. A
        `\label` can appear in a document without ever being created -- inside
        `\verb`, in a comment, in a branch that was not taken -- and counting
        those would release the ratchet for a theorem the document does not
        describe. The `.aux` is what LaTeX wrote down about what it did, which
        is the same reason Hardy believes Lean's kernel and not its own reading
        of a proof.

        Still derived rather than stored: the file is a build artifact of the
        last successful save, and if it is absent nothing is documented yet.

        Read through the guard, like every other file in a project. `.build/`
        is gitignored but not untrackable -- a repository that ships
        `.build/tex/writeup.aux` as a link to a file full of `\\newlabel` lines
        would have had Hardy count those labels as ones LaTeX created here,
        which is the completion gate released by a file nobody in this project
        wrote. Absent is still nothing documented yet; a link is a refusal.
        """
        try:
            written = read_text(
                self.workspace, f"{BUILD_DIR_TEX}/writeup.aux", errors="replace"
            )
        except (FileNotFoundError, NotADirectoryError):
            return set()
        return set(NEWLABEL.findall(written))


    def _tex_tree_digest(self, without: str | None = None) -> str:
        """What the files the compiler reads hash to, ignoring one of them.

        Narrower than `_tex_signature` on purpose. That one also covers the
        banner's inputs and the bibliography, both of which a save legitimately
        moves -- so it cannot answer "did anything ELSE change while the
        compiler was running", which is the question `_save_latex` has to ask.
        The one file it excludes is the candidate, the only one this save is
        entitled to have changed.
        """
        digest = hashlib.sha256()
        for path in self._compilable_paths():
            if path == without:
                continue
            digest.update(path.encode("utf-8"))
            digest.update(b"\0")
            try:
                digest.update(read_bytes(self.tex_root, path))
            except (OSError, ValueError):
                digest.update(b"<unreadable>")
            digest.update(b"\0")
        return digest.hexdigest()


    def _tex_signature(
        self,
        *,
        stamp_inputs: Callable[[], dict[str, Any]],
        tex: dict[str, str] | None = None,
    ) -> str:
        """What the writeup tree hashes to, as a whole.

        `open_names` substitutes an open set for the one the workspace has now,
        which is how `_documentation_gate` asks what this signature *would* be
        if only that had not moved. Everything else is read live.
        """
        digest = hashlib.sha256()
        # Every file the compiler is handed, not the `.tex` ones alone.
        # `_copy_tree` gives TeX the whole tree, so a `.bbl`, an `.inc` or a
        # local `.sty` that the root pulls in is part of what was compiled --
        # and while the signature covered only `.tex`, one of those could
        # change after a successful save and leave the writeup reading as
        # current against a tree the checked PDF no longer describes. Read as
        # bytes, because these are not all text.
        for path in self._compilable_paths():
            digest.update(path.encode("utf-8"))
            digest.update(b"\0")
            if tex is not None and path in tex:
                # A caller holding a snapshot of the `.tex` tree is answered
                # from it, so its obligations all describe one moment. For a
                # file that decodes cleanly this is the same bytes the live
                # read below would hash; where it is not -- a `.tex` that is
                # not UTF-8, or one deleted since the snapshot -- the two
                # disagree and the writeup reads as stale, which is the safe
                # direction for a comparison whose match releases a refusal.
                digest.update(tex[path].encode("utf-8"))
                digest.update(b"\0")
                continue
            try:
                digest.update(read_bytes(self.tex_root, path))
            except (OSError, ValueError):
                # Unreadable is itself a state to be stamped against, and a
                # distinct one from absent: the path is already in the hash
                # above, so this marks it without pretending to its content.
                digest.update(b"<unreadable>")
            digest.update(b"\0")
        # The banner is part of the published document, so a change to what it
        # would say makes the PDF as stale as an edit to the source does.
        # Without this, report_result succeeded and the published PDF went on
        # saying that no result had been reported, with `_stale_writeup` seeing
        # nothing wrong -- the counts live in the record, which this never read.
        #
        # The stamp's *inputs*, not `self._stamp()`. Calling it recurses:
        # `_stamp` asks for the obligations, `_stale_writeup` is one of them,
        # and it asks for this signature. Everything the banner is computed from
        # is either hashed above (the tex sources) or listed here.
        digest.update(
            json.dumps(stamp_inputs(), sort_keys=True).encode("utf-8")
        )
        digest.update(b"\0")
        # And the store the reference list is vouched against. The keys a
        # compile used are checked at the save, and the verdict is remembered
        # by this signature -- so `bibliography.json` edited or deleted after
        # that save left the signature current, the writeup reading as freshly
        # compiled, and `report_result` accepting a document whose references
        # nothing recorded any more. A citation withdrawn from the store is a
        # change to what the document rests on, exactly as an edit to its
        # source is.
        digest.update(self._bibliography_identity().encode("utf-8"))
        digest.update(b"\0")
        return digest.hexdigest()


    def _bibliography_identity(self) -> str:
        """The store as the signature sees it: its bytes, or that it has none.

        Unreadable and absent are told apart, because "there is no
        bibliography" and "the bibliography cannot be read" are different
        states for a writeup to be stamped against.
        """
        try:
            return hashlib.sha256(read_bytes(self.workspace, STORE_BIBLIOGRAPHY)).hexdigest()
        except FileNotFoundError:
            return "absent"
        except (OSError, ValueError):
            return "unreadable"


    def _stamp_writeup(
        self, compiled_against: str | None = None, compiled_tree: str | None = None,
        *, signature_for: Callable[[], str], open_theorems: Callable[[], set[str]],
        publish: Callable[[str, list[str], str | None], None], persist: Callable[[], None]
    ) -> None:
        """Record what this compile was made against.

        The open set is stored beside the signature rather than only folded
        into it, because two different questions are asked of the same stamp:
        whether the compiled document still describes this workspace, and --
        by `_documentation_gate` -- whether the open set is the only reason it
        does not.

        `compiled_against` is the bibliography identity as it stood when the
        compiler was handed its copy of the tree. The compile reads a snapshot
        and this reads the live files, so another session's `cite_paper`
        landing in between meant the PDF was built from the old reference list
        while the stamp recorded the new store -- and the vouching does not
        catch it, since the old keys are a subset of the new ones. When it has
        moved, nothing is stamped: the writeup reads stale, which is what it
        is, and the next compile settles it. Erring the other way would mark a
        PDF current against a bibliography it was not built from.
        """
        # Hashed FIRST, then checked. The comparison used to come before the
        # hash, which leaves the same window one step along: a `cite_paper`
        # landing between them has `_tex_signature` read the new
        # `references.tex` and the new store, so the stamp describes a tree
        # the PDF was not built from -- and vouching does not catch it,
        # because the old compile's keys are a subset of the new store's.
        #
        # This way round the check is about the value being persisted rather
        # than about a value read before it. The signature covers the
        # bibliography identity itself (that is what made it a signature of
        # the whole compile), so a store that moved during the hash produces
        # one that no longer matches `compiled_against` and nothing is
        # stamped at all.
        signature = signature_for()
        open_now = sorted(open_theorems())
        if compiled_against is not None and self._bibliography_identity() != compiled_against:
            return
        # And the tree, checked in the same place and for the same reason.
        # The caller compares before handing the compiler the tree; that
        # closes the window up to the compile and not the one across this
        # hash, which reads every file again. A neighbour landing in there --
        # including one overwriting the candidate this save just committed --
        # left the stamp describing their source rather than the compiled
        # document. `compiled_tree` is the WHOLE tree, candidate included,
        # because by now the candidate is written and is part of what the
        # signature above just hashed.
        if compiled_tree is not None and self._tex_tree_digest() != compiled_tree:
            return
        document_digest = None
        # And what the compile actually produced. A signature says only that
        # Hardy compiled *something* here once; a user who then drops another
        # `writeup.pdf` over it leaves the signature truthy and the bytes
        # someone else's, and the export would still credit Hardy with them.
        # Additive like the two above, and absent for a workspace stamped
        # before it existed -- which the reader is told rather than guessed at.
        document = self.workspace / "writeup.pdf"
        if document.is_file() and not document.is_symlink():
            document_digest = hashlib.sha256(
                document.read_bytes()
            ).hexdigest()
        publish(signature, open_now, document_digest)
        persist()


    def _obligations(
        self, *, facts: FormalDocumentFacts, registry: list[dict[str, Any]],
        assumptions: list[dict[str, Any]], stale_writeup: tuple[completion.Obligation, ...],
        tex: dict[str, str] | None = None
    ) -> tuple[completion.Obligation, ...]:
        """What the workspace still owes, derived from the artifacts alone.

        Never stored. A flag saying the work was finished would outlive the
        file it described -- and the one thing this must not do is report a
        theorem as written up because it *was*, before the statement changed.

        A caller holding a snapshot of the trees passes it in, and everything
        below is derived from that one moment. `/export` is why: it prints the
        results from a snapshot and these obligations beside them, and editing
        a `.lean` file behind Hardy is supported. A later read that no longer
        declares an undocumented theorem answers "nothing outstanding" next to
        that same theorem's statement, which reads as a page saying the work is
        written up. A later read can ask for LESS, not only for more.
        """
        written = self._tex_sources() if tex is None else tex
        owed = completion.outstanding(
            theorems=facts.statements,
            registry=registry,
            labels=self._labels(),
            assumptions=assumptions,
            used=facts.used_assumptions,
            tex=written,
            # Open theorems owe nothing, but they are still saved theorems: they
            # back a `\begin{theorem}` the document asserts, and they decide
            # whether a leaf name is unambiguous. Left out, a document asserting
            # one read as backed by nothing.
            saved=facts.saved_statements,
        )
        # Ahead of the rest: while two modules answer to one name, every
        # obligation below is about whichever of them was read last.
        #
        # The audit gaps are here rather than only inside `report_result`
        # because all three surfaces have to agree. With them counted only at
        # report time, a workspace whose Lean was edited on disk refused the
        # report while `/status` and the end-of-turn notice said nothing was
        # outstanding -- so the claim the notice exists to contradict went
        # uncontradicted, and only a model that tried to report properly ever
        # found out.
        shared = [
            completion.Obligation(
                "lean",
                name,
                f"{modules} each declare a theorem called `{name}`, so one name cannot "
                "stand for both in the registry, the label, or the statement the writeup "
                "quotes. Put one of them in a namespace.",
            )
            for name, modules in sorted(facts.shared_names.items())
        ]
        # `_audit_gaps` is asked only about closed theorems. An open one has a
        # current audit record -- being current is how Hardy knows it is open --
        # so it has no gap to report, and reporting it would say the same thing
        # the `open` obligation beside it already says.
        opened = facts.open_theorems
        holes = tuple(
            completion.Obligation("open", name, "still open -- rests on a hole")
            for name in sorted(opened)
        )
        return (
            *holes,
            *shared,
            *facts.audit_gaps,
            *stale_writeup,
            *owed,
        )


