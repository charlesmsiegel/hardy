"""`hardy latency`: the fixed Lean import cost a warm pool would recover (#54)."""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

from hardy.app import config as configuration
from hardy.formal import latency
from hardy.workflows.batch import WARNING

DEFAULT_PROBE_TIMEOUT = 300.0

# Far beyond any run `hardy latency` describes, and small enough that every
# derived total still renders. See the bound in `run_latency`.
MAX_CALLS = 1_000_000_000


def run_latency(args: argparse.Namespace, config: configuration.Config) -> int:
    """Measure the fixed Lean import cost, for the gate in docs/design/interactive-session.md and #54.

    Runs where the ordinary checks run -- inside the configured Lake project,
    through the configured Lean command -- because an import cost measured
    against a different Mathlib is not the cost this harness pays.
    """
    imports = tuple(args.imports or ("Mathlib",))
    # Checked here, not where the probe source is rendered. `import_probe` is
    # called inside `measure_import_cost`, which runs after the toolchain probe
    # has already spent up to a full deadline on a `--version` that may stall —
    # so a malformed module name paid 300s before being told it was malformed.
    try:
        latency.import_probe(imports)
    except ValueError as error:
        print(str(error))
        return 2
    # Checked before probing, not after: each probe pays a full Mathlib import,
    # and rejecting a negative --calls once minutes have been spent is a
    # traceback where a usage error belongs. Checked before the conversion
    # below too -- `round(nan)` raises ValueError and `round(inf)` raises
    # OverflowError, so converting first turns a usage error into a traceback.
    # Bounded above as well as below. Python integers do not overflow, but the
    # report renders milliseconds as seconds, and `10**308 * 12_000 / 1000`
    # exceeds what a float can hold -- so an absurd count completed every
    # expensive probe and then exited with an OverflowError traceback. A billion
    # Lean calls is already far beyond any run this measures.
    if args.calls is not None and not 0 <= args.calls <= MAX_CALLS:
        print(f"--calls must be between 0 and {MAX_CALLS}")
        return 2
    if args.total_seconds is not None and (
        not math.isfinite(args.total_seconds)
        or args.total_seconds < 0
        # Finite is not enough: 1e308 passes, and 1e308 * 1000 is `inf`, so the
        # conversion to milliseconds below raised OverflowError where a usage
        # error belonged.
        or not math.isfinite(args.total_seconds * 1_000)
    ):
        print("--total-seconds must be a finite, non-negative number of seconds")
        return 2
    # Finite, not merely positive: `nan` and `inf` both pass `<= 0`, and
    # `run_process` then builds a deadline that `time.monotonic()` can never
    # reach, so a stalled probe would run forever inside a command whose entire
    # contract is that every call is bounded.
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        print("--timeout must be a finite, positive number of seconds")
        return 2
    total_ms = None if args.total_seconds is None else round(args.total_seconds * 1_000)
    # A threshold argparse accepts as a float but that no share can be compared
    # against meaningfully: negative makes every run "warranted" including one
    # recovering nothing, NaN fails every comparison so nothing is ever
    # warranted, and above 1 can never be reached. Each manufactures a verdict
    # from malformed input rather than from the measurement.
    #
    # Zero belongs with the negatives, which the original bound missed by
    # admitting it. A single Lean call cannot avoid its own import, so it
    # recovers nothing, and `0% >= 0%` reported that as a warranted pool --
    # affirmative evidence for machinery that saves nothing at all.
    if not math.isfinite(args.threshold) or not 0.0 < args.threshold <= 1.0:
        print("--threshold must be a fraction above 0 and at most 1")
        return 2
    # Checked here rather than left to `measure_import_cost`, which only sees
    # it after `probe_toolchain` has already started a child that can sit on
    # the full deadline before the count is ever rejected.
    if args.repeats < 1:
        print("--repeats must be at least 1")
        return 2
    if args.workers < 1:
        print("--workers must be at least 1")
        return 2
    # Both or neither. One alone produced a report that asked for the other and
    # still exited 0, so a script could not tell an unanswered verdict from a
    # real one -- and it asked only after paying for every probe.
    if (args.calls is None) != (args.total_seconds is None):
        print("--calls and --total-seconds are given together or not at all")
        return 2
    project = config.lean_project if config.lean_project is not None else Path.cwd()
    # A deleted project makes `Popen` raise `FileNotFoundError` for the working
    # directory, which would otherwise be reported as a missing Lean; a project
    # path that is a regular file raises `NotADirectoryError` and escaped as a
    # traceback. Checked up front, as `LeanTools._run` and `doctor` both do.
    if not project.is_dir():
        print(f"Lean project directory not found: {project}")
        return 1
    # Resolved once, so the toolchain probe, the measurement, and the recorded
    # provenance all name the same absolute directory rather than a relative
    # path whose meaning depends on where the command happened to be invoked.
    project = project.resolve()
    # Asked of the Lean actually being invoked, not `_environment_identity`,
    # whose version and commit are constants pinned for the staged path and
    # would misattribute a `--lean-command` pointing at a different compiler.
    # Returns None when the toolchain cannot be identified, and the report then
    # says so rather than naming a version nobody verified.
    if not config.lean_command:
        print("no Lean command configured; set lean_command or pass --lean-command")
        return 2
    # Before any child starts, on stderr, flushed. Two reasons beyond habit:
    # a redirected stdout is block-buffered, so a warning printed there could
    # appear only after a multi-minute probe had already elaborated whatever
    # the user named -- and this report is evidence somebody will redirect to a
    # file, which the warning is not part of. AGENTS.md is explicit that Hardy
    # must never let unsandboxed elaboration pass unsaid.
    print(f"WARNING: {WARNING}", file=sys.stderr, flush=True)
    probe = latency.probe_toolchain(
        config.lean_command, project, timeout_seconds=args.timeout
    )
    try:
        cost = latency.measure_import_cost(
            imports,
            argv=(*config.lean_command, "--json"),
            cwd=project,
            timeout_seconds=args.timeout,
            repeats=args.repeats,
            environment=probe.identity,
            identity_note=probe.reason,
            manifest_bound=probe.manifest_bound,
        )
    except FileNotFoundError:
        print(f"Lean executable not found: {config.lean_command[0]}")
        return 1
    except OSError as error:
        # Not only FileNotFoundError. A command that exists but is not
        # executable raises PermissionError, and a wrong-format binary raises
        # OSError; both escaped as tracebacks, past a toolchain probe that had
        # already caught the identical failure and written down why.
        print(f"Lean command could not be run ({config.lean_command[0]}): {error}")
        return 1
    except ValueError as error:
        print(str(error))
        return 2
    return latency.report(
        cost,
        calls=args.calls,
        total_ms=total_ms,
        threshold=args.threshold,
        workers=args.workers,
    )
