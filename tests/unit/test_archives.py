"""A downloaded source bundle is arbitrary third-party bytes.

Every test here is a way an archive can hurt the machine that unpacks it --
a path that climbs out of the extraction root, a link that points out of it,
a stream that inflates without bound -- and the property under test is the
same each time: the archive is refused by name and nothing is left behind.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import tarfile
from pathlib import Path

import pytest

from hardy import archives


def _tar(*members: tuple[str, bytes | None, str], compress: bool = True) -> bytes:
    """A tarball whose members are (name, content, type).

    `type` is one of `file`, `dir`, `symlink`, `hardlink`, `chr`, `fifo`; the
    content of a link is its target.
    """
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz" if compress else "w") as tar:
        for name, content, kind in members:
            info = tarfile.TarInfo(name)
            if kind == "file":
                info.type = tarfile.REGTYPE
                info.size = len(content or b"")
                tar.addfile(info, io.BytesIO(content or b""))
                continue
            info.type = {
                "dir": tarfile.DIRTYPE,
                "symlink": tarfile.SYMTYPE,
                "hardlink": tarfile.LNKTYPE,
                "chr": tarfile.CHRTYPE,
                "fifo": tarfile.FIFOTYPE,
            }[kind]
            if kind in {"symlink", "hardlink"}:
                info.linkname = (content or b"").decode()
            tar.addfile(info)
    return buffer.getvalue()


def _files(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


@pytest.fixture
def into(tmp_path: Path) -> Path:
    staging = tmp_path / "staging"
    staging.mkdir()
    return staging


# --- What a well-formed bundle becomes ------------------------------------------


def test_a_source_bundle_extracts_with_a_digest_per_file(into: Path) -> None:
    archive = _tar(
        ("main.tex", b"\\documentclass{article}\n", "file"),
        ("figures/", None, "dir"),
        ("figures/plot.png", b"\x89PNG\x00\x00binary", "file"),
    )

    result = archives.extract(archive, into)

    assert result.kind == "tar"
    assert {item.path for item in result.files} == {"main.tex", "figures/plot.png"}
    assert _files(into) == {"main.tex", "figures/plot.png"}
    main = next(item for item in result.files if item.path == "main.tex")
    assert main.size == len(b"\\documentclass{article}\n")
    assert main.sha256 == hashlib.sha256(b"\\documentclass{article}\n").hexdigest()
    assert main.text is True
    plot = next(item for item in result.files if item.path == "figures/plot.png")
    assert plot.text is False


def test_a_gzipped_single_file_is_the_main_tex(into: Path) -> None:
    """arXiv serves a one-file submission as the gzip of the file itself."""
    result = archives.extract(gzip.compress(b"\\documentclass{article}\n"), into)

    assert result.kind == "gzip"
    assert [item.path for item in result.files] == ["main.tex"]
    assert (into / "main.tex").read_bytes() == b"\\documentclass{article}\n"


def test_a_pdf_only_submission_is_kept_as_the_pdf(into: Path) -> None:
    result = archives.extract(b"%PDF-1.7\n%binary\n", into)

    assert result.kind == "pdf"
    assert [item.path for item in result.files] == ["paper.pdf"]
    assert result.files[0].text is False


def test_an_uncompressed_tar_is_accepted(into: Path) -> None:
    result = archives.extract(_tar(("main.tex", b"x", "file"), compress=False), into)

    assert result.kind == "tar"
    assert _files(into) == {"main.tex"}


def test_bytes_that_are_none_of_these_are_refused(into: Path) -> None:
    with pytest.raises(archives.ArchiveError, match="not a gzip, tar, or PDF"):
        archives.extract(b"<html>maintenance</html>", into)
    assert _files(into) == set()


def test_an_empty_archive_is_refused(into: Path) -> None:
    with pytest.raises(archives.ArchiveError, match="no files"):
        archives.extract(_tar(("figures/", None, "dir")), into)


# --- Paths ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "../escape.tex",
        "figures/../../escape.tex",
        "/etc/passwd",
        "C:/windows/system.ini",
        "C:\\windows\\system.ini",
        "figures\\plot.png",
        "./",
        "..",
    ],
)
def test_a_member_path_that_could_leave_the_root_is_refused(into: Path, name: str) -> None:
    with pytest.raises(archives.ArchiveError) as caught:
        archives.extract(_tar(("main.tex", b"ok", "file"), (name, b"evil", "file")), into)

    assert b"evil" not in b"".join(p.read_bytes() for p in into.rglob("*") if p.is_file())
    assert name in str(caught.value) or "path" in str(caught.value)
    assert not (into.parent / "escape.tex").exists()


def test_a_member_path_carrying_a_nul_is_refused() -> None:
    """Not reachable end to end -- `tarfile`'s own writer truncates a name at
    the NUL -- so the rule is stated where it is enforced. A reader that
    stops at the NUL and one that does not disagree about which file a
    member names, and this refuses rather than picking a side."""
    with pytest.raises(archives.ArchiveError, match="NUL"):
        archives.member_path("bad\x00name.tex")


def test_a_path_deeper_than_the_limit_is_refused(into: Path) -> None:
    deep = "/".join(["d"] * (archives.Limits().max_depth + 1)) + "/x.tex"
    with pytest.raises(archives.ArchiveError, match="deep"):
        archives.extract(_tar((deep, b"x", "file")), into)


def test_a_duplicate_member_is_refused(into: Path) -> None:
    """Two entries for one name: a naive extractor keeps whichever came last."""
    with pytest.raises(archives.ArchiveError, match="twice"):
        archives.extract(_tar(("main.tex", b"one", "file"), ("main.tex", b"two", "file")), into)


def test_a_file_through_an_existing_file_is_refused(into: Path) -> None:
    with pytest.raises(archives.ArchiveError, match="through"):
        archives.extract(_tar(("a.tex", b"x", "file"), ("a.tex/b.tex", b"y", "file")), into)


# --- Members that are not plain files ------------------------------------------


@pytest.mark.parametrize(
    ("kind", "target"),
    [("symlink", "/etc/passwd"), ("symlink", "main.tex"), ("hardlink", "main.tex"), ("chr", ""), ("fifo", "")],
)
def test_anything_but_a_file_or_a_directory_is_refused(into: Path, kind: str, target: str) -> None:
    with pytest.raises(archives.ArchiveError) as caught:
        archives.extract(
            _tar(("main.tex", b"ok", "file"), ("link.tex", target.encode(), kind)), into
        )

    assert "link.tex" in str(caught.value)
    assert not (into / "link.tex").exists() and not (into / "link.tex").is_symlink()


# --- Quotas ---------------------------------------------------------------------


def test_more_files_than_the_quota_are_refused(into: Path) -> None:
    members = tuple((f"f{index}.tex", b"x", "file") for index in range(4))
    with pytest.raises(archives.ArchiveError, match="entries"):
        archives.extract(_tar(*members), into, limits=archives.Limits(max_files=3))


def test_more_bytes_than_the_quota_are_refused_on_the_inflated_stream(into: Path) -> None:
    """A megabyte of zeros is a few hundred compressed bytes. The quota is on
    what comes out, not on what went in."""
    archive = _tar(("zeros.tex", b"\0" * (1024 * 1024), "file"))
    assert len(archive) < 8 * 1024
    with pytest.raises(archives.ArchiveError, match="bytes"):
        archives.extract(archive, into, limits=archives.Limits(max_total_bytes=64 * 1024))


def test_a_single_member_over_its_own_limit_is_refused(into: Path) -> None:
    """Under the total quota and over its own: one enormous file in an
    otherwise ordinary bundle is still refused."""
    archive = _tar(("a.tex", b"a" * 100, "file"), ("b.tex", b"b" * 200, "file"))
    with pytest.raises(archives.ArchiveError, match="b.tex"):
        archives.extract(
            archive, into, limits=archives.Limits(max_file_bytes=150, max_total_bytes=10_000)
        )


def test_a_gzip_bomb_that_is_not_a_tar_is_bounded_too(into: Path) -> None:
    with pytest.raises(archives.ArchiveError, match="bytes"):
        archives.extract(
            gzip.compress(b"\0" * (1024 * 1024)), into, limits=archives.Limits(max_total_bytes=4096)
        )


def test_a_header_size_counts_against_the_quota_before_the_body_is_read(into: Path) -> None:
    """The header says 10 bytes and the quota is 5: refused on the header,
    before a byte of the body is copied out."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        info = tarfile.TarInfo("small.tex")
        info.size = 10
        tar.addfile(info, io.BytesIO(b"0123456789"))
    with pytest.raises(archives.ArchiveError):
        archives.extract(buffer.getvalue(), into, limits=archives.Limits(max_total_bytes=5))


# --- Nothing left behind ---------------------------------------------------------


def test_a_refused_archive_leaves_nothing_under_the_root(into: Path) -> None:
    archive = _tar(("main.tex", b"ok", "file"), ("figures/", None, "dir"), ("../x", b"x", "file"))
    with pytest.raises(archives.ArchiveError):
        archives.extract(archive, into)

    assert list(into.iterdir()) == []


def test_directories_count_against_the_file_quota(into: Path) -> None:
    """A directory member costs an inode and a `mkdir` like anything else.
    Counting only files let 282 KB of headers make sixty thousand of them,
    which then land in the library permanently."""
    members = tuple((f"d{index}/", None, "dir") for index in range(50))
    with pytest.raises(archives.ArchiveError, match="entries|files"):
        archives.extract(
            _tar(*members, ("main.tex", b"x", "file")),
            into,
            limits=archives.Limits(max_files=10),
        )


def test_a_file_landing_on_an_implicitly_created_parent_is_refused(into: Path) -> None:
    """`a/b.tex` makes `a` on the way past, so a later file member named `a`
    hit `IsADirectoryError` instead of this module's own refusal."""
    with pytest.raises(archives.ArchiveError, match="through|directory"):
        archives.extract(_tar(("a/b.tex", b"x", "file"), ("a", b"y", "file")), into)


def test_a_file_whose_binary_half_starts_late_is_not_called_text(into: Path) -> None:
    """An EPS figure with a long ASCII header is the ordinary case in a real
    bundle, not an attack. Sampling the first 8 KiB called it text, and
    `read_paper` then served a megabyte of mojibake."""
    # An ASCII header far longer than any prefix a sampler would read.
    body = b"%!PS-Adobe-3.0\n" + b"% comment\n" * 1200 + b"\x00\x80\xff" * 4000
    assert body.index(b"\x00") > 8 * 1024, "the ASCII run must outlast any sampling prefix"

    result = archives.extract(_tar(("fig.eps", body, "file")), into)

    assert result.files[0].text is False


def test_a_file_that_is_text_all_the_way_through_is_text(into: Path) -> None:
    body = b"\\documentclass{article}\n" + b"% a long preamble\n" * 2000

    result = archives.extract(_tar(("main.tex", body, "file")), into)

    assert result.files[0].text is True


def test_a_trailing_byte_that_cannot_be_utf8_is_not_text(into: Path) -> None:
    result = archives.extract(_tar(("odd.tex", b"abc\xff", "file")), into)

    assert result.files[0].text is False
