"""A persistent computer algebra session.

Hardy can ask Lean whether a proof is correct but cannot compute anything, so
the model has no way to find out what is worth proving. This module is the
computation: one long-lived kernel per session, a durable log of the cells that
built its state, and the machinery to rebuild that state in a fresh process.

The kernel is persistent because the alternative is not affordable. Replaying
the accumulated script on every call would recompute a Gröbner basis every
turn. So state lives in a running process, and replay is kept for the two jobs
it is actually good at: rebuilding after a kernel dies, and proving that an
exported script reproduces what the session saw.

Two rules here are easy to get wrong and are therefore enforced in this file
rather than by its callers. Every call is serialised, because a kernel is one
stateful process behind one stdin stream and three different bindings can reach
it. And a rebuild compares what it reconstructed against what was recorded,
because a cell that runs without error may still have produced a different
value, and everything executed afterwards would be standing on it.

Nothing here is sandboxed. A cell can call `os.system`, Macaulay2's `run`, or
Singular's `system("sh", ...)`. Run only trusted output in a disposable
environment.
"""

from __future__ import annotations

import ast
import contextlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError, model_validator

from .domain import FrozenModel, RunLimits
from .layout import WriteGuard
from .process import (
    INTERRUPT_GRACE_SECONDS,
    child_creation,
    child_environment,
    kill_group,
    signal_interrupt,
    terminate_group,
)

HEADER_BYTES = 10
# A cell is bracketed by two markers, not trailed by one. A pipe preserves
# write order, so whatever the interpreter printed for the *previous* cell is
# necessarily before this cell's begin marker in the stream, however late it
# happens to arrive -- which is what lets the extractor exclude it without
# ever having to guess whether it has fully arrived yet.
SENTINEL_BEGIN = "«hardy-begin:{nonce}»"
SENTINEL_END = "«hardy-end:{nonce}»"
BackendName = Literal["sympy", "singular", "macaulay2"]

# Distinguishable non-answers from a read: the deadline passed, the user
# stopped waiting, or the stream said something that cannot belong to the cell
# we sent.
TIMED_OUT = object()
DESYNCHRONISED = object()
# The interrupt was asked for and the kernel did not answer it within the
# grace. Distinct from `TIMED_OUT` because the cell did not exceed anything --
# it was stopped -- and distinct from a kernel that *did* answer, which is the
# whole point of interrupting rather than timing out.
INTERRUPTED = object()

# How hard a stop has been asked for: signal and let the kernel answer, or stop
# waiting for an answer. Mirrors the levels `process` keeps for its own
# register, and for the same reason.
_ASKED, _INSISTED = 1, 2


class CasError(Exception):
    """A CAS call that cannot be answered, phrased for the model that asked."""


class CellOutcome(FrozenModel):
    """What an adapter extracts from one framed reply, before Hardy records it."""

    status: Literal["ok", "error", "kernel_died", "timeout", "interrupted"]
    stdout: str = ""
    stderr: str = ""
    value_repr: str = ""
    capture_truncated: bool = False
    # Whether the kernel has to be dropped because of this outcome. An
    # interrupt is the only status that goes both ways: a kernel that answers
    # one is still a kernel, with its namespace intact, which is the entire
    # reason for interrupting instead of timing out -- and one that does not
    # answer has to be stopped like any other unreachable kernel. This is not
    # on `CellRecord`: the durable log records what the cell did, and whether
    # the kernel survived is the session's live state, reported by
    # `cas_state` and by the restart note on the next cell.
    kernel_lost: bool = False
    # The kernel's fingerprint of its own namespace once this cell was done.
    # Empty when the backend cannot produce one -- a sentinel interpreter has
    # no protocol to carry it -- and `_restore` says so rather than claiming a
    # rebuild it could not check.
    state_digest: str = ""
    # Whether Hardy actually signalled this cell. A cell that reports `ok`
    # after being signalled is not acceptable even though it says it worked: a
    # cell -- or a library under it -- may catch the interrupt and return
    # normally from a path it would not otherwise have taken, and a replay
    # without the signal would then not reproduce it. It is recorded and
    # reported like any other cell; it just cannot be built on.
    signalled: bool = False



# Fields this model used to carry, dropped on the way in rather than refused.
# `FrozenModel` forbids extras, and `model_dump_json` writes every field --
# including a defaulted one nothing ever set -- into every line of the durable
# log. Retiring a field without this makes every log an earlier build wrote
# unloadable, and a `CasSession` that cannot be constructed takes chat startup
# down with it, which is the failure `_mend_log` already exists to avoid.
RETIRED_RECORD_FIELDS = ("output_artifact",)


class CellRecord(FrozenModel):
    seq: int
    # Incremented by reset. Only the highest segment is live, which is how a
    # reset survives a restart: it is on every record rather than inferred from
    # a sentinel line that a reader would have to know how to recognise.
    segment: int
    author: Literal["model", "human"]
    source: str
    # "interrupted" is never accepted, and for the same reason "error" is not:
    # the cell did not finish, and it may well have changed the namespace on
    # its way to being stopped. What it leaves behind is outside the accepted
    # set, exactly as an errored cell's is.
    status: Literal["ok", "error", "timeout", "kernel_died", "interrupted"]
    accepted: bool
    stdout: str = ""
    stderr: str = ""
    value_repr: str = ""
    duration_ms: int = 0
    capture_truncated: bool = False
    # The toolchain that produced this record, carried on the durable log
    # rather than only in an export manifest: a session that is saved but never
    # exported has no other place to say what ran it, and a log reopened under
    # a different `cas_backend` would otherwise be replayed as if the source
    # were the new backend's language. Defaulted to "" so logs written before
    # this field existed still load; `_foreign_backend` ignores empty values
    # for the same reason.
    backend: str = ""
    backend_version: str = ""
    # The kernel's fingerprint of its own namespace once this cell had run.
    # Recorded for every cell, failed ones included: a cell that raised partway
    # through has still changed the namespace, and a rebuild that replays only
    # the accepted cells has to be able to notice that what it rebuilt is not
    # what was there. Empty on a backend that cannot produce one, and on every
    # record written before this field existed -- `reproduces` compares it only
    # when the record carries one, so an older log still loads and still
    # replays.
    state_digest: str = ""
    # Hardy's own commentary on the cell -- currently only that the kernel was
    # rebuilt before it ran. Deliberately its own field rather than a line
    # prepended to `stdout`: `stdout` is what the kernel produced, and it is
    # what `reproduces` compares and what the export replays. A note mixed into
    # it makes the record unreproducible by construction, which poisons the
    # next rebuild and marks every post-restart cell `diverged` on export.
    restart_note: str = ""

    @model_validator(mode="before")
    @classmethod
    def _drop_retired_fields(cls, data: Any) -> Any:
        if isinstance(data, dict) and any(name in data for name in RETIRED_RECORD_FIELDS):
            return {key: value for key, value in data.items() if key not in RETIRED_RECORD_FIELDS}
        return data


class RebuildReport(FrozenModel):
    replayed: int = 0
    diverged: tuple[int, ...] = ()
    failed: int | None = None
    ok: bool = True
    # Cells whose replay reproduced everything Hardy can see and nothing more.
    # A cell that prints nothing and changes the namespace -- `import random;
    # x = random.random()` -- reproduces three empty fields however different
    # the value it rebuilt, so on a backend that carries no state digest a
    # clean replay of it is not evidence of a faithful rebuild. Named here so
    # the session can say which cells it could not check rather than reporting
    # a rebuild as if it had.
    unverified: tuple[int, ...] = ()


def normalise(text: str) -> str:
    """Compare outputs without being defeated by trailing whitespace.

    Trailing only, in both senses: whitespace at the end of each line, and
    whitespace at the end of the whole capture. Leading whitespace is content.
    `text.strip()` used to take it off the front as well, so a replay that
    printed `x` matched a session that had printed `  x` -- and the notebook,
    which stores the original bytes, showed the difference this function had
    just declared not to exist. Indentation is meaningful in every language
    Hardy drives, and in Macaulay2's pretty-printed matrices it is most of the
    value.
    """
    return "\n".join(line.rstrip() for line in text.rstrip().splitlines())


def reproduces(record: CellRecord, outcome: CellOutcome) -> bool:
    """Whether a replayed cell produced what the live session recorded.

    stderr counts. The notebook preserves it, so a cell whose warnings did not
    reproduce has not reproduced, whatever its stdout says.

    So does the namespace, where the backend can describe it. Output is what a
    cell *showed*, not what it *did*: `import random; x = random.random()`
    shows nothing at all, and comparing only what it showed called a replay
    that rebuilt a different `x` faithful -- with every later cell then
    standing on a value nobody had compared. The digest closes that, and it
    closes the other half of the same hole for free: a cell whose recorded
    digest includes an effect left behind by a *failed* cell (`x = 41; 1 / 0`,
    then an accepted `pass`) cannot match a replay that never ran the failure.

    Compared only when the record carries one. A log written before the field
    existed, or by a backend with no protocol to carry it, has nothing to
    compare -- `unobservable` is what says so, rather than this quietly
    passing.
    """
    if not same_output(record, outcome):
        return False
    if record.state_digest:
        return outcome.state_digest == record.state_digest
    return True


def same_output(record: CellRecord, outcome: CellOutcome) -> bool:
    """The half of `reproduces` a reader can see for themselves.

    Separate so a divergence can say *which* comparison failed. A silent
    `x = random.random()` reproduces every printed field and fails only on the
    digest, and calling that "different output" told the manifest and the
    notebook the opposite of what happened.
    """
    return (
        normalise(outcome.stdout) == normalise(record.stdout)
        and normalise(outcome.stderr) == normalise(record.stderr)
        and normalise(outcome.value_repr) == normalise(record.value_repr)
    )


def unobservable(record: CellRecord) -> bool:
    """Whether replaying this cell could prove anything about the state it left.

    A missing digest is the whole answer, and reproduced output is not a
    second opinion on it. This used to also require the cell to have printed
    nothing, on the theory that a cell which printed something had been
    checked -- but output is what a cell *showed*, and a cell that prints a
    stable banner is free to leave a different value behind it. The narrower
    rule reported an ordinary successful rebuild for exactly the cells it
    could not check.

    Empty digests are not rare, either: every sentinel backend, every record
    written before the field existed, and any namespace the default kernel
    could only fingerprint a prefix of.
    """
    return not record.state_digest


def _source_offset(source: str, lineno: int, col_offset: int) -> int:
    """A character index into `source` for one of `ast`'s positions.

    `col_offset` is a count of UTF-8 bytes, not characters, so a cell holding
    any non-ASCII text ahead of the position would be spliced in the wrong
    place by naive arithmetic.
    """
    lines = source.splitlines(keepends=True)
    if lineno - 1 >= len(lines):
        return len(source)
    prefix = sum(len(line) for line in lines[: lineno - 1])
    within = lines[lineno - 1].encode("utf-8")[:col_offset].decode("utf-8", errors="ignore")
    return prefix + len(within)


class SympyBackend:
    """The default backend: Hardy's own interpreter, driven over a byte protocol."""

    name: BackendName = "sympy"
    script_suffix = ".py"
    language = "python"
    kernel_name = "python3"
    framing = "length"
    comment = "#"
    # `import sys` is here for `render_cell` below, which needs `displayhook`
    # to make a trailing expression visible in the script. It goes after the
    # star import so nothing sympy exports can shadow the module.
    preamble = "from sympy import *\nimport sys"
    # Both halves, because both bear on a result. The digest is derived from
    # `repr` output and the exported script is executed by an interpreter, so
    # a different Python can change a representation, an ordering, or a
    # semantic without anything in the record saying which one produced the
    # verdict. `AGENTS.md` asks for the toolchain to be recorded when it can
    # affect results, and the interpreter is as much of it as the library.
    version_source = (
        '"sympy " + __import__("sympy").__version__ + " on " '
        '+ __import__("platform").python_implementation() + " " '
        '+ __import__("platform").python_version()'
    )
    # Not used for framing -- the driver protocol is length-prefixed and needs
    # no marker in the language. It is how `cas_export` asks a backend to print
    # the brackets around a script's own transcript, which every backend must
    # be able to do.
    echo = 'print("{marker}")'
    # The transcript brackets, which are not the same statements. The closing
    # one runs *after* every cell, so any name it resolves is a name the cells
    # have had their turn with: `print = lambda *_: None` swallows it, and
    # `__import__ = None` breaks the workaround for that. Every fix that
    # resolves a global at the end is one more name to shadow.
    #
    # So the end marker resolves nothing at the end. Every `__import__` below
    # runs before cell one, and the `partial` binds the destination as well as
    # the function: `print` with no `file` looks `sys.stdout` up when it runs,
    # so a cell that reassigns it would redirect the closing marker even
    # though the function itself was captured. Both are settled at
    # registration, and the interpreter emits the marker at shutdown -- after
    # the module body, after everything the file printed, out of reach of any
    # global a cell can touch. The begin marker needs no such care: it has
    # already run before a cell can rebind anything.
    transcript_prologue = (
        '__import__("atexit").register(__import__("functools").partial('
        '__import__("builtins").print, "{end}", '
        'file=__import__("sys").stdout))',
        '__import__("builtins").print("{begin}")',
    )
    transcript_epilogue: tuple[str, ...] = ()
    # The exported script is run as an ordinary Python program, not through the
    # driver: the artifact under test is the file a reader would run.
    script_stdin = False
    # Python randomises string hashing per process, so `repr` of a set or of a
    # dict keyed by strings orders itself differently in every kernel. Nothing
    # in a session causes that, and everything Hardy compares across processes
    # -- a rebuild's output, an export's transcript, and now a cell's state
    # digest -- would read it as the session having failed to reproduce. Pinned
    # for the kernel and for the exported script alike, so the file a reader
    # runs is run the way its record was made.
    environment: dict[str, str] = {"PYTHONHASHSEED": "0"}
    # This backend's protocol carries a fingerprint of the namespace, so a
    # record without one means something specific happened -- a value Hardy
    # could only see in prefix, or one whose repr says nothing about what it
    # holds. Sentinel backends have no such protocol, where a missing digest
    # is the norm and carries no signal; `records_state` is what lets an
    # export tell those two silences apart.
    records_state = True

    def argv(self, command: Path | None, max_output_bytes: int = 256 * 1024) -> tuple[str, ...]:
        return (
            str(command) if command else sys.executable,
            "-u",
            "-m",
            "hardy.cas_driver",
            str(max_output_bytes),
        )

    def script_argv(self, command: Path | None, script: Path) -> tuple[str, ...]:
        return (str(command) if command else sys.executable, "-u", str(script))

    def frame(self, source: str, nonce: str, stopping: bool = False) -> bytes:
        """`stopping` is whether a stop is still in force as this cell goes out.

        It is how a stop the driver is *holding* gets discarded. A signal can
        land in the moment after the driver has flushed a reply and before
        Hardy has noticed, where the driver is between cells and can only
        remember it -- and that memory would otherwise reject the next cell,
        which nobody asked to stop. Hardy knows whether it still wants one, and
        says so with every cell.
        """
        payload = json.dumps(
            {"source": source, "stopping": stopping}, ensure_ascii=False
        ).encode("utf-8")
        return f"{len(payload):0{HEADER_BYTES}d}".encode("ascii") + payload

    def sanitize(self, stdout: str, fed: str = "") -> str:
        """No cleanup: the driver hands back exactly what the cell captured."""
        return stdout

    def render_cell(self, source: str) -> str:
        """Emit a cell that shows in a script what it showed in the session.

        This is the difference between a script and a kernel. `cas_driver`
        splits off a trailing expression and evaluates it, so `2 + 2` is
        recorded with `value_repr="4"`; `exec` in a plain script discards that
        value and prints nothing. Writing the raw source out and then calling
        the pair verified was a claim about an artifact that did not behave
        the way the record said, so the trailing expression is handed to
        `sys.displayhook` instead -- which is precisely what the driver does
        with it: nothing when the value is None, otherwise bind `_` and print
        the repr.

        Only the trailing expression is touched, and it is spliced by source
        offsets rather than re-unparsed, so the rest of the cell reaches the
        reader exactly as it was written -- comments, spacing and all.
        """
        try:
            parsed = ast.parse(source)
        except SyntaxError:
            # Unreachable for an accepted cell, and not this function's business
            # to diagnose: the script keeps the source and the run will say so.
            return source
        if not parsed.body or not isinstance(parsed.body[-1], ast.Expr):
            return source
        trailing = parsed.body[-1]
        start = _source_offset(source, trailing.lineno, trailing.col_offset)
        end = _source_offset(
            source,
            trailing.end_lineno or trailing.lineno,
            trailing.end_col_offset or 0,
        )
        # The inner parentheses are load-bearing and are not the call's. A
        # trailing expression may have a top-level comma -- `x, y` is ordinary
        # CAS usage -- and an argument list splits on exactly that: `x, y`
        # became two arguments and the published script died with "displayhook()
        # takes exactly one argument", while `x,` became one argument and
        # printed `x` where the record said `(x,)`. Parenthesised first, the
        # comma builds the tuple the driver evaluated. They also make an
        # expression spread over several lines stay one expression.
        return f"{source[:start]}sys.displayhook(({source[start:end]})){source[end:]}"

    def parse_version(self, sanitized_stdout: str) -> str:
        return sanitized_stdout


class _SentinelBackend:
    """An interpreter reading stdin, framed by a nonce it is asked to echo.

    Less trustworthy than the driver protocol and unavoidable: neither Singular
    nor Macaulay2 offers a way to be spoken to in frames. The nonce is fresh
    per cell so a cell that echoes text cannot forge the end of its own reply.
    """

    framing = "sentinel"
    error_pattern: re.Pattern[str]
    echo: str
    environment: dict[str, str] = {}
    # Neither Singular nor Macaulay2 can be asked what is in its namespace, so
    # every record from them is digestless and no verdict can be drawn from
    # that. What their replays check is what those replays can check.
    records_state = False

    @property
    def transcript_prologue(self) -> tuple[str, ...]:
        """The `echo` statement itself. Singular and Macaulay2 have no
        shutdown hook to hand the closing marker to, so both markers are the
        statement the live protocol already relies on these interpreters
        executing, one at each end of the file."""
        return (self.echo.format(marker="{begin}"),)

    @property
    def transcript_epilogue(self) -> tuple[str, ...]:
        return (self.echo.format(marker="{end}"),)
    # The exported script is fed to the interpreter on stdin, which is how the
    # session itself runs cells: same argv, same input mode, so the transcript
    # the check compares against the record is produced the same way the record
    # was. A file named on the command line is a different execution mode in
    # both Singular and Macaulay2, and verifying one while shipping the other
    # would be the defect this check exists to catch.
    script_stdin = True

    def argv(self, command: Path | None, max_output_bytes: int = 256 * 1024) -> tuple[str, ...]:
        raise NotImplementedError

    def script_argv(self, command: Path | None, script: Path) -> tuple[str, ...]:
        return self.argv(command)

    def render_cell(self, source: str) -> str:
        """Verbatim. These interpreters print a statement's value themselves."""
        return source

    def frame(self, source: str, nonce: str, stopping: bool = False) -> bytes:
        """`stopping` is accepted and ignored: a line-oriented interpreter has
        no protocol for a stop it is holding, and nothing to discard."""
        begin = SENTINEL_BEGIN.format(nonce=nonce)
        end = SENTINEL_END.format(nonce=nonce)
        return (
            self.echo.format(marker=begin)
            + "\n"
            + source.rstrip()
            + "\n"
            + self.echo.format(marker=end)
            + "\n"
        ).encode("utf-8")

    def classify(self, stdout: str, stderr: str = "") -> Literal["ok", "error"]:
        found = self.error_pattern.search(stdout) or self.error_pattern.search(stderr)
        return "error" if found else "ok"

    def sanitize(self, stdout: str, fed: str = "") -> str:
        """Backend-specific stdout cleanup, applied to a cell's captured body
        before it is recorded. Identity by default; Macaulay2 overrides it.

        `fed` is the text the interpreter was given to produce this output --
        the framed cell for a live round trip, the whole file for an exported
        script. An interpreter that echoes its input needs it to tell its own
        echo from what it computed; one that does not may ignore it.
        """
        return stdout

    def parse_version(self, sanitized_stdout: str) -> str:
        """Pull the bare version string out of an already-`sanitize`d reply.

        Identity by default. Macaulay2 overrides it: `sanitize` deliberately
        leaves an `o = ` value marker in place for ordinary cells (it is
        meaningful context there), but `probe_version` wants just the value.
        """
        return sanitized_stdout


class SingularBackend(_SentinelBackend):
    name: BackendName = "singular"
    script_suffix = ".sing"
    language = "singular"
    kernel_name = "singular"
    comment = "//"
    preamble = ""
    version_source = 'system("version");'
    echo = 'print("{marker}");'
    # Singular indents its `?` error banner by call-stack depth, not a fixed
    # maximum -- an error raised inside a nested procedure can be indented
    # arbitrarily far. Any run of leading horizontal whitespace counts;
    # newlines are excluded so this stays anchored to one line's own start.
    error_pattern = re.compile(r"(?m)^[ \t]*\? ")

    def argv(self, command: Path | None, max_output_bytes: int = 256 * 1024) -> tuple[str, ...]:
        return (str(command) if command else "Singular", "-q")


class Macaulay2Backend(_SentinelBackend):
    name: BackendName = "macaulay2"
    script_suffix = ".m2"
    language = "macaulay2"
    kernel_name = "macaulay2"
    comment = "--"
    preamble = ""
    version_source = 'version#"VERSION"'
    echo = '<< "{marker}" << endl;'
    # Observed verbatim from M2 1.26.06 (CI run 30167266358, "Debug M2 raw
    # transcript"): a division-by-zero cell wrote
    # `stdio:2:1:(3):[1]: error: division by zero` to *stderr*, and a call on
    # an undefined symbol wrote
    # `stdio:2:16:(3):[1]: error: no method for adjacent objects:`. Both carry
    # a `:[N]:` interpreter-depth marker between the `(FRAME)` and `error:`
    # that the original guess did not have, and both landed on stderr, not
    # stdout -- see `classify`/`sanitize` below for how that is handled.
    #
    # The `:[N]:` segment is made optional, not required: both samples came
    # from one M2 build, and a build (or error path) that omits it -- the
    # form the pre-verification guess used -- must still classify as
    # "error". A false negative here is accepted into replayable state and
    # the session rebuilds from a cell that never worked, which is strictly
    # worse than a false positive; there is no cost to the wider pattern.
    error_pattern = re.compile(r"(?m)^stdio:\d+:\d+:\(\d+\)(?::\[\d+\])?: error:")
    # M2 echoes each `iN : ` input prompt (and the source line behind it) even
    # when stdin is not a tty, and prints an `oN` counter before every
    # non-suppressed statement's value. Observed verbatim in the same run: a
    # cell containing `R = QQ[x, y]; f = x^2 + y^2; f` came back as
    # `i2 : R = QQ[x, y]; f = x^2 + y^2; f\n\n      2    2\no4 = x  + y\n\no4 : R`.
    # The `iN :` lines are pure noise -- an echo of source Hardy already has on
    # the `CellRecord` -- and the `oN` counter drifts with how many statements
    # ran before it, which is different for a live session (that has already
    # run a version probe) than for the fresh kernel `replay_in_fresh_kernel`
    # starts for export verification. Left unstripped, a cell that reproduces
    # exactly is still reported `diverged` on the counter alone (confirmed:
    # CI run 30167033381 marked the one cell in
    # `test_an_exported_session_reproduces[macaulay2]` diverged on precisely
    # this transcript). Stripping the prompt lines and blanking the counter
    # digits is the fix for the prompt-noise defect flagged in this task's
    # brief.
    _prompt_line = re.compile(r"(?m)^i\d+ : .*\n")
    # The prompt only introduces the echo. M2 keeps echoing under a run of
    # spaces exactly as wide as `iN : ` for every further line it reads before
    # a statement completes, and a comment or a blank line never completes one
    # -- so the header and the `-- --- cell N` note Hardy writes into an
    # exported script come back embedded in the transcript, with only their
    # first line wearing a prompt. Confirmed verbatim in CI run 30175627022:
    # `i2 : \n     -- --- cell 1 (model)\n     x^2 + y^2\n\n      2    2\n
    # o2 = x  + y`. Every cell after the first was therefore separated from
    # the next by two lines the session never printed, and
    # `_appears_in_order` -- which tolerates the interpreter's chrome around
    # the transcript but nothing inside it -- could not match. No Macaulay2
    # session of more than one cell has ever been able to export `verified`.
    _echo_prompt = re.compile(r"^i\d+ : ")
    _output_counter = re.compile(r"(?m)^o\d+(?=[ :=])")
    # The counter is not only a token, it is a *column*. M2 pretty-prints a
    # value as a net: `o4 = x  + y` with its exponent row `      2    2` laid
    # out above it, and every row other than the first is padded to the width
    # of the `oN = ` prefix. Matching that prefix as well as the counter is how
    # the padding can be shrunk by exactly as much as the counter is.
    _output_prefix = re.compile(r"^o(\d+) [:=] ")
    # `[ \t]`, not `\s`: `\s` matches newlines too, which would let this
    # cross onto whatever comes after the marker's own line instead of
    # stopping at it.
    _value_marker = re.compile(r"(?m)^o[ \t]*=[ \t]*")

    def _strip_echo(self, stdout: str, fed: str) -> str:
        """Remove M2's echo of the input, prompt line and continuations alike.

        The prompt line goes whatever is on it -- it is always an echo. A
        continuation line goes only when it is indented to exactly that
        prompt's width *and* what is under the indent is verbatim a line of
        the text the interpreter was fed. Both conditions are needed: an
        alignment row is indented to the same width by coincidence, and a cell
        that prints is free to reproduce its own source. A truly empty line
        ends the block, which is what M2 puts between an echo and the value it
        then computes; a fed blank line comes back as the indent alone and so
        does not end anything.

        What survives this and should not is narrow enough to name: output
        indented by exactly the prompt width, abutting the echo with no blank
        line between, whose text is verbatim one of the lines fed in.
        """
        echoed = {line.rstrip() for line in fed.splitlines() if line.strip()}
        kept: list[str] = []
        width = 0
        for line in stdout.split("\n"):
            prompt = self._echo_prompt.match(line)
            if prompt is not None:
                width = prompt.end()
                continue
            if not line:
                width = 0
            elif width and line.startswith(" " * width) and line[width:].rstrip() in echoed:
                continue
            kept.append(line)
        return "\n".join(kept)

    def sanitize(self, stdout: str, fed: str = "") -> str:
        """Drop M2's echoed prompts and make its output counters comparable.

        Blanking the digits is not enough on its own. `oN = ` is five columns
        wide at `o4` and six at `o12`, and M2 indents a value's alignment rows
        to exactly that width -- so a session and a fresh replay that computed
        the identical polynomial printed alignment rows differing by one space
        as soon as their counters differed in digit count. Nothing in the
        session causes the counters to agree: every cell costs a live kernel
        two extra statements for its own sentinel markers, which the exported
        script does not have. The result was a false `diverged` on export and,
        through `_restore`, a poisoned session over a cell that had reproduced
        perfectly.

        So the rows are dedented by however many characters the counter loses.
        A block is the marker line together with the run of lines immediately
        above and below it that are indented to at least the prefix width --
        which is what M2's net padding guarantees and what any other line
        (blank, a prompt, the next marker) is not.

        The echo goes first, and only when the caller could say what was fed:
        without that there is no way to tell an echoed continuation line from
        an alignment row, and `_prompt_line` remains the older, weaker rule
        that at least takes the prompt lines themselves.
        """
        stdout = self._strip_echo(stdout, fed) if fed else self._prompt_line.sub("", stdout)
        lines = stdout.split("\n")
        dedent = [0] * len(lines)
        for index, line in enumerate(lines):
            prefix = self._output_prefix.match(line)
            if prefix is None:
                continue
            drop = len(prefix.group(1)) - 1
            if drop <= 0:
                continue
            padding = " " * prefix.end()
            for step in (-1, 1):
                at = index + step
                while 0 <= at < len(lines) and lines[at].startswith(padding):
                    dedent[at] = max(dedent[at], drop)
                    at += step
        return self._output_counter.sub(
            "o",
            "\n".join(
                line[width:] if width and line.startswith(" " * width) else line
                for line, width in zip(lines, dedent, strict=True)
            ),
        )

    def parse_version(self, sanitized_stdout: str) -> str:
        # Confirmed of CI run 30168046413's "Debug sanitized M2 stdout" step:
        # without this, session.version came back as the literal string
        # 'o = 1.26.06' -- the `o = ` value marker `sanitize` leaves in place
        # is exactly right for an ordinary cell but wrong for a version
        # string quoted into an exported script's header comment.
        value = self._value_marker.sub("", sanitized_stdout, count=1).strip()
        # `version#"VERSION"` is a plain string, and the one probe transcript
        # captured directly (CI run 30168174637) showed no further lines --
        # but M2 prints an `o : ClassName` annotation after some result
        # types (confirmed for a ring element: `o4 : R`), and `sanitize`
        # would leave that as `o : String` rather than strip it, same as it
        # leaves `o = ` for an ordinary cell. Taking only the first line is
        # correct either way: the version string itself never contains a
        # newline, so this is a no-op when the annotation line is absent
        # and the fix when it is not.
        first_line, _, _ = value.partition("\n")
        return first_line

    def argv(self, command: Path | None, max_output_bytes: int = 256 * 1024) -> tuple[str, ...]:
        # `-s` was a guess and is obsolete in Macaulay2 1.26.06 (CI run
        # 30166702246: "error: command line option -s is obsolete." killed the
        # kernel before it could answer the version probe). Dropped, not
        # replaced -- there is no confirmed silent-mode equivalent yet.
        return (str(command) if command else "M2", "--no-readline", "-q")


BACKENDS: dict[str, Any] = {
    "sympy": SympyBackend,
    "singular": SingularBackend,
    "macaulay2": Macaulay2Backend,
}


def backend_for(name: str) -> Any:
    try:
        return BACKENDS[name]()
    except KeyError:
        raise ValueError(
            f"unknown cas_backend {name!r}; known backends are {sorted(BACKENDS)}"
        ) from None


class _Kernel:
    """One live child, drained by threads so a deadline can always be enforced."""

    def __init__(
        self,
        argv: Sequence[str],
        cwd: Path,
        max_output_bytes: int,
        environment: dict[str, str] | None = None,
    ) -> None:
        self.argv = tuple(argv)
        self.max_output_bytes = max_output_bytes
        self.out = bytearray()
        self.err = bytearray()
        self.truncated = False
        self._finished = 0
        self._marker = b""
        self.marker_seen = False
        self._tail = b""
        self._changed = threading.Condition()
        cwd.mkdir(parents=True, exist_ok=True)
        # Its own process group (see `child_creation`), so an interrupt reaches
        # a cell that shelled out as well as the interpreter that started it,
        # and so nothing aimed at Hardy's own group lands here by accident.
        self.process = subprocess.Popen(
            self.argv,
            cwd=str(cwd),
            env=child_environment(dict(environment or {})),
            shell=False,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **child_creation(),
        )
        for pipe, destination in ((self.process.stdout, self.out), (self.process.stderr, self.err)):
            threading.Thread(target=self._drain, args=(pipe, destination), daemon=True).start()

    def _drain(self, pipe, destination: bytearray) -> None:
        # read1, not read: a buffered `read(n)` blocks until it has all n bytes
        # or the stream ends, and a persistent kernel never ends. The reply to
        # a small cell would sit in the pipe unread forever.
        try:
            while chunk := pipe.read1(4_096):
                with self._changed:
                    room = max(0, self.max_output_bytes - len(destination))
                    destination.extend(chunk[:room])
                    if len(chunk) > room:
                        self.truncated = True
                    # Retention stops at the cap; scanning does not. A sentinel
                    # backend that overran the cap would otherwise never be seen
                    # to finish, and a large answer would read as a dead kernel.
                    if self._marker and destination is self.out:
                        self._tail = (self._tail + chunk)[-(len(self._marker) + 4_096) :]
                        if self._marker in self._tail:
                            self.marker_seen = True
                    self._changed.notify_all()
        except (OSError, ValueError):
            # `kill()` closes these pipes, and a `read1` already in flight on
            # one of them does not politely return b"" -- it raises, on a
            # thread with nobody to catch it, and Python prints the traceback
            # to stderr as if Hardy had crashed. `_drain_capped` has caught
            # exactly this pair since it was written; there is no reason for
            # the two drains to disagree. The stream is over either way, which
            # is what the `finally` below records.
            pass
        finally:
            with self._changed:
                self._finished += 1
                self._changed.notify_all()

    def clear(self, marker: bytes = b"") -> None:
        """Discard everything read so far and scan fresh for `marker`.

        Only for the length path: the driver emits exactly one frame and
        nothing else, so there is never anything worth keeping behind it.
        """
        with self._changed:
            self.out.clear()
            self.err.clear()
            self.truncated = False
            self._marker = marker
            self.marker_seen = False
            self._tail = b""

    def consume(self, upto: int) -> None:
        """Drop the bytes belonging to the cell just answered, keep the rest.

        A prompt printed after the end marker belongs to no cell. It is not
        deleted here on the theory that it might not have arrived yet -- it
        might not have -- but that no longer matters: the *next* cell's begin
        marker, once found, is proof that everything before it, arrived or
        not at the time this runs, is behind it in the stream.
        """
        with self._changed:
            del self.out[:upto]
            self.truncated = False
            self._marker = b""
            self.marker_seen = False
            self._tail = b""

    def rearm(self, marker: bytes) -> None:
        """Scan fresh for the end `marker` without discarding what is in `out`.

        Nothing here needs to guess whether the previous cell's trailing
        prompt has fully arrived: the begin marker this cell is about to send
        settles that by pipe order alone, once the extractor finds it.
        """
        with self._changed:
            self.err.clear()
            self.truncated = False
            self._marker = marker
            self.marker_seen = False
            self._tail = b""

    def send(self, payload: bytes) -> bool:
        stdin = self.process.stdin
        if stdin is None:
            return False
        try:
            stdin.write(payload)
            stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            return False
        return True

    def read_reply(
        self,
        extract: Callable[[bytes], Any],
        deadline: float,
        interrupted: threading.Event | None = None,
    ) -> Any:
        """Wait for a complete reply, the kernel's death, the deadline, or a stop.

        The extractor sees raw bytes. Decoding first would make a partial
        multi-byte character into a replacement character three bytes wide,
        and a length-prefixed frame would then look complete before it was.

        `interrupted` is set by whoever pressed Esc, on their thread, after the
        signal has already gone to the child. It does not end the wait by
        itself: an interrupted kernel is *expected* to answer -- the driver
        turns the signal into a traceback and replies with it, which is what
        leaves the namespace intact -- so the reply is still what this is
        waiting for, only now with a much shorter deadline. `INTERRUPTED` is
        the answer that no reply came within the grace, and it means the kernel
        can no longer be spoken to.
        """
        with self._changed:
            grace_deadline: float | None = None
            while True:
                found = extract(bytes(self.out))
                # Checked before the interrupt, so a cell that finished in the
                # same instant Esc was pressed is reported as what it did
                # rather than as what the user asked for a moment too late.
                if found is not None:
                    return found
                if self._finished >= 2:
                    return None
                now = time.monotonic()
                if interrupted is not None and interrupted.is_set() and grace_deadline is None:
                    grace_deadline = now + INTERRUPT_GRACE_SECONDS
                stop_at = deadline if grace_deadline is None else min(deadline, grace_deadline)
                remaining = stop_at - now
                if remaining <= 0:
                    # A stop that was asked for is reported as one even if the
                    # cell's own deadline happened to pass while the kernel was
                    # being given its grace. The user stopped this; calling it
                    # a timeout would credit the limit for what Esc did.
                    if interrupted is not None and interrupted.is_set():
                        return INTERRUPTED
                    return TIMED_OUT
                # Nothing notifies this condition when the interrupt flag is
                # set on another thread, so the poll interval is also what
                # bounds how long it takes to notice one.
                self._changed.wait(min(remaining, 0.05))

    def stderr_text(self) -> str:
        with self._changed:
            return bytes(self.err).decode("utf-8", errors="replace")

    def stderr_settled(self, timeout: float = 0.2, quiet: float = 0.02) -> str:
        """Stderr once it has stopped growing, not just whatever is in yet.

        A sentinel cell's own interpreter is single-threaded: an error for
        the cell is necessarily written to stderr before the interpreter goes
        on to process the end-marker echo that shows up on stdout, which is
        what `read_reply` waits for. But that ordering is *inside the child*
        -- stdout and stderr are two independent pipes drained by two
        independent threads here, and nothing ties their delivery to Hardy
        together. Reading stderr the instant the stdout marker is found (as
        this used to do) can win a race against the drain thread that has not
        yet appended bytes already sitting in the OS pipe, silently reading a
        broken M2 cell as clean.

        `quiet` seconds have to pass with no growth in `self.err`, measured
        against the wall clock -- not "the next wakeup shows no growth",
        which the stdout drain thread's `notify_all()` on every chunk
        defeats: it wakes this wait long before `quiet` has actually
        elapsed, so a between-wakeups check would report "settled" on a
        stdout-driven spurious wakeup microseconds in, never having waited
        at all.
        """
        with self._changed:
            deadline = time.monotonic() + timeout
            last_growth = time.monotonic()
            last_len = len(self.err)
            while True:
                now = time.monotonic()
                if now - last_growth >= quiet:
                    break
                remaining = deadline - now
                if remaining <= 0:
                    break
                self._changed.wait(min(quiet - (now - last_growth), remaining))
                current_len = len(self.err)
                if current_len != last_len:
                    last_len = current_len
                    last_growth = time.monotonic()
            return bytes(self.err).decode("utf-8", errors="replace")

    def interrupt(self) -> bool:
        """Ask the cell in flight to stop, leaving the kernel alive to say so."""
        return signal_interrupt(self.process)

    def kill(self, *, immediate: bool = False) -> None:
        """Stop the kernel. `immediate` skips the polite half.

        The graceful teardown asks with SIGTERM and waits up to two seconds
        before SIGKILL, which is right when a session is closing. It is wrong
        for the second Esc: that runs on the terminal's own event loop, so the
        wait would freeze the UI -- for exactly as long as the press was made
        to avoid waiting.
        """
        # The group, whether or not the interpreter still leads it. A cell that
        # shelled out leaves its helpers in the group, and an interpreter that
        # took the signal and exited leaves them there with no leader -- still
        # running, and still holding the pipes this session drains. Gating on
        # `poll()` would skip exactly that case, which is why the shared group
        # helpers do not gate on it either.
        if immediate:
            kill_group(self.process)
        else:
            terminate_group(self.process)
        if self.process.poll() is None:
            try:
                # SIGKILL cannot be caught, so the immediate path is only ever
                # waiting to reap; the polite one is waiting on the child.
                self.process.wait(timeout=0 if immediate else 2)
            except subprocess.TimeoutExpired:
                kill_group(self.process)
                self.process.wait()
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None:
                with contextlib.suppress(OSError):
                    stream.close()


class CasSession:
    """A durable cell log and the kernel that its accepted cells describe."""

    def __init__(
        self,
        *,
        backend: Any,
        command: Path | None,
        log_path: Path,
        limits: RunLimits,
        cwd: Path | None = None,
        observe: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.backend = backend
        self.command = command
        self.log_path = log_path
        # The only door the cell log is read or written through. It matters
        # more here than anywhere else in Hardy: `cells.jsonl` is a versioned
        # file a clone can ship as a symlink, and `_truncate_log` opens the log
        # `r+b` and TRUNCATES it -- so a followed link would not merely append
        # to whatever it named, it would destroy it. `Layout.ensure` cannot
        # help: it runs once at startup and never enumerates this file.
        #
        # Not created here. A session whose log directory cannot be made --
        # the path is a plain file, the disk is full -- must still be
        # constructible, and fail at the append, where the failure is a
        # poisoned session naming the log rather than a constructor that
        # raised. `_append` creates the directory, and the guard pins it then.
        self._log = WriteGuard(log_path.parent)
        self.limits = limits
        self.cwd = cwd or log_path.parent
        self.observe = observe
        self.state: Literal["cold", "live", "dead", "poisoned"] = "cold"
        self.version: str | None = None
        self.spent_seconds = 0.0
        # One stateful process behind one stdin stream, reachable from chat,
        # staged runs, and MCP. The lock belongs to the resource, not a caller.
        self._lock = threading.RLock()
        self._kernel: _Kernel | None = None
        # Both read and written without the lock, on purpose: the thread that
        # holds it is the one inside `execute`, waiting on the kernel, and that
        # is precisely the thread an interrupt has to reach. An `Event` rather
        # than a bool because these cross threads with no lock between them.
        #
        # `_in_flight` is what makes an interrupt safe to send at all. A signal
        # delivered between cells lands on a driver blocked reading stdin,
        # where it has no cell to abandon and nothing to answer with, so
        # `interrupt` refuses unless a cell is actually out there.
        self._in_flight = threading.Event()
        self._interrupted = threading.Event()
        # The window between the frame being built and the kernel having read
        # it. The write is to a pipe, so a kernel that has stopped reading --
        # the deaf one this whole change exists for -- lets a large enough cell
        # fill the buffer and block it. Nothing is in flight yet, so `interrupt`
        # has nothing to ask; but `escalate` does, because killing the kernel
        # is what ends the write, and without that a session could hang there
        # with no deadline running and no press that could reach it.
        self._sending = threading.Event()
        # Held across "write the frame and arm the flag" and across "decide
        # whether to signal", so the two cannot interleave. Without it a press
        # landing between the two reached a driver that was still idle: the
        # signal was swallowed by the between-cells handler, the cell went out
        # immediately afterwards, and the only press was already spent -- so
        # the cell ran on until the grace expired and took the kernel with it,
        # which is the exact loss interrupting exists to avoid.
        self._signal_lock = threading.Lock()
        # How hard a stop asked for while no cell was in flight was asked,
        # remembered so the cell about to go out is stopped rather than the
        # press being lost. A level rather than a flag for the same reason the
        # register in `process` keeps one: if both presses land before the cell
        # is sent, a flag would give it the first press's signal and lose the
        # second, so a deaf cell would sit out a grace the user had already
        # declined to wait for. Lifted by `resume`, at the start of the next
        # turn or command, exactly as that register is.
        self._stop_level = 0
        self._records: list[CellRecord] = self._load()

    # ------------------------------------------------------------------ log

    def _load(self) -> list[CellRecord]:
        if not self.log_path.exists():
            return []
        raw = self._repair_interrupted_append(self._read_log())
        records = []
        for line in raw.split(b"\n"):
            # Bytes, not text: a torn write can cut a multi-byte character in
            # half, and decoding the whole file first would fail on the very
            # record this is here to survive.
            if line.strip():
                records.append(CellRecord.model_validate_json(line))
        return records

    def _repair_interrupted_append(self, raw: bytes) -> bytes:
        """Heal a final record that a crash cut off mid-write.

        Every append is one write of `record + "\\n"` followed by an fsync, so
        a log that does not end in a newline ended in an append that did not
        finish. That is the *only* damage tolerated here: a record with a
        terminator behind it was durable, and a bad one is corruption that must
        still be refused rather than quietly dropped.

        The partial bytes are removed from the file, not merely skipped. The
        log is appended to, so leaving them would glue the next record onto the
        fragment and turn a one-off interruption into a line that is neither
        final nor valid -- permanently unreadable, which is the failure this
        exists to prevent.
        """
        if not raw or raw.endswith(b"\n"):
            return raw
        head, separator, tail = raw.rpartition(b"\n")
        try:
            CellRecord.model_validate_json(tail)
        except ValidationError:
            kept = head + separator
            self._mend_log(truncate_to=len(kept))
            return kept
        # The record itself arrived; only its terminator was lost. Nothing is
        # discarded -- the newline is supplied so the next append starts on its
        # own line.
        self._mend_log(truncate_to=None)
        return raw + b"\n"

    def _read_log(self) -> bytes:
        """Every byte of the log, through the guard.

        Reading is guarded as strictly as writing. A symlinked log read at load
        time would have this session answer from cells recorded somewhere
        outside the project entirely, and only notice when the first append was
        refused -- long after it had replayed a stranger's history as its own.
        """
        with self._log.open(self.log_path.name, "rb") as stream:
            return stream.read()

    def _truncate_log(self, size: int) -> None:
        with self._log.open(self.log_path.name, "r+b") as stream:
            stream.truncate(size)
            stream.flush()
            os.fsync(stream.fileno())

    def _terminate_log(self) -> None:
        with self._log.open(self.log_path.name, "ab") as stream:
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _mend_log(self, *, truncate_to: int | None) -> None:
        """Write the repair to disk, or leave the file alone if it will not take it.

        Best effort on purpose. This runs from `_load`, which runs from
        `__init__`, so an `OSError` escaping here -- a read-only workspace, a
        full disk -- would stop the session being constructed at all. That is
        the same startup failure a torn record used to cause, arriving by a
        different route, and this method exists to fix that failure rather than
        to relocate it.

        A log that can be read but not repaired still opens: the fragment is
        skipped in memory, and `_append` redoes the repair before it writes,
        which is where refusing it turns into a poisoned session rather than
        into a record glued onto a fragment.
        """
        with contextlib.suppress(OSError):
            if truncate_to is None:
                self._terminate_log()
            else:
                self._truncate_log(truncate_to)

    def _ensure_terminated(self) -> None:
        """Leave the log ending on a record boundary, or raise `OSError`.

        `_mend_log` swallows the failure it may hit, so a repair that the
        filesystem refused at load time exists only in memory: `_load` returns
        the healed bytes while the file still ends mid-record. The old claim
        that a filesystem refusing the repair would refuse the append too is
        simply untrue for a transient failure -- ENOSPC, a quota freed a minute
        later, an `fsync` that returned EINVAL once. The next append then lands
        on the fragment and welds it into a line that is neither final nor
        valid, which is exactly the permanently unreadable log
        `_repair_interrupted_append` exists to prevent.

        So the repair is redone here, where it is allowed to fail loudly: this
        runs inside `_append`, whose caller has already decided that a log it
        cannot write means a poisoned session rather than a silent one.
        """
        try:
            size = self.log_path.stat().st_size
        except FileNotFoundError:
            return
        if not size:
            return
        with self._log.open(self.log_path.name, "rb") as stream:
            stream.seek(-1, os.SEEK_END)
            if stream.read(1) == b"\n":
                return
        # Same rule as at load: a well-formed final record lost only its
        # terminator and is kept, anything else is a fragment and goes. The
        # whole file is read only on this path, which is the damaged one.
        head, separator, tail = self._read_log().rpartition(b"\n")
        try:
            CellRecord.model_validate_json(tail)
        except ValidationError:
            self._truncate_log(len(head + separator))
        else:
            self._terminate_log()

    def _append(self, record: CellRecord) -> CellRecord:
        """Publish a record, durably first and only then in memory.

        The order matters in a long-lived server. A record added to `_records`
        before the write succeeded numbers and anchors everything after it, so
        a workspace that filled up or went read-only would keep answering from
        a history that a restart cannot reconstruct. If the log cannot be
        written the cell has already run, so what the kernel holds is no longer
        described by anything durable: the session is poisoned rather than
        allowed to continue on state it can never explain.
        """
        try:
            # Through the guard, which re-proves the directory and re-pins it
            # if it had to be recreated: a session whose workspace was deleted
            # underneath it must still be able to record the cell that just
            # ran, and the fresh directory has a fresh inode.
            self._log.mkdir()
            self._ensure_terminated()
            with self._log.open(self.log_path.name, "ab") as stream:
                stream.write(record.model_dump_json().encode("utf-8") + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as error:
            self._drop_kernel()
            self.state = "poisoned"
            raise CasError(
                f"the CAS cell log could not be written ({self.log_path}): {error}. "
                "The session is poisoned because its live state is no longer recorded. "
                "Reset it to start clean."
            ) from None
        self._records.append(record)
        if self.observe is not None:
            self.observe({"type": "cas", "record": record.model_dump(mode="json")})
        return record

    def records(self) -> tuple[CellRecord, ...]:
        """Every durable record, boundaries included."""
        return tuple(self._records)

    @property
    def segment(self) -> int:
        return max((record.segment for record in self._records), default=0)

    def accepted(self) -> tuple[CellRecord, ...]:
        segment = self.segment
        return tuple(
            record
            for record in self._records
            if record.accepted and record.segment == segment and record.source.strip()
        )

    def cells(self) -> tuple[CellRecord, ...]:
        segment = self.segment
        return tuple(
            record
            for record in self._records
            if record.segment == segment and record.source.strip()
        )

    # -------------------------------------------------------------- kernel

    def _start(self) -> None:
        cap = self.limits.cas_output_bytes
        argv = self.backend.argv(self.command, cap)
        # A length-framed backend clips its own output to `cap`, so the reader
        # keeps headroom: tripping the retention limit there would mean a frame
        # that can never be assembled, and is treated as a broken kernel rather
        # than as a large answer.
        #
        # The factor is six, and it is not slack. `cas_driver.run_cell`
        # budgets stdout, stderr and value_repr *jointly* against `cap`, so a
        # payload carries at most `cap` bytes of captured text -- but it
        # carries them JSON-escaped, and a control byte becomes a six-byte
        # backslash-u escape. A cell printing NUL bytes is legal, so anything
        # smaller is a cap a legal cell can walk past, and walking past it
        # means a frame that never assembles, a cell that waits out the whole
        # timeout, and a kernel dropped with all its state.
        retain = cap if self.backend.framing == "sentinel" else cap * 6 + 65_536
        try:
            self._kernel = _Kernel(
                argv, self.cwd, retain, getattr(self.backend, "environment", {})
            )
        except (OSError, ValueError) as error:
            self.state = "dead"
            raise CasError(
                f"could not start the {self.backend.name} kernel "
                f"({' '.join(argv)}): {error}"
            ) from None
        self.state = "live"

    def probe_version(self) -> str:
        """Ask the kernel what it is, in a kernel that is then thrown away.

        Doubles as the smoke test: a backend that cannot answer this is not a
        working backend, however present it looks.

        The probe is thrown away because it is a cell like any other. For the
        default backend the version source is a trailing expression, so the
        driver binds its value to `_` -- and a session discovered that way
        would start with state no fresh kernel has, so a first cell mentioning
        `_` would work live and fail in export or recovery. Discarding the
        kernel leaves the session cold, and the next cell builds it from the
        accepted log, which is the only state anything else can reconstruct.
        """
        with self._lock:
            if self._kernel is None:
                self._start()
            try:
                outcome = self._send(self.backend.version_source)
            finally:
                self._discard_kernel()
            if outcome.status != "ok":
                raise CasError(
                    f"{self.backend.name} kernel did not answer a version query: "
                    f"{(outcome.stderr or outcome.stdout).strip()[:200]}"
                )
            raw = self.backend.parse_version(outcome.value_repr or outcome.stdout)
            self.version = raw.strip().strip("'\"") or "unknown"
            return self.version

    def _extractor(self, nonce: str, fed: str = "") -> Callable[[bytes], Any]:
        """`fed` is the framed text this cell was sent, for `sanitize` to
        recognise the interpreter's echo of it. Unused by the length path,
        whose driver never echoes anything."""
        if self.backend.framing == "length":

            def extract_length(raw: bytes) -> Any:
                if len(raw) < HEADER_BYTES:
                    return None
                try:
                    length = int(raw[:HEADER_BYTES].decode("ascii"))
                except (ValueError, UnicodeDecodeError):
                    return DESYNCHRONISED
                body = raw[HEADER_BYTES : HEADER_BYTES + length]
                if len(body) < length:
                    return None
                try:
                    payload = json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return DESYNCHRONISED
                outcome = CellOutcome(
                    status=payload.get("status", "ok"),
                    stdout=payload.get("stdout", ""),
                    stderr=payload.get("stderr", ""),
                    value_repr=payload.get("value_repr", ""),
                    capture_truncated=bool(payload.get("capture_truncated")),
                    state_digest=str(payload.get("state_digest", "")),
                )
                return outcome, HEADER_BYTES + length

            return extract_length

        begin = SENTINEL_BEGIN.format(nonce=nonce).encode("utf-8")
        end = SENTINEL_END.format(nonce=nonce).encode("utf-8")
        # A backend that echoes stdin (confirmed of Macaulay2: it prints
        # `iN : ` followed by the exact line it was fed, tty or not) writes
        # the marker text a *second* time before the real one: once inside
        # its own echoed source line (`iN : << "«marker»" << endl;`), and
        # only afterwards as the bare line the `<<` statement actually
        # prints. A bare `raw.find(marker)` matches the first, embedded
        # occurrence, and every cell's captured body ends up carrying a
        # fragment of that echoed statement.
        #
        # The two occurrences are told apart by what immediately follows
        # them, not by what precedes them -- a preceding newline is not a
        # reliable signal, since an in-flight prompt from the *previous* cell
        # can legitimately sit directly in front of the real marker too (see
        # `test_state_is_not_polluted_by_the_previous_cells_prompt`, which
        # exists precisely to pin that down). The embedded copy is always
        # immediately followed by the fixed tail of the echo template itself
        # (`" << endl;` for Macaulay2, `");` for Singular) because that is
        # what is on the rest of the line we sent; the real, bare-printed
        # copy never is. Skipping any occurrence with that tail right after
        # it, and continuing the search past it, finds the real one
        # regardless of what backend-specific noise precedes either.
        tail = self.backend.echo.rsplit("{marker}", 1)[1].encode("utf-8")

        def _find_marker(raw: bytes, marker: bytes, after: int) -> int:
            pos = after
            while (hit := raw.find(marker, pos)) != -1:
                echoed_end = hit + len(marker)
                if raw[echoed_end : echoed_end + len(tail)] == tail:
                    pos = echoed_end
                    continue
                return hit
            return -1

        def extract_sentinel(raw: bytes) -> Any:
            kernel = self._kernel
            # A pipe preserves write order, so whatever the previous cell
            # printed -- including a prompt still arriving when this cell was
            # armed -- is necessarily before this cell's begin marker in the
            # stream, however late it happens to show up. Waiting for the
            # begin marker before looking at anything excludes it without
            # ever having to guess whether it has fully arrived.
            begin_at = _find_marker(raw, begin, 0)
            if begin_at == -1:
                return None
            start = begin_at + len(begin)
            # `marker_seen` is the *scanner's* answer, and the scanner is a
            # bare `in` test over a rolling tail: it cannot tell a marker the
            # interpreter printed from a marker the interpreter echoed off its
            # own stdin. Macaulay2 echoes, and it flushes the echoed end-marker
            # statement a statement before it runs it -- so trusting
            # `marker_seen` on its own ends a cell at the echo and drops
            # whatever the cell was still writing. It exists for exactly one
            # case, where the real end marker's bytes were dropped at the
            # retention cap and only the scan could have seen them, so it is
            # only consulted when retention actually overflowed.
            truncated_past_the_marker = (
                kernel is not None and kernel.truncated and kernel.marker_seen
            )
            end_at = _find_marker(raw, end, start)
            if end_at == -1 and not truncated_past_the_marker:
                return None
            if end_at != -1:
                body = raw[start:end_at].decode("utf-8", errors="replace")
                consumed = end_at + len(end)
            else:
                # Retention stopped before the end marker; scanning continued
                # via `marker_seen`, but the bytes themselves are gone.
                body = raw[start:].decode("utf-8", errors="replace")
                consumed = len(raw)
            body = self.backend.sanitize(body, fed)
            outcome = CellOutcome(
                # Provisional, and never read: `_send` reclassifies every
                # sentinel reply once stderr has settled, because a Macaulay2
                # error leaves nothing error-shaped on stdout at all.
                # Classifying here as well would only be a second answer to a
                # question that already has one.
                status="ok",
                stdout=body,
                capture_truncated=bool(kernel and kernel.truncated),
            )
            return outcome, consumed

        return extract_sentinel

    def _send(self, source: str, seconds: float | None = None) -> CellOutcome:
        """One round trip. Assumes the lock and a started kernel.

        `seconds` is the deadline actually applied, which is not always
        `cas_cell_seconds`: a caller with less session budget left than that
        must not be able to buy a full cell's worth of wall clock with it.
        """
        limit = self.limits.cas_cell_seconds if seconds is None else seconds
        kernel = self._kernel
        assert kernel is not None
        nonce = f"{time.monotonic_ns():x}"
        if self.backend.framing == "length":
            kernel.clear()
        else:
            end = SENTINEL_END.format(nonce=nonce).encode()
            kernel.rearm(end)
        # The frame is built inside the lock, below, because the bit it carries
        # is the stop level: read outside, a press landing between the read and
        # the lock would be recorded while the frame already said no stop was
        # wanted -- and the driver, told that, would discard the very signal
        # that press sent.
        with self._signal_lock:
            # Cleared before the cell is out there, and only then armed: an
            # interrupt asked for while nothing was running would otherwise
            # stop the *next* cell, which nobody asked to stop.
            self._interrupted.clear()
            frame = self.backend.frame(source, nonce, self._stop_level > 0)
            self._sending.set()
        # Outside the lock. `write` to a full pipe blocks until the kernel
        # reads, and `interrupt` and `escalate` are called from the terminal's
        # own event loop -- so holding the lock across the write would let a
        # kernel that has stopped reading freeze the interface, with the cell's
        # deadline not yet running and the one press that could end it unable
        # to be delivered. A press landing in this window is remembered in
        # `_stop_level` and spent below, at the level it was asked at.
        sent = kernel.send(frame)
        with self._signal_lock:
            self._sending.clear()
            self._in_flight.set()
            # A stop asked for before this cell existed still applies to it:
            # the press was aimed at the work, and the work is now this. It
            # goes out here, under the lock, at the level it was asked at --
            # a second press that landed before the cell did still means kill
            # rather than ask.
            if self._stop_level and sent:
                self._interrupted.set()
                if self._stop_level >= _INSISTED:
                    kernel.kill(immediate=True)
                else:
                    kernel.interrupt()
        try:
            if not sent:
                if self._interrupted.is_set():
                    # The write failed because the second press killed the
                    # kernel out from under it -- which is what ends a write to
                    # a kernel that has stopped reading. Hardy stopped this, so
                    # the record says so; `kernel_died` would blame the
                    # toolchain for what Esc did.
                    return CellOutcome(
                        status="interrupted",
                        stderr=(
                            "the kernel was not reading its input, so the cell was "
                            "never sent; it was stopped and its state is gone"
                        ),
                        kernel_lost=True,
                        signalled=True,
                    )
                return CellOutcome(status="kernel_died", stderr=kernel.stderr_text())
            deadline = time.monotonic() + limit
            # The frame, not the source: an echoing interpreter echoes the
            # sentinel statements bracketing the cell as readily as the cell
            # itself, and a multi-line cell's second line onward comes back
            # under a continuation indent rather than a prompt of its own.
            fed = frame.decode("utf-8", errors="replace")
            reply = kernel.read_reply(
                self._extractor(nonce, fed), deadline, self._interrupted
            )
        finally:
            # Both under the lock `interrupt` takes, so a press either lands
            # before this (and is recorded against the cell) or finds nothing
            # in flight and is not. Read here rather than at the bottom of the
            # method: between the reply arriving and this line, a press is
            # still aimed at a cell nobody could yet know was over, and it is
            # resolved conservatively -- the cell is recorded and reported, and
            # simply not accepted, which costs a rerun rather than correctness.
            with self._signal_lock:
                signalled = self._interrupted.is_set()
                self._in_flight.clear()
        if reply is INTERRUPTED:
            # Asked to stop and did not answer within the grace. Whatever it is
            # doing, it is not talking, and a kernel that cannot be spoken to
            # cannot be replayed from or built on -- so it goes, exactly as a
            # timed-out one does. This is the case the interrupt exists to
            # avoid, not the one it produces when it works.
            return CellOutcome(
                status="interrupted",
                stderr=(
                    "the cell was interrupted and the kernel did not answer within "
                    f"{INTERRUPT_GRACE_SECONDS:g}s, so it was stopped and its state is gone"
                ),
                kernel_lost=True,
            )
        if reply is TIMED_OUT:
            return CellOutcome(
                status="timeout",
                stderr=f"cell exceeded its {limit:g}s limit",
            )
        if reply is None or reply is DESYNCHRONISED:
            # A stream that desynchronised cannot be trusted to be answering
            # the cell we sent, so it is a death rather than a bad answer.
            if self._interrupted.is_set():
                # Died *because* it was signalled. Not every interpreter turns
                # an interrupt into a traceback it can report -- one still
                # starting up has no handler installed yet, and a REPL may
                # simply exit -- and a kernel Hardy stopped must not be
                # recorded as one that fell over on its own. The state is gone
                # either way; what changes is which cause the record names.
                return CellOutcome(
                    status="interrupted",
                    stderr=(
                        "the cell was interrupted and the kernel did not survive it, "
                        "so its state is gone"
                    ),
                    kernel_lost=True,
                )
            return CellOutcome(status="kernel_died", stderr=kernel.stderr_text())
        outcome, consumed = reply
        if self.backend.framing == "sentinel":
            # Stderr first, and the truncation flag read after it. Both come
            # before `consume`, which clears the flag for the next cell.
            #
            # `extract_sentinel` snapshots `kernel.truncated` at the moment the
            # stdout end marker was found, and stderr is a second pipe drained
            # by a second thread: a cell that overran `cas_output_bytes` on
            # stderr alone had its overflow recorded *after* that snapshot and
            # then cleared by `consume`, so the record said nothing had been
            # discarded. That is the one thing the flag exists to say. A
            # Macaulay2 error banner sitting in the discarded stderr tail was
            # then classified from a prefix, called clean, and accepted --
            # which is exactly the "verification that accepts too much" the
            # truncated-capture rule in `execute` refuses.
            stderr_text = kernel.stderr_settled()
            truncated = outcome.capture_truncated or kernel.truncated
            kernel.consume(consumed)
            # Reclassify now that both streams are in: confirmed of Macaulay2
            # (CI run 30167266358), whose errors ("stdio:...: error: ...") land
            # on stderr with nothing error-shaped left on stdout at all, so a
            # stdout-only classification always read a broken M2 cell as "ok".
            status = self.backend.classify(outcome.stdout, stderr_text)
            outcome = outcome.model_copy(
                update={
                    "stderr": stderr_text,
                    "status": status,
                    "capture_truncated": truncated,
                }
            )
        if signalled:
            outcome = outcome.model_copy(update={"signalled": True})
        if not signalled and outcome.status == "interrupted":
            # The driver says it caught a `KeyboardInterrupt` and Hardy never
            # sent one, so the cell raised it itself -- `raise
            # KeyboardInterrupt` in the source, or a library that uses it to
            # unwind. That is an ordinary failure of the cell, and recording it
            # as a cancellation would put a user action nobody took into the
            # durable log. The parent is the only side that can tell these
            # apart: the driver has no idea where the signal came from.
            outcome = outcome.model_copy(update={"status": "error"})
        elif signalled and outcome.status != "ok":
            # The kernel answered after being asked to stop, so it is alive and
            # its namespace is intact -- but what it answered with is the cell
            # failing, not the cell's result. A sentinel backend has no status
            # of its own (`classify` is Hardy's own inference from an error
            # banner), and the driver's own word for this is already
            # "interrupted"; either way the honest name for a cell the user
            # stopped is that it was stopped. A cell that came back "ok"
            # finished before the signal reached it and keeps its result.
            outcome = outcome.model_copy(update={"status": "interrupted"})
        return outcome

    def interrupt(self) -> bool:
        """Stop the cell in flight without stopping the kernel.

        Callable from any thread. Takes `_signal_lock` but never `_lock` -- the
        latter is held by the thread inside `execute`, which is the thread this
        exists to reach, and waiting for it would deadlock against the very
        cell being stopped.

        Reports whether a cell was actually running to be stopped, so the
        terminal can say what it did rather than claiming to have stopped
        something that was not there. A press that finds nothing is still
        remembered: the cell it was aimed at may be a microsecond from going
        out, and `_send` signals it as soon as it does.

        There is still a window in which the cell finishes just as the signal
        is sent, so the driver survives one arriving with no cell to abandon;
        that is defence in depth, not the design.
        """
        with self._signal_lock:
            self._stop_level = max(self._stop_level, _ASKED)
            if not self._in_flight.is_set():
                return False
            kernel = self._kernel
            if kernel is None:
                return False
            # Set before the signal, not after: the reader must never see a
            # kernel that has already answered the interrupt while the flag
            # that explains the answer is still clear.
            self._interrupted.set()
            return kernel.interrupt()

    def resume(self) -> None:
        """Lift a remembered stop, so the next turn's cells may run.

        The counterpart of `process.resume_children`, called at the same
        moment and for the same reason: a stop that outlived the turn it
        belonged to would interrupt the next turn's first cell on sight.
        """
        with self._signal_lock:
            self._stop_level = 0

    def escalate(self) -> bool:
        """Stop waiting for the interrupt to be answered, and stop the kernel.

        The second Esc. An interrupt is a request, and a cell deep in a library
        that never returns to its interpreter will not hear it; this is the way
        out of waiting for an answer that is not coming. It costs exactly what
        the timeout costs -- the namespace -- which is why it is the second
        press and not the first.

        Like `interrupt`, takes `_signal_lock` but never `_lock`, and refuses
        when nothing is running: killing an idle kernel would leave the session
        holding a dead child it still believed in. The stop is remembered
        either way, so a cell about to go out is stopped rather than the press
        being lost.

        A cell still being *written* counts as running here, though `interrupt`
        refuses it: a kernel that has stopped reading blocks the write with no
        deadline yet running, and killing it is what ends that write. There is
        nothing for the first press to ask of a kernel that is not listening,
        and this is the press that does not ask.
        """
        with self._signal_lock:
            self._stop_level = _INSISTED
            if not (self._in_flight.is_set() or self._sending.is_set()):
                return False
            kernel = self._kernel
            if kernel is None:
                return False
            self._interrupted.set()
        # The reading thread sees both streams end, and -- because the flag is
        # set -- records the cell as interrupted with the kernel lost, which is
        # what happened. It drops the kernel itself when it gets there; nothing
        # here touches `_kernel`, so the two threads cannot race over it.
        #
        # Straight to SIGKILL: this runs on the terminal's event loop, and the
        # graceful teardown's two-second wait would freeze the UI for exactly
        # as long as this press was made to avoid waiting.
        kernel.kill(immediate=True)
        return True

    # -------------------------------------------------------------- budget

    @property
    def remaining_seconds(self) -> float:
        """What is left of `cas_session_seconds`, never negative."""
        return max(0.0, self.limits.cas_session_seconds - self.spent_seconds)

    def charge(self, seconds: float) -> None:
        """Bill CAS wall clock to the session budget.

        Public because the budget covers every kernel a session causes to run,
        not only the cells a caller asked for: the fresh kernel an export
        replays in is the session's own time too, and an export that could
        spend it unbilled would make `cas_session_seconds` describe nothing.
        """
        with self._lock:
            self.spent_seconds += max(0.0, seconds)

    def _cell_seconds(self) -> float:
        """The deadline one round trip may have: the smaller of the two limits.

        A session with one second left must not be able to run a sleeping cell
        for a full `cas_cell_seconds`, or the session limit is not a limit --
        it is only a value consulted before work that ignores it.
        """
        return min(float(self.limits.cas_cell_seconds), self.remaining_seconds)

    # ------------------------------------------------------------- execute

    def _foreign_backend(self) -> str | None:
        """The name on the live segment's records, if it is not this backend's."""
        segment = self.segment
        for record in self._records:
            if record.segment == segment and record.backend and record.backend != self.backend.name:
                return record.backend
        return None

    def _guard(self) -> None:
        if self.state == "poisoned":
            raise CasError(
                "the CAS session is poisoned: its state could not be rebuilt faithfully. "
                "Reset it to start a clean kernel."
            )
        foreign = self._foreign_backend()
        if foreign is not None:
            # Replaying this segment would feed one backend's language to
            # another, and a cell that happens to parse would be worse than one
            # that does not. Refusing leaves a way out: a reset opens a clean
            # segment under the configured backend without deleting anything.
            raise CasError(
                f"this CAS cell log was written by the {foreign} backend, but "
                f"{self.backend.name} is configured. Reset the session to start a "
                "clean segment, or restore the original cas_backend setting."
            )
        if self.spent_seconds >= self.limits.cas_session_seconds:
            raise CasError("CAS session budget exhausted")

    def execute(self, source: str, *, author: str = "model") -> CellRecord:
        with self._lock:
            self._guard()
            if not source.strip():
                raise CasError("an empty cell has nothing to execute")
            if len(source.encode("utf-8")) > 64 * 1024:
                raise CasError("cell source exceeds the 64 KiB limit")

            notes = ""
            # `_kernel is None` covers a session that has never run a cell in
            # this process as well as one whose kernel died -- including the
            # kernel discovery threw away. A merely probed kernel used to leave
            # this false, so a reopened workspace listed its accepted cells and
            # then answered the next one from an empty namespace.
            if self._kernel is None or self.state == "dead":
                # A death and an ordinary reopen both rebuild, and they are not
                # the same news. Reading "kernel restarted" on the first cell of
                # a session nobody had run yet turns opening a saved workspace
                # into an incident report.
                died = self.state == "dead"
                report = self._restore()
                if report.replayed and died:
                    notes = f"[kernel restarted; replayed {report.replayed} cell(s)]"
                elif report.replayed:
                    notes = (
                        f"[saved session reopened; replayed {report.replayed} cell(s) "
                        "to rebuild its state]"
                    )
                if report.unverified:
                    # Named individually only when some cells *were* checked.
                    # On a backend that records no digest at all, every cell is
                    # on the list and printing all of them buries the point.
                    which = (
                        "no cell's"
                        if len(report.unverified) == report.replayed
                        else f"cell(s) {list(report.unverified)}'"
                    )
                    notes = (notes + " " if notes else "") + (
                        f"[{which} state could be compared against the record: "
                        "there is no state digest for them, so their replay "
                        "agrees whatever namespace it rebuilt. What they printed "
                        "did reproduce. The state is reconstructed, not verified: "
                        "rerun anything you mean to build on.]"
                    )
                # The rebuild is billed, so it can be what exhausts the budget.
                self._guard()

            started = time.monotonic()
            outcome = self._send(source, self._cell_seconds())
            elapsed = time.monotonic() - started
            self.spent_seconds += elapsed

            status = outcome.status
            truncated = outcome.capture_truncated
            # A sentinel backend has no status of its own: Hardy decides whether
            # the cell failed by looking for an error banner in what it printed.
            # When the capture hit `cas_output_bytes` that decision was made
            # from a prefix, and Singular's `? ` banner or Macaulay2's
            # `stdio:...: error:` can be sitting in the tail that was thrown
            # away. Hardy knows the capture was cut, so it must not then assert
            # success: the cell is recorded and reported in full, and kept out
            # of the accepted set that recovery replays and export publishes,
            # where a wrong "ok" would be repeated as fact forever after.
            #
            # The driver protocol is untouched. There the child reports its own
            # status and clips afterwards, so truncation cannot hide a failure
            # -- what it can still hide is a differing tail, which is export's
            # problem and is answered there with an `unverified` verdict.
            unverifiable = (
                truncated and status == "ok" and self.backend.framing == "sentinel"
            )
            # A cell Hardy signalled that answered `ok` anyway. It really did
            # finish, so it keeps that status -- but it finished under a signal,
            # and a cell (or a library beneath it) that catches one can return
            # normally from a path it would not otherwise have taken. A replay
            # without the signal would then not reproduce it, which is the one
            # thing an accepted cell has to be able to do.
            perturbed = outcome.signalled and status == "ok"
            if perturbed:
                notes = (notes + " " if notes else "") + (
                    "[this cell was interrupted but reported success anyway, so it "
                    "ran under a signal it may have caught. It is recorded, and its "
                    "output is what it produced, but it was not accepted into the "
                    "state replay and export rebuild from: without the interrupt it "
                    "may not take the same path. Rerun it if you mean to build on it.]"
                )
            if unverifiable:
                notes = (notes + " " if notes else "") + (
                    "[output exceeded cas_output_bytes, so this cell was classified "
                    "from a prefix and an error banner could be in the discarded "
                    "tail. It ran, and its output is recorded, but it was not "
                    "accepted into the state replay and export rebuild from. It did "
                    "change the live namespace, and that change is now outside the "
                    "accepted set: any later cell that depends on it will diverge on "
                    "export and after a kernel restart. Rerun it printing less, or "
                    "raise cas_output_bytes and rerun it, before building on it.]"
                )
            if outcome.kernel_lost:
                notes = (notes + " " if notes else "") + (
                    "[the kernel did not answer the interrupt, so it was stopped. "
                    "Every value in the session is gone; the next cell rebuilds "
                    "from the accepted ones.]"
                )
            if status in {"timeout", "kernel_died"} or outcome.kernel_lost:
                self._drop_kernel()
            record = CellRecord(
                seq=len(self._records),
                segment=self.segment,
                author=author,  # type: ignore[arg-type]
                source=source,
                status=status,  # type: ignore[arg-type]
                accepted=status == "ok" and not unverifiable and not perturbed,
                stdout=outcome.stdout,
                stderr=outcome.stderr,
                value_repr=outcome.value_repr,
                duration_ms=round(elapsed * 1_000),
                capture_truncated=truncated,
                backend=self.backend.name,
                backend_version=self.version or "",
                state_digest=outcome.state_digest,
                restart_note=notes,
            )
            return self._append(record)

    def _drop_kernel(self) -> None:
        if self._kernel is not None:
            self._kernel.kill()
        self._kernel = None
        self.state = "dead"

    def _discard_kernel(self) -> None:
        """Close a kernel that nothing died in, leaving the session cold."""
        if self._kernel is not None:
            self._kernel.kill()
        self._kernel = None
        if self.state != "poisoned":
            self.state = "cold"

    def _restore(self) -> RebuildReport:
        """Rebuild live state after a death, and verify what was rebuilt."""
        self._start()
        pending = self.accepted()
        if not pending:
            return RebuildReport()
        diverged: list[int] = []
        for record in pending:
            # A rebuild is the session's own time. Left unbilled, a session
            # holding expensive accepted cells could time out and replay many
            # minutes of work over and over while the budget never moved.
            if self.remaining_seconds <= 0:
                self._drop_kernel()
                self.state = "poisoned"
                raise CasError(
                    "could not rebuild CAS state: the session budget ran out during "
                    "replay. Reset the session to start clean."
                )
            started = time.monotonic()
            outcome = self._send(record.source, self._cell_seconds())
            self.charge(time.monotonic() - started)
            # A replay Hardy signalled establishes nothing about the log, and
            # an `ok` is the dangerous case rather than the safe one: a cell
            # that caught the signal can skip a mutation and still print what
            # it printed before, so `reproduces` would pass over a namespace
            # that differs -- and every later cell would be built on it.
            #
            # Left retryable rather than poisoned. Poisoning says the accepted
            # cells no longer describe a state that can be rebuilt, and nothing
            # here has shown that: the user simply stopped the rebuild. The
            # kernel is dropped, so the next cell tries again.
            if outcome.signalled or outcome.status == "interrupted":
                self._drop_kernel()
                raise CasError(
                    "the CAS rebuild was interrupted before it finished. Nothing is "
                    "lost -- the saved cells are intact, and the next cell rebuilds "
                    "from them again."
                )
            if outcome.status != "ok":
                self._drop_kernel()
                self.state = "poisoned"
                raise CasError(
                    f"could not rebuild CAS state: cell {record.seq} failed on replay. "
                    "Reset the session to start clean."
                )
            # Running clean is not the same as recovering. A cell that depends
            # on randomness, time, or the filesystem can succeed here and
            # reconstruct a different value, and every later cell would be
            # built on it.
            if not reproduces(record, outcome):
                diverged.append(record.seq)
        if diverged:
            self.state = "poisoned"
            raise CasError(
                "could not rebuild CAS state faithfully: cell(s) "
                f"{diverged} did not reproduce on replay. "
                "Reset the session to start clean."
            )
        # A clean replay is not the same as a checked one. On a backend that
        # carries no state digest, a cell that printed nothing agrees with its
        # record whatever it rebuilt, so the rebuild is reported with those
        # cells named rather than as a rebuild that was verified.
        return RebuildReport(
            replayed=len(pending),
            # A truncated capture belongs here for the same reason a missing
            # digest does: the retained prefixes matched and the discarded
            # tails were never compared, so a cell printing a deterministic
            # prefix over a random tail replays "cleanly" without anything
            # having checked the part that differs. Export already refuses to
            # call such a cell verified; a rebuild says the same now.
            unverified=tuple(
                record.seq
                for record in pending
                if unobservable(record) or record.capture_truncated
            ),
        )

    def reset(self, *, author: str = "model") -> None:
        """Close the current segment. Nothing is deleted.

        The boundary is itself a `CellRecord` carrying the new segment, so one
        schema describes the whole log and the reset is durable the moment it
        happens rather than when the next cell is written.

        A reset clears state, not the bill. `cas_reset` is a tool the model can
        call, so refunding `spent_seconds` here would make the session budget
        advisory: a model near the limit could buy the whole allowance again,
        as often as it liked, by discarding a namespace it no longer needed.

        `author` is carried through for the same reason `execute` carries it.
        A state-destroying action recorded as the human's when a model asked
        for it makes the timeline lie about why earlier definitions vanished.
        """
        with self._lock:
            self._drop_kernel()
            self.state = "cold"
            self._append(
                CellRecord(
                    seq=len(self._records),
                    segment=self.segment + 1,
                    author=author,  # type: ignore[arg-type]
                    source="",
                    status="ok",
                    accepted=False,
                    backend=self.backend.name,
                    backend_version=self.version or "",
                )
            )

    def close(self) -> None:
        with self._lock:
            if self._kernel is not None:
                self._kernel.kill()
            self._kernel = None


class ScriptRun(FrozenModel):
    """What running an exported script produced. `returncode` is None on timeout."""

    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    # Retention stopped at `cas_output_bytes`, so what is above is a prefix.
    # Any verdict drawn from it can only be `unverified`: an unread tail is not
    # evidence of agreement and is not evidence of disagreement either.
    capture_truncated: bool = False


def _drain_capped(pipe: Any, into: bytearray, cap: int, overflowed: list[bool]) -> None:
    """Read a pipe to its end while keeping at most `cap` bytes.

    Reading has to continue past the cap even though retaining does not: a
    child whose pipe stops being read blocks on its next write and never
    reaches the deadline, and the wait for it never returns.
    """
    try:
        while chunk := pipe.read1(65_536):
            room = max(0, cap - len(into))
            into.extend(chunk[:room])
            if len(chunk) > room:
                overflowed[0] = True
    except (OSError, ValueError):
        overflowed[0] = True


def _feed(process: subprocess.Popen, payload: bytes) -> None:
    """Write the script to the child's stdin, then close it.

    Closing is not optional and is not only for the payload's sake: an
    interpreter reading stdin runs until EOF, so a handle left open keeps a
    child alive that has already done everything asked of it.
    """
    stdin = process.stdin
    if stdin is None:
        return
    try:
        if payload:
            stdin.write(payload)
            stdin.flush()
    except (OSError, ValueError):
        # The child exited, or was killed at the deadline, with the payload
        # part-written. Its own status is the answer; this thread just stops.
        pass
    finally:
        with contextlib.suppress(OSError, ValueError):
            stdin.close()


def run_exported_script(
    *,
    backend: Any,
    command: Path | None,
    script: Path,
    cwd: Path,
    timeout: float,
    max_output_bytes: int,
) -> ScriptRun:
    """Execute a rendered script the way a reader would, and capture it.

    Not a kernel and not a replay: one process, the whole file, no framing.
    That is the point -- the artifact Hardy publishes is a file somebody runs,
    and until it has been run there is no evidence about what it does.

    Bounded like every other capture Hardy takes. `subprocess.run` with
    `capture_output` grows a buffer until the child stops writing, so a script
    of cells that print heavily would hold its whole transcript in Hardy's
    memory -- and a `MemoryError` raised there would take the export's
    artifacts with it, which is exactly what an export is supposed to survive.
    """
    cwd.mkdir(parents=True, exist_ok=True)
    argv = backend.script_argv(command, script)
    # A line-oriented interpreter is fed the file on stdin, as the session
    # feeds it cells. Everything else gets an immediately closed stdin, so a
    # program that reads it sees EOF instead of blocking until the deadline.
    payload = script.read_bytes() if getattr(backend, "script_stdin", False) else b""
    cap = max(1, max_output_bytes)
    out, err = bytearray(), bytearray()
    overflowed = [False]
    try:
        process = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=child_environment(dict(getattr(backend, "environment", {}))),
            shell=False,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **child_creation(),
        )
    except (OSError, ValueError) as error:
        raise CasError(
            f"could not run the exported script ({' '.join(argv)}): {error}"
        ) from None

    workers = [
        threading.Thread(target=_drain_capped, args=(pipe, buffer, cap, overflowed), daemon=True)
        for pipe, buffer in ((process.stdout, out), (process.stderr, err))
    ]
    stdout_worker, stderr_worker = workers
    # Feeding stdin is a third concurrent job, not a preamble to the other two.
    # `subprocess.run(input=...)` multiplexes all three inside `communicate()`;
    # writing the payload straight through on this thread instead re-created
    # the deadlock `communicate` exists to avoid. Once the payload passes the
    # OS pipe buffer and the child is not draining it fast enough, the write
    # blocks -- and it blocks *before* the deadline loop below is ever reached,
    # so no timeout applies and nothing can kill the child. That is not a
    # corner: `script_stdin` is how Singular and Macaulay2 are fed, and with a
    # 64 KiB per-cell source cap a handful of cells passes any pipe buffer.
    feeder = threading.Thread(target=_feed, args=(process, payload), daemon=True)
    workers.append(feeder)
    for worker in workers:
        worker.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        # Killed first, and only then joined. A writer still blocked on a full
        # pipe is released by the child's death -- the write fails and the
        # thread unwinds -- whereas closing the handle from here would queue
        # behind the very write that is stuck. Every worker is a daemon and
        # every join is bounded, so a wedged one costs a thread.
        if process.poll() is None:
            process.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=5)
        for worker in workers:
            worker.join(timeout=5)
        # A reader still going is a reader with more to read. A descendant that
        # holds the pipe open and writes after the script itself has exited
        # keeps its worker alive past the join, and snapshotting the buffer
        # there and calling the capture complete let an export compare against
        # a transcript that was still arriving -- and report `verified` for it.
        if stdout_worker.is_alive() or stderr_worker.is_alive():
            overflowed[0] = True
        # And each stream is closed only once its own worker has actually let
        # go of it. A drain thread blocked in `pipe.read1` -- because, say, a
        # grandchild the child spawned inherited the handle and is still
        # holding it open -- holds that `BufferedReader`'s lock for as long as
        # the read is stuck; `_feed` closes stdin itself and holds its
        # `BufferedWriter`'s lock the same way for as long as its write is
        # stuck. Closing any of those same handles from here would wait on
        # that same blocked call, with no timeout, and the bounded joins two
        # lines above buy nothing at all. Leaving a stream to its wedged
        # worker costs a file handle for as long as that thread lives; the
        # child is already dead, so its end of the pipe is gone regardless and
        # nothing is kept alive by the wait.
        closing = []
        if not stdout_worker.is_alive():
            closing.append(process.stdout)
        if not stderr_worker.is_alive():
            closing.append(process.stderr)
        if not feeder.is_alive():
            closing.append(process.stdin)
        for stream in closing:
            if stream is not None:
                with contextlib.suppress(OSError, ValueError):
                    stream.close()
    return ScriptRun(
        returncode=None if timed_out else process.returncode,
        stdout=_decode(bytes(out)),
        stderr=_decode(bytes(err)),
        timed_out=timed_out,
        capture_truncated=overflowed[0],
    )


def _decode(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    return raw.decode("utf-8", errors="replace")


def replay_in_fresh_kernel(
    *,
    backend: Any,
    command: Path | None,
    cells: Sequence[CellRecord],
    limits: RunLimits,
    cwd: Path,
    budget_seconds: float | None = None,
    charge: Callable[[float], None] | None = None,
) -> list[CellOutcome | None]:
    """Run cells in a throwaway kernel. `None` marks a cell never reached.

    `charge` reports each cell's wall clock back to whoever owns the budget
    this replay is spending. An export replays the whole accepted segment, and
    that is the session's own time however fresh the kernel running it is.
    """
    foreign = next(
        (record.backend for record in cells if record.backend and record.backend != backend.name),
        None,
    )
    if foreign is not None:
        raise CasError(
            f"these cells were recorded by the {foreign} backend and cannot be "
            f"replayed under {backend.name}"
        )
    session = CasSession(
        backend=backend,
        command=command,
        log_path=cwd / "replay-scratch.jsonl",
        limits=limits,
        cwd=cwd,
    )
    outcomes: list[CellOutcome | None] = []
    budget = budget_seconds if budget_seconds is not None else limits.cas_session_seconds
    try:
        session._start()
        spent = 0.0
        for record in cells:
            remaining = budget - spent
            if remaining <= 0:
                outcomes.append(None)
                continue
            started = time.monotonic()
            outcome = session._send(
                record.source, min(float(limits.cas_cell_seconds), remaining)
            )
            elapsed = time.monotonic() - started
            spent += elapsed
            if charge is not None:
                charge(elapsed)
            outcomes.append(outcome)
            # `kernel_lost` as well: whatever stopped this kernel, the cells
            # behind it have no kernel left to run in, and reporting them as
            # anything other than unreplayed would be a claim about a process
            # that no longer exists.
            if outcome.status in {"kernel_died", "timeout"} or outcome.kernel_lost:
                outcomes.extend([None] * (len(cells) - len(outcomes)))
                break
    except CasError:
        outcomes.extend([None] * (len(cells) - len(outcomes)))
    finally:
        session.close()
        # Through the session's own guard: the scratch log is removed by the
        # same proven directory it was written through, not by a path rebuilt
        # here that nothing re-checks.
        session._log.unlink(session.log_path.name, missing_ok=True)
    return outcomes
