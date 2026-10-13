"""Explicit native TeX checks; never part of a corpus or provider sweep."""

import shutil
from pathlib import Path

import pytest

from hardy.foundation.files import WriteGuard
from hardy.foundation.locking import FileInUse

pytestmark = pytest.mark.real_toolchain

DOCUMENT = (
    "% ignored \\begin{document}\n\\documentclass{article}\n"
    "\\newcommand{\\claim}{Claim}\n\\begin{document}\n"
    "\\claim.\\label{old} See \\ref{old}.\n\\end{document}\n"
)


def test_real_fragment_probe_uses_preamble_after_commented_document_marker(session):
    executable = shutil.which("pdflatex")
    if executable is None:
        pytest.skip("pdflatex is not installed")
    session.latex.command = (executable, "-interaction=nonstopmode", "-halt-on-error")
    assert session._save_latex("writeup.tex", DOCUMENT).ok
    result = session._check_latex("one.tex", r"\claim. An unused fragment.")
    assert result.ok, result.output


@pytest.mark.parametrize("held_name", [None, "writeup.pdf", "writeup.aux"])
def test_real_tex_publication_after_compile(session, monkeypatch, held_name):
    executable = shutil.which("pdflatex")
    if executable is None:
        pytest.skip("pdflatex is not installed")
    session.latex.command = (executable, "-interaction=nonstopmode", "-halt-on-error")
    initial = session._save_latex("writeup.tex", DOCUMENT)
    assert initial.ok, initial.output
    old_signature = session.state["tex_signature"]
    old_pdf = (session.workspace / "writeup.pdf").read_bytes()
    old_aux = (session.workspace / ".build/tex/writeup.aux").read_bytes()
    original = WriteGuard.write_from
    reached = []

    def held(self, name, incoming, **kwargs):
        if name == held_name:
            # This failure happens only after the actual compiler made its
            # derived outputs, rather than before compilation starts.
            assert (Path(incoming).parent / "writeup.pdf").is_file()
            reached.append(name)
            raise FileInUse(f"{name} held after compilation")
        return original(self, name, incoming, **kwargs)

    monkeypatch.setattr(WriteGuard, "write_from", held)
    revised = DOCUMENT.replace("{old}", "{new}").replace("Claim}", "Revised}")
    result = session._save_latex("writeup.tex", revised)
    if held_name == "writeup.aux":
        assert not result.ok
        assert session.documents._tex_sources()["writeup.tex"] == DOCUMENT
        assert (session.workspace / ".build/tex/writeup.aux").read_bytes() == old_aux
    else:
        assert result.ok, result.output
        assert session.documents._tex_sources()["writeup.tex"] == revised
        assert "new" in session.documents._labels() and "old" not in session.documents._labels()
    if held_name:
        assert reached == [held_name]
        assert held_name in result.output
        assert session.state["tex_signature"] == old_signature
        assert (session.workspace / "writeup.pdf").read_bytes() == old_pdf
    else:
        assert not session._stale_writeup()
    if held_name == "writeup.pdf":
        assert session._stale_writeup()
        refusal = session._report_result([], "Finished")
        assert not refusal.ok
