import argparse
import os
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument('--stdout')
parser.add_argument('--stderr')
parser.add_argument('--echo')
parser.add_argument('--sleep', type=float, default=0)
parser.add_argument('--sleep-after', type=float, default=0)
parser.add_argument('--bytes', type=int, default=0)
parser.add_argument('--env', action='append', default=[])
args = parser.parse_args()

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
if args.sleep_after:
    time.sleep(args.sleep_after)
for name in args.env:
    print(f'{name}={os.environ.get(name, "<missing>")}')
