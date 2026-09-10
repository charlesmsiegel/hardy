"""Conservative source identity for runs, independent of the run launcher."""
from __future__ import annotations

from pathlib import Path

from hardy import __version__
from hardy.evals import digests

RUN_SOURCE_ROOT = Path(__file__).resolve().parents[1]

# Excluded because no run path reaches them. Inclusion is the default: a
# module added tomorrow counts without anyone remembering to list it, which
# is the whole reason this is a denylist. An allowlist drawn from the obvious
# imports omitted `closers` -- which decides whether a proof closes -- and
# `usage`, which computes the very token counts a pool aggregates.
#
# The cost of getting this wrong runs in both directions. A module wrongly
# excluded lets a run change while the key claims it did not. A module wrongly
# *included* is quieter and was the more expensive mistake here: `summary.py`
# only reads finished boards, so adding a column to its report moved the key
# and orphaned every scoreboard on disk -- `evals todo` reported
# `boards_counted: 0` for two models that plainly had boards, and topping them
# up became a full re-baseline. The test that a downstream reader cannot reach
# a run is `test_no_module_the_digest_covers_reaches_a_run_through_cli`'s
# shape: nothing the digest covers may import it.
RUN_SOURCE_EXCLUDED_FILES = frozenset({
    "__main__.py",        # a console-script shim
    "cli.py",
    "app/cli.py",        # argument parsing; construction lives in wiring.py
    "app/corpus_viewer.py",    # the corpus review viewer
    "evals/summary.py",   # reads finished boards; cannot reach a run
    "evals/compare.py",   # reads paired finished boards; cannot reach a run
})
RUN_SOURCE_EXCLUDED_DIRS = ("app/tui/",)


def run_source_paths() -> tuple[Path, ...]:
    """Every module whose bytes can change what a run does, in a stable order."""
    found = []
    for path in RUN_SOURCE_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(RUN_SOURCE_ROOT).as_posix()
        if rel in RUN_SOURCE_EXCLUDED_FILES or rel.startswith(RUN_SOURCE_EXCLUDED_DIRS):
            continue
        found.append(path)
    # Sorted on the POSIX-relative name, not the OS path: a Windows and a
    # Linux checkout must hash the same modules in the same order.
    return tuple(sorted(found, key=lambda p: p.relative_to(RUN_SOURCE_ROOT).as_posix()))


def run_procedure_digest_of(*, model: str, mode: str, limits: dict[str, float | int], repeats: int) -> str:
    """What a pooled row must share: the deciding source, the prompts, the model, its budgets and its repeats.

    The mirror of `sweep.procedure_digest_of`, for the run rather than the
    sweep, and for the same reason its docstring gives: `__version__` is fixed
    at 0.1.0 across every checkout, so only hashing the deciding modules can
    tell that two measurements came from the same code.

    The prompt-set hashes stay separate keys rather than folding into
    `source`, so a changed digest says which input moved. They cover template
    *text* only -- never the `prompts/` code that renders it, which
    `run_source_paths` picks up.

    `repeats` is part of the key, not provenance beside it. The bar a pool
    has to clear is that the combined result be indistinguishable from one
    long sequential run made at a single moment, and one sequential run has
    one `--repeats` setting: folding a board sampled once per entry into one
    sampled three times per entry is an unbalanced design, where entries with
    more samples pull the pooled rate towards their own.
    """
    from hardy.prompts import BATCH_PROMPT_SET_SHA256, PROMPT_SET_SHA256

    return digests.procedure_digest({
        "hardy_version": __version__,
        "source": [digests.source_digest(p.read_bytes()) for p in run_source_paths()],
        "staged_prompt_set_sha256": PROMPT_SET_SHA256,
        "batch_prompt_set_sha256": BATCH_PROMPT_SET_SHA256,
        "model": model,
        "mode": mode,
        "limits": limits,
        "repeats": repeats,
    })


def run_source_digest_of() -> str:
    """Identify source independently of experimental model/prompt/budget choices.

    Include module names as well as normalized bytes so moving identical code
    between import paths cannot masquerade as the same source. Record only
    for newly launched runs; never backfill existing evidence from this tree.
    """
    return digests.procedure_digest({
        "source": {path.relative_to(RUN_SOURCE_ROOT).as_posix(): digests.source_digest(path.read_bytes())
                   for path in run_source_paths()},
    })


