"""Backend-specific framing and rendering; differing CAS semantics stay explicit."""
from __future__ import annotations

import abc
import ast
import json
import re
import sys
from collections import Counter
from collections.abc import Callable, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Protocol

from hardy.algebra.contracts import (
    DESYNCHRONISED,
    HEADER_BYTES,
    SENTINEL_BEGIN,
    SENTINEL_END,
    BackendName,
    CellOutcome,
    Extractor,
    ReadFailure,
    Reply,
)
from hardy.algebra.kernel import Kernel

CaptureMode = Literal["separate", "merged"]

# The driver's default retention cap, when a caller names none.
DEFAULT_OUTPUT_BYTES = 256 * 1024
# The widest a byte of captured text becomes in the driver's JSON reply: a
# control byte is written as a six-byte backslash-u escape.
_JSON_ESCAPE_BYTES = 6
# Room in a reply for what surrounds the captured text: its keys, its status,
# and the namespace digest.
_REPLY_ENVELOPE_BYTES = 65_536


class CasBackend(Protocol):
    """What a session, a replay and an export need from one interpreter.

    There are two reply protocols behind it. `SympyBackend` speaks to Hardy's
    own driver in length-prefixed JSON, which reports each cell's status and a
    fingerprint of its namespace. `SentinelBackend` speaks to a line-oriented
    interpreter between echoed markers, where Hardy infers the status from an
    error banner and nothing describes the namespace. Every difference between
    them that the rest of the package acts on is a member here, so a third
    protocol is a third implementation rather than a new arm at each caller.
    """

    name: BackendName
    script_suffix: str
    language: str
    kernel_name: str
    # The line-comment marker the exported script's header and notes use.
    comment: str
    # What the script runs before the first cell, as the kernel did.
    preamble: str
    version_source: str
    # A statement printing `{marker}`: the brackets around a transcript.
    echo: str
    # The conditions the kernel and the exported script both run under, and
    # which the manifest records as the ones the verdict was checked under.
    environment: Mapping[str, str]
    # Whether the exported script is fed on stdin, as the session feeds cells.
    script_stdin: bool
    # Whether stderr shares stdout's pipe, so a record's `stdout` holds both.
    capture_mode: CaptureMode
    # Whether the interpreter reports each cell's status itself. Where Hardy
    # infers it from an error banner instead, a clipped transcript cannot
    # support `ok`.
    reports_status: bool
    # Whether a frame's `stopping` bit is itself the stop. Where it is, no
    # signal follows it; where it is not, the signal is the only carrier.
    frame_carries_stop: bool
    # The banner that marks a failed statement in a script's output, for an
    # interpreter that reports an error and carries on with exit status 0.
    error_pattern: re.Pattern[str] | None
    # Lines a script run writes to stderr on the interpreter's own account.
    script_stderr_chrome: tuple[re.Pattern[str], ...]

    @property
    def transcript_prologue(self) -> tuple[str, ...]:
        """Statements opening the script's own transcript, with `{begin}`."""
        ...

    @property
    def transcript_epilogue(self) -> tuple[str, ...]:
        """Statements closing it, with `{end}` and perhaps `{finished}`."""
        ...

    def argv(self, command: Path | None, max_output_bytes: int = DEFAULT_OUTPUT_BYTES) -> tuple[str, ...]:
        """How the persistent kernel is started."""
        ...

    def script_argv(self, command: Path | None, script: Path) -> tuple[str, ...]:
        """How an exported script is run, the way a reader would run it."""
        ...

    def frame(self, source: str, nonce: str, *, stopping: bool = False) -> bytes:
        """One cell, as the kernel reads it."""
        ...

    def retention(self, cap: int) -> int:
        """How many bytes of output the parent's reader may keep per cell."""
        ...

    def arm(self, kernel: Kernel, nonce: str) -> None:
        """Ready the kernel's buffers for the reply to the cell about to go out."""
        ...

    def extractor(self, kernel: Kernel | None, nonce: str, fed: str) -> Extractor:
        """Read that reply out of what the kernel has written so far."""
        ...

    def release(self, kernel: Kernel, consumed: int) -> None:
        """Let go of the bytes a reply was read from."""
        ...

    def partial(self, kernel: Kernel, outcome: CellOutcome) -> CellOutcome:
        """`outcome`, for a cell with no complete reply, with what did arrive."""
        ...

    def sanitize(self, stdout: str, fed: str = "") -> str:
        """Clean an interpreter's own noise out of what a cell printed."""
        ...

    def render_cell(self, source: str) -> str:
        """A cell as the exported script writes it."""
        ...

    def parse_version(self, sanitized_stdout: str) -> str:
        """The bare version string out of the version probe's reply."""
        ...


def _source_offset(source: str, lineno: int, col_offset: int) -> int:
    """A character index into `source` for one of `ast`'s positions.

    `col_offset` is a count of UTF-8 bytes, not characters, so a cell holding
    any non-ASCII text ahead of the position would be spliced in the wrong
    place by naive arithmetic.
    """
    lines = re.findall(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+\Z", source)
    if lineno - 1 >= len(lines):
        return len(source)
    prefix = sum(len(line) for line in lines[: lineno - 1])
    within = lines[lineno - 1].encode("utf-8")[:col_offset].decode("utf-8", errors="ignore")
    return prefix + len(within)


def _expression_span(source: str, node: ast.stmt) -> tuple[int, int]:
    """Where `node` starts and ends in `source`, as character indices."""
    start = _source_offset(source, node.lineno, node.col_offset)
    end = _source_offset(source, node.end_lineno or node.lineno, node.end_col_offset or 0)
    return start, end


def _length_reply(raw: bytes) -> Reply | ReadFailure | None:
    """The driver's reply: a ten-digit length, then that many bytes of JSON."""
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


class SympyBackend:
    """The default backend: Hardy's own interpreter, driven over a byte protocol."""

    name: BackendName = "sympy"
    script_suffix = ".py"
    language = "python"
    kernel_name = "python3"
    framing = "length"
    comment = "#"
    # The display helper reproduces the driver's module-global `_`, unlike
    # Python's displayhook, which binds builtins._. Capture the namespace and
    # repr/print operations before a cell can rebind their global names.
    preamble = "\n".join((
        "from sympy import *",
        "import sys",
        '_hardy_display = lambda value, namespace=globals(), '
        'write=__import__("builtins").print, representation=repr, output=sys: '
        'None if value is None else '
        '(namespace.__setitem__("_", value), '
        'write(representation(value), file=output.stdout))',
    ))
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
    #
    # A shutdown hook cannot say whether the file *finished*, though: `atexit`
    # fires on `SystemExit(0)` as readily as on reaching the end, so a first
    # cell raising one put both markers around an empty transcript that matched
    # a record of silent cells. The second registration below prints whatever
    # the file's last statement left under one name -- registered after the
    # first, so LIFO runs it before, and the line lands just inside the closing
    # marker.
    #
    # Everything it needs is bound at registration, the namespace included, so
    # nothing here resolves at the end either. And what the epilogue does is a
    # bare assignment of a string: it imports nothing, calls nothing, and
    # cannot raise, so a cell that has broken `__import__` or `print` costs the
    # export its verdict and not the artifact's ability to run.
    #
    # The evidence is the string rather than the name. A flag would be a name
    # in the script's own namespace, and a cell that sets it buys itself the
    # claim that the file finished; this one has to be `completion_marker`'s
    # value, which is generated per export. A cell that reads its own source
    # can still find it -- as it can find the markers -- but nothing a cell
    # writes can collide with it by accident, and every way of failing to
    # produce it reports the run cut short.
    transcript_prologue = (
        '__import__("atexit").register(__import__("functools").partial('
        '__import__("builtins").print, "{end}", '
        'file=__import__("sys").stdout))',
        '__import__("atexit").register(__import__("functools").partial('
        '(lambda write, namespace: write(namespace.get("_hardy_finished", ""))), '
        '__import__("functools").partial(__import__("builtins").print, '
        'file=__import__("sys").stdout), globals()))',
        '__import__("builtins").print("{begin}")',
    )
    transcript_epilogue: tuple[str, ...] = ('_hardy_finished = "{finished}"',)
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
    environment: Mapping[str, str] = MappingProxyType({"PYTHONHASHSEED": "0"})
    # The driver writes one frame per cell on a descriptor of its own, with
    # stdout and stderr captured separately inside it, and reports the cell's
    # status itself -- including that it stopped a cell its frame said to --
    # with a fingerprint of the namespace the cell left.
    capture_mode: CaptureMode = "separate"
    reports_status = True
    frame_carries_stop = True
    # A Python script fails with a non-zero exit status, not a banner.
    error_pattern: re.Pattern[str] | None = None
    script_stderr_chrome: tuple[re.Pattern[str], ...] = ()

    def argv(self, command: Path | None, max_output_bytes: int = DEFAULT_OUTPUT_BYTES) -> tuple[str, ...]:
        return (
            str(command) if command else sys.executable,
            "-u",
            "-m",
            "hardy.cas_driver",
            str(max_output_bytes),
        )

    def script_argv(self, command: Path | None, script: Path) -> tuple[str, ...]:
        return (str(command) if command else sys.executable, "-u", str(script))

    def frame(self, source: str, nonce: str, *, stopping: bool = False) -> bytes:
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

    def retention(self, cap: int) -> int:
        """Headroom over `cap`, because the driver clips its own output to it.

        Tripping the retention limit here would mean a frame that can never be
        assembled, and is treated as a broken kernel rather than as a large
        answer.

        The factor is six, and it is not slack. `cas_driver.run_cell` budgets
        stdout, stderr and value_repr *jointly* against `cap`, so a payload
        carries at most `cap` bytes of captured text -- but it carries them
        JSON-escaped, and a control byte becomes a six-byte backslash-u escape.
        A cell printing NUL bytes is legal, so anything smaller is a cap a
        legal cell can walk past, and walking past it means a frame that never
        assembles, a cell that waits out the whole timeout, and a kernel
        dropped with all its state.
        """
        return cap * _JSON_ESCAPE_BYTES + _REPLY_ENVELOPE_BYTES

    def arm(self, kernel: Kernel, nonce: str) -> None:
        """Discard everything read so far: the driver writes one frame and
        nothing else, so there is never anything worth keeping behind it."""
        kernel.clear()

    def extractor(self, kernel: Kernel | None, nonce: str, fed: str) -> Extractor:
        """The length header says where the reply ends; the driver echoes
        nothing, so neither the nonce nor what was fed matters here."""
        return _length_reply

    def release(self, kernel: Kernel, consumed: int) -> None:
        """Nothing to do: `arm` clears the stream before the next cell."""

    def partial(self, kernel: Kernel, outcome: CellOutcome) -> CellOutcome:
        """A frame that never assembled has nothing worth quoting."""
        return outcome

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
        `_hardy_display` instead -- matching what the driver does
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
        start, end = _expression_span(source, parsed.body[-1])
        # The inner parentheses are load-bearing and are not the call's. A
        # trailing expression may have a top-level comma -- `x, y` is ordinary
        # CAS usage -- and an argument list splits on exactly that: `x, y`
        # became two arguments and the published script died with "displayhook()
        # takes exactly one argument", while `x,` became one argument and
        # printed `x` where the record said `(x,)`. Parenthesised first, the
        # comma builds the tuple the driver evaluated. They also make an
        # expression spread over several lines stay one expression.
        return f"{source[:start]}_hardy_display(({source[start:end]})){source[end:]}"

    def parse_version(self, sanitized_stdout: str) -> str:
        return sanitized_stdout


def _find_marker(raw: bytes, marker: bytes, after: int, echo_tail: bytes) -> int:
    """Where the interpreter printed `marker`, skipping its echo of the source.

    A backend that echoes stdin (confirmed of Macaulay2: it prints `iN : `
    followed by the exact line it was fed, tty or not) writes the marker text a
    *second* time before the real one: once inside its own echoed source line
    (`iN : << "«marker»" << endl;`), and only afterwards as the bare line the
    `<<` statement actually prints. A bare `raw.find(marker)` matches the
    first, embedded occurrence, and every cell's captured body ends up carrying
    a fragment of that echoed statement.

    The two occurrences are told apart by what immediately follows them, not by
    what precedes them -- a preceding newline is not a reliable signal, since
    an in-flight prompt from the *previous* cell can legitimately sit directly
    in front of the real marker too (see
    `test_state_is_not_polluted_by_the_previous_cells_prompt`, which exists
    precisely to pin that down). The embedded copy is always immediately
    followed by `echo_tail`, the fixed tail of the echo template itself
    (`" << endl;` for Macaulay2, `");` for Singular), because that is what is on
    the rest of the line we sent; the real, bare-printed copy never is.
    Skipping any occurrence with that tail right after it, and continuing the
    search past it, finds the real one regardless of what backend-specific
    noise precedes either.
    """
    pos = after
    while (hit := raw.find(marker, pos)) != -1:
        echoed_end = hit + len(marker)
        following = raw[echoed_end : echoed_end + len(echo_tail)]
        # A read boundary is not a language boundary. Until a byte
        # distinguishes a bare marker from its echoed source, wait.
        if echo_tail.startswith(following) and len(following) < len(echo_tail):
            return -1
        if following == echo_tail:
            pos = echoed_end
            continue
        return hit
    return -1


class SentinelBackend(abc.ABC):
    """An interpreter reading stdin, framed by a nonce it is asked to echo.

    Less trustworthy than the driver protocol and unavoidable: neither Singular
    nor Macaulay2 offers a way to be spoken to in frames. The nonce is fresh
    per cell so a cell that echoes text cannot forge the end of its own reply.

    Both child descriptors share one pipe, so synchronous writes preceding the
    end marker stay in its cell regardless of parent scheduling. The combined
    transcript lives in stdout, with capture_mode="merged"; stream origin is
    unavailable. Background writers and unflushed output after the marker are
    outside this protocol, which does not isolate or certify computations.
    """

    name: BackendName
    script_suffix: str
    language: str
    kernel_name: str
    comment: str
    preamble: str
    version_source: str
    error_pattern: re.Pattern[str]
    echo: str
    environment: Mapping[str, str] = MappingProxyType({})
    # Neither Singular nor Macaulay2 can be asked what is in its namespace, so
    # every record from them is digestless, and `check_replay` reports every
    # replay of one as output reproduced and state not compared -- in a
    # rebuild's note and an export's verdicts alike.
    #
    # Both child descriptors share one pipe (see the class docstring), the
    # status is Hardy's reading of an error banner, and a frame is plain
    # source with no word in it for a stop.
    capture_mode: CaptureMode = "merged"
    reports_status = False
    frame_carries_stop = False
    script_stderr_chrome: tuple[re.Pattern[str], ...] = ()

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

    @abc.abstractmethod
    def argv(self, command: Path | None, max_output_bytes: int = DEFAULT_OUTPUT_BYTES) -> tuple[str, ...]:
        """How this interpreter is started reading cells from stdin."""

    def script_argv(self, command: Path | None, script: Path) -> tuple[str, ...]:
        return self.argv(command)

    def render_cell(self, source: str) -> str:
        """Verbatim. These interpreters print a statement's value themselves."""
        return source

    def frame(self, source: str, nonce: str, *, stopping: bool = False) -> bytes:
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
        """A cell's status, read from its transcript: an error banner fails it."""
        found = self.error_pattern.search(stdout) or self.error_pattern.search(stderr)
        return "error" if found else "ok"

    def retention(self, cap: int) -> int:
        """`cap` itself. There is no framing here for a cut to break, so the
        parent's reader enforces the cap directly and keeps scanning for the
        end marker past it."""
        return cap

    def arm(self, kernel: Kernel, nonce: str) -> None:
        """Scan for this cell's end marker without discarding what is buffered.

        Whatever the previous cell left behind is settled by pipe order once
        the extractor finds this cell's begin marker.
        """
        kernel.rearm(SENTINEL_END.format(nonce=nonce).encode())

    def extractor(self, kernel: Kernel | None, nonce: str, fed: str) -> Extractor:
        """Read one cell's reply from between its begin and end markers.

        `fed` is the framed text this cell was sent, for `sanitize` to
        recognise the interpreter's echo of it.
        """
        begin = SENTINEL_BEGIN.format(nonce=nonce).encode("utf-8")
        end = SENTINEL_END.format(nonce=nonce).encode("utf-8")
        echo_tail = self.echo.rsplit("{marker}", 1)[1].encode("utf-8")

        def extract(raw: bytes) -> Reply | None:
            # A pipe preserves write order, so whatever the previous cell
            # printed -- including a prompt still arriving when this cell was
            # armed -- is necessarily before this cell's begin marker in the
            # stream, however late it happens to show up. Waiting for the
            # begin marker before looking at anything excludes it without
            # ever having to guess whether it has fully arrived.
            begin_at = _find_marker(raw, begin, 0, echo_tail)
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
            end_at = _find_marker(raw, end, start, echo_tail)
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
            body = self.sanitize(body, fed)
            outcome = CellOutcome(
                # Both child descriptors share one OS pipe. Errors written
                # before the marker cannot arrive behind it in another drain.
                status=self.classify(body),
                stdout=body,
                capture_mode="merged",
                capture_truncated=bool(kernel and kernel.truncated),
            )
            return outcome, consumed

        return extract

    def release(self, kernel: Kernel, consumed: int) -> None:
        """Drop the answered cell's bytes; keep anything behind its end marker."""
        kernel.consume(consumed)

    def partial(self, kernel: Kernel, outcome: CellOutcome) -> CellOutcome:
        """Without a closing marker this is a bounded partial transcript,
        including framing. It is preserved as diagnostics, never as success."""
        return outcome.model_copy(update={
            "stdout": kernel.stdout_text(),
            "capture_mode": "merged",
            "capture_truncated": kernel.truncated,
        })

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


class SingularBackend(SentinelBackend):
    """Singular, quiet (`-q`), reporting failure with its `? ` error banner."""

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
    # The usual three-space banner can also follow an unterminated stdout
    # write in combined capture. Such banner-shaped user text is conservative
    # failure too; a lone question mark in prose is still ordinary output.
    error_pattern = re.compile(r"(?m)(?:^[ \t]*|[ \t]{3,})\? ")

    def argv(self, command: Path | None, max_output_bytes: int = DEFAULT_OUTPUT_BYTES) -> tuple[str, ...]:
        return (str(command) if command else "Singular", "-q")


class _EchoReader:
    """M2's echo of the text it was fed, recognised line by line in feed order.

    `Macaulay2Backend._strip_echo` says which lines count as echo and why.
    """

    def __init__(self, lines: list[str], fed: str, prompt: re.Pattern[str]) -> None:
        self._prompt = prompt
        self._pending = [line.rstrip() for line in fed.splitlines() if line.strip()]
        source_counts = Counter(self._pending)
        prompt_counts = Counter(
            line[found.end():].rstrip()
            for line in lines
            if (found := prompt.match(line)) is not None
        )
        # Prompt text seen more often than it was fed: some of it is the
        # cell's own output, and no occurrence can be told from the rest.
        self._ambiguous = {
            shown for shown, count in prompt_counts.items() if count > source_counts[shown]
        }
        self._cursor = 0
        self._width = 0

    def consumes(self, line: str) -> bool:
        """Whether `line` is echo, moving past it in the fed text if so."""
        found = self._prompt.match(line)
        if found is not None:
            return self._consumes_prompt(line[found.end():].rstrip(), found.end())
        if not line:
            self._width = 0
            return False
        if self._continues(line):
            self._cursor += 1
            return True
        return False

    def _consumes_prompt(self, shown: str, width: int) -> bool:
        if not shown:
            # An empty interpreter prompt can introduce the next continuation
            # block, or remain after EOF.
            self._width = width
            return True
        # User output can itself look like `i42 : coefficient`. The prefix
        # alone is not evidence that this line was echoed.
        if shown in self._ambiguous or shown not in self._pending[self._cursor:]:
            self._width = 0
            return False
        self._cursor = self._pending.index(shown, self._cursor) + 1
        self._width = width
        return True

    def _continues(self, line: str) -> bool:
        width = self._width
        return (
            bool(width)
            and self._cursor < len(self._pending)
            and line.startswith(" " * width)
            and line[width:].rstrip() == self._pending[self._cursor]
        )


class Macaulay2Backend(SentinelBackend):
    """Macaulay2, whose echo, output counters and stderr banners need taming.

    Every rule below was fixed against a pinned build's verbatim transcript;
    each comment names the CI run that showed it.
    """

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
    # In combined capture a preceding stdout write need not end in a newline.
    # A full banner embedded in ordinary text is conservatively an error too.
    # Loaded files use their path (which may contain spaces), not `stdio`.
    # Start at the numeric suffix's colon. Consuming an arbitrary path here
    # retries the entire remaining line at every position on clean output.
    error_pattern = re.compile(r"(?<=[^\r\n]):\d+:\d+:\(\d+\)(?::\[\d+\])?: error:")
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

        A nonempty prompt line goes only when its text matches remaining fed
        source and its occurrences do not exceed the source occurrences. Extra
        matches are ambiguous user output: preserve all of them, even if that
        leaves echo in the comparison and causes export divergence. A
        continuation line goes only when it is indented to exactly that
        prompt's width *and* what is under the indent is verbatim a line of
        the text the interpreter was fed. Both conditions are needed: an
        alignment row is indented to the same width by coincidence, and a cell
        that prints is free to reproduce its own source. A truly empty line
        ends the block, which is what M2 puts between an echo and the value it
        then computes; a fed blank line comes back as the indent alone and so
        does not end anything.

        The echo arrives in the order the lines were fed, so the match is
        against a cursor into the fed text rather than a set over all of it.
        A set let a cell whose genuine output equalled a *different* line's
        source -- cell 2 printing `     x^2 + y^2` under cell 1's `x^2 + y^2`
        -- lose that output silently. The cursor rejects the match: by the
        time cell 2's output arrives, cell 1's line has already been echoed
        and passed.

        What survives this and should not is narrow enough to name: output
        indented by exactly the prompt width, abutting the echo with no blank
        line between, whose text is verbatim the *next* line to be echoed.
        """
        lines = stdout.split("\n")
        echo = _EchoReader(lines, fed, self._echo_prompt)
        return "\n".join([line for line in lines if not echo.consumes(line)])

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
        """The version alone, without the `o = ` marker `sanitize` keeps."""
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

    def argv(self, command: Path | None, max_output_bytes: int = DEFAULT_OUTPUT_BYTES) -> tuple[str, ...]:
        # `-s` was a guess and is obsolete in Macaulay2 1.26.06 (CI run
        # 30166702246: "error: command line option -s is obsolete." killed the
        # kernel before it could answer the version probe). Dropped, not
        # replaced -- there is no confirmed silent-mode equivalent yet.
        return (str(command) if command else "M2", "--no-readline", "-q")


BACKENDS: dict[str, Callable[[], CasBackend]] = {
    "sympy": SympyBackend,
    "singular": SingularBackend,
    "macaulay2": Macaulay2Backend,
}


def backend_for(name: str) -> CasBackend:
    """The backend `cas_backend` names, or a `ValueError` listing the known ones."""
    try:
        return BACKENDS[name]()
    except KeyError:
        raise ValueError(
            f"unknown cas_backend {name!r}; known backends are {sorted(BACKENDS)}"
        ) from None


