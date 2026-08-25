"""Premise search for the interactive session.

`chat.py` reaches Lean through a `LeanTools` built around a placeholder
`Request`, and its `_environment` is a cache-invalidation stamp rather than an
`EnvironmentIdentity`. Neither can be handed to a `LeanService`, which is why
this assembles one from the `Config` instead and hands the session a runtime
rather than parts it would have to wire up itself.

The shape follows `cas_tools.build_runtime`: discover first, and return either
a runtime or the reason there is none. What differs is what the caller does
with a `None`. A missing CAS backend means the `cas_*` tools are not offered,
because a tool that can only fail costs the model a turn to discover what
Hardy already knew. A missing Lake project means the search tools are offered
*and refuse with the reason*, because those two absences are not the same
thing: a CAS backend is optional, and a Lean project is what Hardy is for. A
model handed no search tool concludes Hardy cannot search -- which is the
defect this module exists to fix -- while a model told "no Lake project is
configured" can put that in front of the user, who can fix it.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from .config import Config
from .lean import LeanService, environment_identity
from .models import ToolResult
from .retrieval import PremiseRetriever, build_retriever

SEARCH_TOOL_NAMES = frozenset({"rank_premises", "search_declarations", "inspect_declarations"})

# What one `inspect_declarations` call may resolve. The session bounds a model
# observation elsewhere; this bounds the work before it is done.
MAX_NAMES = 20


class SearchToolRuntime:
    """The search tools, bound to one pinned environment."""

    def __init__(self, service: LeanService, retriever: PremiseRetriever) -> None:
        self.service = service
        self.retriever = retriever

    def rank_premises(self, goal: str, limit: int = 10) -> ToolResult:
        return self._answer(lambda: self.retriever.rank(goal, limit))

    def search_declarations(self, query: str, limit: int = 10) -> ToolResult:
        return self._answer(lambda: self.service.search_declarations(query, limit))

    def inspect_declarations(self, names: list[str]) -> ToolResult:
        # Refused, not truncated. `DeclarationInspection` carries no marker for
        # names that were never looked at, so silently dropping the tail hands
        # back a successful result in which "not found" and "not asked" are
        # indistinguishable -- a partial answer presented as a whole one.
        if len(names) > MAX_NAMES:
            return ToolResult(
                False,
                f"inspect_declarations takes at most {MAX_NAMES} names; {len(names)} were given",
            )
        return self._answer(
            lambda: self.service.inspect_declarations(tuple(str(name) for name in names))
        )

    def _answer(self, call: Any) -> ToolResult:
        """Every refusal is an answer the model can read.

        A `ValueError` from a bound -- a goal that is too long, a limit out of
        range -- is the tool saying what it will accept, and reaches the model
        as that sentence rather than as the dispatcher's generic "invalid tool
        call". A transport failure is likewise an outcome: the ranking already
        records which source did not answer, so only a failure that took the
        whole call needs catching here.
        """
        try:
            value = call()
        except ValueError as error:
            return ToolResult(False, str(error))
        except Exception as error:  # noqa: BLE001 - a failed search is an answer, not a crash
            return ToolResult(False, f"{type(error).__name__}: {error}")
        return ToolResult(True, value.model_dump_json())


# Chat elaborates through `config.lean_command` (default `lake env lean`),
# while `LeanService` runs `config.lake` as `lake env lean --json`. Under the
# default those are one program. Under a customised `HARDY_LEAN_COMMAND` -- a
# wrapper script, a bare `lean`, a second Lake binary -- they are not, and the
# model would search one environment and check the name it found in another,
# reading a provenance that names neither discrepancy.
LAKE_ENV_LEAN = ("lake", "env", "lean")


def _same_toolchain(config: Config) -> bool:
    """Whether searching and checking would run the same program.

    Resolved through `PATH` and compared by inode, not by basename. Comparing
    names was the first attempt and does not work: with
    `HARDY_LAKE=/opt/pinned/lake` and the default `lean_command`, both reduce
    to `lake` while `PATH` resolves chat's to an unrelated binary -- the exact
    split this exists to catch, passing it.

    Anything that cannot be resolved is refused rather than assumed equal. A
    wrapper that execs the right Lake is refused too, and that is the right
    trade: no check short of running it could tell, and a false equivalence
    here hands the model a declaration its own Lean cannot elaborate.
    """
    command = tuple(config.lean_command)
    if command[1:] != LAKE_ENV_LEAN[1:]:
        return False
    resolved = _resolve(command[0], config.lean_project)
    lake = _resolve(str(config.lake), config.lean_project)
    if resolved is None or lake is None:
        return False
    try:
        return os.path.samefile(resolved, lake)
    except OSError:
        return False


def _resolve(command: str, project: Path | None) -> str | None:
    """Where this command actually runs from.

    A relative path is resolved against the *project* directory, because that
    is the working directory both Lean facades hand the child. Resolving it
    against Hardy's own process directory instead made a matching pair like
    `./bin/lake` on both sides compare unequal whenever Hardy was started
    outside the project -- refusing search over a difference that does not
    exist.
    """
    path = Path(command)
    if not path.is_absolute() and (path.parent != Path(".") or command.startswith(".")):
        if project is None:
            return None
        return shutil.which(str((project / path).resolve()))
    return shutil.which(command)


def build_runtime(config: Config) -> tuple[SearchToolRuntime | None, str]:
    """A search runtime for this configuration, or None and the reason why."""
    if not _same_toolchain(config):
        return None, (
            f"lean_command is {' '.join(config.lean_command)!r} but search would run "
            f"{config.lake}; searching one toolchain and checking in another would hand the "
            "model a declaration its own Lean cannot elaborate"
        )
    try:
        environment = environment_identity(config.lean_project)
    except (ValueError, OSError, KeyError, StopIteration, json.JSONDecodeError) as error:
        return None, str(error) or f"the Lake project could not be read: {type(error).__name__}"
    assert config.lean_project is not None  # environment_identity refuses None
    service = LeanService(
        lake=config.lake,
        lean_project=config.lean_project,
        environment=environment,
        limits=config.limits,
    )
    return (
        SearchToolRuntime(service, build_retriever(service, config.limits)),
        f"Mathlib {environment.mathlib_revision[:12]} in {config.lean_project}",
    )
