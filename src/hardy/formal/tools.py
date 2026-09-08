"""Bounded Lean operations shared by in-process and MCP adapters.

Claim identity is checked before spending a proof attempt. Full observations
are recorded before bounded replies are returned, independently of transport.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import PurePosixPath
from typing import Any

from ..declarations import DeclarationIndex, search_result
from ..domain import FrozenClaim
from ..lean import DeclarationInspection, DeclarationSearch, LeanCheckResult
from ..retrieval import PremiseRanking, PremiseRetriever
from ..storage import RunStore

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
        declarations: DeclarationIndex | None = None,
        allowed: Sequence[Any] = (),
    ) -> None:
        self.claim = claim
        #: What this run declared it may stand on. Rendered into every proof
        #: this runtime checks, so the model develops against the environment
        #: its work is finally judged in.
        self.allowed = tuple(allowed)
        self.service = service
        self.store = store
        self.remaining_official_checks = official_checks
        self.observation_bytes = observation_bytes
        # Optional because a machine can be configured without one; the tool
        # then says there is no retrieval rather than ranking an empty list,
        # which a model would read as "no such lemma exists". The declaration
        # index is optional for the same reason and answers the same way.
        self.retriever = retriever
        self.declarations = declarations
        self._artifact_sequence = 0

    def rank_premises(self, goal: str, limit: int) -> PremiseRanking:
        if self.retriever is None:
            raise ValueError("no premise retrieval is configured for this run")
        return self.bound_ranking(self.retriever.rank(goal, limit))

    def search_declarations(self, query: str, limit: int) -> DeclarationSearch:
        if self.declarations is None:
            raise ValueError("no declaration index is configured for this run")
        # This runtime serves the staged and MCP surfaces, and both register
        # the inspection tool under its `lean_` name; a miss diagnostic
        # naming the chat spelling would send the model to a tool that does
        # not exist here.
        return self.bound_search(
            search_result(
                self.declarations, query, limit, inspect_tool="lean_inspect_declarations"
            )
        )

    def check_proof(self, claim_id: str, proof_body: str) -> LeanCheckResult:
        if claim_id != self.claim.content_hash:
            raise ValueError("Frozen Claim identifier does not match this run")
        if len(proof_body.encode("utf-8")) > 64 * 1024:
            raise ValueError("proof body exceeds the 64 KiB limit")
        if self.remaining_official_checks <= 0:
            raise ValueError("official proof-check budget exhausted")
        self.remaining_official_checks -= 1
        return self.bound_check(self.service.check_proof(self.claim, proof_body, self.allowed))

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
