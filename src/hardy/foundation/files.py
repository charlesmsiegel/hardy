"""Guarded reads and writes beneath explicitly owned directories.

Each operation checks the directory identity and refuses symlink leaves;
callers decide which project or capability owns the directory.
"""
from __future__ import annotations

import errno
import json
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any


class LayoutError(ValueError):
    """A slug that is not a single safe directory beneath the root."""


def resolve_named_child(path: Path, parent: Path) -> Path:
    """`path`, proven to BE `parent`'s child of exactly that name.

    THE INVARIANT THIS WHOLE MODULE ENFORCES, stated in one function so there
    is one place to read it and one place to change it:

        every path Hardy reads or writes inside a project must resolve to a
        real, non-symlink entry at exactly its expected location.

    Identity, not parentage, and the difference is the entire history of this
    check. "Somewhere under the root" was the first version, and it accepted
    `sylow/.gitignore -> ../README.md`. "Its own parent's immediate child" was
    the second, and it accepts `<problem>/.local -> tex`: the resolved path is
    a sibling, so its PARENT is right and the check passes -- while machine-
    local provider state lands in the tracked TeX tree, outside the `/.local/`
    rule written to cover it. `<root>/main -> other-project` opens a different
    problem the same way. Asking `resolved == parent / path.name` is the only
    form of the question that a link cannot satisfy, because a symlink by
    definition resolves somewhere its own name is not.
    """
    resolved = path.resolve()
    expected = parent / path.name
    if resolved != expected:
        raise LayoutError(
            f"{path} resolves to {resolved}, not to {expected}; "
            "refusing to read or write through it"
        )
    return resolved


def _refuse_if_symlink(path: Path) -> None:
    """Refuse `path` outright if it is already a symlink, without resolving it.

    Both `.gitignore` files here are files Hardy itself generates and rewrites
    on every `ensure()`; unlike a directory, a project has no legitimate
    reason to have replaced one with a symlink, so this does not even ask
    where a symlink would lead -- being a symlink at all is refused.
    """
    if path.is_symlink():
        raise LayoutError(f"{path} is a symlink; refusing to read or write through it")


def _ensure_dir(directory: Path, parent: Path) -> None:
    """Create `directory`, refusing to follow a symlink that leaves `parent`.

    Checked as a symlink *before* `mkdir` runs, not after: `mkdir(exist_ok=
    True)` on a path that is already a symlink neither creates anything nor
    raises (if the target exists) or raises the wrong thing entirely (if the
    target is missing), so a post-hoc check alone would either miss the
    escape or surface a confusing `FileExistsError` instead of `LayoutError`.
    `parent` is the directory's own owning directory, not the project root at
    large -- see `_refuse_unless_direct_child`.
    """
    if directory.is_symlink():
        resolve_named_child(directory, parent)
        return
    directory.mkdir(parents=True, exist_ok=True)
    resolve_named_child(directory, parent)


# `O_NOFOLLOW` where the platform has it, nothing where it does not. It is the
# one flag that closes the gap between "this leaf is not a symlink" and "the
# file descriptor I just got is that same leaf"; Windows has no equivalent, so
# there the `lstat` in `_leaf` is the whole of the leaf check. See `WriteGuard`
# for why that is enough there: the identity check below re-walks the path in
# the kernel on every write, which is what catches a junction swapped in
# underneath us.
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class WriteGuard:
    """The one door a file inside a Hardy-owned directory is written through.

    `Layout.ensure` proves the shape of the tree ONCE, when a project opens.
    That is setup, and it cannot speak for a write that happens ten minutes
    later: `transcript.jsonl` is a tracked file that a cloned repository is
    free to ship as `../../victim.sh`, `ensure` never enumerates it, and the
    first appended event then lands outside the root. `cas/cells.jsonl` is
    worse still, because `_truncate_log` opens it `r+b` and truncates -- so a
    symlinked log destroys whatever it names rather than merely growing it.

    So the proof is renewed at the moment of every write, and made of two
    cheap parts:

    * The OWNING DIRECTORY is resolved once, here, and its `(st_dev, st_ino)`
      pinned. Every write re-`stat`s the directory BY PATH -- one syscall, in
      which the kernel walks every ancestor for us -- and refuses if the
      identity that comes back is not the pinned one. Any component of the
      path being replaced by a link to somewhere else lands on a different
      directory, hence a different inode, hence a refusal. This is deliberately
      not `Path.resolve()` per write: that is a Python-level `lstat` loop over
      every component, and these writes happen once per chat event.
    * The LEAF is `lstat`ed and refused if it is a symlink at all, and then
      opened with `O_NOFOLLOW` where the platform has it, which closes the
      window between the two.

    A file is named to this class rather than handed to it as a path, which is
    the point: a new file inside a guarded directory cannot be written without
    going through here, because there is no path to open.

    THE THREAT MODEL, stated so the next reader does not have to guess at it.
    This defends against symlinks SHIPPED IN A REPOSITORY: a clone is a
    hostile artifact, Hardy opens one before any human has looked at it, and
    `git` will happily version `transcript.jsonl -> ../../victim.sh`. That is
    the whole of it.

    It is NOT a sandbox for the model. Hardy runs model-authored Lean and
    model-authored CAS code as ordinary subprocesses, by design, so a model
    that wanted to write outside the project would write a Lean `IO` action
    and never come near this class.

    It is NOT a defence against a concurrent local attacker either. There is a
    TOCTOU window between the `stat` in `confirm` and the `open` that follows,
    narrowed by `O_NOFOLLOW` but not closed, and it cannot be closed in pure
    Python without `openat`. Given the paragraph above -- an adversary who can
    race Hardy on its own filesystem can simply read the source it is about to
    run -- that window buys nothing worth another round of work.
    """

    def __init__(self, directory: Path, *, create: bool = False) -> None:
        """Prove `directory` is where it appears to be, and pin what it is now.

        The rule is `Layout.ensure`'s own, applied to whatever directory is
        handed over: once resolved it must be an immediate child of its own
        parent, resolved. So `<problem>/.local` is refused when it links into a
        sibling project even though that is still inside the root, and the
        problem directory itself is refused when it links out of the root --
        without this class needing to know which of the two it is holding.
        """
        self.directory = directory
        if create:
            self._make()
        else:
            self._prove()

    def _prove(self) -> None:
        """Resolve the directory, check it against its parent, and pin it."""
        self._resolved = resolve_named_child(self.directory, self.directory.parent.resolve())
        self._identity = _identity(self.directory)

    def _make(self) -> None:
        """Create the directory if it is absent, then prove it.

        Through `_ensure_dir`, not `mkdir`, because `mkdir(exist_ok=True)` on a
        path that is ALREADY a symlink to a real directory neither creates
        anything nor raises -- it succeeds, silently, on someone else's
        directory.
        """
        self.directory.parent.mkdir(parents=True, exist_ok=True)
        _ensure_dir(self.directory, self.directory.parent.resolve())
        self._prove()

    def mkdir(self) -> None:
        """Put the directory back if something removed it, and re-pin it.

        Not just convenience. `CasSession._append` recreates the log's
        directory before every append, because a session whose workspace was
        deleted underneath it should not lose the record of a cell that has
        already run. A fresh directory has a fresh inode, so without re-pinning
        here the next write would be refused for the rest of the session by a
        guard that is comparing against a directory nobody can reach any more.
        """
        if self.directory.is_dir() and not self.directory.is_symlink():
            return
        self._make()

    def confirm(self) -> None:
        """Refuse unless the directory is still the one that was proven.

        One `stat` of the whole path: the kernel resolves every component,
        including any symlink or Windows junction swapped in since, and what
        comes back is the identity of wherever the path leads NOW.
        """
        current = _identity(self.directory)
        if self._identity is not None:
            if current != self._identity:
                raise LayoutError(
                    f"{self.directory} is no longer the directory it was proven to be "
                    f"(it now resolves to {self.directory.resolve()}); refusing to write through it"
                )
            return
        # Nothing pinned. Either the filesystem does not number files -- some
        # Windows ones report zero, and a comparison of zeros accepts
        # everything -- or the directory did not exist yet when this guard was
        # made, which is ordinary: a CAS session is constructed before its log
        # directory is. Pay for the full resolution rather than pretend the
        # cheap check happened, and pin whatever is there now for next time.
        resolved = self.directory.resolve()
        if resolved != self._resolved:
            raise LayoutError(
                f"{self.directory} now resolves to {resolved}, not {self._resolved}; "
                "refusing to write through it"
            )
        self._identity = current

    def path(self, name: str) -> Path:
        """Where `name` lives, without proving anything about it.

        For a caller that must show or store the path (a report, an error
        message). Writing to it is what `open` and `write_json` are for.
        """
        return self.directory / _name(name)

    def reserve(self, name: str) -> Path:
        """`name`'s path, proven writable at this instant.

        For a writer Hardy does not own the file handle of -- `os.replace`
        onto a target, or a library that opens the file itself. Everything the
        guard can check is checked; what it cannot promise is that nothing
        moves between this returning and the write, which is exactly why every
        writer that CAN take a file object uses `open` instead.
        """
        self.confirm()
        return _leaf(self.directory / _name(name))

    def open(self, name: str, mode: str = "r", **kwargs: Any) -> Any:
        """Open `name` inside this directory, or refuse.

        Reads go through here too, not only writes. A symlinked `cells.jsonl`
        that were read at load time and refused only at append time would have
        Hardy parse a file from outside the project as its own history, and
        report the refusal about a record it had already answered from.
        """
        path = self.reserve(name)

        def opener(target: str, flags: int) -> int:
            # The flags builtin `open` computed for the mode, plus the refusal
            # to traverse a final symlink. Racing the `lstat` in `_leaf` is the
            # only way to reach the `ELOOP` below, and it comes back as the
            # same `LayoutError` that the check would have raised.
            return os.open(target, flags | _NOFOLLOW)

        try:
            return open(path, mode, opener=opener, **kwargs)  # noqa: PTH123 - `opener` is the point
        except OSError as error:
            if _NOFOLLOW and error.errno in {errno.ELOOP, errno.EMLINK}:
                raise LayoutError(f"{path} became a symlink while it was being opened; refusing to write through it") from None
            raise

    def write_bytes(self, name: str, content: bytes, *, sync: bool = True) -> None:
        """Replace `name` with `content`, whole or not at all.

        The temporary matters as much as the target here, and a fixed
        `<name>.tmp` was the hole: a repository that ships `session.json.tmp`
        as a symlink gets the new bytes written straight THROUGH it, and the
        `os.replace` afterwards renames the link over the record -- so the
        escape succeeds even though the rename itself followed nothing.
        `NamedTemporaryFile` opens with `O_CREAT | O_EXCL` under a name nobody
        can have shipped, which fails outright on an existing path of any kind,
        symlink included.

        The target is guarded even so. Replacing it would not follow a link --
        `os.replace` renames over the link itself -- but it would silently
        delete something a user put there, and Hardy would rather say so.

        Bytes, not `write_text`: that translates `\n` to `\r\n` on Windows, so
        the same record would be one file on Linux and a different one there,
        and any digest of it would disagree across a shared repository.
        """
        target = self.reserve(name)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory, delete=False) as handle:
                temporary = handle.name
                handle.write(content)
                if sync:
                    handle.flush()
                    os.fsync(handle.fileno())
            os.replace(temporary, target)
            temporary = None
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def write_from(self, name: str, source: Path, *, sync: bool = True) -> None:
        """Replace `name` with the contents of `source`, whole or not at all.

        `write_bytes` for a file too big to hold. `writeup.pdf` is the one
        output nothing bounds -- a long document with embedded figures is
        legitimately enormous, and reading it whole to hand it to
        `write_bytes` made a successful compile able to end the session
        instead of returning a result. There is nothing to parse here and no
        reason to have it all at once.

        Bounded rather than streamed was the alternative and it is the wrong
        one for this file: an oversized `.aux` is refused because half of it
        is a wrong answer about what was cited, while a PDF is copied rather
        than read, so a size limit would only refuse a document the compiler
        really made.

        The same temporary-and-rename as `write_bytes`, for the same reasons:
        `NamedTemporaryFile` cannot be a name a repository shipped, and the
        rename means a reader never sees half a document.
        """
        target = self.reserve(name)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory, delete=False) as handle:
                temporary = handle.name
                with source.open("rb") as reading:
                    shutil.copyfileobj(reading, handle)
                if sync:
                    handle.flush()
                    os.fsync(handle.fileno())
            os.replace(temporary, target)
            temporary = None
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def unlink(self, name: str, *, missing_ok: bool = False) -> None:
        """Remove `name` from this directory, or refuse.

        Deleting needs the same proof writing does. `delete_file` reaches a
        model-chosen path, and `<problem>/tex/sections -> $HOME` turned that
        tool into one that unlinks a file in the user's home directory --
        `os.unlink` never follows a final symlink, but every DIRECTORY
        component on the way to it is followed, which is the whole escape.
        `confirm` is what re-proves those components; `_leaf` refuses a link in
        the last position as well, since nothing behind this guard has any
        business being one.
        """
        self.confirm()
        _leaf(self.directory / _name(name)).unlink(missing_ok=missing_ok)

    def write_json(self, name: str, value: Any) -> None:
        """Replace `name` with `value` as JSON, whole or not at all.

        Not fsynced, unlike `write_bytes`'s default. `session.json` is rewritten
        on every tool call that touches the manifest, and a flush to the
        platter per call is a cost paid on the common path for a guarantee the
        record does not need: it is derived from the transcript beside it, and
        the rename is atomic whether or not the bytes have reached the disk.
        """
        self.write_bytes(name, (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8"), sync=False)


def guard_for(base: Path, relative: str | PurePosixPath, *, create: bool = False) -> tuple[WriteGuard, str]:
    """A guard for the directory a nested file lands in, and that file's name.

    `Layout.ensure` proves `lean/` and `tex/` are the problem's own children
    and then stops, because it cannot know which subdirectories a development
    will grow. The paths reaching those two trees are multi-component and
    model-chosen -- `Escape/Owned.lean`, `sections/one.tex` -- so the proof has
    to be carried the rest of the way down.

    One guard per component, not one guard on the leaf directory. A single
    guard on `lean/Escape` would ACCEPT `lean/Escape -> /tmp/OUTSIDE`: the rule
    is "resolved, this is its own parent's immediate child", and
    `/tmp/OUTSIDE`'s parent really is what `lean/Escape` resolves to, so the
    check passes and the file lands outside the root with content the model
    chose entirely. Proving each component against the one above it is what
    makes the chain say something about `base`. Reproduced before this existed:
    `lean/Escape -> /tmp/OUTSIDE` plus `save_lean("Escape/Owned.lean", ...)`
    wrote `/tmp/OUTSIDE/Owned.lean`.

    `create=True` makes the intermediate directories as it goes, which is what
    a save into a new subdirectory needs; `create=False` proves what is there
    without bringing anything into existence.
    """
    parts = PurePosixPath(str(relative).replace("\\", "/")).parts
    if not parts:
        raise LayoutError(f"{relative!r} does not name a file inside {base}")
    guard = WriteGuard(base, create=create)
    for part in parts[:-1]:
        guard = WriteGuard(guard.directory / _name(part), create=create)
    return guard, _name(parts[-1])


def read_text(base: Path, relative: str | PurePosixPath, *, encoding: str = "utf-8", errors: str | None = None) -> str:
    """The text of a file inside `base`, proven to be that file.

    READS, not only writes. `LeanWorkspace.sources()` used `Path.read_text`,
    which follows a symlink without a word: a repository shipping
    `lean/Imported.lean -> ~/notes/Scratch.lean` had Hardy discover that host
    file as a workspace module, compile it, AUDIT it, and save a kernel-checked
    theorem whose source is not in the versioned problem at all -- and which
    changes or vanishes on the next machine. An audit that believes something
    it cannot check is the one failure this whole module exists to prevent, so
    every read of a project file goes through the same proof its writes do.
    """
    guard, name = guard_for(base, relative)
    with guard.open(name, "r", encoding=encoding, errors=errors) as handle:
        return handle.read()


def read_bytes(base: Path, relative: str | PurePosixPath) -> bytes:
    """The bytes of a file inside `base`, proven to be that file.

    `read_text`'s counterpart, for a project tree that is not all text. The
    writeup is copied into a LaTeX scratch directory before every compile, and
    that tree carries figures and `.bib` files beside the `.tex` -- decoding
    those to copy them would corrupt them, and copying them with
    `shutil.copytree` is what followed a link out of the project in the first
    place.
    """
    guard, name = guard_for(base, relative)
    with guard.open(name, "rb") as handle:
        return handle.read()


def files_under(base: Path, suffix: str) -> tuple[PurePosixPath, ...]:
    """Every file beneath `base` whose name ends in `suffix`, or a refusal.

    `Path.rglob` reports a symlinked entry as an ordinary match and descends
    through a symlinked directory as if it were one, so discovery was the way
    a host file entered the workspace even after every writer was guarded.
    This refuses instead of skipping: a symlink in a source tree is a
    repository saying something Hardy cannot honour, and quietly leaving it out
    would make the tree Hardy compiles differ from the tree a reader sees.

    Sorted, because callers build digests and build orders from the result and
    those must not depend on directory order.
    """
    found: list[PurePosixPath] = []
    _collect(base, base, suffix, found)
    return tuple(sorted(found))


def _collect(base: Path, directory: Path, suffix: str, found: list[PurePosixPath]) -> None:
    """Walk one directory, refusing any symlink met on the way."""
    with os.scandir(directory) as entries:
        listing = sorted(entries, key=lambda entry: entry.name)
    for entry in listing:
        path = Path(entry.path)
        if entry.is_symlink():
            raise LayoutError(f"{path} is a symlink; refusing to read or write through it")
        if entry.is_dir():
            _collect(base, path, suffix, found)
        elif entry.name.endswith(suffix):
            found.append(PurePosixPath(path.relative_to(base).as_posix()))


def _name(name: str) -> str:
    """`name`, refusing anything that is not a single file name.

    A guard that accepted `../elsewhere` would be a guard in name only. This
    is the same rule `validate_slug` applies, minus the parts that are about a
    project slug in particular.
    """
    text = str(name)
    if not text or text in {".", ".."}:
        raise LayoutError(f"{name!r} is not a file name")
    if "/" in text or "\\" in text or os.sep in text or (os.altsep and os.altsep in text):
        raise LayoutError(f"a guarded file is one name, not a path: {name!r}")
    # One `Path`, not two: this runs on every append, and building the same
    # object twice to ask it two questions is the kind of cost that is
    # invisible per call and measurable per session.
    candidate = Path(text)
    if candidate.is_absolute() or candidate.name != text:
        raise LayoutError(f"a guarded file is one name, not a path: {name!r}")
    return text


def _leaf(path: Path) -> Path:
    """`path`, refused outright if it is a symlink.

    Not resolved and compared, the way a directory is. Every file behind this
    guard is one Hardy itself creates and rewrites, so there is no legitimate
    reason for one to be a link -- and asking where the link led would invite
    the answer "somewhere acceptable", which is not a question worth having an
    answer to for a file nobody should have replaced.
    """
    if path.is_symlink():
        # Where it led, when the platform will say. `os.readlink` can itself
        # fail -- a link removed between the two calls, a Windows reparse point
        # it declines to read -- and an `OSError` escaping here would turn a
        # refusal that is supposed to be one clean sentence into a traceback.
        try:
            target: object = os.readlink(path)
        except OSError:
            target = "somewhere it will not say"
        raise LayoutError(f"{path} is a symlink to {target}; refusing to read or write through it")
    return path


def _identity(directory: Path) -> tuple[int, int] | None:
    """What `directory` is right now, or None if it will not say.

    None covers two cases, and `confirm` treats them the same way -- by
    resolving in full instead. `st_ino` is zero on filesystems that do not
    number files (some Windows ones do not), and a pair of zeros would compare
    equal to every other pair of zeros, which is a check that accepts
    everything. A directory that is not there yet has no identity at all, and
    that is not an error to raise here: a caller writing into a directory that
    does not exist gets the `OSError` from the write, which is the failure it
    already knows how to answer.
    """
    try:
        status = directory.stat()
    except OSError:
        return None
    if not status.st_ino:
        return None
    return (status.st_dev, status.st_ino)
