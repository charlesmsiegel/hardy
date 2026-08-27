#!/usr/bin/env python3
"""A stand-in interpreter framed by an echoed marker, not by byte counts.

Singular and Macaulay2 are line-oriented interpreters that cannot be spoken to
in frames, so Hardy asks them to echo a per-cell nonce and reads until it
appears. That path has different failure modes from the driver protocol -- an
unterminated statement swallows the echo, a prompt arrives between cells -- and
this reproduces them without needing either binary.
"""

import sys
import threading
import time

PROMPT = "fake> "
# The interpreter's own "ready for more" prompt lags behind its answer --
# long enough, in a hermetic test, that a cell relying on timing rather than
# pipe order to exclude it would already have armed and dispatched the next
# cell before this prompt is even written.
PROMPT_DELAY = 0.15


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 256 * 1024
    swallowed = False
    for line in sys.stdin:
        line = line.rstrip("\n")
        # A comment is not a statement: it neither terminates nor swallows one.
        # Hardy's exported scripts carry a header and a per-cell comment, and
        # this interpreter is fed one of those whole when an export checks that
        # the script it published reproduces the session.
        if line.lstrip().startswith("//"):
            continue
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
        # Both the begin and the end marker are just an echoed statement, so
        # both are recognised and extracted the same way. Only the end marker
        # is followed by a prompt -- the begin marker is mid-cell, not the
        # interpreter going idle -- and that prompt is what is delayed.
        if "«hardy-" in line:
            sys.stdout.write(line.split('"')[1] if '"' in line else line)
            if "«hardy-end:" not in line:
                sys.stdout.write("\n")
                sys.stdout.flush()
                continue
            sys.stdout.flush()
            time.sleep(PROMPT_DELAY)
            sys.stdout.write("\n" + PROMPT)
            sys.stdout.flush()
            continue
        if line.startswith("latestderr"):
            # An overflow that lands on *stderr*, and lands there while Hardy
            # is already reading the end marker off stdout. Two pipes, two
            # drain threads, and nothing tying their delivery together: the
            # cell's reply is extracted from stdout with only the first chunk
            # of stderr in, so the retention cap has not been tripped yet, and
            # everything that trips it arrives during the wait for stderr to
            # settle. The banner at the end is inside the discarded tail,
            # which is the whole reason a cut capture cannot be accepted.
            #
            # Dribbled rather than written at once so the arrival is spread
            # across the settle wait instead of racing it: each chunk is well
            # inside `stderr_settled`'s quiet period, so the wait keeps
            # extending until the last one is in.
            def dribble(size: int = max(1, limit // 4)) -> None:
                for _ in range(12):
                    time.sleep(0.004)
                    sys.stderr.write("z" * size)
                    sys.stderr.flush()
                sys.stderr.write("   ? this is an error\n")
                sys.stderr.flush()

            threading.Thread(target=dribble, daemon=True).start()
        elif line.startswith("errorflood"):
            # An error banner *behind* more output than Hardy retains. A real
            # interpreter does this every time a long computation fails at the
            # end, and the banner is the only evidence the cell failed at all:
            # a sentinel backend has no status of its own.
            sys.stdout.write("z" * min(400_000, limit * 4) + "\n")
            sys.stdout.write("   ? this is an error\n")
        elif line.startswith("error"):
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
