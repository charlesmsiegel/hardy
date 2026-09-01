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
from .declarations import DeclarationIndex, search_result
from .domain import RunLimits
from .lean import DeclarationInspection, LeanService, environment_identity
from .models import ToolResult
from .modules import ModuleIndex
from .prompts import CONCEPT_HINT, SPELLINGS_HINT
from .retrieval import PremiseRetriever, build_retriever

SEARCH_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "rank_premises",
            "description": (
                "Rank the Mathlib declarations most likely to help with one goal. Paste "
                "the goal exactly as Lean printed it, hypotheses and all. A ranking is a "
                "heuristic, never evidence -- confirm any name with inspect_declarations "
                "before relying on it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string"},
                    "description": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["goal"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_declarations",
            "description": (
                "Search declaration names read from the pinned Mathlib package sources -- "
                "instant, no Lean process. Give a name fragment or concept words "
                "(`simple group` finds `IsSimpleGroup`). A hit is a lead to confirm with "
                "inspect_declarations; a miss is about this index, not Mathlib. For a "
                "result-type pattern, use rank_premises."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_declarations",
            "description": (
                "Resolve up to 20 exact Lean declaration names to their signatures in the "
                "pinned environment. This is how a name from a ranking is confirmed before "
                "it is used."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "names": {"type": "array", "items": {"type": "string"}, "maxItems": 20}
                },
                "required": ["names"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_modules",
            "description": (
                "Confirm a module path exists, and find the path of a workspace or "
                "shared-library module. From Mathlib itself, import `Mathlib` whole -- use "
                "this to check a path you were about to name, not to narrow an import. "
                "Answers from the package index, so it works even when Lean will not run."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
]

SEARCH_TOOL_NAMES = tuple(spec["function"]["name"] for spec in SEARCH_TOOLS)

# What one `inspect_declarations` call may resolve. The session bounds a model
# observation elsewhere; this bounds the work before it is done.
MAX_NAMES = 20


def _did_not_finish(value: Any) -> str:
    """Why this answer is not a report about Mathlib, or "".

    A search that timed out comes back as `success=False, timed_out=True,
    results=[]`, and it was being handed to the model as a *successful* tool
    call whose answer was an empty list. There is no way to read that except
    "Mathlib does not have it", and that is what a session concluded -- about
    `IsSimpleGroup`, which Mathlib has, before going on to assume four classical
    theorems Mathlib proves.

    This is the same distinction `inspect_declarations` refuses to blur when it
    will not truncate a name list: "not found" and "not asked" are different
    answers, and only one of them is evidence.
    """
    if getattr(value, "timed_out", False):
        return (
            "This search did not finish, so it is NOT a report that nothing matched -- "
            "Lean was stopped before it answered. Do not conclude the result is absent "
            "from Mathlib. Narrow the query, or ask the user to raise the Lean process "
            "timeout."
        )
    if getattr(value, "success", True) is False:
        return (
            "This search failed, so it is NOT a report that nothing matched. Do not "
            "conclude the result is absent from Mathlib; the diagnostics below say what "
            "went wrong."
        )
    return ""


class SearchToolRuntime:
    """The search tools, bound to one pinned environment."""

    def __init__(
        self,
        service: LeanService,
        retriever: PremiseRetriever,
        modules: ModuleIndex,
        declarations: DeclarationIndex,
    ) -> None:
        self.service = service
        self.retriever = retriever
        self.modules = modules
        self.declarations = declarations

    def rank_premises(self, goal: str, limit: int = 10) -> ToolResult:
        return self._answer(lambda: self.retriever.rank(goal, limit))

    def search_declarations(self, query: str, limit: int = 10) -> ToolResult:
        """Declaration names from the package sources, not from a Lean process.

        This ran `#find` in a fresh Lean process, and on the pinned toolchain
        that never answered once -- `declarations.py` records the measurement.
        The index answers instantly and offline; what it gives up is pattern
        matching, which `rank_premises` still speaks through Loogle.
        """
        return self._answer(lambda: search_result(self.declarations, query, limit))

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
        # `_answer` only hands back rendered text, and the decision below needs
        # the answer itself: `answer.resolved` empty is what makes a completed
        # batch worth the hint, and nothing in `result.output` says that any
        # more reliably than re-parsing the JSON `_answer` already built.
        answer = None

        def call() -> DeclarationInspection:
            nonlocal answer
            answer = self.service.inspect_declarations(tuple(str(name) for name in names))
            return answer

        result = self._answer(call)
        # A completed batch that resolved nothing is not a stopped run --
        # `_did_not_finish` already refused that case -- but it still is not
        # ordinary silence: it is telling the model about its spellings, not
        # about Mathlib.
        if result.ok and answer is not None and not answer.resolved:
            return ToolResult(True, SPELLINGS_HINT + result.output)
        return result

    def search_modules(self, query: str, limit: int = 20) -> ToolResult:
        """Module names, from the package index rather than from Lean.

        Deliberately not routed through `_answer`. The other three run a Lean
        process and report what it said; this one reads a file that Lake
        already wrote. A machine whose Lean will not start is exactly the
        machine on which a model most needs to be told what a module is called
        -- which is the position the graded session was in when it decided the
        installation was broken and stopped writing Lean.
        """
        found = self.modules.search(query, limit)
        if not found:
            where = f" under {self.modules.project}" if self.modules.project else ""
            message = (
                f"no module in this project has `{query}` in its name; "
                f"{len(self.modules.names())} modules were read from the package index{where}"
            )
            if len(query.split()) > 1:
                message += CONCEPT_HINT
            return ToolResult(False, message)
        return ToolResult(True, json.dumps({"modules": list(found)}, ensure_ascii=False))

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
        unfinished = _did_not_finish(value)
        if unfinished:
            return ToolResult(False, f"{unfinished}\n{value.model_dump_json()}")
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
        # Identified by the Lean chat elaborates with, which `_same_toolchain`
        # has just established is the one search would run.
        environment = environment_identity(
            config.lean_project,
            lean_command=tuple(config.lean_command),
            timeout_seconds=config.limits.lean_process_seconds,
        )
    except (ValueError, OSError, KeyError, StopIteration, json.JSONDecodeError) as error:
        return None, str(error) or f"the Lake project could not be read: {type(error).__name__}"
    assert config.lean_project is not None  # environment_identity refuses None
    service = LeanService(
        lake=config.lake,
        lean_project=config.lean_project,
        environment=environment,
        limits=config.limits,
    )
    # One declaration index shared between the plain search and the ranking's
    # index source, so the session pays the one-time source scan once.
    declarations = DeclarationIndex(config.lean_project)
    return (
        SearchToolRuntime(
            service,
            build_retriever(service, config.limits, declarations),
            ModuleIndex(config.lean_project),
            declarations,
        ),
        f"Mathlib {environment.mathlib_revision[:12]} in {config.lean_project}",
    )


def renew(runtime: SearchToolRuntime, limits: RunLimits) -> SearchToolRuntime:
    """The same pinned environment, with a retrieval budget that starts again.

    For opening a second problem in one process. `build_runtime` is the
    expensive half of a launch -- it reads and hashes the Lake manifest and
    builds the module index -- and none of that describes a problem, so it is
    carried over. `PremiseRetriever._spent` is the opposite: it accumulates
    for the retriever's whole life, deliberately, because a budget reset per
    call would be no budget at all.

    Carrying the retriever too gave the second problem whatever the first had
    left, or nothing. A budget is frozen per run and every ranking records
    what it spent against it, so a problem whose allowance had already been
    spent elsewhere produced rankings shaped by calls that appear nowhere in
    its own record -- the reproducible-provenance claim `rank_premises` makes
    is exactly what that breaks. `LeanService`, `ModuleIndex` and
    `DeclarationIndex` hold no such counter (config and a runner, and two
    read-only indexes), so they are shared without the same hazard.
    """
    return SearchToolRuntime(
        runtime.service,
        build_retriever(runtime.service, limits, runtime.declarations),
        runtime.modules,
        runtime.declarations,
    )
