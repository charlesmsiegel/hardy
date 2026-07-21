#!/usr/bin/env python
"""M0 exit criterion: compile-check a sample writeup, inside the sandbox.

Direct:    python scripts/sample_writeup.py
Sandboxed: python scripts/sample_writeup.py --sandbox   (the criterion run)

Output lands in results/sqrt2_irrational_sample/ per DESIGN.md Component 5.
"""

import argparse
import sys
from pathlib import Path

from hardy.latex.compile import compile_tex, compile_tex_sandboxed
from hardy.latex.template import render_writeup

STATEMENT = r"There is no rational number $q$ with $q^2 = 2$."
PROOF = r"""Suppose toward a contradiction that $q = a/b$ in lowest terms with
$q^2 = 2$. Then $a^2 = 2b^2$, so $a^2$ is even, hence $a$ is even; write
$a = 2c$. Substituting, $4c^2 = 2b^2$, so $b^2 = 2c^2$ and $b$ is even as
well --- contradicting that $a/b$ is in lowest terms."""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sandbox", action="store_true")
    parser.add_argument("--image", default="hardy-tex:dev")
    args = parser.parse_args()

    source = render_writeup(
        title="Irrationality of the Square Root of Two",
        statement=STATEMENT,
        informal_proof=PROOF,
        formalization_status="not formalized",
    )
    staging = Path("results/sqrt2_irrational_sample")
    staging.mkdir(parents=True, exist_ok=True)

    if args.sandbox:
        result = compile_tex_sandboxed(source, staging, image=args.image, timeout=300)
    else:
        result = compile_tex(source, staging, timeout=300)
    if not result.success:
        print("FAIL: writeup did not compile")
        for error in result.errors:
            print(f"  {error.file or '?'}:{error.line or '?'}: {error.message}")
        return 1
    print(f"PASS: compile-checked writeup at {result.pdf_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
