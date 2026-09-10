#!/usr/bin/env python3
"""A stand-in interpreter that echoes stdin and writes errors to stderr.

`fake_sentinel_cas.py` is modelled on Singular in `-q` mode: it never echoes
what it is fed. Macaulay2 echoes source and writes errors to stderr; two real
bugs were only ever found by running against the real binary in CI because
of it: the sentinel marker's own echoed source line contains the marker text
a second time, ahead of the bare copy the interpreter actually answers with
(`_find_marker`'s tail-aware skip), and errors land on stderr with nothing
error-shaped left on stdout at all (`classify(stdout, stderr)`). This fake
reproduces both without needing Macaulay2 installed, so the hermetic suite
notices a regression in either.
"""

import sys
import time

ECHO_PREFIX = 'ECHO "'
ECHO_TAIL = '";'
# How far a deferred statement's output lags behind the echo of the line that
# follows it. Long enough that the parent is certainly woken by the echo
# first, so a reader that ends the cell on the echoed end marker ends it
# before this output exists.
DEFER_DELAY = 0.05
# A synchronous diagnostic may be slow but precedes the next statement.
STDERR_DELAY = 0.05


def main() -> None:
    # Output an interpreter has not caught up with yet. Macaulay2 echoes a
    # line when it *reads* it, not when it finishes running the line before,
    # so a statement's own output can legitimately arrive after the echo of
    # the statement that follows it -- including after the echo of the end
    # marker, which is what makes trusting that echo cut a cell short.
    pending: list[str] = []
    # An error owed before the next marker executes. Parent-side delivery
    # delays are tested at the reader, not by reversing the child's writes.
    late_error = False

    def write_late_error() -> None:
        time.sleep(STDERR_DELAY)
        sys.stderr.write("stdio:1:1:(1): error: fake late division by zero\n")
        sys.stderr.flush()

    for counter, line in enumerate(sys.stdin, start=1):
        line = line.rstrip("\n")
        # Echo the prompt and the exact source line, unconditionally -- this
        # is what puts a marker statement's own marker text on the stream a
        # *second* time: embedded here, mid-line, immediately followed by
        # this echo template's own tail (`";`), never by a newline. The bare
        # copy an interpreter's own `<<`/`print` actually writes follows on
        # its own line below.
        sys.stdout.write(f"i{counter} : {line}\n")
        sys.stdout.flush()
        if pending:
            time.sleep(DEFER_DELAY)
            sys.stdout.write("".join(pending))
            pending.clear()
            sys.stdout.flush()
        if line == "defer;":
            pending.append("deferred-output\n")
            continue
        if line.startswith(ECHO_PREFIX) and line.endswith(ECHO_TAIL):
            marker = line[len(ECHO_PREFIX) : -len(ECHO_TAIL)]
            if late_error and "hardy-end" in marker:
                late_error = False
                write_late_error()
            sys.stdout.write(marker + "\n")
            sys.stdout.flush()
        elif line == "laterror;":
            # The error is emitted after the echoed marker source, before
            # the actual marker is executed.
            late_error = True
        elif line == "error;":
            # Nothing on stdout for a failed statement -- exactly what a
            # real division-by-zero cell does in Macaulay2. The error lands
            # only on stderr.
            sys.stderr.write("stdio:1:1:(1): error: fake division by zero\n")
            sys.stderr.flush()
        elif line == "die;":
            return
        elif line == "hang;":
            time.sleep(10)
        elif line == "flooddie;":
            sys.stderr.write("diagnostic-prefix\n" + "x" * 20_000)
            sys.stderr.flush()
            return
        elif line:
            sys.stdout.write(f"{line}\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
