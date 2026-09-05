r"""Unpack a downloaded archive as if it were trying to hurt the machine.

An arXiv source bundle is arbitrary third-party data, and unpacking one with
`tarfile.extractall` is the textbook way to be hurt by it: a member named
`../../.ssh/authorized_keys`, a symlink to `/etc` followed by a file "inside"
it, a header claiming a size the stream never delivers, a few hundred
compressed bytes that inflate to gigabytes. Hardy has no process isolation to
fall back on (#84), so every one of those has to be refused here, by name,
with nothing left behind.

The rules, each of which has a test:

- A member path is normalised to a relative POSIX path with no `..`, no
  leading `/`, no drive letter, no backslash, no NUL, bounded depth, and it
  may not repeat or pass through a path already written as a file.
- Only regular files and directories are written. Symlinks, hardlinks,
  devices and FIFOs are refused -- not skipped, refused, because an archive
  that carries one is not an archive Hardy wants any part of.
- Quotas on file count, bytes per file and bytes in total are enforced on the
  *decompressed* stream as it is read, not on what the headers claim, and the
  stream itself is bounded so a header that lies about its own size cannot
  make the reader inflate its way through the rest of the archive.
- Anything refused is removed before the error is raised, so the caller's
  staging directory is either the whole extraction or empty.

This module knows nothing about arXiv, records, or digests beyond the one it
computes per file. `arxiv.PaperLibrary.admit_source` is what stages a
directory, calls `extract` into it, and moves the result into the library
with one rename.

What this is not: a sandbox. Nothing here is executed or compiled -- the
files are text for a reader and an inventory -- and the documentation says so
rather than letting a careful unpacker read as isolation.
"""

from __future__ import annotations

import codecs
import gzip
import hashlib
import io
import re
import shutil
import tarfile
import zlib
from dataclasses import dataclass
from pathlib import Path

READ_CHUNK_BYTES = 64 * 1024
#: The name a one-file gzip submission is stored under. arXiv serves a
#: single-file source as the gzip of that file, with no tar around it and no
#: name, and the file is the TeX source by arXiv's own convention.
SINGLE_FILE_NAME = "main.tex"
PDF_NAME = "paper.pdf"

DRIVE = re.compile(r"^[A-Za-z]:")
TAR_MAGIC_OFFSET = 257
TAR_MAGIC = b"ustar"


class ArchiveError(ValueError):
    """An archive Hardy will not unpack, and the reason in one sentence."""


@dataclass(frozen=True)
class Limits:
    """What one archive may produce. Generous for a paper, fatal for a bomb."""

    max_files: int = 4_000
    max_file_bytes: int = 64 * 1024 * 1024
    max_total_bytes: int = 256 * 1024 * 1024
    max_depth: int = 16
    #: Bounds are in bytes, not characters: ext4 bounds a name at 255
    #: bytes and Linux bounds a path at 4096, so a name of 255 two-byte
    #: characters is twice what the filesystem will take. Counting
    #: characters let that member through to an `OSError` no caller of
    #: this module catches.
    max_component_bytes: int = 255
    #: The assembled relative path. Generous for a paper, and far enough
    #: under `PATH_MAX` to leave room for whatever root it is joined to.
    max_path_bytes: int = 1024

    @property
    def stream_bytes(self) -> int:
        """The most decompressed bytes the reader will pull from the stream.

        The file quota plus room for every member's headers. A pax or GNU long
        name header carries a size of its own that `tarfile` reads whole, so
        the stream is bounded independently of the per-file accounting: a
        header claiming eight gigabytes for a name stops here, not in memory.
        """
        return self.max_total_bytes + (self.max_files + 1) * 3 * 512


#: The default quotas, as one shared value. Frozen and stateless, so every
#: caller that does not pass its own gets this object rather than an
#: equivalent one built per call.
DEFAULT_LIMITS = Limits()


@dataclass(frozen=True)
class ExtractedFile:
    path: str
    size: int
    sha256: str
    text: bool


@dataclass(frozen=True)
class Extraction:
    kind: str  # "tar" | "gzip" | "pdf"
    files: tuple[ExtractedFile, ...]


class _Bounded(io.RawIOBase):
    """A read-only stream that refuses to yield more than `limit` bytes.

    Wrapped around the decompressor rather than around each member, so the
    bound covers what `tarfile` reads for its own purposes -- headers,
    padding, the pax records it parses in memory -- and not only the member
    bodies this module copies out.
    """

    def __init__(self, inner, limit: int, what: str) -> None:
        self._inner = inner
        self._limit = limit
        self._read = 0
        self._what = what

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:  # pragma: no cover - `read` is what tarfile uses
        chunk = self.read(len(buffer))
        buffer[: len(chunk)] = chunk
        return len(chunk)

    def read(self, size: int = -1) -> bytes:
        wanted = READ_CHUNK_BYTES if size is None or size < 0 else size
        try:
            chunk = self._inner.read(wanted)
        except (OSError, EOFError, zlib.error, gzip.BadGzipFile) as error:
            raise ArchiveError(f"the {self._what} is corrupt: {error}") from error
        self._read += len(chunk)
        if self._read > self._limit:
            raise ArchiveError(
                f"the {self._what} inflates past {self._limit} bytes; refusing to read further"
            )
        return chunk


def kind_of(data: bytes) -> str:
    """Which of the three shapes arXiv serves these bytes are, or a refusal."""
    if data[:2] == b"\x1f\x8b":
        return "gzip"
    if data[:5] == b"%PDF-":
        return "pdf"
    if len(data) > TAR_MAGIC_OFFSET + len(TAR_MAGIC) and data[
        TAR_MAGIC_OFFSET : TAR_MAGIC_OFFSET + len(TAR_MAGIC)
    ] == TAR_MAGIC:
        return "tar"
    raise ArchiveError("the download is not a gzip, tar, or PDF; refusing to unpack it")


def extract(data: bytes, into: Path, *, limits: Limits = DEFAULT_LIMITS) -> Extraction:
    """Unpack `data` into `into`, an existing empty directory, or refuse.

    Refusal removes everything this call wrote under `into`, so the caller
    holds either a complete extraction or an empty directory and never the
    prefix of one.
    """
    kind = kind_of(data)
    try:
        if kind == "pdf":
            # Never text, whatever its first bytes decode to. A short PDF's
            # header and object table are ASCII, so sampling would call one
            # text and invite a reader to quote a file that is a container
            # for compressed streams rather than prose.
            files = (_write_whole(into, PDF_NAME, io.BytesIO(data), limits, text=False),)
            return Extraction("pdf", files)
        if kind == "tar":
            stream = _Bounded(io.BytesIO(data), limits.stream_bytes, "archive")
            return Extraction("tar", _extract_tar(stream, into, limits))
        # gzip: a tar inside it, or one bare file.
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as peek:
            head = _Bounded(peek, limits.stream_bytes, "gzip stream").read(512)
        inner = gzip.GzipFile(fileobj=io.BytesIO(data))
        stream = _Bounded(inner, limits.stream_bytes, "gzip stream")
        if head[TAR_MAGIC_OFFSET : TAR_MAGIC_OFFSET + len(TAR_MAGIC)] == TAR_MAGIC:
            return Extraction("tar", _extract_tar(stream, into, limits))
        return Extraction("gzip", (_write_whole(into, SINGLE_FILE_NAME, stream, limits),))
    except tarfile.TarError as error:
        _clear(into)
        raise ArchiveError(f"the archive is corrupt: {error}") from error
    except ArchiveError:
        _clear(into)
        raise
    except OSError as error:
        # The path bounds above are what should stop this, but the filesystem
        # has the last word on what it will take, and an `OSError` out of here
        # ends the model's turn: no caller of this module catches one. A
        # refusal by name is the promise the module makes, so it keeps it even
        # when the refusal comes from the kernel.
        _clear(into)
        raise ArchiveError(f"the archive could not be written: {error}") from error
    except BaseException:
        _clear(into)
        raise


def member_path(name: str, limits: Limits = DEFAULT_LIMITS) -> str:
    """The relative POSIX path a member may be written to, or a refusal.

    Everything a path can do to leave the root is refused rather than
    repaired: a `..` is not stripped, a leading `/` is not dropped, a drive
    letter is not ignored. An archive that carries one of those was not
    written for Hardy to unpack, and "repaired" paths are how two members
    come to land on one file.
    """
    if "\x00" in name:
        raise ArchiveError("a member path contains a NUL byte")
    if "\\" in name:
        raise ArchiveError(f"the member path {name!r} contains a backslash")
    if DRIVE.match(name):
        raise ArchiveError(f"the member path {name!r} carries a drive letter")
    if name.startswith("/"):
        raise ArchiveError(f"the member path {name!r} is absolute")
    parts = [part for part in name.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ArchiveError(f"the member path {name!r} climbs out of the archive root")
    if not parts:
        raise ArchiveError(f"the member path {name!r} names nothing")
    if len(parts) > limits.max_depth:
        raise ArchiveError(f"the member path {name!r} is nested too deep")
    if any(len(part.encode("utf-8")) > limits.max_component_bytes for part in parts):
        raise ArchiveError(f"a component of the member path {name!r} is too long")
    path = "/".join(parts)
    if len(path.encode("utf-8")) > limits.max_path_bytes:
        raise ArchiveError(f"the member path {name!r} is too long")
    return path


def _extract_tar(stream: _Bounded, into: Path, limits: Limits) -> tuple[ExtractedFile, ...]:
    files: list[ExtractedFile] = []
    written: set[str] = set()
    directories: set[str] = set()
    total = 0
    # Stream mode: members are read in order and never seeked back to, which
    # is what lets the bounded reader above be the only source of bytes.
    with tarfile.open(fileobj=stream, mode="r|") as tar:
        for member in tar:
            path = member_path(member.name, limits)
            if member.isdir():
                _no_file_on_the_way(path, written, include_self=True)
                if len(files) + len(directories) + 1 > limits.max_files:
                    raise ArchiveError(
                        f"the archive holds more than {limits.max_files} entries"
                    )
                # Every component, not just the leaf: a member `a/b/c/` makes
                # three directories, and counting one let a bounded number of
                # members produce an unbounded number of inodes.
                directories.update(_prefixes(path))
                (into / path).mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                what = (
                    "a symlink"
                    if member.issym()
                    else "a hardlink"
                    if member.islnk()
                    else "a device or FIFO"
                )
                raise ArchiveError(
                    f"{member.name!r} is {what}; only files and directories are unpacked"
                )
            if path in written:
                raise ArchiveError(f"the archive names {path!r} twice")
            if path in directories:
                raise ArchiveError(
                    f"the archive names {path!r} as a file and as a directory"
                )
            _no_file_on_the_way(path, written, include_self=False)
            if len(files) + len(directories) + 1 > limits.max_files:
                raise ArchiveError(f"the archive holds more than {limits.max_files} entries")
            if member.size > limits.max_file_bytes:
                raise ArchiveError(
                    f"{path!r} is {member.size} bytes, over the {limits.max_file_bytes}-byte "
                    "limit for one file"
                )
            if total + member.size > limits.max_total_bytes:
                raise ArchiveError(
                    f"the archive inflates past {limits.max_total_bytes} bytes in total"
                )
            body = tar.extractfile(member)
            if body is None:
                raise ArchiveError(f"{path!r} has no readable body")
            files.append(_write(into, path, body, limits, member.size))
            written.add(path)
            # The parents `_write` made on the way past. Without them a later
            # member named `a` -- after `a/b.tex` created `a` -- reached the
            # open as a directory and raised `IsADirectoryError` from three
            # frames down instead of this module's own refusal.
            directories.update(_prefixes(path)[:-1])
            total += files[-1].size
    if not files:
        raise ArchiveError("the archive holds no files")
    return tuple(sorted(files, key=lambda item: item.path))


def _prefixes(path: str) -> list[str]:
    """Every directory prefix of `path`, and `path` itself, outermost first."""
    parts = path.split("/")
    return ["/".join(parts[: index + 1]) for index in range(len(parts))]


def _no_file_on_the_way(path: str, written: set[str], *, include_self: bool) -> None:
    parts = path.split("/")
    stop = len(parts) if include_self else len(parts) - 1
    for index in range(1, stop + 1):
        prefix = "/".join(parts[:index])
        if prefix in written:
            raise ArchiveError(f"{path!r} passes through {prefix!r}, which is a file")


def _write_whole(
    into: Path, name: str, stream, limits: Limits, *, text: bool | None = None
) -> ExtractedFile:
    found = _write(into, name, stream, limits, None, text=text)
    if found.size == 0:
        raise ArchiveError("the archive holds no files")
    return found


def _write(
    into: Path,
    path: str,
    stream,
    limits: Limits,
    declared: int | None,
    *,
    text: bool | None = None,
) -> ExtractedFile:
    """Copy one member out in chunks, counting what actually arrives."""
    target = into / path
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    ceiling = min(limits.max_file_bytes, limits.max_total_bytes)
    # Decided as the bytes go past, over every one of them. The digest loop
    # already sees the whole file, so this costs a decoder rather than a
    # second read.
    decoder = codecs.getincrementaldecoder("utf-8")()
    textual = True
    with open(target, "wb") as handle:
        while True:
            chunk = stream.read(READ_CHUNK_BYTES)
            if not chunk:
                break
            size += len(chunk)
            if size > ceiling or (declared is not None and size > declared):
                raise ArchiveError(
                    f"{path!r} delivered more than {ceiling if declared is None else declared} "
                    "bytes; refusing the archive"
                )
            if textual:
                if b"\x00" in chunk:
                    textual = False
                else:
                    try:
                        decoder.decode(chunk)
                    except UnicodeDecodeError:
                        textual = False
            digest.update(chunk)
            handle.write(chunk)
    if textual:
        try:
            # The tail: a multibyte character cut by the last chunk boundary is
            # incomplete, not binary, and only the final call can tell them
            # apart.
            decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            textual = False
    return ExtractedFile(path, size, digest.hexdigest(), textual if text is None else text)


def _clear(into: Path) -> None:
    """Remove everything under `into`, leaving the directory itself."""
    for child in into.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
