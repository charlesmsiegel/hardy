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

import os
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID

from mcp.server.fastmcp import FastMCP

from .cas_export import ExportReport, export_session
from .cas_tools import CasCellResult, CasStateResult, CasToolRuntime, build_runtime
from .config import load as load_config
from .domain import FrozenClaim, freeze_claim
from .lean import DeclarationInspection, DeclarationSearch, LeanCheckResult, LeanService
from .retrieval import PremiseRanking, PremiseRetriever, build_retriever
from .storage import RunStore

mcp = FastMCP("Hardy Lean Tools", json_response=True)


class LeanToolRuntime:
    def __init__(
        self,
        *,
        claim: FrozenClaim,
        service: Any,
        store: RunStore,
        official_checks: int,
        observation_bytes: int,
        retriever: PremiseRetriever | None = None,
    ) -> None:
        self.claim = claim
        self.service = service
        self.store = store
        self.remaining_official_checks = official_checks
        self.observation_bytes = observation_bytes
        # Optional because a machine can be configured without one; the tool
        # then says there is no retrieval rather than ranking an empty list,
        # which a model would read as "no such lemma exists".
        self.retriever = retriever
        self._artifact_sequence = 0

    def rank_premises(self, goal: str, limit: int) -> PremiseRanking:
        if self.retriever is None:
            raise ValueError("no premise retrieval is configured for this run")
        return self.bound_ranking(self.retriever.rank(goal, limit))

    def check_proof(self, claim_id: str, proof_body: str) -> LeanCheckResult:
        if claim_id != self.claim.content_hash:
            raise ValueError("Frozen Claim identifier does not match this run")
        if len(proof_body.encode("utf-8")) > 64 * 1024:
            raise ValueError("proof body exceeds the 64 KiB limit")
        if self.remaining_official_checks <= 0:
            raise ValueError("official proof-check budget exhausted")
        self.remaining_official_checks -= 1
        return self.bound_check(self.service.check_proof(self.claim, proof_body))

    def bound_check(self, result: LeanCheckResult) -> LeanCheckResult:
        if len(result.model_dump_json().encode("utf-8")) <= self.observation_bytes:
            return result
        path = PurePosixPath(f"process/mcp-lean-{self._artifact_sequence}.json")
        self._artifact_sequence += 1
        artifact = self.store.write_json(path, result)
        process = result.process.model_copy(update={"stdout": "", "stderr": ""})
        diagnostics = tuple(
            item.model_copy(update={"message": item.message[:256]})
            for item in result.diagnostics[:4]
        )
        bounded = result.model_copy(
            update={
                "diagnostics": diagnostics,
                "open_goals": tuple(goal[:256] for goal in result.open_goals[:4]),
                "process": process,
                "observation_truncated": True,
                "output_artifact": artifact.relative_path,
            }
        )
        if len(bounded.model_dump_json().encode("utf-8")) > self.observation_bytes:
            bounded = bounded.model_copy(update={"diagnostics": (), "open_goals": ()})
        if len(bounded.model_dump_json().encode("utf-8")) > self.observation_bytes:
            raise ValueError("model observation budget is smaller than the result envelope")
        return bounded

    def bound_inspection(self, result: DeclarationInspection) -> DeclarationInspection:
        if len(result.model_dump_json().encode("utf-8")) <= self.observation_bytes:
            return result
        artifact = self._write_full_result(result)
        resolved = tuple(
            item.model_copy(update={"signature": item.signature[:256]}) for item in result.resolved
        )
        return result.model_copy(
            update={
                "resolved": resolved,
                "observation_truncated": True,
                "output_artifact": artifact,
            }
        )

    def bound_search(self, result: DeclarationSearch) -> DeclarationSearch:
        if len(result.model_dump_json().encode("utf-8")) <= self.observation_bytes:
            return result
        artifact = self._write_full_result(result)
        results = tuple(
            item.model_copy(update={"signature": item.signature[:256]}) for item in result.results
        )
        diagnostics = tuple(
            item.model_copy(update={"message": item.message[:256]})
            for item in result.diagnostics[:4]
        )
        bounded = result.model_copy(
            update={
                "results": results,
                "diagnostics": diagnostics,
                "truncated": True,
                "observation_truncated": True,
                "output_artifact": artifact,
            }
        )
        if len(bounded.model_dump_json().encode("utf-8")) > self.observation_bytes:
            raise ValueError("model observation budget is smaller than the result envelope")
        return bounded

    def bound_ranking(self, result: PremiseRanking) -> PremiseRanking:
        """Fit a ranking into the observation budget by dropping premises only.

        Never by trimming the provenance. The digest is taken over that record,
        so a record cut to fit would either stop matching its digest or, worse,
        be re-stamped -- leaving a hash over something that never produced a
        ranking. Premises are what a shorter answer legitimately means, and the
        artifact holds the whole thing either way.
        """
        if len(result.model_dump_json().encode("utf-8")) <= self.observation_bytes:
            return result
        artifact = self._write_full_result(result)
        # Signatures are *not* shortened, which the earlier code did before
        # dropping anything. A cut Lean type can still read as a complete one
        # while saying something else, and the model cannot open the artifact
        # to find out -- the same reason an over-long declaration name is
        # discarded rather than trimmed. Fewer premises, each of them true.
        premises = list(result.premises)
        while True:
            bounded = result.model_copy(
                update={
                    "premises": tuple(premises),
                    "observation_truncated": True,
                    "output_artifact": artifact,
                }
            )
            if len(bounded.model_dump_json().encode("utf-8")) <= self.observation_bytes:
                return bounded
            if not premises:
                raise ValueError("model observation budget is smaller than the result envelope")
            premises.pop()

    def _write_full_result(self, result: Any) -> str:
        path = PurePosixPath(f"process/mcp-result-{self._artifact_sequence}.json")
        self._artifact_sequence += 1
        return self.store.write_json(path, result).relative_path


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


def load_runtime(environ: Mapping[str, str]) -> LeanToolRuntime:
    required = ("HARDY_RUN_DIR", "HARDY_CONFIG", "HARDY_CLAIM_SHA256")
    missing = [name for name in required if not environ.get(name)]
    if missing:
        raise ValueError("missing MCP environment settings: " + ", ".join(missing))
    run_dir = Path(environ["HARDY_RUN_DIR"])
    config = load_config(Path(environ["HARDY_CONFIG"]))
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
    runtime = LeanToolRuntime(
        claim=claim,
        service=service,
        store=RunStore(run_dir, UUID(int=0)),
        official_checks=config.limits.official_checks,
        observation_bytes=config.limits.model_observation_bytes,
        retriever=build_retriever(service, config.limits),
    )
    configure_runtime(runtime)

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
    return runtime


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
    """Search the pinned Lean environment for declarations."""
    runtime = _configured()
    return runtime.bound_search(runtime.service.search_declarations(query, limit))


@mcp.tool()
def rank_premises(goal: str, limit: int = 10) -> PremiseRanking:
    """Rank the declarations most likely to help with one goal.

    Fuses Lean's own search with Loogle. The answer carries the provenance of
    every source that was asked, and says whether the ranking can be replayed:
    Loogle tracks a Mathlib it does not name, so a ranking it shaped cannot.
    """
    return _configured().rank_premises(goal, limit)


def main() -> None:
    load_runtime(os.environ)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
