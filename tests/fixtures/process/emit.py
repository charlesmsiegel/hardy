import argparse
import os
import signal
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument('--ignore-interrupt', action='store_true')
parser.add_argument('--ignore-terminate', action='store_true')
parser.add_argument('--stdout')
parser.add_argument('--stderr')
parser.add_argument('--echo')
parser.add_argument('--sleep', type=float, default=0)
parser.add_argument('--sleep-after', type=float, default=0)
parser.add_argument('--bytes', type=int, default=0)
parser.add_argument('--env', action='append', default=[])
# Written once everything above has been said, so a test can wait for the
# child to have actually spoken instead of guessing at how long that takes.
parser.add_argument('--ready')
args = parser.parse_args()

if args.ignore_interrupt:
    # A child that will not stop when it is asked to, which is what the
    # escalation after the grace period exists for. SIGBREAK is the Windows
    # spelling of what CTRL_BREAK_EVENT raises in the target.
    for name in ('SIGINT', 'SIGBREAK'):
        handled = getattr(signal, name, None)
        if handled is not None:
            signal.signal(handled, signal.SIG_IGN)

if args.ignore_terminate:
    # Deaf to the polite teardown as well, so only SIGKILL is left: the last
    # rung of the ladder, which nothing else in these fixtures forces Hardy on
    # to.
    handled = getattr(signal, 'SIGTERM', None)
    if handled is not None:
        signal.signal(handled, signal.SIG_IGN)

if args.sleep:
    time.sleep(args.sleep)
if args.stdout is not None:
    print(args.stdout)
if args.stderr is not None:
    print(args.stderr, file=sys.stderr)
if args.echo is not None:
    print(args.echo)
if args.bytes:
    sys.stdout.buffer.write(b'x' * args.bytes)
    sys.stdout.buffer.flush()
if args.ready:
    with open(args.ready, 'w', encoding='utf-8') as handle:
        handle.write('ready')
if args.sleep_after:
    time.sleep(args.sleep_after)
for name in args.env:
    print(f'{name}={os.environ.get(name, "<missing>")}')
