"""What Hardy says before its first prompt.

The warning line is not decoration. AGENTS.md makes the missing sandbox a
standing disclosure, and this is the only notice a user gets before the model
executes code on their machine, so it is specified here rather than left to
whichever path happens to start the session. Computer algebra cells execute
unsandboxed exactly like Lean and LaTeX do, so a live CAS backend extends the
same warning rather than getting a separate, easier-to-miss one.
"""

from __future__ import annotations

from typing import Any

from ..runner import WARNING


def lines(config: Any, *, cas: Any = None, cas_detail: str = "") -> list[tuple[str, str]]:
    """`cas` is the runtime (or `None` if no backend was discovered) and
    `cas_detail` is `cas_tools.build_runtime`'s second return value: a
    version string when `cas` is not `None`, the reason it is `None`
    otherwise. Neither argument changes the banner at all when `cas_detail`
    is empty -- the default, and what every caller that knows nothing about
    CAS (tests, `PlainUi`-less callers) gets.
    """
    project = config.lean_project or "current directory"
    warning = f"WARNING: {WARNING} LaTeX is also executed without isolation."
    if cas is not None:
        warning += " So are computer algebra cells."
    rows = [
        ("normal", "Hardy — interactive mathematics workspace"),
        (
            "hint",
            f"Workspace: {config.layout.problem}    Model: {config.model}  "
            f"(Claude Code subscription)",
        ),
        ("hint", f"Lean project: {project}"),
    ]
    if cas_detail:
        status = cas_detail if cas is not None else f"unavailable — {cas_detail}"
        rows.append(("hint", f"Computer algebra: {status}"))
    rows.append(("warning", warning))
    rows.append(
        (
            "hint",
            "/help for commands · /exit to leave · your transcript and artifacts "
            "are saved as you work",
        )
    )
    return rows
