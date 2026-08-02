"""Premise retrieval: which declarations are worth looking at for this goal.

Mathlib does not fit in a context window, and the usual reason a proof attempt
fails is that the model never learned the relevant lemma exists. So Hardy asks
several searches the same question and fuses their answers into one order.

The whole module is built around one rule: **a ranking is a value that carries
what produced it.** A list of lemma names is easy to produce and impossible to
audit, so nothing here returns premises without a `RetrievalProvenance` naming
every source that was asked, what it searched, whether it answered, and what it
spent. The digest over that record is derived rather than declared -- the same
discipline `VerificationEvidence` follows -- so a reader holding the ranking can
rebuild the record and recompute the number.

Two consequences worth stating plainly, because they are the honest part:

- **Pinning.** Lean's own search runs against the environment the run is frozen
  under, so it is reproducible. Loogle is a live service that tracks whatever
  Mathlib it tracks today, and it reports no revision, so a ranking it shaped
  cannot be replayed. `SourceIdentity.pinned` records which is which and
  `PremiseRanking.reproducible` is the conjunction over the sources that
  actually answered. Retrieval is a heuristic and this is not a defect; a
  ranking that implied otherwise would be.
- **Absence.** The embedding identities the design asks provenance to record --
  model, tokenizer, pooling, corpus, index -- live in `IndexIdentity` and are
  required of an embedding source. There is no embedding index yet, so no
  source carries one. Absent is a different claim from omitted, and a
  `lean_search` identity carrying those fields would be describing an index
  that does not exist.

Retrieval time is metered like the official proof checks: the budget refuses
the call rather than interrupting it. A source declares its worst case, and one
that could outlast what is left of the run's budget is never started -- so the
budget cannot be overspent by the query that happens to be last. Wall-clock
seconds, not CPU: Loogle's CPU burns on someone else's machine, and what Hardy
can actually enforce is how long it is willing to wait.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from typing import Literal, Protocol
from urllib.parse import urlencode

from pydantic import model_validator

from .domain import FrozenModel, RunLimits
from .lean import DECLARATION_NAME, DeclarationRecord
from .process import MAX_TEARDOWN_SECONDS

# Loogle's public instance. The endpoint is configurable because a project that
# cares about reproducibility will want to run its own against a pinned Mathlib.
DEFAULT_LOOGLE_ENDPOINT = "https://loogle.lean-lang.org/json"
# Measured rather than guessed: a pattern query against the public instance
# took 19s where a name lookup took under one. Ten seconds -- the first value
# here -- turned an ordinary search into a source that never answered.
DEFAULT_LOOGLE_TIMEOUT = 30.0

# The response is data off the internet: bounded before it is parsed, and every
# field bounded again after. Neither number needs to be generous -- a premise
# ranking reads names and types, and nothing here executes any of it.
MAX_RESPONSE_BYTES = 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024
MAX_SIGNATURE_CHARACTERS = 512
MAX_HITS = 200

# The query is one bounded line, matching what `LeanService.search_declarations`
# will accept: a goal that Lean would refuse is refused here, before it costs
# any source a call.
MAX_GOAL_CHARACTERS = 512

# Reciprocal rank fusion. The sources return ordered names and no comparable
# scores -- `#find` reports matches, Loogle reports hits -- so rank is the only
# signal they share, and 60 is the constant the method is usually stated with.
RRF_K = 60
RANKER = "reciprocal-rank-fusion/1"

# How many candidates each source is asked for, per premise the answer holds.
# Fusion needs to see past the cutoff to find agreement there; see `rank`.
CANDIDATE_DEPTH = 3
# And never fewer than this, because the multiplier alone cannot fix a short
# answer. A premise two sources both rank at `r` scores 2/(RRF_K + r), which
# beats the best a single source can offer -- 1/(RRF_K + 1) -- for every r up
# to RRF_K. So looking RRF_K + 1 deep is exactly sufficient for two sources,
# and at r = RRF_K + 2 the shared premise genuinely has lost.
FUSION_DEPTH_FLOOR = RRF_K + 1

# HTTP statuses that mean the service is unwell rather than that Hardy asked it
# the wrong thing. Everything else in 4xx says the request was refused on its
# merits, which is the endpoint's contract having moved.
TRANSIENT_STATUSES = frozenset({408, 429})

SourceKind = Literal["lean_search", "loogle", "embedding"]


class RetrievalError(RuntimeError):
    """A source could not answer. Recorded against that source, never hidden."""


class RetrievalTransportError(RetrievalError):
    """The service never answered: unreachable, refused, or too slow.

    Split from its parent because the two failures mean opposite things to a
    reader. A service being down says nothing about anyone's code; a response
    Hardy cannot read says the contract moved. `rank` records both as one thing
    -- a source that did not answer -- but the live contract test skips on this
    one and must fail on the other, which is the whole reason it exists.
    """


class IndexIdentity(FrozenModel):
    """What produced an embedding ranking, pinned so the ranking can be replayed.

    A ranking is only reproducible if the thing that produced it is pinned, and
    for an embedding index that is five separate things: the same weights, the
    same tokenizer, the same pooling over them, the same corpus embedded, and
    the same index built from it. Change any one and the order changes.
    """

    model: str
    tokenizer: str
    pooling: str
    corpus_sha256: str
    index_sha256: str
    index_version: str


class SourceIdentity(FrozenModel):
    """What a source is, in enough detail to say whether it can be replayed."""

    name: str
    kind: SourceKind
    # What was searched: a pinned environment for Lean's own search, the service
    # endpoint for Loogle.
    corpus: str
    pinned: bool
    index: IndexIdentity | None = None

    @model_validator(mode="after")
    def index_belongs_to_an_embedding_source(self) -> SourceIdentity:
        if self.kind == "embedding" and self.index is None:
            raise ValueError("an embedding source must name the index that produced its ranking")
        if self.kind != "embedding" and self.index is not None:
            raise ValueError(f"a {self.kind} source has no embedding index to name")
        return self


class SourceOutcome(FrozenModel):
    """One source's part in one ranking, including having had no part in it."""

    identity: SourceIdentity
    answered: bool
    returned: int = 0
    seconds: float = 0.0
    detail: str | None = None

    @model_validator(mode="after")
    def a_source_that_did_not_answer_says_why(self) -> SourceOutcome:
        if not self.answered and not self.detail:
            raise ValueError("a source that did not answer must say why")
        return self


class SourceRank(FrozenModel):
    source: str
    rank: int


class RankedPremise(FrozenModel):
    """One declaration, with the ranks it was fused from.

    The per-source ranks travel with the score because the score alone cannot
    be argued with. "Second on both searches" is a reason; 0.032 is not.
    """

    name: str
    signature: str
    score: float
    ranks: tuple[SourceRank, ...]


class RetrievalProvenance(FrozenModel):
    """Everything that produced one ranking, as a record its digest is taken over."""

    goal_sha256: str
    ranker: str
    sources: tuple[SourceOutcome, ...]

    @property
    def digest(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()


class PremiseRanking(FrozenModel):
    goal: str
    premises: tuple[RankedPremise, ...]
    provenance: RetrievalProvenance
    provenance_sha256: str
    # Every source that was configured actually answered.
    complete: bool
    # Every source that answered is pinned, so this order can be replayed.
    reproducible: bool
    seconds_spent: float
    # What is left of the run's retrieval budget after this ranking.
    run_seconds_remaining: float
    budget_exhausted: bool
    observation_truncated: bool = False
    output_artifact: str | None = None

    @staticmethod
    def _complete(provenance: RetrievalProvenance) -> bool:
        return all(source.answered for source in provenance.sources)

    @staticmethod
    def _reproducible(provenance: RetrievalProvenance) -> bool:
        """Over the sources that answered rather than the ones that changed the
        order: an unpinned search returning nothing still shaped the result, by
        having nothing to add. Calling that replayable would be reproducibility
        by luck.
        """
        answered = [source for source in provenance.sources if source.answered]
        return bool(answered) and all(source.identity.pinned for source in answered)

    @model_validator(mode="after")
    def claims_match_the_record_they_are_taken_over(self) -> PremiseRanking:
        """Stored rather than computed on read, and rechecked on every read.

        These two were properties, which is why they never reached a model: a
        tool answers over `model_dump_json()`, and a Python property is not
        part of it. Fields carry them across the wire; recomputing them here
        keeps them derived, so a ranking cannot claim to be replayable any more
        than it can claim a digest it does not have.
        """
        if self.provenance_sha256 != self.provenance.digest:
            raise ValueError("provenance_sha256 does not match the provenance it names")
        # The provenance hashes the goal, so the goal beside it must be that
        # goal. Unchecked, a ranking read back with a swapped `goal` passed
        # every integrity check here while offering its premises as answers to
        # a question nobody asked -- the one substitution all of this exists to
        # make impossible.
        if hashlib.sha256(self.goal.encode("utf-8")).hexdigest() != self.provenance.goal_sha256:
            raise ValueError("goal does not hash to the goal_sha256 its provenance records")
        if self.complete != self._complete(self.provenance):
            raise ValueError("`complete` disagrees with the sources the provenance names")
        if self.reproducible != self._reproducible(self.provenance):
            raise ValueError("`reproducible` disagrees with the sources the provenance names")
        return self


class PremiseSource(Protocol):
    """A search Hardy can ask about a goal.

    `worst_case_seconds` is what the meter reasons about: a source that cannot
    bound itself cannot be scheduled against a budget.
    """

    @property
    def identity(self) -> SourceIdentity: ...

    @property
    def worst_case_seconds(self) -> float: ...

    def search(self, goal: str, limit: int) -> tuple[DeclarationRecord, ...]: ...


class LeanSearchSource:
    """Lean's own `#find`, run in the environment the run is frozen under."""

    def __init__(self, service: object, *, limits: RunLimits) -> None:
        self._service = service
        self._limits = limits

    @property
    def identity(self) -> SourceIdentity:
        environment = self._service.environment
        return SourceIdentity(
            name="lean-find",
            kind="lean_search",
            # `lean_commit` and not only `lean_version`: two builds can display
            # one version and be different Leans, and `#find` runs in whichever
            # of them this is. `EnvironmentIdentity` carries the commit for
            # exactly that reason, so a corpus identity that dropped it would
            # give two toolchains the same provenance digest.
            corpus=(
                f"Mathlib {environment.mathlib_revision} / "
                f"Lean {environment.lean_version} ({environment.lean_commit}) / "
                f"manifest {environment.lake_manifest_sha256}"
            ),
            pinned=True,
        )

    @property
    def worst_case_seconds(self) -> float:
        """The Lean deadline plus what it costs to stop a child that reached it.

        `run_process` bounds the search, and then bounds the *teardown*
        separately: a `wait` on the terminated child and a `join` on each output
        reader. Declaring only the deadline was the same defect
        `LoogleSource.worst_case_seconds` documents in the other direction -- a
        search admitted with exactly its deadline left could overrun the run's
        budget by the teardown, having passed the check meant to stop it.
        """
        return float(self._limits.lean_process_seconds) + MAX_TEARDOWN_SECONDS

    def search(self, goal: str, limit: int) -> tuple[DeclarationRecord, ...]:
        # `search_declarations` takes 1..20; the retriever's limit is the
        # ranking's length, which a caller may set higher than one source will
        # serve.
        found = self._service.search_declarations(goal, max(1, min(limit, 20)))
        if not found.success:
            # An empty list from a search that failed reads as "no such lemma",
            # which is the one thing it does not mean.
            raise RetrievalError(
                "Lean search timed out" if found.timed_out else "Lean search failed"
            )
        return tuple(found.results)


class LoogleSource:
    """Loogle, over its JSON API.

    Unpinned on purpose: the public instance follows Mathlib master and reports
    no revision, so nothing here can say which corpus answered.
    """

    def __init__(
        self,
        endpoint: str = DEFAULT_LOOGLE_ENDPOINT,
        *,
        timeout: float = DEFAULT_LOOGLE_TIMEOUT,
        fetch: Callable[[str, float], bytes] | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._timeout = timeout
        self._fetch = fetch or _fetch_url

    @property
    def identity(self) -> SourceIdentity:
        return SourceIdentity(
            name="loogle", kind="loogle", corpus=self._endpoint, pinned=False
        )

    @property
    def worst_case_seconds(self) -> float:
        """Twice the deadline, because that is what can actually elapse.

        `_fetch_url` bounds the transfer by a monotonic deadline, but the read
        it is sitting in when the deadline passes is bounded only by the socket
        timeout -- so a request admitted at deadline-minus-epsilon can still
        block for one more socket operation, and `timeout` seconds is a bound
        the code cannot keep.

        Reporting the intended number rather than the true one would be the
        same defect the metering exists to prevent: the admission check spends
        this figure, so a figure that flatters the source lets one call overrun
        the run's budget after being let through. Better to admit against what
        can happen and let the source look expensive, because it is.

        Tightening it instead would need one of two things Hardy does not have.
        Re-arming the socket timeout per read reaches through `response.fp.raw`
        to a private socket, and `http.client.HTTPResponse` offers no public
        equivalent. A short per-read timeout is worse than useless here: the
        public Loogle spends ~19s computing before it sends a first byte, so a
        timeout small enough to tighten this bound would fail every ordinary
        pattern query outright.
        """
        return 2 * self._timeout

    def search(self, goal: str, limit: int) -> tuple[DeclarationRecord, ...]:
        url = f"{self._endpoint}?{urlencode({'q': goal})}"
        try:
            body = self._fetch(url, self._timeout)
        except RetrievalError:
            raise
        except Exception as error:  # noqa: BLE001 - any transport failure is one outcome
            raise RetrievalTransportError(f"Loogle request failed: {error}") from error
        if len(body) > MAX_RESPONSE_BYTES:
            raise RetrievalError(f"Loogle response too large: over {MAX_RESPONSE_BYTES} bytes")
        try:
            payload = json.loads(body.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as error:
            raise RetrievalError(f"Loogle response was not JSON: {error}") from error
        if not isinstance(payload, dict):
            raise RetrievalError("Loogle response was not a JSON object")
        if payload.get("error"):
            raise RetrievalError(f"Loogle: {str(payload['error'])[:MAX_SIGNATURE_CHARACTERS]}")
        hits = payload.get("hits")
        # A response with no usable hit list is the contract having changed or
        # broken. Reading it as an empty result would file a protocol failure
        # under "found nothing", leaving the ranking `complete` and a source
        # that never answered looking like one that answered emptily. An
        # actually empty `hits` is still an answer, and passes here.
        if not isinstance(hits, list):
            raise RetrievalError("Loogle response carried no `hits` list")
        return _records_from_hits(hits, limit)


def _fetch_url(url: str, timeout: float) -> bytes:
    """Read the whole response under one deadline, not one timeout per read.

    `urlopen(timeout=...)` bounds each blocking socket operation, which is not
    the same promise: a server dripping a byte at a time resets it on every
    read and keeps `read()` alive indefinitely. That would make this source's
    declared `worst_case_seconds` a fiction, and the admission check that
    protects the run's retrieval budget is built entirely on that number.

    So the body is read in chunks against a monotonic deadline. The residual is
    one socket operation: a transfer can overshoot by however long the last
    `read` blocks, which the socket timeout bounds in turn.
    """
    deadline = time.monotonic() + timeout
    request = urllib.request.Request(url, headers={"User-Agent": "Hardy/0.1"})
    try:
        opened = urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 - fixed https endpoint
    except urllib.error.HTTPError as error:
        # A refused request and an unwell service are different findings. 5xx,
        # 408 and 429 say the service is having a bad day; the rest of 4xx says
        # this request was wrong, which is the endpoint's contract having moved
        # -- and that is precisely what the live test must fail on rather than
        # skip past.
        if error.code >= 500 or error.code in TRANSIENT_STATUSES:
            raise RetrievalTransportError(
                f"Loogle is unavailable: HTTP {error.code} {error.reason}"
            ) from error
        raise RetrievalError(
            f"Loogle rejected the request with HTTP {error.code} {error.reason}"
        ) from error
    with opened as response:
        chunks: list[bytes] = []
        received = 0
        # One byte past the limit, so an oversized body is detected rather than
        # silently truncated into something that parses as a shorter answer.
        wanted = MAX_RESPONSE_BYTES + 1
        while received < wanted:
            if time.monotonic() >= deadline:
                raise RetrievalTransportError(
                    f"Loogle exceeded its {timeout:g}s deadline with {received} bytes read"
                )
            chunk = response.read(min(READ_CHUNK_BYTES, wanted - received))
            if not chunk:
                break
            chunks.append(chunk)
            received += len(chunk)
    return b"".join(chunks)


def _records_from_hits(hits: list, limit: int) -> tuple[DeclarationRecord, ...]:
    """Read hits into declaration records, discarding what does not look like one.

    A hit whose name is not a Lean identifier is not a lemma Hardy can go on to
    `#check`, so it is dropped rather than passed along to a model as if it
    were. This is a filter, not a sanitiser: nothing downstream executes any of
    it, and the names that survive are still checked against the real
    environment before they mean anything.
    """
    records = []
    for hit in hits[:MAX_HITS]:
        if not isinstance(hit, dict):
            continue
        name = str(hit.get("name", ""))
        if not DECLARATION_NAME.fullmatch(name):
            continue
        # Loogle's `type` is the declaration's binders and proposition without
        # its name -- " (n m : ℕ) : n + m = m + n" -- so the two are joined
        # rather than punctuated. Rendering it as `name : type` would produce a
        # second colon and a signature that reads like nothing Lean prints.
        rendered = str(hit.get("type", "")).replace("\n", " ").strip()
        signature = f"{name} {rendered}" if rendered else name
        module = hit.get("module")
        records.append(
            DeclarationRecord(
                name=name,
                signature=signature[:MAX_SIGNATURE_CHARACTERS],
                source_file=str(module) if module else None,
            )
        )
        if len(records) >= limit:
            break
    return tuple(records)


class PremiseRetriever:
    """Asks every configured source about a goal and fuses one order from them."""

    def __init__(
        self,
        *,
        sources: Sequence[PremiseSource],
        limits: RunLimits,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sources = tuple(sources)
        self._limits = limits
        self._clock = clock
        # Across the retriever's whole life, not one call. A retriever is built
        # per proving stage and `rank` is called as often as the model likes, so
        # a budget reset per call would be no budget at all -- a loop could
        # spend an arbitrary multiple of what the run was frozen under.
        self._spent = 0.0

    @property
    def seconds_remaining(self) -> float:
        return max(0.0, self._limits.retrieval_seconds - self._spent)

    def rank(self, goal: str, limit: int = 10) -> PremiseRanking:
        if not 1 <= len(goal) <= MAX_GOAL_CHARACTERS or "\n" in goal or "\r" in goal:
            raise ValueError("a retrieval goal must be one line of at most 512 characters")
        if not 1 <= limit <= 50:
            raise ValueError("a premise ranking holds between 1 and 50 premises")

        # Deeper than the answer is long, and never shallower than the floor.
        # A premise both sources rank past the cutoff outscores one that only a
        # single source found, so truncating each source at `limit` before
        # fusing would throw away exactly the agreement the fusion exists to
        # find -- and for a short answer the multiplier is no better, which is
        # what `FUSION_DEPTH_FLOOR` is derived to fix. Costless in requests:
        # each source is asked once either way and only the parsing goes
        # deeper. `LeanSearchSource` still clamps to the 20 that
        # `search_declarations` accepts, so its half of the fusion stays
        # partial by that limit rather than by this one.
        depth = min(max(limit * CANDIDATE_DEPTH, FUSION_DEPTH_FLOOR), MAX_HITS)
        started = self._clock()
        spent = 0.0
        exhausted = False
        outcomes: list[SourceOutcome] = []
        # name -> (rank per source, best record seen)
        found: dict[str, list[SourceRank]] = {}
        records: dict[str, DeclarationRecord] = {}
        pinned_signature: set[str] = set()

        for source in self._sources:
            identity = source.identity
            remaining = self._limits.retrieval_seconds - self._spent - spent
            if remaining < source.worst_case_seconds:
                # Refused rather than started and cut short: a source
                # interrupted halfway has spent the budget and answered nothing.
                exhausted = True
                outcomes.append(
                    SourceOutcome(
                        identity=identity,
                        answered=False,
                        detail=(
                            f"retrieval budget exhausted: {remaining:.1f}s left of "
                            f"{self._limits.retrieval_seconds}s, and this source may take "
                            f"{source.worst_case_seconds:.1f}s"
                        ),
                    )
                )
                continue

            detail: str | None = None
            results: tuple[DeclarationRecord, ...] = ()
            try:
                results = source.search(goal, depth)
            except Exception as error:  # noqa: BLE001 - one source failing is an outcome, not the end
                # Every way a source can fail, not the two that were foreseen.
                # `lake` losing its execute bit raises `PermissionError`, and
                # letting that escape would discard the other sources' results
                # along with the provenance that exists to report the failure --
                # turning one broken source into no ranking at all.
                # A `RetrievalError` already reads as a sentence; anything else
                # is named, because "not executable" alone says nothing.
                detail = (
                    str(error)
                    if isinstance(error, RetrievalError)
                    else f"{type(error).__name__}: {error}"
                )
            seconds = max(0.0, self._clock() - started - spent)
            spent += seconds
            outcomes.append(
                SourceOutcome(
                    identity=identity,
                    answered=detail is None,
                    returned=len(results),
                    seconds=seconds,
                    detail=detail,
                )
            )
            if detail is not None:
                continue
            seen: set[str] = set()
            for position, record in enumerate(results, start=1):
                # A source that lists a name twice must not vote twice: two
                # entries from one search would inflate the score and leave
                # `ranks` reading as though two searches had agreed.
                if record.name in seen:
                    continue
                seen.add(record.name)
                found.setdefault(record.name, []).append(
                    SourceRank(source=identity.name, rank=position)
                )
                # The signature a model reads should be the one the environment
                # it will elaborate against actually holds, so a pinned source's
                # rendering wins over a remote service's.
                if record.name not in records or (
                    identity.pinned and record.name not in pinned_signature
                ):
                    records[record.name] = record
                if identity.pinned:
                    pinned_signature.add(record.name)

        premises = [
            RankedPremise(
                name=name,
                signature=records[name].signature,
                score=sum(1.0 / (RRF_K + item.rank) for item in ranks),
                ranks=tuple(ranks),
            )
            for name, ranks in found.items()
        ]
        # Name breaks the tie, so the same answers always fuse to the same order.
        premises.sort(key=lambda premise: (-premise.score, premise.name))

        self._spent += spent
        provenance = RetrievalProvenance(
            goal_sha256=hashlib.sha256(goal.encode("utf-8")).hexdigest(),
            ranker=RANKER,
            sources=tuple(outcomes),
        )
        return PremiseRanking(
            goal=goal,
            premises=tuple(premises[:limit]),
            provenance=provenance,
            provenance_sha256=provenance.digest,
            complete=PremiseRanking._complete(provenance),
            reproducible=PremiseRanking._reproducible(provenance),
            seconds_spent=spent,
            run_seconds_remaining=self.seconds_remaining,
            budget_exhausted=exhausted,
        )


def build_retriever(service: object, limits: RunLimits) -> PremiseRetriever:
    """The default source set, in the order the budget should spend on them.

    The pinned environment goes first, which decides two things when the budget
    is tight: its rendering of a signature is the one a model reads, and the
    source dropped for want of time is the unpinned one. A ranking that lost
    Lean's own search and kept Loogle would be the worse half of the feature.
    """
    return PremiseRetriever(
        sources=(LeanSearchSource(service, limits=limits), LoogleSource()),
        limits=limits,
    )
