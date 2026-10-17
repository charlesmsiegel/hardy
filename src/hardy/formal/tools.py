"""Bounded Lean operations shared by in-process and MCP adapters.

Claim identity is checked before spending a proof attempt. Full observations
are recorded before bounded replies are returned, independently of transport.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from pathlib import PurePosixPath
from typing import Protocol, TypeVar

from hardy.formal.budget import CheckBudget, ReservedBudget
from hardy.formal.contracts import DeclaredAssumption, FrozenClaim
from hardy.formal.declarations import DeclarationIndex, search_result
from hardy.formal.lean import DeclarationInspection, DeclarationSearch, LeanCheckResult
from hardy.formal.retrieval import PremiseRanking, PremiseRetriever
from hardy.foundation.values import FrozenModel
from hardy.workflows.storage import RunStore

#: The largest proof body an official check accepts, in UTF-8 bytes.
MAX_PROOF_BYTES = 64 * 1024
#: How much of one diagnostic, goal or signature a cut observation keeps, in
#: characters, and how many diagnostics or goals it keeps. A ranking is cut by
#: dropping premises instead and never shows a cut signature (`bound_ranking`).
EXCERPT_CHARACTERS = 256
EXCERPT_ITEMS = 4
TOO_SMALL = "model observation budget is smaller than the result envelope"

Observation = TypeVar("Observation", bound=FrozenModel)


class LeanChecks(Protocol):
    """The Lean checks a staged run reaches, as `LeanService` provides them.

    The runtime spends an official check only through `check_proof`; the
    staged agent and the MCP server reach the other two through it as well,
    bounded on the way out. A protocol so a test can pass a double that
    implements the one check it exercises.
    """

    def check_proof(
        self, claim: FrozenClaim, proof_body: str, allowed: Sequence[DeclaredAssumption] = ()
    ) -> LeanCheckResult: ...

    def check_scratch(self, source: str) -> LeanCheckResult: ...

    def inspect_declarations(self, names: tuple[str, ...]) -> DeclarationInspection: ...


def _excerpt(text: str) -> str:
    return text[:EXCERPT_CHARACTERS]


class LeanToolRuntime:
    """One frozen claim's Lean tools, metered and bounded for a model.

    Every answer that does not fit `observation_bytes` is recorded whole under
    `process/` first and returned as a shorter view naming that artifact, so
    what a model is shown is never the only copy of what Lean said.
    """

    def __init__(
        self,
        *,
        claim: FrozenClaim,
        service: LeanChecks,
        store: RunStore,
        official_checks: int,
        observation_bytes: int,
        retriever: PremiseRetriever | None = None,
        declarations: DeclarationIndex | None = None,
        allowed: Sequence[DeclaredAssumption] = (),
        budget: CheckBudget | ReservedBudget | None = None,
    ) -> None:
        self.claim = claim
        #: What this run declared it may stand on. Rendered into every proof
        #: this runtime checks, so the model develops against the environment
        #: its work is finally judged in.
        self.allowed = tuple(allowed)
        self.service = service
        self.store = store
        self._remaining_official_checks = official_checks
        self._budget = budget
        self.observation_bytes = observation_bytes
        # Optional because a machine can be configured without one; the tool
        # then says there is no retrieval rather than ranking an empty list,
        # which a model would read as "no such lemma exists". The declaration
        # index is optional for the same reason and answers the same way.
        self.retriever = retriever
        self.declarations = declarations
        self._artifact_sequence = 0

    def bind_budget(self, budget: CheckBudget | ReservedBudget) -> None:
        """Bind a run owner without resetting this runtime's local check count."""
        if self._budget is not None and self._budget is not budget:
            raise ValueError("Lean runtime already has a shared budget")
        self._budget = budget

    def rank_premises(self, goal: str, limit: int) -> PremiseRanking:
        """Rank premises for `goal`, bounded; a heuristic, never evidence."""
        if self.retriever is None:
            raise ValueError("no premise retrieval is configured for this run")
        return self.bound_ranking(self.retriever.rank(goal, limit))

    def search_declarations(self, query: str, limit: int) -> DeclarationSearch:
        """Search the declaration index for `query`, bounded."""
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

    def check_scratch(self, source: str) -> LeanCheckResult:
        """Elaborate exploratory source under the scratch header, bounded.

        Free: a scratch check proves nothing about the claim, so it spends no
        official check.
        """
        return self.bound_check(self.service.check_scratch(source))

    def inspect_declarations(self, names: Sequence[str]) -> DeclarationInspection:
        """Resolve exact declaration names in the pinned environment, bounded."""
        return self.bound_inspection(self.service.inspect_declarations(tuple(names)))

    def check_proof(self, claim_id: str, proof_body: str) -> LeanCheckResult:
        """Spend one official check on `proof_body` against this run's frozen claim.

        Refused before anything is spent when `claim_id` names another claim,
        the body is over `MAX_PROOF_BYTES`, or the budget is gone.
        """
        if claim_id != self.claim.content_hash:
            raise ValueError("Frozen Claim identifier does not match this run")
        if len(proof_body.encode("utf-8")) > MAX_PROOF_BYTES:
            raise ValueError("proof body exceeds the 64 KiB limit")
        if self.remaining_official_checks <= 0:
            raise ValueError("official proof-check budget exhausted")
        if self._budget is not None:
            self._budget.acquire()
        self._remaining_official_checks -= 1
        return self.bound_check(self.service.check_proof(self.claim, proof_body, self.allowed))

    @property
    def remaining_official_checks(self) -> int:
        """Checks left: this runtime's own allowance, capped by any shared budget."""
        if self._budget is None:
            return self._remaining_official_checks
        return min(self._remaining_official_checks, self._budget.remaining_checks)

    def bound_check(self, result: LeanCheckResult) -> LeanCheckResult:
        """A check result within the budget: the process output goes, then excerpts.

        Lean's raw output is the bulk and lives in the artifact, so the first
        view keeps the leading diagnostics and goals as excerpts; the second
        drops them and keeps only the verdict and where the rest is.
        """

        def shorter(artifact: str) -> Iterator[LeanCheckResult]:
            excerpted = result.model_copy(
                update={
                    "diagnostics": tuple(
                        item.model_copy(update={"message": _excerpt(item.message)})
                        for item in result.diagnostics[:EXCERPT_ITEMS]
                    ),
                    "open_goals": tuple(_excerpt(goal) for goal in result.open_goals[:EXCERPT_ITEMS]),
                    "process": result.process.model_copy(update={"stdout": "", "stderr": ""}),
                    "observation_truncated": True,
                    "output_artifact": artifact,
                }
            )
            yield excerpted
            yield excerpted.model_copy(update={"diagnostics": (), "open_goals": ()})

        return self._bounded(result, "mcp-lean", shorter)

    def bound_inspection(self, result: DeclarationInspection) -> DeclarationInspection:
        """An inspection with its signatures excerpted when it does not fit.

        The one bound that never refuses: the excerpted view is returned even
        when it still exceeds the budget, as it always has been.
        """
        if self._fits(result):
            return result
        artifact = self._record(result, "mcp-result")
        return result.model_copy(
            update={
                "resolved": tuple(
                    item.model_copy(update={"signature": _excerpt(item.signature)})
                    for item in result.resolved
                ),
                "observation_truncated": True,
                "output_artifact": artifact,
            }
        )

    def bound_search(self, result: DeclarationSearch) -> DeclarationSearch:
        """A search with excerpted signatures and leading diagnostics, marked truncated."""

        def shorter(artifact: str) -> Iterator[DeclarationSearch]:
            yield result.model_copy(
                update={
                    "results": tuple(
                        item.model_copy(update={"signature": _excerpt(item.signature)})
                        for item in result.results
                    ),
                    "diagnostics": tuple(
                        item.model_copy(update={"message": _excerpt(item.message)})
                        for item in result.diagnostics[:EXCERPT_ITEMS]
                    ),
                    "truncated": True,
                    "observation_truncated": True,
                    "output_artifact": artifact,
                }
            )

        return self._bounded(result, "mcp-result", shorter)

    def bound_ranking(self, result: PremiseRanking) -> PremiseRanking:
        """Fit a ranking into the observation budget by dropping premises only.

        Never by trimming the provenance. The digest is taken over that record,
        so a record cut to fit would either stop matching its digest or, worse,
        be re-stamped -- leaving a hash over something that never produced a
        ranking. Premises are what a shorter answer legitimately means, and the
        artifact holds the whole thing either way.

        Signatures are *not* shortened, which the earlier code did before
        dropping anything. A cut Lean type can still read as a complete one
        while saying something else, and the model cannot open the artifact
        to find out -- the same reason an over-long declaration name is
        discarded rather than trimmed. Fewer premises, each of them true.
        """

        def shorter(artifact: str) -> Iterator[PremiseRanking]:
            for kept in range(len(result.premises), -1, -1):
                yield result.model_copy(
                    update={
                        "premises": result.premises[:kept],
                        "observation_truncated": True,
                        "output_artifact": artifact,
                    }
                )

        return self._bounded(result, "mcp-result", shorter)

    def _bounded(
        self,
        result: Observation,
        artifact_name: str,
        shorter: Callable[[str], Iterator[Observation]],
    ) -> Observation:
        """`result` if it fits, else the first of its shorter views that does.

        The whole result is recorded before any view is made, so every view
        names an artifact that holds all of it. When even the shortest view is
        over budget the call is refused rather than answered over budget.
        """
        if self._fits(result):
            return result
        for view in shorter(self._record(result, artifact_name)):
            if self._fits(view):
                return view
        raise ValueError(TOO_SMALL)

    def _fits(self, result: FrozenModel) -> bool:
        return len(result.model_dump_json().encode("utf-8")) <= self.observation_bytes

    def _record(self, result: FrozenModel, name: str) -> str:
        path = PurePosixPath(f"process/{name}-{self._artifact_sequence}.json")
        self._artifact_sequence += 1
        return self.store.write_json(path, result).relative_path
