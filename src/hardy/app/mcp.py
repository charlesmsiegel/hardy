"""Local stdio MCP server for Hardy's bounded Lean tools.

The same Lean service the workflow uses, served over stdio so a client that
cannot host in-process tools — the Codex SDK, an editor, another agent — still
goes through Hardy's checks rather than around them.

Two bounds are enforced here and nowhere else. The official proof-check budget
is spent per run, so a client cannot buy extra attempts by asking again. And
every result is measured before it is returned: anything larger than the
model's observation budget is written to the run store whole and answered with
a bounded summary that names the artifact holding the rest.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from uuid import UUID

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from hardy.algebra.export import ExportReport, export_session
from hardy.algebra.tools import CasCellResult, CasStateResult, CasToolRuntime, build_runtime
from hardy.app.config import Config
from hardy.app.config import load as load_config
from hardy.formal.contracts import FrozenClaim, freeze_claim
from hardy.formal.declarations import DeclarationIndex
from hardy.formal.lean import DeclarationInspection, DeclarationSearch, LeanCheckResult, LeanService
from hardy.formal.retrieval import PremiseRanking, build_retriever
from hardy.formal.tools import LeanToolRuntime
from hardy.workflows.storage import RunStore

mcp = FastMCP("Hardy Lean Tools", json_response=True)


_runtime: LeanToolRuntime | None = None
_cas: CasToolRuntime | None = None
_cas_directory: Path | None = None


def configure_runtime(runtime: LeanToolRuntime) -> None:
    global _runtime
    _runtime = runtime


def _configured_cas() -> CasToolRuntime:
    if _cas is None:
        raise RuntimeError("no computer algebra backend is configured")
    return _cas


def register_cas_tools(runtime: CasToolRuntime, directory: Path) -> None:
    """Advertise the CAS tools only once a backend has actually answered.

    Registration cannot be a module-level decorator here. Those run at import,
    long before `load_runtime` has discovered whether a kernel exists, and a
    tool that can only fail is worse than an absent one — a client spends a
    call learning what Hardy already knew.
    """
    global _cas, _cas_directory
    _cas, _cas_directory = runtime, directory

    @mcp.tool()
    def cas_run(source: str) -> CasCellResult:
        """Execute one cell in the persistent computer algebra session.

        State carries over between cells. A trailing expression's value is
        reported and bound to `_`. Cells are executed without any sandbox.
        """
        return _configured_cas().run(source)

    @mcp.tool()
    def cas_state() -> CasStateResult:
        """List the accepted cells that built the current session state."""
        return _configured_cas().state()

    @mcp.tool()
    def cas_reset() -> CasStateResult:
        """Discard the session state and start a clean kernel."""
        return _configured_cas().reset()

    @mcp.tool()
    def cas_export() -> ExportReport:
        """Export the session, replaying it in a fresh kernel to check it reproduces."""
        assert _cas_directory is not None
        return export_session(_configured_cas().session, _cas_directory)


LEAN_TOOL_NAMES = (
    "lean_check_proof",
    "lean_check_scratch",
    "lean_inspect_declarations",
    "lean_search_declarations",
    "rank_premises",
)


def _withdraw_lean_tools() -> None:
    """Stop advertising the Lean tools on a server that has no claim to scope them to.

    They are registered at import, before anyone knows whether a Frozen Claim
    exists, and a server started for the formalization stage has none: every
    one of them could only fail. The same rule `register_cas_tools` states --
    a tool that can only fail is worse than an absent one.
    """
    for name in LEAN_TOOL_NAMES:
        # Already withdrawn is not an error: a process that loads twice without
        # a claim -- a test does -- has nothing further to take down.
        with contextlib.suppress(ToolError):
            mcp.remove_tool(name)


def load_runtime(environ: Mapping[str, str]) -> LeanToolRuntime | None:
    """Configure the server from its environment.

    `HARDY_CLAIM_SHA256` is what says a Frozen Claim exists. Without it the
    server serves the computer algebra session and nothing else, which is
    what the formalization stage needs and all it can honestly offer; the
    Lean tools are withdrawn rather than left to fail, and `None` says no
    Lean runtime was built.
    """
    required = ("HARDY_RUN_DIR", "HARDY_CONFIG")
    missing = [name for name in required if not environ.get(name)]
    if missing:
        raise ValueError("missing MCP environment settings: " + ", ".join(missing))
    run_dir = Path(environ["HARDY_RUN_DIR"])
    config = load_config(Path(environ["HARDY_CONFIG"]))
    if not environ.get("HARDY_CLAIM_SHA256"):
        _withdraw_lean_tools()
        _serve_cas(config, run_dir)
        return None
    if config.lean_project is None:
        raise ValueError("Hardy configuration has no registered Lean project")
    claim = FrozenClaim.model_validate_json(
        (run_dir / "formalization.json").read_text(encoding="utf-8")
    )
    # The claim on disk must hash to itself and to the hash this server was
    # started for, so a tool call cannot be answered against another run.
    expected = freeze_claim(
        claim.original_text,
        claim.proposal,
        claim.environment,
        claim.approved_at,
    )
    if (
        claim.content_hash != expected.content_hash
        or claim.content_hash != environ["HARDY_CLAIM_SHA256"]
        or claim.imports != claim.environment.imports
    ):
        raise ValueError("Frozen Claim hash or imports do not match")
    service = LeanService(
        lake=config.lake,
        lean_project=config.lean_project,
        environment=claim.environment,
        limits=config.limits,
    )
    # One declaration index shared between the plain search tool and the
    # ranking's index source, so the run pays the one-time source scan once.
    declarations = DeclarationIndex(config.lean_project)
    runtime = LeanToolRuntime(
        claim=claim,
        service=service,
        store=RunStore(run_dir, UUID(int=0)),
        official_checks=config.limits.official_checks,
        observation_bytes=config.limits.model_observation_bytes,
        retriever=build_retriever(service, config.limits, declarations),
        declarations=declarations,
    )
    configure_runtime(runtime)
    _serve_cas(config, run_dir)
    return runtime


def _serve_cas(config: Config, run_dir: Path) -> None:
    # Discovery before advertisement: an absent or broken backend leaves this
    # server with Lean tools only, which is the honest description of it.
    store = RunStore(run_dir, UUID(int=0))
    cas_directory = run_dir / "cas"
    cas_runtime, _ = build_runtime(
        backend_name=config.cas_backend,
        command=config.cas_command,
        limits=config.limits,
        log_path=cas_directory / "cells.jsonl",
        cwd=cas_directory,
        spill=lambda name, text: store.write_text(
            PurePosixPath(f"process/{name}"), text
        ).relative_path,
    )
    if cas_runtime is not None:
        register_cas_tools(cas_runtime, cas_directory)


def _configured() -> LeanToolRuntime:
    if _runtime is None:
        raise RuntimeError("Hardy Lean tool runtime is not configured")
    return _runtime


@mcp.tool()
def lean_check_proof(claim_id: str, proof_body: str) -> LeanCheckResult:
    """Check one proof body against the exact Frozen Claim."""
    return _configured().check_proof(claim_id, proof_body)


@mcp.tool()
def lean_check_scratch(source: str) -> LeanCheckResult:
    """Check bounded exploratory source under Hardy's fixed imports."""
    runtime = _configured()
    return runtime.bound_check(runtime.service.check_scratch(source))


@mcp.tool()
def lean_inspect_declarations(names: list[str]) -> DeclarationInspection:
    """Resolve a bounded list of exact Lean declaration names."""
    runtime = _configured()
    return runtime.bound_inspection(runtime.service.inspect_declarations(tuple(names)))


@mcp.tool()
def lean_search_declarations(query: str, limit: int = 10) -> DeclarationSearch:
    """Search declaration names read from the pinned package sources.

    No Lean process runs: the names come from the sources on disk, so this
    answers instantly and offline. A hit is a lead to confirm with
    lean_inspect_declarations; a miss is about the index, not Mathlib.
    """
    return _configured().search_declarations(query, limit)


@mcp.tool()
def rank_premises(goal: str, limit: int = 10) -> PremiseRanking:
    """Rank the declarations most likely to help with one goal.

    Fuses the declaration-name index over the pinned sources with Loogle. The
    answer carries the provenance of every source that was asked, and says
    whether the ranking can be replayed: Loogle tracks a Mathlib it does not
    name, so a ranking it shaped cannot.
    """
    return _configured().rank_premises(goal, limit)


def main() -> None:
    load_runtime(os.environ)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
