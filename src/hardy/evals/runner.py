"""The set runner: every entry through batch or staged, one row each, refusing before it spends (spec §3)."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import threading
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from typing import Any

from hardy.corpus.catalog import load_corpus, manifest_digest
from hardy.corpus.problems import Entry, sha256_of
from hardy.corpus.problems import ProblemSet as ProblemSet
from hardy.evals.contracts import Condition, RefusedRun, Scoreboard
from hardy.evals.identity import RUN_SOURCE_EXCLUDED_DIRS as RUN_SOURCE_EXCLUDED_DIRS
from hardy.evals.identity import RUN_SOURCE_EXCLUDED_FILES as RUN_SOURCE_EXCLUDED_FILES
from hardy.evals.identity import RUN_SOURCE_ROOT as RUN_SOURCE_ROOT
from hardy.evals.identity import run_procedure_digest_of as run_procedure_digest_of
from hardy.evals.identity import run_source_paths as run_source_paths
from hardy.evals.scoreboard import Row, active_ids, aggregate, batch_row, staged_row
from hardy.evals.selection import select
from hardy.evals.sweep import Baseline, host_info, staleness
from hardy.formal.contracts import EnvironmentIdentity

BatchRunner = Callable[[Entry, Path, int, float], None]
StagedRunner = Callable[[Entry, Path, str], None]   # (entry, row_dir, model): writes the nested run and canonical.json

# A single safe path component: no `/` or `\`, no leading `.` or `-`, nothing
# that could turn `scoreboards_root / label` into a path outside
# `evals/scoreboards` (item 4). `..` alone, `../x`, `a/b`, and every absolute
# path (POSIX or Windows) all fail this on the first character or the
# separator, so no further check is needed.
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")




def _source_anchor() -> Path:
    """Where the walk for Hardy's own source checkout starts: the installed
    package's own directory, not wherever `--problems` happens to point.

    A problem file under another Git repository (or under `/tmp`, with none
    above it at all) must not attribute a run to that repository's HEAD --
    only the checkout the running Hardy code itself came from is `source_
    revision`'s subject (item 1). Exposed as its own function so a test can
    monkeypatch the anchor without touching the git-invocation logic below.
    """
    import hardy

    return Path(hardy.__file__).resolve().parent


def _git_root(start: Path) -> Path | None:
    """The nearest ancestor of `start` (`start` included) that carries `.git`, or `None` at the filesystem root."""
    current = start
    while not (current / ".git").exists():
        parent = current.parent
        if parent == current:
            return None
        current = parent
    return current


def source_revision() -> str | None:
    """The Git commit Hardy's own running source checkout is at, or `None`.

    Never raises: a source checkout with no `.git` (a stripped clone, a
    packaging step run outside the repository) or no `git` on `PATH` must not
    turn "record the revision" into a refusal to run a set. `-dirty` is
    appended when `git status --porcelain` reports uncommitted changes, so a
    scoreboard's `condition` cannot be mistaken for stating a commit's own
    committed state when the tree that actually ran had more than that.
    """
    root = _git_root(_source_anchor())
    if root is None:
        return None
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if head.returncode != 0:
        return None
    revision = head.stdout.strip()
    if not revision:
        return None
    try:
        status = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return revision
    if status.returncode == 0 and status.stdout.strip():
        revision += "-dirty"
    return revision


def _write(path: Path, board: Scoreboard) -> None:
    tmp = path.with_suffix(".json.tmp")
    # newline="\n": the same repository-evidence integrity concern as
    # commands.py's baseline write -- Path.write_text's default would
    # checkin a scoreboard as CRLF on Windows despite `.gitattributes`
    # marking evals/scoreboards/** -text so its bytes stay the ones any
    # digest is taken over.
    tmp.write_text(json.dumps(board.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(path)


def run_set(*, label: str, problems_path: Path, baseline_path: Path, scoreboards_root: Path, condition: Condition,
            environment: EnvironmentIdentity, batch_runner: BatchRunner, staged_runner: StagedRunner | None = None,
            now: Callable[[], datetime], report: Callable[[str], None], workers: int = 1) -> Path:
    if not LABEL_RE.fullmatch(label):
        # Before anything is read or created: `scoreboards_root / label`
        # would otherwise resolve outside `evals/scoreboards` for a label
        # like `../../new-eval` or an absolute path, and the run tree would
        # be written there despite the documented output contract (item 4).
        raise RefusedRun(f"--label must be a single path component matching {LABEL_RE.pattern!r}, not {label!r}")
    problems = load_corpus(problems_path)
    baseline = Baseline.model_validate_json(baseline_path.read_text(encoding="utf-8"))
    sel = condition.selection
    # Selection first, then the gate over exactly what was selected. The gate
    # is per entry by design (`staleness`'s own docstring), and a row's tier
    # and its twin's mechanical falsity come from its own baseline entry --
    # an entry that never runs needs none. Asking for whole-corpus coverage
    # made a 1166-entry corpus demand a 1166-entry sweep before a run of 11.
    entries = select(problems, baseline, only=sel.get("only"), tiers=sel.get("tiers"), twins=sel.get("twins", True))
    if not entries:
        # Before `out` is created: `--tiers 2` against a baseline with no
        # tier-2 entries, or `--only <twin> --no-twins`, would otherwise
        # write a finished, zero-row scoreboard that `hardy evals check`
        # accepts (the same empty selection derives the same empty expected
        # order), presenting a nominally completed experiment with no
        # samples (item 5).
        raise RefusedRun("the selection matches no entries (tiers/only/twins filters left nothing to run)")
    # `baseline_entries_mismatch` (inside `staleness`) reads `problem_ids` as
    # an exhaustive listing to match `baseline.entries` against exactly, so a
    # baseline that legitimately covers more than this run selected -- the
    # ordinary case once a baseline has grown to cover the whole corpus --
    # would otherwise report every unselected entry as a spurious "extra".
    # Widening `problem_ids` to the selected ids plus whatever the baseline
    # already carries (restricted to ids the corpus still names) keeps that
    # check meaningful -- a baseline entry for a retired id is still "extra"
    # -- while a selected id the baseline has never covered is still "missing".
    known_ids = {e.id for e in problems.entries}
    selected_ids = {e.id for e in entries}
    mismatch_scope = sorted(selected_ids | (set(baseline.entries) & known_ids))
    host = host_info()
    issues = staleness(
        baseline,
        statement_digests={e.id: e.statement_digest() for e in entries},
        environment=environment,
        problem_ids=mismatch_scope,
        host=host,
        expectations={e.id: e.expected for e in entries},
    )
    if issues:
        raise RefusedRun("; ".join(issues))
    out = scoreboards_root / label
    if out.exists():
        raise RefusedRun(f"{out} already exists; a label is one condition on one day")
    if condition.mode == "staged" and staged_runner is None:
        raise RefusedRun("staged mode needs a staged runner")
    out.mkdir(parents=True)
    board = Scoreboard(label=label, condition=condition, environment=environment, baseline_sha256=sha256_of(baseline_path),
                       problems_sha256=manifest_digest(problems_path), rows=(), aggregates=aggregate([], baseline, active_ids=active_ids(problems)),
                       started_at=now(), finished_at=None, interrupted=False, host=host)
    _write(out / "scoreboard.json", board)
    # One job per (entry, repeat), in exactly the order the sequential runner
    # used to produce rows in: `select()`'s own order, then repeats. `evals
    # check` requires a finished board's rows in this order and an
    # interrupted board's rows to be a *prefix* of it (scoreboard.py's
    # "the scoreboard's rows are not in the run's order" and "interrupted
    # scoreboard rows are not a prefix of the run order" checks) -- so a
    # result lands in its own slot at its job's index, and only the
    # completed contiguous prefix of `slots` is ever written out. Appending
    # rows as workers finish would satisfy neither check.
    jobs = [(entry, repeat) for entry in entries for repeat in range(condition.repeats)]
    slots: list[Row | None] = [None] * len(jobs)
    lock = threading.Lock()

    def run_job(entry: Entry, repeat: int) -> Row:
        tier = baseline.entries[entry.id].tier
        # Twins never run staged: the loop grades every unverified run partial (#23).
        mode = "batch" if entry.expected == "false" else condition.mode
        report(f"{entry.id} [{mode} {repeat}]")
        row_dir = out / "runs" / entry.id / f"{mode}-{repeat}"
        if mode == "batch":
            # A batch-mode condition's own limits govern a batch row. A twin
            # under a staged condition still runs batch (the loop grades
            # every unverified staged run partial, #23), but staged limits
            # carry active_seconds/proof_seconds/official_checks, not
            # max_turns/wall_seconds -- so its budget is the separately-
            # recorded twin_* pair instead.
            if condition.mode == "batch":
                max_turns, wall_seconds = int(condition.limits["max_turns"]), float(condition.limits["wall_seconds"])
            else:
                max_turns, wall_seconds = int(condition.limits["twin_max_turns"]), float(condition.limits["twin_wall_seconds"])
            batch_runner(entry, row_dir, max_turns, wall_seconds)
            row = batch_row(entry, tier, row_dir, out, repeat=repeat)
        else:
            row_dir.mkdir(parents=True, exist_ok=True)
            staged_runner(entry, row_dir, condition.model)  # type: ignore[misc]
            row = staged_row(entry, tier, row_dir, out, repeat=repeat)
        # The concurrency this row was produced under, recorded on every row
        # this run makes -- including a `workers=1` run -- so `wall_seconds`
        # is self-describing rather than silently assuming serial wall clock.
        row = row.model_copy(update={"workers": workers})
        report(f"  -> {row.outcome} ({row.terminal_reason})")
        return row

    def publish() -> None:
        nonlocal board
        done: list[Row] = []
        for row in slots:
            if row is None:
                break
            done.append(row)
        board = board.model_copy(update={
            "rows": tuple(done),
            "aggregates": aggregate(done, baseline, active_ids=active_ids(problems)),
        })
        _write(out / "scoreboard.json", board)

    try:
        # Threads, not processes: each job spends nearly all its time
        # waiting on a model call or a Lean subprocess, both of which
        # release the GIL, and threads keep this exception path simple.
        #
        # Jobs are submitted in a bounded window of at most `workers` at a
        # time -- never all of them upfront -- and the window stops
        # refilling the instant any job raises. Submitting everything
        # upfront and cancelling the rest on failure was tried first and
        # rejected: `Future.cancel()` only cancels a future the pool has not
        # yet started running, and the pool starts pulling its next queued
        # item the moment a worker frees up, racing the main thread's own
        # notice of the failure -- a race with no guaranteed winner. Never
        # submitting the next job at all has no such race: at `workers=1`
        # this makes a mid-run failure leave every later entry genuinely
        # unexecuted, exactly as the old sequential loop did (this module's
        # own docstring: "refuses before it spends"), and at `workers > 1`
        # it bounds this `with` block's own `shutdown(wait=True)` -- on any
        # exit, success, a job's own exception, or a raw KeyboardInterrupt
        # raised while waiting below -- to the handful of jobs already in
        # flight rather than the whole remaining queue.
        with ThreadPoolExecutor(max_workers=workers) as executor:
            pending = list(enumerate(jobs))
            in_flight: dict[Future[Row], int] = {}
            first_error: BaseException | None = None
            while pending or in_flight:
                while pending and len(in_flight) < workers and first_error is None:
                    index, (entry, repeat) = pending.pop(0)
                    in_flight[executor.submit(run_job, entry, repeat)] = index
                if not in_flight:
                    break   # a failure already stopped submission and nothing is left running
                # Drain whichever in-flight job(s) finish next -- including
                # after the first failure, so a job that was already running
                # concurrently with the one that failed still lands in its
                # slot before the run is declared interrupted; only the
                # *first* error is the one this re-raises.
                done, _pending_futures = wait(in_flight, return_when=FIRST_COMPLETED)
                for future in done:
                    index = in_flight.pop(future)
                    try:
                        row = future.result()
                    except BaseException as error:  # noqa: BLE001 - re-raised below, not swallowed
                        if first_error is None:
                            first_error = error
                        continue
                    with lock:
                        slots[index] = row
                        publish()
        if first_error is not None:
            raise first_error
    except BaseException:
        _write(out / "scoreboard.json", board.model_copy(update={"interrupted": True}))
        raise
    _write(out / "scoreboard.json", board.model_copy(update={"finished_at": now()}))
    return out


def _batch_runner(config: Any, model: str) -> BatchRunner:
    # `..wiring`, not the `..cli` re-export: `cli.py` is excluded from
    # `run_procedure_digest` (RUN_SOURCE_EXCLUDED_FILES) on the grounds that
    # the run hooks moved out of it. While a run still imports through that
    # re-export, editing `cli.py` changes what a run does without moving the
    # digest -- which is to say the digest is defeatable, and the pooling key
    # stops meaning "the same code produced these rows".
    from hardy.formal.contracts import Request
    from hardy.lean import LeanTools
    from hardy.runner import run
    from hardy.wiring import runtime_factory

    def run_one(entry: Entry, output: Path, max_turns: int, wall_seconds: float) -> None:
        request = Request.from_dict({"declaration": entry.declaration(), "informal_claim": entry.input, "imports": list(entry.imports)})
        # The same command `environment_identity` was asked about for this
        # run's `environment` field (run_set_command), not `config.lean_
        # command`: a global `--lean-command` that differs from `config.lake
        # env lean` would otherwise check every batch proof against a
        # different toolchain from the one the scoreboard's `environment`
        # and the baseline sweep both name, so a row could pass its own
        # checks under a Lean this experiment was never actually measured
        # against (item 2).
        lean = LeanTools(request, (str(config.lake), "env", "lean"), timeout=config.lean_timeout, project=config.lean_project)
        # `model`, not `config.model`: the condition already recorded
        # whichever model `--model` selected (run_set_command), and every row
        # must actually be produced by that model -- not silently by
        # whatever `config.model` happens to be, which under `--model
        # override@test` would be a different one (item 1).
        run(request, runtime_factory(model), lean, output, max_turns=max_turns, wall_seconds=wall_seconds)

    return run_one


BATCH_DEFAULT_MAX_TURNS = 60
BATCH_DEFAULT_WALL_SECONDS = 1800.0


def limits_for(args: argparse.Namespace, config: Any) -> dict[str, float | int]:
    """The condition's own budgets, built exactly as `run_set_command` builds
    them for the run it is about to launch.

    Factored out so `evals todo` can report the pooling key a run started now
    -- with these flags, no more -- would actually produce; duplicating this
    would let the two drift and `todo` report a key no run uses.
    """
    if args.mode == "staged":
        # A staged run is bounded by the workflow's own budgets, not
        # --max-turns/--wall-seconds (refused before this is called, in
        # `run_set_command`); a twin still runs batch under staged mode
        # (#23), so its budget -- and, since `_batch_runner` hands a twin's
        # `LeanTools` the same `config.lean_timeout` a staged run's own
        # checks use, its per-check Lean timeout too -- is recorded here.
        # `lean_process_seconds` governs the toolchain-identity probe, not a
        # proof check, but it is still one of the run's own budgets and
        # belongs beside the others.
        return {
            "active_seconds": config.limits.active_seconds, "proof_seconds": config.limits.proof_seconds,
            "official_checks": config.limits.official_checks, "lean_process_seconds": config.limits.lean_process_seconds,
            "twin_max_turns": BATCH_DEFAULT_MAX_TURNS, "twin_wall_seconds": BATCH_DEFAULT_WALL_SECONDS,
            "lean_timeout": float(config.lean_timeout),
        }
    # `lean_timeout` here is condition provenance only: it is what
    # `_batch_runner` hands `LeanTools` for every per-check Lean call this run
    # makes, but the batch trajectory itself records no per-check timeout
    # (item 3), so the validator has nothing to cross-check it against.
    # `getattr`, not `args.max_turns`: `evals todo`'s namespace carries no
    # --max-turns/--wall-seconds at all, and its absence there must default
    # the same way omitting the flag on `evals run` does.
    max_turns = getattr(args, "max_turns", None)
    wall_seconds = getattr(args, "wall_seconds", None)
    return {
        "max_turns": max_turns if max_turns is not None else BATCH_DEFAULT_MAX_TURNS,
        "wall_seconds": wall_seconds if wall_seconds is not None else BATCH_DEFAULT_WALL_SECONDS,
        "lean_timeout": float(config.lean_timeout),
    }


