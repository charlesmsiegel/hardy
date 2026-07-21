#!/usr/bin/env python
"""M0 exit criterion: >= 100 proof checks/minute against warm sessions.

Direct:    python scripts/bench_throughput.py
Sandboxed: python scripts/bench_throughput.py --sandbox   (the criterion run)
"""

import argparse
import asyncio
import sys
import time

from hardy.lean.launch import (
    LEAN_PROJECT,
    repl_argv,
    repl_env,
    sandboxed_worker_spec,
)
from hardy.lean.pool import ReplPool


def theorems(n: int) -> list[str]:
    batch = []
    for i in range(n):
        match i % 3:
            case 0:
                batch.append(f"theorem bench{i} : {i} + 0 = {i} := by simp")
            case 1:
                batch.append(f"theorem bench{i} : {i} ≤ {i} + 1 := by omega")
            case _:
                batch.append(f"theorem bench{i} : ({i} : ℤ) * 1 = {i} := by norm_num")
    return batch


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--proofs", type=int, default=200)
    parser.add_argument("--sandbox", action="store_true")
    parser.add_argument("--image", default="hardy-lean:dev")
    parser.add_argument(
        "--imports",
        default="import Mathlib",
        help="warm-session imports (e.g. 'import Mathlib.Tactic' for a lighter session)",
    )
    args = parser.parse_args()

    if args.sandbox:
        pool = ReplPool(
            size=args.workers,
            spec_factory=lambda: sandboxed_worker_spec(args.image),
            imports=args.imports,
        )
    else:
        pool = ReplPool(
            size=args.workers,
            argv=repl_argv(),
            cwd=LEAN_PROJECT,
            env=repl_env(),
            imports=args.imports,
        )
    print(f"starting {args.workers} workers (importing Mathlib; slow first time)...")
    await pool.start()

    batch = theorems(args.proofs)
    try:
        t0 = time.monotonic()
        verdicts = await asyncio.gather(*(pool.check_proof(t) for t in batch))
        elapsed = time.monotonic() - t0
    finally:
        # A raising check (e.g. broken pool) must not skip cleanup: in
        # sandbox mode the idle workers are containers only close() kills.
        await pool.close()

    solved = sum(v.complete for v in verdicts)
    rate = args.proofs / elapsed * 60
    print(f"{solved}/{args.proofs} verified in {elapsed:.1f}s -> {rate:.0f} proofs/minute")
    if solved != args.proofs:
        for theorem, v in zip(batch, verdicts):
            if not v.complete:
                print("FAILED:", theorem, "|", v.failure or [e.data for e in v.errors])
        return 1
    if rate < 100:
        print("FAIL: below the 100 proofs/minute M0 exit criterion")
        return 1
    print("PASS: M0 throughput criterion met")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
