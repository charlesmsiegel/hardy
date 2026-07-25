"""A persistent Python kernel, spoken to in length-prefixed JSON.

Hardy drives SymPy through this rather than by scraping a REPL prompt. A
REPL's framing is a guess about where one answer ends; a byte count is not.
Every reply carries its length in a fixed ten-byte header, so a cell that
prints something resembling a prompt cannot be mistaken for the end of output.

Two behaviours here exist because `exec` alone would get them wrong. A bare
final expression — `groebner(F, x, y)` — has its value discarded by `exec`,
and no display hook fires, so the value would be invisible; the trailing
expression is therefore split off and evaluated separately. And that value is
bound to `_`, as an interactive interpreter does, which is what lets Hardy
answer an over-large result with a summary and still leave the model a way to
reach the whole thing.

This module imports nothing from Hardy. It is executed as a child process and
must keep working when the rest of the package is not importable.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import sys
import traceback

HEADER_BYTES = 10
CELL_FILENAME = "<hardy-cell>"


def read_exact(stream, count: int) -> bytes | None:
    """Read exactly `count` bytes, or None if the stream ended first."""
    chunks: list[bytes] = []
    while count > 0:
        chunk = stream.read(count)
        if not chunk:
            return None
        chunks.append(chunk)
        count -= len(chunk)
    return b"".join(chunks)


def clip(text: str, limit: int) -> tuple[str, bool]:
    """Truncate here, at the source, rather than in the parent's pipe reader.

    A reply is length-prefixed, so a parent that stopped reading at a byte cap
    could never assemble the frame it was promised — it would wait out the cell
    timeout and lose the kernel over an answer that was merely large. Clipping
    before serialising keeps every envelope parseable.
    """
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def run_cell(source: str, namespace: dict, limit: int) -> dict:
    """Execute one cell against the persistent namespace and describe what happened."""
    try:
        parsed = ast.parse(source, filename=CELL_FILENAME)
    except SyntaxError:
        return {
            "status": "error",
            "stdout": "",
            "stderr": traceback.format_exc(),
            "value_repr": "",
        }

    # The trailing expression is evaluated rather than executed so its value
    # survives; anything before it is ordinary statement execution.
    trailing = None
    if parsed.body and isinstance(parsed.body[-1], ast.Expr):
        trailing = ast.Expression(parsed.body.pop().value)

    captured_out, captured_err = io.StringIO(), io.StringIO()
    status, value_repr = "ok", ""
    with contextlib.redirect_stdout(captured_out), contextlib.redirect_stderr(captured_err):
        try:
            if parsed.body:
                exec(compile(parsed, CELL_FILENAME, "exec"), namespace)
            if trailing is not None:
                value = eval(compile(trailing, CELL_FILENAME, "eval"), namespace)
                if value is not None:
                    namespace["_"] = value
                    value_repr = repr(value)
        # A cell is untrusted input, and `exit()` raises SystemExit: the kernel
        # has to outlive whatever a cell does, or one stray call ends a session
        # and every value in it.
        except BaseException:
            status = "error"
            traceback.print_exc()
    out, out_clipped = clip(captured_out.getvalue(), limit)
    err, err_clipped = clip(captured_err.getvalue(), limit)
    value, value_clipped = clip(value_repr, limit)
    return {
        "status": status,
        "stdout": out,
        "stderr": err,
        "value_repr": value,
        "capture_truncated": out_clipped or err_clipped or value_clipped,
    }


def _refuse_to_quit(*_args, **_kwargs):
    raise RuntimeError(
        "exit() and quit() would end the computer algebra session and discard "
        "every value in it. Ask Hardy to reset the session instead."
    )


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 256 * 1024
    namespace: dict = {"__name__": "__hardy_cas__", "__builtins__": __builtins__}
    # `exit` is site.Quitter, which closes stdin before raising SystemExit. No
    # handler can undo that: the kernel is deaf from then on and the session's
    # state is gone. Shadowed, because these only mean anything in a REPL that
    # owns its terminal, and this one is spoken to over a pipe.
    namespace["exit"] = namespace["quit"] = _refuse_to_quit
    # Preloaded so a cell can say `groebner(...)` directly, and so the exported
    # script — which emits this same import — reproduces the session.
    with contextlib.suppress(BaseException):
        exec("from sympy import *", namespace)

    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    while True:
        header = read_exact(stdin, HEADER_BYTES)
        if header is None:
            return
        try:
            length = int(header.decode("ascii"))
        except ValueError:
            return
        payload = read_exact(stdin, length)
        if payload is None:
            return
        try:
            request = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        reply = run_cell(str(request.get("source", "")), namespace, limit)
        encoded = json.dumps(reply, ensure_ascii=False).encode("utf-8")
        stdout.write(f"{len(encoded):0{HEADER_BYTES}d}".encode("ascii"))
        stdout.write(encoded)
        stdout.flush()


if __name__ == "__main__":
    main()
