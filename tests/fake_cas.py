#!/usr/bin/env python3
"""A stand-in CAS kernel speaking Hardy's length-prefixed driver protocol.

Real enough to exercise the transport: a genuine child process, genuine pipes,
genuine framing. What it computes is scripted by the cell source, so a test can
ask for a timeout, a flood, or an answer that refuses to reproduce without
needing SymPy, Singular, or Macaulay2 installed.
"""

import contextlib
import json
import pathlib
import signal
import sys
import time
import uuid

HEADER_BYTES = 10
HISTORY: list[str] = []


PENDING_INTERRUPT = False
# Set by the `deafread` cell: the loop answers it and then stops reading.
STOP_READING = False
_STOP_SIGNALS = tuple(
    found
    for found in (getattr(signal, name, None) for name in ("SIGINT", "SIGBREAK"))
    if found is not None
)


def _remember(_signum, _frame):
    """As `cas_driver._remember`: record the stop rather than raising it."""
    global PENDING_INTERRUPT
    PENDING_INTERRUPT = True


def _handle_stops_by(handler):
    for number in _STOP_SIGNALS:
        with contextlib.suppress(OSError, ValueError):
            signal.signal(number, handler)


def read_exact(stream, count):
    """As `cas_driver.read_exact`: read with the stop deferred, never raised."""
    chunks = []
    while count > 0:
        chunk = stream.read(count)
        if not chunk:
            return None
        chunks.append(chunk)
        count -= len(chunk)
    return b"".join(chunks)


def _matches(word: str, prefix: str) -> bool:
    return word == prefix or word.startswith(prefix + " ")


def _announce(word: str) -> None:
    """Write the readiness path a cell was given, if any.

    `hang /tmp/x` writes `/tmp/x` before blocking, so a test can wait for the
    cell to be *running* before pressing Esc. Without it a test can only press
    blind, and a press landing before the kernel has read the frame is answered
    without the cell ever running -- a real behaviour, but not the one a test of
    the in-flight path means to exercise.
    """
    _, _, path = word.partition(" ")
    if path.strip():
        pathlib.Path(path.strip()).write_text("ready", encoding="utf-8")


def answer(source: str) -> dict:
    word = source.strip()
    if word == "boom":
        return {"status": "error", "stdout": "", "stderr": "boom", "value_repr": ""}
    if _matches(word, "deaf"):
        # Refuses the interrupt, as a cell sitting in a C loop that never
        # returns to the interpreter would. The parent's grace has to run out
        # and the kernel has to be dropped, which is the escalation path.
        # Announced *after* the handlers are in place, so a test that waits for
        # it cannot press into the window where the default handler still runs.
        for name in ("SIGINT", "SIGBREAK"):
            handled = getattr(signal, name, None)
            if handled is not None:
                signal.signal(handled, signal.SIG_IGN)
        _announce(word)
        time.sleep(120)
    if _matches(word, "deafterm"):
        # Deaf to SIGTERM as well, so only SIGKILL is left -- which is what the
        # second press has to reach for without waiting out a polite teardown.
        for name in ("SIGINT", "SIGBREAK", "SIGTERM"):
            handled = getattr(signal, name, None)
            if handled is not None:
                signal.signal(handled, signal.SIG_IGN)
        _announce(word)
        time.sleep(120)
    if _matches(word, "hang"):
        _announce(word)
        time.sleep(120)
    if _matches(word, "swallow"):
        # Catches the interrupt and answers as though nothing happened, which
        # a library that uses `KeyboardInterrupt` to unwind can genuinely do.
        _announce(word)
        with contextlib.suppress(KeyboardInterrupt):
            time.sleep(120)
        return {"status": "ok", "stdout": "", "stderr": "", "value_repr": "swallowed"}
    if _matches(word, "catcher"):
        # `swallow`, but only from its second process onwards. A cell has to be
        # accepted before a rebuild will replay it, and a cell that blocks
        # until pressed cannot be accepted -- so the first run answers at once
        # and leaves its marker behind, and every later run finds the marker,
        # announces itself under a name of its own, and waits to be signalled.
        # The answer is the same either way, which is the whole point: the
        # replay reproduces the recorded output while the namespace it was
        # supposed to rebuild took a different path.
        _, _, raw = word.partition(" ")
        marker = pathlib.Path(raw.strip())
        if marker.exists():
            with contextlib.suppress(KeyboardInterrupt):
                # Inside the suppression, so a press landing between the
                # announcement and the sleep is caught like any other.
                marker.with_name(marker.name + ".replay").write_text(
                    "ready", encoding="utf-8"
                )
                time.sleep(120)
        else:
            marker.write_text("ready", encoding="utf-8")
        return {"status": "ok", "stdout": "", "stderr": "", "value_repr": "caught"}
    if word == "slow":
        # Long enough to be measurable against a budget, short enough that a
        # test replaying it a few times still finishes quickly. Falls through
        # to the deterministic counter below, so it is accepted and replays
        # faithfully like any other cell.
        time.sleep(0.5)
    if word == "deafread":
        # Answers this cell and then never reads its input again -- a kernel
        # wedged between cells rather than inside one. Nothing is in flight, so
        # there is no cell to interrupt; what there is, once the next cell
        # outgrows the pipe buffer, is a write with nowhere to go.
        global STOP_READING
        STOP_READING = True
    if word == "selfinterrupt":
        # The cell raising it, with nobody having pressed anything. The driver
        # cannot tell this from Hardy's own signal; the parent can.
        raise KeyboardInterrupt
    if word == "die":
        raise SystemExit(1)
    if word == "flood":
        return {"status": "ok", "stdout": "y" * 400_000, "stderr": "", "value_repr": ""}
    if word == "drift":
        # A different answer every process, so a replay of an accepted cell
        # cannot reproduce it. This is what divergence detection is for.
        return {"status": "ok", "stdout": "", "stderr": "", "value_repr": uuid.uuid4().hex}
    if word == "longdrift":
        # `drift`, but with a line long enough to matter when something quotes
        # it. A recorded line is allowed to be `cas_output_bytes` long, and an
        # export's explanation of a divergence is copied into export.json, the
        # notebook, and every tool result.
        return {
            "status": "ok",
            "stdout": "q" * 5_000 + uuid.uuid4().hex + "\n",
            "stderr": "",
            "value_repr": "",
        }
    if word == "noisy":
        return {"status": "ok", "stdout": "out", "stderr": "warning: noisy", "value_repr": "1"}
    HISTORY.append(word)
    # Deterministic in the sequence of cells, so an honest replay reproduces it.
    return {"status": "ok", "stdout": "", "stderr": "", "value_repr": str(len(HISTORY))}


def _interrupted() -> dict:
    return {
        "status": "interrupted",
        "stdout": "",
        "stderr": "interrupted before the cell ran",
        "value_repr": "",
    }


def clip(reply: dict, limit: int) -> dict:
    """Clip at the source, as the real driver does."""
    truncated = False
    for key in ("stdout", "stderr", "value_repr"):
        if len(reply[key]) > limit:
            reply[key] = reply[key][:limit]
            truncated = True
    reply["capture_truncated"] = truncated
    return reply


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 256 * 1024
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    global PENDING_INTERRUPT
    while True:
        if STOP_READING:
            # Wedged between cells with the stop still deferred, so it is deaf
            # as well as silent: only a kill ends this, and ending it is what
            # unblocks the parent's write.
            time.sleep(120)
            return
        _handle_stops_by(_remember)
        header = read_exact(stdin, HEADER_BYTES)
        if header is None:
            return
        payload = read_exact(stdin, int(header))
        if payload is None:
            return
        request = json.loads(payload.decode("utf-8"))
        source = request.get("source", "")
        # Exactly `cas_driver.main`'s shape: the handler goes up inside the
        # guarded block and comes down in its `finally`, and the deferred flag
        # is read after the switch, never before. Both switches are windows a
        # press can land in, and an uncaught `KeyboardInterrupt` in either one
        # kills the kernel and the whole namespace.
        try:
            _handle_stops_by(signal.default_int_handler)
            held = PENDING_INTERRUPT
            PENDING_INTERRUPT = False
            # `held` without `stopping` is a stop that arrived after the last
            # reply was flushed, aimed at a cell already over. Hardy says it no
            # longer wants one, so this cell runs.
            stop_this = held and request.get("stopping", True)
            reply = _interrupted() if stop_this else answer(source)
        except KeyboardInterrupt:
            # As `cas_driver.run_cell` does: the cell is abandoned and *the
            # kernel answers*, which is what leaves the session's state intact.
            reply = _interrupted()
        finally:
            _handle_stops_by(_remember)
        reply = clip(reply, limit)
        encoded = json.dumps(reply).encode("utf-8")
        stdout.write(f"{len(encoded):0{HEADER_BYTES}d}".encode("ascii"))
        stdout.write(encoded)
        stdout.flush()


if __name__ == "__main__":
    main()
