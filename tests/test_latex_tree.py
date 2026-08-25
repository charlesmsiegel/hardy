from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from hardy import layout
from hardy.latex import LatexTools

COMMAND = (sys.executable, str(Path(__file__).with_name("fake_latex.py")))
ROOT = "\\documentclass{article}\n\\begin{document}\\input{sections/one}\\end{document}\n"
FINE = "\\documentclass{article}\n\\begin{document}Fine.\\end{document}\n"


def test_input_resolves_against_the_saved_tree(tmp_path: Path):
    tree = tmp_path / "tex"
    (tree / "sections").mkdir(parents=True)
    (tree / "writeup.tex").write_text(ROOT, encoding="utf-8")
    (tree / "sections" / "one.tex").write_text("Section one.\\label{sec:one}\n", encoding="utf-8")
    result = LatexTools(COMMAND).check(
        "Section one, revised.\\label{sec:one}\n", path="sections/one.tex", tree=tree
    )
    assert result.ok


def test_a_candidate_root_overrides_the_saved_one(tmp_path: Path):
    tree = tmp_path / "tex"
    tree.mkdir(parents=True)
    (tree / "writeup.tex").write_text("broken", encoding="utf-8")
    result = LatexTools(COMMAND).check(FINE, tree=tree)
    assert result.ok


def test_check_still_works_with_no_tree(tmp_path: Path):
    assert LatexTools(COMMAND).check(FINE).ok


def test_a_fragment_is_actually_compiled_when_the_root_ignores_it(tmp_path: Path):
    """The fragment-first order the prompt prescribes.

    Compiling the unchanged root would check nothing about the candidate, so
    malformed source would be saved as though it had been checked.
    """
    tree = tmp_path / "tex"
    tree.mkdir(parents=True)
    (tree / "writeup.tex").write_text(FINE, encoding="utf-8")
    broken = LatexTools(COMMAND).check(
        "\\input{nowhere}\n", path="sections/one.tex", tree=tree
    )
    assert not broken.ok, "a fragment the root does not include must still be compiled"
    good = LatexTools(COMMAND).check("Section one.\n", path="sections/one.tex", tree=tree)
    assert good.ok


def test_a_fragment_cannot_be_saved_before_a_root_exists(tmp_path: Path):
    """Otherwise a fragment that happens to be a whole document publishes a PDF
    the durable tree has no root for."""
    tree = tmp_path / "tex"
    tree.mkdir(parents=True)
    result = LatexTools(COMMAND).check(FINE, path="sections/one.tex", tree=tree)
    assert not result.ok
    assert "writeup.tex" in result.output


def test_the_aux_records_the_labels_that_were_created(tmp_path: Path):
    tree = tmp_path / "tex"
    tree.mkdir(parents=True)
    aux = tmp_path / "build"
    document = (
        "\\documentclass{article}\n\\begin{document}\n"
        "Real.\\label{thm:real}\n% \\label{thm:commented}\n\\end{document}\n"
    )
    assert LatexTools(COMMAND).check(document, tree=tree, output_dir=tmp_path, aux_dir=aux).ok
    written = (aux / "writeup.aux").read_text()
    assert "thm:real" in written
    assert "thm:commented" not in written


def test_a_fragment_is_judged_by_the_root_that_includes_it(tmp_path: Path):
    """A fragment has no preamble; compiling it alone would fail for a reason
    that says nothing about the mathematics."""
    tree = tmp_path / "tex"
    (tree / "sections").mkdir(parents=True)
    (tree / "writeup.tex").write_text(ROOT, encoding="utf-8")
    (tree / "sections" / "one.tex").write_text("Section one.\n", encoding="utf-8")
    assert LatexTools(COMMAND).check("Just prose.\n", path="sections/one.tex", tree=tree).ok


def test_the_pdf_lands_in_the_output_directory(tmp_path: Path):
    output = tmp_path / "workspace"
    assert LatexTools(COMMAND).check(FINE, output_dir=output).ok
    assert (output / "writeup.pdf").read_bytes() == b"%PDF-fake"


def test_a_braced_mention_is_not_an_inclusion(tmp_path: Path):
    r"""`{sections/one}` in a comment, or as another command's argument, means
    nothing to TeX. Reading it as an inclusion would leave the fragment
    uncompiled while reporting it as checked."""
    tree = tmp_path / "tex"
    (tree / "sections").mkdir(parents=True)
    (tree / "sections" / "one.tex").write_text("Section one.\n", encoding="utf-8")
    mentioned = (
        "\\documentclass{article}\n"
        "% see \\input{sections/one} for the argument\n"
        "\\begin{document}\\index{sections/one}Body.\\end{document}\n"
    )
    (tree / "writeup.tex").write_text(mentioned, encoding="utf-8")
    # The fragment is not really included, so it must be compiled on its own
    # through a probe root -- and a broken one must fail.
    broken = LatexTools(COMMAND).check("\\input{nowhere}\n", path="sections/one.tex", tree=tree)
    assert not broken.ok


def test_a_real_inclusion_is_recognised(tmp_path: Path):
    tree = tmp_path / "tex"
    (tree / "sections").mkdir(parents=True)
    (tree / "sections" / "one.tex").write_text("Section one.\n", encoding="utf-8")
    (tree / "writeup.tex").write_text(ROOT, encoding="utf-8")
    assert LatexTools(COMMAND).check("Revised.\n", path="sections/one.tex", tree=tree).ok


def test_a_latex_compile_can_be_interrupted(tmp_path: Path):
    """LaTeX drives its own `Popen` rather than going through `run_process`, so
    it can keep the caller's environment -- a TeX installation's own variables
    would not survive `run_process`'s filter. That is exactly why it has to
    register the child itself: without it, Esc stops Lean and the CAS kernel
    but a compile runs on to its timeout, which is not the contract the
    terminal now advertises."""
    import threading
    import time

    from hardy import process

    ready = tmp_path / "tex-started"
    source = (
        "\\documentclass{article}\n"
        f"% slow: 300\n% ready: {ready}\n"
        "\\begin{document}Fine.\\end{document}\n"
    )

    def press_escape() -> None:
        end = time.monotonic() + 30
        while time.monotonic() < end and not ready.exists():
            time.sleep(0.01)
        assert ready.exists(), "the fake LaTeX child never started"
        assert process.interrupt_children() == 1, "the compile was not registered"

    threading.Thread(target=press_escape, daemon=True).start()

    started = time.monotonic()
    result = LatexTools(COMMAND, timeout=300).check(source)

    assert not result.ok
    # Stopped, not judged: reporting the exit status of a compile nobody let
    # finish would read as LaTeX rejecting the document.
    assert "interrupted" in result.output
    assert "timeout" not in result.output
    assert time.monotonic() - started < 30


def test_a_latex_compile_that_refuses_the_interrupt_is_still_stopped(tmp_path: Path):
    """`communicate` cannot be told to stop waiting, so without a watcher a
    compiler that ignores the signal would be waited on for the whole compile
    timeout and then reported as a timeout -- when what happened was a press
    the user made seconds earlier."""
    import threading
    import time

    from hardy import process

    ready = tmp_path / "tex-deaf"
    source = (
        "\\documentclass{article}\n"
        f"% slow: 300\n% deaf\n% ready: {ready}\n"
        "\\begin{document}Fine.\\end{document}\n"
    )

    def press_escape() -> None:
        end = time.monotonic() + 30
        while time.monotonic() < end and not ready.exists():
            time.sleep(0.01)
        assert ready.exists(), "the fake LaTeX child never started"
        assert process.interrupt_children() == 1

    threading.Thread(target=press_escape, daemon=True).start()

    started = time.monotonic()
    result = LatexTools(COMMAND, timeout=300).check(source)
    elapsed = time.monotonic() - started

    assert not result.ok
    assert "interrupted" in result.output
    # It waited out the grace and no more -- not the 300s compile timeout.
    assert 1.5 <= elapsed < 30


def test_a_latex_compile_deaf_to_sigterm_is_killed(tmp_path: Path):
    """A watcher that stopped at SIGTERM left the main thread blocked in
    `communicate` until the compile timeout anyway -- so the press bought
    nothing against the child most in need of stopping."""
    import threading
    import time

    from hardy import process

    ready = tmp_path / "tex-stubborn"
    source = (
        "\\documentclass{article}\n"
        f"% slow: 300\n% deaf: sigterm\n% ready: {ready}\n"
        "\\begin{document}Fine.\\end{document}\n"
    )

    def press_escape() -> None:
        end = time.monotonic() + 30
        while time.monotonic() < end and not ready.exists():
            time.sleep(0.01)
        assert ready.exists(), "the fake LaTeX child never started"
        assert process.interrupt_children() == 1

    threading.Thread(target=press_escape, daemon=True).start()

    started = time.monotonic()
    result = LatexTools(COMMAND, timeout=300).check(source)
    elapsed = time.monotonic() - started

    assert not result.ok
    assert "interrupted" in result.output
    # Grace, then SIGTERM, then SIGKILL -- and nowhere near the 300s timeout.
    assert 1.5 <= elapsed < 30


def test_a_second_escape_does_not_wait_out_the_first_presss_grace(tmp_path: Path):
    """A single `wait(GRACE)` is not woken by the second press -- nothing sets
    the event it waits on -- so a press arriving inside the grace sat out the
    whole of it, which is precisely what the press was made to skip."""
    import threading
    import time

    from hardy import process

    ready = tmp_path / "tex-stubborn"
    source = (
        "\\documentclass{article}\n"
        f"% slow: 300\n% deaf: sigterm\n% ready: {ready}\n"
        "\\begin{document}Fine.\\end{document}\n"
    )

    def press_twice() -> None:
        end = time.monotonic() + 30
        while time.monotonic() < end and not ready.exists():
            time.sleep(0.01)
        assert ready.exists(), "the fake LaTeX child never started"
        assert process.interrupt_children() == 1
        # Lands while the watcher is inside the grace.
        time.sleep(0.2)
        assert process.stop_children() == 1

    threading.Thread(target=press_twice, daemon=True).start()

    started = time.monotonic()
    result = LatexTools(COMMAND, timeout=300).check(source)
    elapsed = time.monotonic() - started

    assert not result.ok
    assert "interrupted" in result.output
    # Well inside the 2s grace the first press started, let alone the 4s the
    # full ladder takes when nobody presses again.
    assert elapsed < 1.5


needs_symlinks = pytest.mark.skipif(os.name == "nt", reason="symlink_to needs Developer Mode on Windows")


@needs_symlinks
def test_a_symlink_in_the_writeup_tree_is_not_copied_into_the_scratch_tree(tmp_path: Path):
    """Neither of `copytree`'s two settings is safe on a tree a clone wrote.

    Reproduced with the default, `symlinks=False`: `tex/sections -> $HOME` was
    copied BY CONTENT, so every check dragged the whole of the user's home
    directory into the scratch tree and handed it to a TeX process that can
    `\\input` any of it -- which is what the compile succeeding here used to
    demonstrate. `symlinks=True` is worse: the link is recreated, and the
    candidate written to `sections/one.tex` afterwards lands in the linked
    directory instead.

    This is also the test `UNGUARDED` names for `latex.check`. Its three
    writes go into a `TemporaryDirectory`, and they cannot leave it because
    nothing in the scratch tree is a link for them to follow out.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.tex").write_text("The user's private notes.\n", encoding="utf-8")
    tree = tmp_path / "tex"
    tree.mkdir()
    (tree / "sections").symlink_to(outside, target_is_directory=True)
    document = (
        "\\documentclass{article}\n\\begin{document}\\input{sections/secret}\\end{document}\n"
    )

    result = LatexTools(COMMAND).check(document, tree=tree)

    # The compiler could not reach it, because it was never copied in.
    assert not result.ok
    assert "not found" in result.output
    # And nothing was written back out through the link either.
    assert sorted(item.name for item in outside.iterdir()) == ["secret.tex"]


@needs_symlinks
def test_a_symlinked_writeup_pdf_is_refused_rather_than_written_through(tmp_path: Path):
    """Reproduced: `%PDF-` written into whatever a clone pointed the PDF at.

    `writeup.pdf` is versioned and travels with a clone, and `shutil.copyfile`
    opens its DESTINATION `wb` -- following the symlink to do it. A repository
    shipping `writeup.pdf -> ~/.bashrc` had the file destroyed on the first
    successful save, before anyone had read a line of the project.
    """
    victim = tmp_path / "bashrc"
    victim.write_text("export PATH=/usr/bin\n", encoding="utf-8")
    output = tmp_path / "workspace"
    output.mkdir()
    (output / "writeup.pdf").symlink_to(victim)

    with pytest.raises(layout.LayoutError):
        LatexTools(COMMAND).check(FINE, output_dir=output)

    assert victim.read_text(encoding="utf-8") == "export PATH=/usr/bin\n"
