#!/usr/bin/env python3
"""A stand-in CAS kernel speaking Hardy's length-prefixed driver protocol.

Real enough to exercise the transport: a genuine child process, genuine pipes,
genuine framing. What it computes is scripted by the cell source, so a test can
ask for a timeout, a flood, or an answer that refuses to reproduce without
needing SymPy, Singular, or Macaulay2 installed.
"""

import json
import sys
import time
import uuid

HEADER_BYTES = 10
HISTORY: list[str] = []


def read_exact(stream, count):
    chunks = []
    while count > 0:
        chunk = stream.read(count)
        if not chunk:
            return None
        chunks.append(chunk)
        count -= len(chunk)
    return b"".join(chunks)


def answer(source: str) -> dict:
    word = source.strip()
    if word == "boom":
        return {"status": "error", "stdout": "", "stderr": "boom", "value_repr": ""}
    if word == "hang":
        time.sleep(120)
    if word == "die":
        raise SystemExit(1)
    if word == "flood":
        return {"status": "ok", "stdout": "y" * 400_000, "stderr": "", "value_repr": ""}
    if word == "drift":
        # A different answer every process, so a replay of an accepted cell
        # cannot reproduce it. This is what divergence detection is for.
        return {"status": "ok", "stdout": "", "stderr": "", "value_repr": uuid.uuid4().hex}
    if word == "noisy":
        return {"status": "ok", "stdout": "out", "stderr": "warning: noisy", "value_repr": "1"}
    HISTORY.append(word)
    # Deterministic in the sequence of cells, so an honest replay reproduces it.
    return {"status": "ok", "stdout": "", "stderr": "", "value_repr": str(len(HISTORY))}


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
    while True:
        header = read_exact(stdin, HEADER_BYTES)
        if header is None:
            return
        payload = read_exact(stdin, int(header))
        if payload is None:
            return
        reply = clip(answer(json.loads(payload.decode("utf-8")).get("source", "")), limit)
        encoded = json.dumps(reply).encode("utf-8")
        stdout.write(f"{len(encoded):0{HEADER_BYTES}d}".encode("ascii"))
        stdout.write(encoded)
        stdout.flush()


if __name__ == "__main__":
    main()
