#!/usr/bin/env python3
"""A stand-in interpreter framed by an echoed marker, not by byte counts.

Singular and Macaulay2 are line-oriented interpreters that cannot be spoken to
in frames, so Hardy asks them to echo a per-cell nonce and reads until it
appears. That path has different failure modes from the driver protocol -- an
unterminated statement swallows the echo, a prompt arrives between cells -- and
this reproduces them without needing either binary.
"""

import sys

PROMPT = "fake> "


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 256 * 1024
    swallowed = False
    for line in sys.stdin:
        line = line.rstrip("\n")
        # An unterminated statement means the interpreter is still waiting, so
        # the marker line that follows is swallowed as part of it -- exactly
        # what a missing semicolon does in Singular. The combined, malformed
        # statement is never executed, so no marker it happens to contain is
        # ever echoed -- but real interpreters recover at the next terminator
        # rather than hanging forever, so this does too: it reports an error
        # for the swallowed statement and goes back to answering normally.
        if swallowed:
            if line.endswith(";"):
                swallowed = False
                sys.stdout.write("   ? this is an error\n")
                sys.stdout.flush()
            continue
        if line and not line.endswith((";", "»")):
            swallowed = True
            continue
        if "«hardy-end:" in line:
            sys.stdout.write(line.split('"')[1] if '"' in line else line)
            sys.stdout.write("\n" + PROMPT)
            sys.stdout.flush()
            continue
        if line.startswith("error"):
            sys.stdout.write("   ? this is an error\n")
        elif line.startswith("flood"):
            sys.stdout.write("z" * min(400_000, limit * 4) + "\n")
        elif line.startswith("silent"):
            pass
        else:
            sys.stdout.write(f"{line}\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
