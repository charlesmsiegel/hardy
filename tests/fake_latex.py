#!/usr/bin/env python3
"""A LaTeX that models just enough of a real one to test the harness.

It resolves `\\input` against files that actually exist in the compile
directory. Without that, a document whose fragment had been deleted would still
"compile", and a test asserting the deletion is refused would pass for the
wrong reason.
"""

import pathlib
import re
import sys
import time

INPUT = re.compile(r"\\(?:input|include)\{([^}]*)\}")
# `\include` gives its fragment an auxiliary file of its own, which the root's
# aux merely `\@input`s on a later pass. Modelled here because Hardy reads the
# whole auxiliary tree for exactly this reason.
INCLUDE = re.compile(r"\\include\{([^}]*)\}")
LABEL = re.compile(r"\\label\{([^}]*)\}")
REF = re.compile(r"\\(?:page|eq)?ref\{([^}]*)\}")
CITE = re.compile(r"\\cite\{([^}]*)\}")
BIBITEM = re.compile(r"\\bibitem\{([^}]*)\}")
# `\csname bibitem\endcsname{key}` is a `\bibitem{key}` by the time TeX runs
# it. The stand-in expands the form before looking for commands, so a test can
# show that Hardy judges what the compiler DID rather than what the source
# spells.
CSNAME = re.compile(r"\\csname\s*([a-zA-Z@]+)\s*\\endcsname")
VERB = re.compile(r"\\verb(.)(.*?)\1", re.DOTALL)


def _uncommented(text: str) -> str:
    r"""Drop what LaTeX would never execute: comments and \verb content."""
    text = VERB.sub("", text)
    kept = []
    for line in text.splitlines():
        cut = 0
        while True:
            found = line.find("%", cut)
            if found < 0:
                kept.append(line)
                break
            run = len(line[:found]) - len(line[:found].rstrip("\\"))
            if run % 2 == 0:
                kept.append(line[:found])
                break
            cut = found + 1
    return "\n".join(kept)

path = pathlib.Path(sys.argv[-1])
source = path.read_text()

pending = [(path, source)]
seen = set()
while pending:
    current, text = pending.pop()
    for name in INPUT.findall(text):
        target = current.parent / name
        if not target.suffix:
            target = target.with_suffix(".tex")
        if not target.is_file():
            print(f"! LaTeX Error: File `{name}' not found.")
            raise SystemExit(1)
        if target in seen:
            continue
        seen.add(target)
        pending.append((target, target.read_text()))

# `% slow: 30` makes this stand-in grind, so a test can stop it. A real TeX run
# over a long document is the child Esc has to reach; one that returns instantly
# cannot stand in for it. The touch file lets a test wait for the child to
# genuinely exist rather than guess how long that takes.
SLOW = re.search(r"%\s*slow:\s*([0-9.]+)", source)
if SLOW:
    DEAF = re.search(r"%\s*deaf(:\s*(\S+))?", source)
    if DEAF:
        # Refuses the interrupt, as a compiler sitting in a C loop would. The
        # grace has to run out and the group has to be terminated. `% deaf:
        # sigterm` refuses that too, so only SIGKILL is left -- which is the
        # rung of the ladder a watcher that stopped at SIGTERM never reached.
        import signal

        refused = ["SIGINT", "SIGBREAK"]
        if (DEAF.group(2) or "") == "sigterm":
            refused.append("SIGTERM")
        for name in refused:
            handled = getattr(signal, name, None)
            if handled is not None:
                signal.signal(handled, signal.SIG_IGN)
    READY = re.search(r"%\s*ready:\s*(\S+)", source)
    if READY:
        pathlib.Path(READY.group(1)).write_text("ready", encoding="utf-8")
    time.sleep(float(SLOW.group(1)))

if "\\begin{document}" in source and "\\end{document}" in source:
    # `-draftmode`, or `\pdfdraftmode` in the source: everything runs, the
    # log is written, the exit status is zero, and no PDF appears. Modelled
    # because a compile that produces no document is the case Hardy has to
    # refuse rather than report as a success nobody can read.
    if not re.search(r"%\s*draftmode", source):
        pathlib.Path("writeup.pdf").write_bytes(b"%PDF-fake")
    # Real LaTeX records the labels it created in an .aux file, and Hardy reads
    # that rather than the source text. Modelling it here is what lets a test
    # tell a label the compiler made from one that only appears in the text --
    # inside a comment, or inside \verb.
    body = "".join(text for _, text in [(None, source)] + [(t, t.read_text()) for t in sorted(seen)])
    executed = CSNAME.sub(lambda found: "\\" + found.group(1), _uncommented(body))
    # `\nofiles` suppresses every auxiliary file while still producing the
    # PDF, so a later pass has no record of the labels this one created.
    nofiles = "\\nofiles" in _uncommented(body)
    aux = pathlib.Path("writeup.aux")
    # What the PREVIOUS pass wrote down. Cross-references resolve out of the
    # .aux and not out of the text, which is why one pass can never resolve
    # one: that is the behaviour Hardy's multi-pass compile exists to handle,
    # so the stand-in has to have it too.
    previous = aux.read_text(encoding="utf-8") if aux.is_file() else ""
    known = set(re.findall(r"\\newlabel\{([^}]*)\}", previous))
    cited = set(re.findall(r"\\bibcite\{([^}]*)\}", previous))
    written = [f"\\newlabel{{{name}}}{{{{1}}{{1}}}}" for name in LABEL.findall(executed)]
    written += [f"\\bibcite{{{name}}}{{1}}" for name in BIBITEM.findall(executed)]
    # Real LaTeX records every `\cite` it ran, whether or not anything defined
    # the key, so Hardy can ask what the text cited as well as what the
    # reference list defined.
    written += [f"\\citation{{{name}}}" for name in CITE.findall(executed)]
    record = "\n".join(written) + "\n"
    # `% unstable` models the document whose numbers never settle: a reference
    # that moves a page number that moves a reference. A real one converges in
    # two or three passes; this one never does, which is the case Hardy has to
    # refuse rather than publish with whatever numbers the last pass produced.
    if re.search(r"%\s*unstable", source):
        record += f"\\newlabel{{pass}}{{{{{len(previous)}}}{{1}}}}\n"
    if nofiles:
        aux.unlink(missing_ok=True)
    else:
        aux.write_text(record, encoding="utf-8")
    for name in INCLUDE.findall(executed):
        target = path.parent / name
        if not target.suffix:
            target = target.with_suffix(".tex")
        if not target.is_file():
            continue
        part = _uncommented(target.read_text())
        pathlib.Path(name).with_suffix(".aux").write_text(
            "\n".join(
                [f"\\bibcite{{{key}}}{{1}}" for key in BIBITEM.findall(part)]
                + [f"\\citation{{{key}}}" for key in CITE.findall(part)]
            )
            + "\n",
            encoding="utf-8",
        )

    undefined = False
    for name in dict.fromkeys(REF.findall(executed)):
        if name not in known:
            undefined = True
            print(f"LaTeX Warning: Reference `{name}' on page 1 undefined on input line 1.")
    for group in dict.fromkeys(CITE.findall(executed)):
        for name in dict.fromkeys(part.strip() for part in group.split(",") if part.strip()):
            if name not in cited:
                undefined = True
                print(f"LaTeX Warning: Citation `{name}' on page 1 undefined on input line 1.")
    seen_labels = set()
    for name in LABEL.findall(executed):
        if name in seen_labels:
            print(f"LaTeX Warning: Label `{name}' multiply defined.")
        seen_labels.add(name)
    if undefined:
        print("LaTeX Warning: There were undefined references.")
    # A further pass is asked for only while the record is still moving. A
    # reference nothing defines never resolves, so a stand-in that asked
    # forever would make every missing label look like a compile that merely
    # needed one more go.
    # With no auxiliary file there is nothing to compare against and nothing
    # a further pass could read, so a real LaTeX does not ask for one.
    if record != previous and not nofiles:
        print("LaTeX Warning: Label(s) may have changed. Rerun to get cross-references right.")
    print("Output written on writeup.pdf")
    raise SystemExit(0)
print("! Emergency stop.")
raise SystemExit(1)
