"""`hardy evals`: the baseline sweep, the set runner, and the scoreboard check."""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..domain import EnvironmentIdentity
from ..lean import Elaboration, elaborate, environment_identity
from . import sweep
from .corpus import load_corpus, manifest_digest
from .problems import ProblemSet

DEFAULT_CORPUS = Path("corpus")
DEFAULT_PROBLEMS = DEFAULT_CORPUS
DEFAULT_BASELINE = Path("evals") / "baseline.json"
DEFAULT_SCOREBOARDS = Path("evals") / "scoreboards"
DEFAULT_POOLS = Path("evals") / "pools"


def _positive_repeats(value: str) -> int:
    """A run with zero rows would still write a finished scoreboard and pass
    `hardy evals check`: `range(0)` is empty, so the runner and the checker
    would agree on nothing to compare. Refused here, before any row runs.
    """
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("--repeats must be at least 1")
    return parsed


def _positive_wall_seconds(value: str) -> float:
    """`ClaudeAgentRuntime._within_budget` treats a falsy `wall_seconds` (0)
    as "no budget" and skips `asyncio.wait_for` entirely, so `--wall-seconds
    0` would let a batch run spend through its turn budget unbounded, with
    every proof it submits merely discarded as late. Refused here, before any
    row runs, rather than silently recorded as a budget that never bound
    anything.

    Positivity alone is not enough: `float("inf")` passes it and
    `asyncio.wait_for(..., timeout=inf)` imposes no effective deadline
    either, so `--wall-seconds inf` would be the same unbounded run under a
    condition recorded with a non-standard JSON value; `float("nan")` also
    passes (`nan <= 0` is `False`) and instead produces an immediate timeout.
    `math.isfinite` refuses both (item 6).
    """
    parsed = float(value)
    if parsed <= 0 or not math.isfinite(parsed):
        raise argparse.ArgumentTypeError("--wall-seconds must be a positive, finite number of seconds")
    return parsed


def _positive_max_turns(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("--max-turns must be at least 1")
    return parsed


class SelectionError(ValueError):
    """A selection this refuses to narrow silently."""


def selected_ids(args: argparse.Namespace, problems: ProblemSet) -> list[str] | None:
    """The ids named explicitly, or None when the caller named none.

    Order is the caller's: naming entries is choosing a run order, which
    `select` already honours. `--only` and `--status` intersect rather than
    union -- a caller who gives both is narrowing twice, not asking for either.
    """
    named: list[str] | None = None
    if getattr(args, "only", None):
        named = [id_.strip() for id_ in args.only.split(",") if id_.strip()]
    if getattr(args, "only_file", None) is not None:
        text = sys.stdin.read() if str(args.only_file) == "-" else Path(args.only_file).read_text(encoding="utf-8")
        from_file = [line.strip() for line in text.splitlines() if line.strip()]
        named = from_file if named is None else [id_ for id_ in named if id_ in set(from_file)]
    if named is not None:
        known = {e.id for e in problems.entries}
        unknown = [id_ for id_ in named if id_ not in known]
        if unknown:
            raise SelectionError("these ids name no entry: " + ", ".join(unknown))
        seen: set[str] = set()
        named = [id_ for id_ in named if not (id_ in seen or seen.add(id_))]
    if getattr(args, "status", None):
        wanted = set(args.status)
        at_status = [e.id for e in problems.entries if e.status in wanted]
        named = at_status if named is None else [id_ for id_ in named if id_ in set(at_status)]
    return named


def add_parser(subparsers: Any) -> None:
    evals = subparsers.add_parser("evals", help="the fixed problem set: baseline sweep, set runs, scoreboard checks")
    verbs = evals.add_subparsers(dest="evals_command", required=True)
    baseline = verbs.add_parser("baseline", help="sweep the tactic set over every canonical statement and write the tier file")
    baseline.add_argument("--problems", type=Path, default=DEFAULT_PROBLEMS)
    baseline.add_argument("--out", type=Path, default=DEFAULT_BASELINE)
    baseline.add_argument("--only", default=None, help="comma-separated entry ids")
    baseline.add_argument("--only-file", type=Path, default=None,
                          help="a file of entry ids, one per line; '-' reads stdin")
    baseline.add_argument("--status", action="append", default=None,
                          help="select by corpus status, e.g. --status active; repeatable")
    baseline.add_argument("--acknowledge-unsafe-execution", action="store_true")
    baseline.add_argument("--workers", type=int, default=1, help="concurrent Lean elaborations (default 1)")
    run = verbs.add_parser("run", help="run every entry through batch or staged and write a scoreboard")
    run.add_argument("--label", required=True)
    run.add_argument("--mode", choices=("batch", "staged"), default="batch")
    run.add_argument("--backend", choices=("claude", "codex"), default="claude")
    # SUPPRESS so that omitting it here leaves the global --model alone rather
    # than overwriting it with this subparser's default (same reason as
    # `prove`'s `--model`, cli.py:1545).
    run.add_argument("--model", default=argparse.SUPPRESS)
    run.add_argument("--repeats", type=_positive_repeats, default=1)
    run.add_argument("--only", default=None, help="comma-separated entry ids")
    run.add_argument("--only-file", type=Path, default=None,
                     help="a file of entry ids, one per line; '-' reads stdin")
    run.add_argument("--status", action="append", default=None,
                     help="select by corpus status, e.g. --status active; repeatable")
    run.add_argument("--tiers", default=None, help="comma-separated tiers, e.g. 2,3")
    run.add_argument("--no-twins", action="store_true")
    run.add_argument("--max-turns", type=_positive_max_turns, default=None, help="batch mode default: 60. Refused under --mode staged.")
    run.add_argument("--wall-seconds", type=_positive_wall_seconds, default=None, help="batch mode default: 1800.0. Refused under --mode staged.")
    run.add_argument("--problems", type=Path, default=DEFAULT_PROBLEMS)
    run.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    run.add_argument("--scoreboards", type=Path, default=DEFAULT_SCOREBOARDS)
    run.add_argument("--acknowledge-unsafe-execution", action="store_true")
    run.add_argument("--workers", type=int, default=1, help="concurrent rows (default 1)")
    corpus = verbs.add_parser("corpus", help="the corpus directory: mechanical checks and coverage")
    corpus_verbs = corpus.add_subparsers(dest="corpus_verb", required=True)
    for verb, helptext in (("check", "report every mechanical objection to the corpus on disk"),
                           ("report", "coverage by group, status, difficulty and source"),
                           ("serve", "browse the corpus in a local page, re-read on every refresh"),
                           ("release", "bump every shard and write the changelog head it binds")):
        sub = corpus_verbs.add_parser(verb, help=helptext)
        sub.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
        if verb == "serve":
            sub.add_argument("--port", type=int, default=8765)
            sub.add_argument("--host", default="127.0.0.1")
        if verb == "release":
            sub.add_argument("--version", required=True, help="three numbers, greater than the last")
            sub.add_argument("--note", action="append", default=[],
                             help="a changelog bullet citing the ids that moved; repeatable")
        if verb == "check":
            sub.add_argument(
                "--since-registry", type=Path, default=None,
                help="the previous release's tombstones.json; the registry is append-only, "
                     "which only a comparison can establish. CI passes the merge base's copy.",
            )
            sub.add_argument(
                "--since", type=Path, default=None,
                help="the previous release's CHANGELOG.md (its head carries the version and the "
                     "manifest digest it bound); refuses content that moved under a version "
                     "already released. CI passes the merge base's copy.",
            )
    check = verbs.add_parser("check", help="re-derive a committed scoreboard from its run directories")
    check.add_argument("scoreboard", type=Path)
    check.add_argument("--problems", type=Path, default=DEFAULT_PROBLEMS)
    check.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    todo = verbs.add_parser(
        "todo",
        help="what's left to sweep or run under the pooling key this checkout would produce, as JSON on stdout",
    )
    todo.add_argument("--problems", type=Path, default=DEFAULT_PROBLEMS)
    todo.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    todo.add_argument("--scoreboards", type=Path, default=DEFAULT_SCOREBOARDS)
    # SUPPRESS for the same reason `run`'s own `--model` is: omitting it here
    # must leave the global `--model` alone rather than overwriting it with
    # this subparser's default (cli.py:1545).
    todo.add_argument("--model", default=argparse.SUPPRESS)
    todo.add_argument("--mode", choices=("batch", "staged"), default="batch")
    # The same budget and repeat flags `run` takes, with the same types and
    # the same defaults, because all four feed `run_procedure_digest_of`
    # through the shared `limits_for`. Without them `todo` silently reported
    # the key of a *default* run: `evals todo` then `evals run --max-turns 40`
    # named one key and recorded another, and `evals pool` later refused the
    # board the agent had just been told to produce.
    todo.add_argument("--max-turns", type=_positive_max_turns, default=None, help="batch mode default: 60. Refused under --mode staged.")
    todo.add_argument("--wall-seconds", type=_positive_wall_seconds, default=None, help="batch mode default: 1800.0. Refused under --mode staged.")
    todo.add_argument("--repeats", type=_positive_repeats, default=1)
    pool = verbs.add_parser(
        "pool",
        help="combine scoreboards sharing one pooling key into one derived, recomputable score",
    )
    pool.add_argument("labels", nargs="+", help="scoreboard labels, resolved against --scoreboards")
    pool.add_argument("--scoreboards", type=Path, default=DEFAULT_SCOREBOARDS)
    pool.add_argument("--corpus", type=Path, default=DEFAULT_PROBLEMS)
    pool.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    pool.add_argument("--out", type=Path, default=None, help="default: evals/pools/<first label>/pool.json")
    summary = verbs.add_parser(
        "summary",
        help="write a Markdown report over every scoreboard, one row per model (read-only)",
    )
    summary.add_argument("--scoreboards", type=Path, default=DEFAULT_SCOREBOARDS)
    summary.add_argument("--corpus", type=Path, default=DEFAULT_PROBLEMS)
    summary.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    summary.add_argument("--out", type=Path, default=DEFAULT_CORPUS / "EVALS.md")


def _refuse_missing(*paths: Path) -> str | None:
    """Not packaged: the corpus, the tier file and every scoreboard are
    repository evidence under `corpus/` and `evals/`, read relative to the
    current working directory. A released wheel carries neither, so a default
    path resolved outside a source checkout is a clear refusal here rather than
    a bare `FileNotFoundError` from whatever reads it next.
    """
    for path in paths:
        if not path.exists():
            return (
                f"Refused: {path} is not here. The corpus, its tier file and every scoreboard "
                "are repository evidence under corpus/ and evals/ and are read from the current "
                "directory; run from a source checkout's root or pass --problems/--baseline "
                "explicitly."
            )
    return None


def make_elaborate(config: Any) -> Callable[[str], Elaboration]:
    argv = (str(config.lake), "env", "lean", "--json")
    timeout = max(float(config.lean_timeout), sweep.WALL_BACKSTOP_FLOOR)
    return lambda source: elaborate(source, argv=argv, cwd=config.lean_project, timeout_seconds=timeout)


# Re-exported: `staleness` needs the current host to compare against the one a
# baseline recorded, so the function itself lives in `sweep`.
host_info = sweep.host_info


def _identity(config: Any) -> EnvironmentIdentity:
    return environment_identity(config.lean_project, lean_command=(str(config.lake), "env", "lean"), timeout_seconds=config.limits.lean_process_seconds)


def run_baseline(args: argparse.Namespace, config: Any, *, elaborate: Callable[[str], Elaboration] | None = None,
                 identity: EnvironmentIdentity | None = None, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> int:
    from ..runner import WARNING

    # `getattr`, not `args.workers`: a caller (or a test's hand-built
    # Namespace) that predates this flag carries no `workers` attribute, and
    # its absence must default the same way omitting the flag on `evals
    # baseline` does.
    workers = getattr(args, "workers", 1)
    if workers < 1:
        print("--workers must be at least 1", file=sys.stderr)
        return 2
    refusal = _refuse_missing(args.problems)
    if refusal is not None:
        print(refusal, file=sys.stderr)
        return 2
    if not args.acknowledge_unsafe_execution:
        # Untrusted `evals/problems.json` imports, binders and conclusion are
        # interpolated into Lean source and elaborated for real -- the same
        # unsafe-execution contract `evals run` and the staged terminal
        # already enforce, so a crafted problem file gets no free pass here
        # just because there is no run-time model to hand it to.
        print(WARNING, file=sys.stderr)
        print(
            "The sweep elaborates Lean built from the problem file's imports, binders and "
            "conclusion. Re-run with --acknowledge-unsafe-execution to accept this for the whole sweep.",
            file=sys.stderr,
        )
        return 2
    print(WARNING, file=sys.stderr)
    problems = load_corpus(args.problems)
    try:
        ids = selected_ids(args, problems)
    except SelectionError as error:
        print(f"Refused: {error}", file=sys.stderr)
        return 2
    if identity is None:
        try:
            identity = _identity(config)
        except (ValueError, OSError, KeyError, StopIteration, json.JSONDecodeError) as error:
            print(f"Refused: the Lean toolchain could not be identified: {error}", file=sys.stderr)
            return 2
    elaborate = elaborate or make_elaborate(config)
    import_seconds = None
    if config is not None:
        probe = elaborate(sweep.header(("Mathlib",)) + "\nexample : True := trivial\n")
        import_seconds = probe.process.duration_ms / 1000.0 if probe.success else None
    # Carry forward every entry whose identity did not move (spec §3). The
    # single repair route for a stale baseline is this command, so without it
    # a one-line correction re-elaborates the whole corpus.
    prior = None
    if args.out.exists():
        try:
            prior = sweep.Baseline.model_validate_json(args.out.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            prior = None   # unreadable prior: sweep everything, say nothing
    if ids is None:
        # Nobody named entries: default to the active entries the tier file
        # does not yet cover, not the whole corpus's candidates and retirees
        # too. Needs no run digest -- a sweep is Lean-only, gated by the
        # corpus and the toolchain, not by which model a run would use.
        from .outstanding import unbaselined_active

        default = unbaselined_active(problems, prior)
        if not default:
            print(
                "Refused: every active entry already has a baseline row; "
                "name entries with --only to re-sweep them",
                file=sys.stderr,
            )
            return 2
        ids = default
    baseline = sweep.sweep(
        problems, problems_sha256=manifest_digest(args.problems), environment=identity, elaborate=elaborate, now=now,
        prior=prior,
        host=host_info(), import_seconds=import_seconds,
        wall_backstop_seconds=max(float(config.lean_timeout), sweep.WALL_BACKSTOP_FLOOR) if config is not None else sweep.WALL_BACKSTOP_FLOOR,
        report=lambda line: print(line, file=sys.stderr),
        only=tuple(ids),
        workers=workers,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n": Path.write_text's default translates every "\n" to the
    # platform line separator, so on Windows this would checkin a repository
    # evidence file as CRLF even though .gitattributes marks it `-text` (no
    # conversion) precisely so its bytes are the ones a digest is taken over.
    args.out.write_text(json.dumps(baseline.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    for problem in baseline.problems:
        print("PROBLEM: " + problem, file=sys.stderr)
    tiers = {t: sum(1 for e in baseline.entries.values() if e.tier == t) for t in range(4)}
    print(f"Baseline written to {args.out}: tiers " + ", ".join(f"{t}: {n}" for t, n in tiers.items()))
    return 1 if baseline.problems else 0


def run_todo(args: argparse.Namespace, config: Any) -> int:
    """`evals todo`: free, before anything is spent -- what a `baseline` or a
    `run` launched right now, with these flags, would still have left to do.

    JSON on stdout and nothing else there, so a control agent can parse it
    without combing prose off the same stream; commentary goes to stderr.
    """
    from .outstanding import matching_boards
    from .outstanding import outstanding as compute_outstanding
    from .runner import limits_for, run_procedure_digest_of

    refusal = _refuse_missing(args.problems, args.baseline)
    if refusal is not None:
        print(refusal, file=sys.stderr)
        return 2
    if args.mode == "staged" and (args.max_turns is not None or args.wall_seconds is not None):
        # The same refusal `run_set_command` makes, for the same reason and in
        # the same words: `todo` exists to report the key a run launched now
        # would produce, and a run with these flags would not launch at all.
        print(
            "Refused: --max-turns/--wall-seconds do not govern a staged run; its budgets are "
            "config.limits.active_seconds, proof_seconds and official_checks",
            file=sys.stderr,
        )
        return 2
    try:
        identity = _identity(config)
    except (ValueError, OSError, KeyError, StopIteration, json.JSONDecodeError) as error:
        print(f"Refused: the Lean toolchain could not be identified: {error}", file=sys.stderr)
        return 2
    problems = load_corpus(args.problems)
    try:
        baseline = sweep.Baseline.model_validate_json(args.baseline.read_text(encoding="utf-8"))
    except (ValueError, OSError) as error:
        print(f"Refused: {args.baseline} does not read as a baseline: {error}", file=sys.stderr)
        return 2
    model = str(getattr(args, "model", None) or config.model)
    limits = limits_for(args, config)
    run_digest = run_procedure_digest_of(model=model, mode=args.mode, limits=limits, repeats=args.repeats)
    environment_digest = sweep.environment_digest_of(identity, host_info())
    key = (run_digest, environment_digest)
    print(json.dumps({
        "pooling_key": {"run_procedure_digest": run_digest, "environment_digest": environment_digest},
        "boards_counted": matching_boards(args.scoreboards, key=key),
        **compute_outstanding(problems, baseline, args.scoreboards, key=key),
    }, indent=2))
    return 0


def run_pool(args: argparse.Namespace) -> int:
    """`evals pool`: combine scoreboards under one condition into one derived,
    recomputable score. Refuses -- never merges -- when the boards named do
    not share one pooling key, when the same `(id, repeat)` is claimed twice,
    or when a board fails its own audit; see `pool.pool`'s docstring.

    Writes only its own output (`--out`, default
    `evals/pools/<first label>/pool.json`) and never touches a scoreboard.
    """
    from .pool import PoolRefused
    from .pool import pool as pool_boards

    refusal = _refuse_missing(args.corpus, args.baseline)
    if refusal is not None:
        print(refusal, file=sys.stderr)
        return 2
    labels = [args.scoreboards / label for label in args.labels]
    missing = [str(label) for label in labels if not (label / "scoreboard.json").exists()]
    if missing:
        print("Refused: no scoreboard.json under " + ", ".join(missing), file=sys.stderr)
        return 2
    try:
        result = pool_boards(labels, problems_path=args.corpus, baseline_path=args.baseline)
    except PoolRefused as error:
        print(f"Refused: {error}", file=sys.stderr)
        return 2
    # `<label>/pool.json`, not a bare extensionless `<label>` file (spec §3.5):
    # a pool is a named directory so the derived view has somewhere to grow --
    # and a file with no extension where a directory is documented is the kind
    # of surprise that only shows up when something tries to write beside it.
    out = args.out if args.out is not None else DEFAULT_POOLS / args.labels[0] / "pool.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n": the same repository-evidence integrity concern as the
    # baseline and scoreboard writes -- Path.write_text's default would
    # checkin this on Windows as CRLF.
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    headline = result["aggregates"]["headline"]
    print(
        f"Pooled {len(result['boards'])} board(s) ({', '.join(result['boards'])}) into {out}: "
        f"headline solve_rate={headline['solve_rate']} over n={headline['n']}",
        file=sys.stderr,
    )
    print(out)
    return 0


def run_summary(args: argparse.Namespace) -> int:
    """`evals summary`: a Markdown report over every scoreboard, one row per
    model. Read-only over `--scoreboards`, `--corpus` and `--baseline` --
    writes only `--out` (default `corpus/EVALS.md`) -- and never fails merely
    because there is nothing yet to report (`summary.render` writes a valid,
    empty-of-tables file when `--scoreboards` holds no boards).

    Refuses, like `evals pool`, when one model's own boards do not share a
    pooling key, claim the same `(id, repeat)` twice, or fail their own
    audit; see `summary.build` and `summary.SummaryRefused`.
    """
    from .summary import SummaryRefused
    from .summary import write as write_summary

    refusal = _refuse_missing(args.corpus, args.baseline)
    if refusal is not None:
        print(refusal, file=sys.stderr)
        return 2
    try:
        out = write_summary(args.scoreboards, problems_path=args.corpus, baseline_path=args.baseline, out_path=args.out)
    except SummaryRefused as error:
        print(f"Refused: {error}", file=sys.stderr)
        return 2
    print(out)
    return 0


def main(args: argparse.Namespace, config: Any) -> int:
    if args.evals_command == "baseline":
        return run_baseline(args, config)
    if args.evals_command == "run":
        from .runner import run_set_command
        return run_set_command(args, config)
    if args.evals_command == "corpus":
        from .corpus import check_issues, report
        if args.corpus_verb == "check":
            from .corpus import release_issues

            issues = check_issues(args.corpus)
            if getattr(args, "since", None) is not None:
                issues.extend(release_issues(args.corpus, args.since.read_text(encoding="utf-8")))
            if getattr(args, "since_registry", None) is not None:
                from .corpus import CorpusError, load_tombstones, registry_issues

                # Both sides are gathered, not raised: CI always passes this
                # option, so a malformed registry -- the very case the check
                # exists to report -- would otherwise abort the command before
                # it printed anything, including the objection about it.
                try:
                    prior = json.loads(args.since_registry.read_text(encoding="utf-8"))["issued"]
                    if not isinstance(prior, dict):
                        raise CorpusError("the previous registry's 'issued' is not a mapping")
                    issues.extend(registry_issues(load_tombstones(args.corpus), prior))
                except (CorpusError, OSError, ValueError, KeyError, TypeError) as error:
                    issues.append(f"tombstones.json: {error}")
            for issue in issues:
                print(issue, file=sys.stderr)
            return 1 if issues else 0
        if args.corpus_verb == "release":
            from datetime import date

            from .corpus import CorpusError, release

            try:
                issues = release(args.corpus, args.version, args.note, today=date.today().isoformat())
            except CorpusError as error:
                print(f"Refused: {error}", file=sys.stderr)
                return 2
            for issue in issues:
                print(issue, file=sys.stderr)
            print(f"corpus {args.version} written to {args.corpus / 'CHANGELOG.md'}")
            return 1 if issues else 0
        if args.corpus_verb == "serve":
            from .viewer import serve

            serve(args.corpus, host=args.host, port=args.port)
            return 0
        for line in report(args.corpus):
            print(line)
        return 0
    if args.evals_command == "check":
        from .scoreboard import check_command
        return check_command(args)
    if args.evals_command == "todo":
        return run_todo(args, config)
    if args.evals_command == "pool":
        return run_pool(args)
    if args.evals_command == "summary":
        return run_summary(args)
    raise AssertionError(args.evals_command)
