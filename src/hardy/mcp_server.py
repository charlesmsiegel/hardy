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

from .config import load as load_config
from .domain import FrozenClaim, freeze_claim
from .lean import DeclarationInspection, DeclarationSearch, LeanCheckResult, LeanService
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
    ) -> None:
        self.claim = claim
        self.service = service
        self.store = store
        self.remaining_official_checks = official_checks
        self.observation_bytes = observation_bytes
        self._artifact_sequence = 0

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

    def _write_full_result(self, result: Any) -> str:
        path = PurePosixPath(f"process/mcp-result-{self._artifact_sequence}.json")
        self._artifact_sequence += 1
        return self.store.write_json(path, result).relative_path


_runtime: LeanToolRuntime | None = None


def configure_runtime(runtime: LeanToolRuntime) -> None:
    global _runtime
    _runtime = runtime


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
    runtime = LeanToolRuntime(
        claim=claim,
        service=LeanService(
            lake=config.lake,
            lean_project=config.lean_project,
            environment=claim.environment,
            limits=config.limits,
        ),
        store=RunStore(run_dir, UUID(int=0)),
        official_checks=config.limits.official_checks,
        observation_bytes=config.limits.model_observation_bytes,
    )
    configure_runtime(runtime)
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


def main() -> None:
    load_runtime(os.environ)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
