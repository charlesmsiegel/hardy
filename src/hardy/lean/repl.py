"""One REPL worker process: spawn, blank-line-framed JSON over stdio, timeouts.

Framing (leanprover-community/repl): a request is a JSON object followed by
a blank line; a response is a JSON object followed by a blank line.

Not concurrency-safe per instance — one in-flight request at a time. The
pool (hardy.lean.pool) enforces exclusivity by checking workers out.

On timeout, process death, or a malformed (non-JSON) response the process
is killed and left dead: a worker that hung, crashed, or desynced
mid-command has unknowable state and must be replaced, never reused
(DESIGN.md Component 1, environment isolation).

cleanup_argv: sandboxed workers pass e.g. ["docker", "kill", <name>] here.
Killing our child process alone kills only the docker *client* — SIGKILL
is never proxied to the container, and --rm removes a container only after
it exits on its own — so close() must also run this trusted cleanup
command, or a hung REPL survives as a runaway container.
"""

import asyncio
import json
from pathlib import Path

from pydantic import ValidationError

from .messages import CommandResponse, TacticResponse


class LeanReplError(Exception):
    pass


class ReplTimeout(LeanReplError):
    pass


class ReplDied(LeanReplError):
    pass


class LeanRepl:
    def __init__(
        self,
        argv: list[str],
        cwd: Path | None = None,
        default_timeout: float = 60.0,
        cleanup_argv: list[str] | None = None,
        stream_limit: int = 10 * 1024 * 1024,
    ):
        self._argv = argv
        self._cwd = cwd
        self._default_timeout = default_timeout
        self._cleanup_argv = cleanup_argv
        self._stream_limit = stream_limit
        self._proc: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        # limit bounds a single response line; asyncio's 64 KB default is far
        # too small for real goal states, and overflow raises ValueError.
        self._proc = await asyncio.create_subprocess_exec(
            *self._argv,
            cwd=self._cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=self._stream_limit,
        )

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    async def send(self, request: dict, timeout: float | None = None) -> dict:
        if not self.alive:
            raise ReplDied("repl process is not running")
        payload = json.dumps(request, ensure_ascii=False) + "\n\n"
        self._proc.stdin.write(payload.encode())
        try:
            await self._proc.stdin.drain()
            raw = await asyncio.wait_for(
                self._read_response(), timeout or self._default_timeout
            )
        except TimeoutError:
            await self.close()
            raise ReplTimeout(
                f"no response within {timeout or self._default_timeout}s"
            ) from None
        except (ReplDied, ConnectionResetError, BrokenPipeError) as exc:
            await self.close()
            raise ReplDied(str(exc) or "repl process died") from None
        except ValueError as exc:
            # readline() raises ValueError when a frame exceeds stream_limit
            # (UnicodeDecodeError on garbled bytes is a ValueError too).
            await self.close()
            raise ReplDied(f"oversized or undecodable repl frame: {exc}") from None
        try:
            return json.loads(raw)
        except ValueError:
            # Truncated/garbled output = protocol desync; the worker is dirty.
            await self.close()
            raise ReplDied("malformed JSON from repl") from None

    async def _read_response(self) -> str:
        lines: list[bytes] = []
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                raise ReplDied("repl closed stdout")
            if line.strip() == b"":
                if lines:
                    return b"".join(lines).decode()
                continue  # tolerate leading blank lines
            lines.append(line)

    async def _validate(self, model, payload: dict):
        try:
            return model.model_validate(payload)
        except ValidationError as exc:
            # A response we cannot type is a protocol mismatch (e.g. a newer
            # repl); the worker's state is unknowable — same as garbled JSON.
            await self.close()
            raise ReplDied(f"response failed schema validation: {exc}") from None

    async def run_command(
        self, code: str, env: int | None = None, timeout: float | None = None
    ) -> CommandResponse:
        request: dict = {"cmd": code}
        if env is not None:
            request["env"] = env
        return await self._validate(CommandResponse, await self.send(request, timeout))

    async def run_tactic(
        self, tactic: str, proof_state: int, timeout: float | None = None
    ) -> TacticResponse:
        request = {"tactic": tactic, "proofState": proof_state}
        return await self._validate(TacticResponse, await self.send(request, timeout))

    async def close(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            self._proc.kill()
            await self._proc.wait()
        if self._cleanup_argv is not None:
            try:
                cleanup = await asyncio.create_subprocess_exec(
                    *self._cleanup_argv,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await cleanup.wait()
            except OSError:
                pass  # best-effort: close() must never raise over cleanup
            self._cleanup_argv = None
