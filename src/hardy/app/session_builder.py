"""Construct interactive sessions from one configuration and prepared capabilities.

Launch and project reopening own resource admission, cancellation and cleanup.
This builder only binds those resources and settings to the session, so neither
path can silently omit a configured limit or give background workers different
kernels. A fallback build still receives a fresh provider runtime factory.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from hardy.algebra import tools as cas_tools
from hardy.app.config import Config
from hardy.app.wiring import session_runtime
from hardy.formal.search import SearchToolRuntime
from hardy.workflows.interactive.session import MathematicsSession


@dataclass(frozen=True)
class SessionBuilder:
    """Configured construction over resources whose lifetime the caller owns."""

    config: Config
    cas: cas_tools.CasToolRuntime | None
    cas_detail: str
    search: SearchToolRuntime | None
    search_detail: str

    def worker_cas(self, cwd: Path) -> cas_tools.CasToolRuntime | None:
        """A fresh kernel, with the same policy, logged in one worker's directory."""
        runtime, _detail = cas_tools.build_runtime(
            backend_name=self.config.cas_backend, command=self.config.cas_command,
            limits=self.config.limits, log_path=cwd / "cells.jsonl", cwd=cwd,
        )
        return runtime

    def build(
        self, confirm: Callable[[dict[str, object]], bool], *, fresh_thread: bool = False,
        require_existing: bool = False,
        make_session: Callable[..., MathematicsSession] = MathematicsSession,
        make_runtime: Callable[[Config], Callable[..., object]] = session_runtime,
    ) -> MathematicsSession:
        """Bind every setting; launch-specific fresh-thread policy stays with its caller.

        Construction functions are explicit so launchers can retain their existing
        injection seams without duplicating the session's argument mapping.
        """
        config = self.config
        return make_session(
            config.layout.problem,
            make_runtime(config),
            config.lean_command,
            config.latex_command,
            confirm,
            lean_project=config.lean_project,
            lean_timeout=config.lean_timeout,
            cas=self.cas,
            cas_detail=self.cas_detail,
            search=self.search,
            search_detail=self.search_detail,
            project_context=config.project_context,
            context_window=config.context_window,
            fresh_thread=fresh_thread,
            limits=config.limits,
            delegation_slots=config.delegation_workers,
            detach_after=config.compute_detach_seconds,
            cas_factory=self.worker_cas,
            chat=config.chat,
            require_existing=require_existing,
        )
