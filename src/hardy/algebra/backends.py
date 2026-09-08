"""Backend-specific framing and rendering; differing CAS semantics stay explicit."""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import Any, Literal

from hardy.algebra.contracts import HEADER_BYTES, SENTINEL_BEGIN, SENTINEL_END, BackendName


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


