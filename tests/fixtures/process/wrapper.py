"""A launcher that exits while the work it started keeps the captured pipes.

`lake` and `latexmk` are both this shape: the process Hardy waits on hands off
to a compiler and returns, and the compiler inherits the captured stdout. So
the leader is gone long before the run is over, and `communicate` goes on
waiting on a pipe held open by something Hardy never spawned directly.
"""

import argparse
import subprocess
import sys
from pathlib import Path

EMIT = Path(__file__).with_name("emit.py")

parser = argparse.ArgumentParser()
parser.add_argument("--ready")
parser.add_argument("--sleep", type=float, default=60)
parser.add_argument("--ignore-interrupt", action="store_true")
parser.add_argument("--ignore-terminate", action="store_true")
args = parser.parse_args()

argv = [sys.executable, str(EMIT), "--sleep-after", str(args.sleep)]
if args.ignore_interrupt:
    argv.append("--ignore-interrupt")
if args.ignore_terminate:
    argv.append("--ignore-terminate")
if args.ready:
    argv += ["--ready", args.ready]

# No new process group and no new session: the grandchild stays in the group
# Hardy created for the wrapper, which is what a group-addressed signal has to
# reach. It inherits this process's stdout and stderr, so the pipes stay open
# after this line returns and this process exits.
subprocess.Popen(argv)
