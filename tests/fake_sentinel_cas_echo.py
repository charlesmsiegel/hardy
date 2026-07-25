#!/usr/bin/env python3
"""A stand-in interpreter that echoes stdin and writes errors to stderr.

`fake_sentinel_cas.py` is modelled on Singular in `-q` mode: it never echoes
what it is fed and never writes to stderr. Macaulay2 does both, and two real
bugs were only ever found by running against the real binary in CI because
of it: the sentinel marker's own echoed source line contains the marker text
a second time, ahead of the bare copy the interpreter actually answers with
(`_find_marker`'s tail-aware skip), and errors land on stderr with nothing
error-shaped left on stdout at all (`classify(stdout, stderr)`). This fake
reproduces both without needing Macaulay2 installed, so the hermetic suite
notices a regression in either.
"""

import sys

ECHO_PREFIX = 'ECHO "'
ECHO_TAIL = '";'


def main() -> None:
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
        if line.startswith(ECHO_PREFIX) and line.endswith(ECHO_TAIL):
            marker = line[len(ECHO_PREFIX) : -len(ECHO_TAIL)]
            sys.stdout.write(marker + "\n")
        elif line == "error;":
            # Nothing on stdout for a failed statement -- exactly what a
            # real division-by-zero cell does in Macaulay2. The error lands
            # only on stderr.
            sys.stderr.write("stdio:1:1:(1): error: fake division by zero\n")
            sys.stderr.flush()
        elif line:
            sys.stdout.write(f"{line}\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
