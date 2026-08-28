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

- **Pinning.** The declaration-name index reads the package sources the run is
  frozen under, so it is reproducible. Loogle is a live service that tracks
  whatever Mathlib it tracks today, and it reports no revision, so a ranking it
  shaped cannot be replayed. `SourceIdentity.pinned` records which is which and
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

There used to be another source here: `LeanSearchSource`, running Lean's own
`#find` in the frozen environment. It is gone because it was measured never to
answer -- on the pinned toolchain `#find` still ran at 300 seconds while
`exact?` finished in 22, so every ranking spent a full process timeout to
learn nothing. The measurement and its reading are recorded in
`declarations.py`, whose index is the replacement.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlencode

from pydantic import model_validator

from .declarations import DeclarationIndex
from .domain import EnvironmentIdentity, FrozenModel, RunLimits
from .lean import DECLARATION_NAME, DeclarationRecord

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
# Lean declaration names are short. A longer one is not a name Hardy could go
# on to `#check`, and it is *discarded* rather than truncated -- a cut name is
# a different name, and would crowd genuine premises out of the observation
# budget on its way to meaning nothing.
MAX_NAME_CHARACTERS = 256
MAX_HITS = 200

# The query is one bounded line, matching what the search tools accept: a
# query they would refuse is refused here, before it costs any source a call.
# The goal it is derived from may be longer, because Lean prints a goal with
# its hypotheses and that is the form a model has to hand.
MAX_QUERY_CHARACTERS = 512
MAX_GOAL_CHARACTERS = 4_096
# What Lean puts before a goal's conclusion, and what Loogle accepts at the
# head of a conclusion filter. The declaration index reads only the constant
# names out of a query, so it never sees it either way.
TURNSTILE = "⊢"

# What a self-hosted Loogle's declared corpus has to look like. A git object
# name is immutable because it *is* the content; `master`, a branch or a tag
# can all be repointed under the identity that named them -- the same way an
# elan `stable` alias repoints while the file naming it stays byte-identical.
# Accepting any nonempty string would let a corpus identity move under the
# ranking that recorded it.
IMMUTABLE_REVISION = re.compile(r"\A[0-9a-f]{7,40}\Z")

# Reciprocal rank fusion. The sources return ordered names and no comparable
# scores -- the index reports name matches, Loogle reports hits -- so rank is
# the only signal they share, and 60 is the constant the method is usually
# stated with.
RRF_K = 60
RANKER = "reciprocal-rank-fusion/1"

# Fusion over truncated lists is an approximation, and no cutoff makes it
# exact. An earlier comment here derived RRF_K + 1 as "exactly sufficient" from
# the equal-rank case; that was wrong for unequal ranks. A premise ranked 20th
# by one source and 62nd by the other scores 1/80 + 1/122, which still beats a
# single source's best 1/61 -- and if it is ranked first by one source, a vote
# at *any* depth from the other moves its score. So there is nothing to derive.
#
# What is left is to ask each source for as much as it will give and be honest
# that the result is bounded by that: every source is asked to `MAX_HITS`
# deep. Costless in requests -- each source is asked once either way, and only
# the parsing goes deeper.

# HTTP statuses that mean the service is unwell rather than that Hardy asked it
# the wrong thing. Everything else in 4xx says the request was refused on its
# merits, which is the endpoint's contract having moved.
TRANSIENT_STATUSES = frozenset({408, 429})

# `lean_search` stays in the vocabulary although no default source produces
# one any more -- `#find` was dropped for never answering, see
# `declarations.py` -- because rankings already written to run stores carry
# it, and a kind removed from the Literal would refuse to read them back.
SourceKind = Literal["lean_search", "declaration_index", "loogle", "embedding"]

# The kinds whose rendering of a signature is the local environment's own --
# what the model's Lean will actually elaborate -- as opposed to a remote
# service's. See the signature-preference note in `PremiseRetriever._ranked`.
LOCAL_KINDS = frozenset({"lean_search", "declaration_index"})


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
    # What this source was actually asked, which is not always the shared
    # query: the declaration index reads only the constant names out of it, so
    # the two are handed different spellings of one question. Recording only
    # the shared one would leave the provenance naming a query the pinned
    # source never ran.
    query: str | None = None
    returned: int = 0
    seconds: float = 0.0
    # Refused by the budget rather than asked. A flag rather than a prefix on
    # `detail`, so `budget_exhausted` can be re-derived from this record
    # instead of re-read out of a sentence.
    skipped_for_budget: bool = False
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
    # What was actually sent to the sources, which a goal carrying hypotheses
    # is not. The digest has to cover the question that was asked.
    query_sha256: str
    # And what came back. Without it the record described how a ranking was
    # made and not what it said, so premises could be swapped wholesale --
    # names, scores, source ranks -- and every check still passed.
    premises_sha256: str
    # The frozen budget this ranking was admitted against, and what the
    # retriever had already spent before it. Recorded so `run_seconds_remaining`
    # is derivable rather than asserted -- it was the one budget claim a reader
    # had to take on faith, which is not a thing this record does.
    budget_seconds: int
    prior_seconds_spent: float
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


def premises_digest(premises: Sequence[RankedPremise]) -> str:
    """The digest a ranking's premises are bound by, in their ranked order."""
    canonical = json.dumps(
        [premise.model_dump(mode="json") for premise in premises],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class PremiseRanking(FrozenModel):
    goal: str
    # The line the sources were given. Equal to `goal` unless the goal arrived
    # in Lean's display form, in which case this is its conclusion and the
    # hypotheses were dropped -- visible here rather than implied.
    query: str
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
        if hashlib.sha256(self.query.encode("utf-8")).hexdigest() != self.provenance.query_sha256:
            raise ValueError("query does not hash to the query_sha256 its provenance records")
        # Only for a whole ranking. Bounding drops premises on purpose, so the
        # digest cannot describe what is left -- and recomputing it there would
        # stamp a hash over a list no search produced, which is the one thing
        # `bound_ranking` exists to avoid. A truncated view says so and names
        # the artifact holding the ranking the digest is over.
        if self.observation_truncated:
            if not self.output_artifact:
                raise ValueError("a truncated ranking must name the artifact holding the whole one")
        elif premises_digest(self.premises) != self.provenance.premises_sha256:
            raise ValueError("premises do not hash to the premises_sha256 its provenance records")
        if self.complete != self._complete(self.provenance):
            raise ValueError("`complete` disagrees with the sources the provenance names")
        if self.reproducible != self._reproducible(self.provenance):
            raise ValueError("`reproducible` disagrees with the sources the provenance names")
        # The budget claims come from the same record, for the same reason:
        # they sat beside two derived booleans while being neither derived nor
        # checked, so a ranking could misreport what an experiment spent and
        # still validate. Including what earlier calls spent is what made the
        # last of the three derivable too.
        spent = sum(source.seconds for source in self.provenance.sources)
        if abs(self.seconds_spent - spent) > 1e-9:
            raise ValueError("`seconds_spent` disagrees with what the sources recorded")
        if self.budget_exhausted != any(
            source.skipped_for_budget for source in self.provenance.sources
        ):
            raise ValueError("`budget_exhausted` disagrees with the sources the provenance names")
        remaining = max(
            0.0,
            self.provenance.budget_seconds - self.provenance.prior_seconds_spent - spent,
        )
        if abs(self.run_seconds_remaining - remaining) > 1e-9:
            raise ValueError("`run_seconds_remaining` disagrees with the budget and the spend")
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

    def query_for(self, query: str) -> str:
        """This source's spelling of the shared query."""
        ...

    def search(self, goal: str, limit: int) -> tuple[DeclarationRecord, ...]: ...


# The identifier-shaped words that name no declaration: Lean keywords and the
# sorts. Small and static rather than complete -- an unfiltered `fun` would
# match hundreds of names about functors, while a keyword this list misses
# merely widens one query. Locals are already `_` by the time a query gets
# here, so what remains identifier-shaped is either a constant or one of these.
NOT_A_CONSTANT = frozenset(
    {"fun", "let", "in", "if", "then", "else", "match", "with", "do", "by",
     "at", "have", "show", "from", "where", "Type", "Prop", "Sort"}
)

# What the admission meter spends on the index, by temperature. The cold
# figure covers the one-time read of every source file the packages ship;
# measured at 7.5s for half a million lines on this machine, which
# extrapolates to ~30s for Mathlib whole, and `modules.py` records a build
# tree walk taking two minutes on Windows -- so the declared figure carries
# that margin rather than the flattering one. The warm figure covers a
# substring pass over names already in memory, measured in tenths of a
# second. Declared, not enforced: nothing interrupts a scan that outruns
# them, the same honest limit `LeanSearchSource` used to state about a wedged
# child process. Generous beats flattering here, because the admission check
# protects the run's budget with exactly these numbers.
DECLARATION_INDEX_COLD_SECONDS = 120.0
DECLARATION_INDEX_WARM_SECONDS = 5.0


class DeclarationIndexSource:
    """The declaration-name index over the package sources the run is frozen under.

    This is the replacement for `LeanSearchSource`, which ran Lean's own
    `#find` and was measured never to answer on the pinned toolchain -- the
    finding is recorded in `declarations.py`. What it gives up is honest to
    name: `#find` matched *result types*, and a name index matches names, so a
    pure-shape query is this source refusing and Loogle answering. What it
    gains is that it answers at all, instantly and offline, which `#find` did
    not do once.
    """

    def __init__(self, index: DeclarationIndex, *, environment: EnvironmentIdentity) -> None:
        self._index = index
        self._environment = environment

    @property
    def _manifest_matches(self) -> bool:
        """Whether the project about to be searched is the frozen one.

        `mcp_server.load_runtime` takes the project from `HARDY_CONFIG` and the
        environment identity from the claim on disk, and never checks that they
        describe the same thing -- so the index can read one Lake project while
        the corpus identity names another. Hashing the manifest is the check
        that closes it, and it is the same number `EnvironmentIdentity` already
        carries.

        What it does not establish, said plainly rather than implied: the
        manifest names the revisions Lake resolved, not the bytes on disk. A
        locally edited Mathlib leaves it byte-identical while the index reads
        something else. Closing that would mean hashing every source file per
        identity call, which for Mathlib is not a thing to do per search -- the
        same shape of limit the axiom audit states about the environment it
        elaborates in. No toolchain pin is required here, unlike the `#find`
        source it replaces: no compiler runs, so the corpus is the text alone.
        """
        project = self._index.project
        if project is None:
            return False
        try:
            manifest = (Path(project) / "lake-manifest.json").read_bytes()
        except OSError:
            return False
        return hashlib.sha256(manifest).hexdigest() == self._environment.lake_manifest_sha256

    @property
    def identity(self) -> SourceIdentity:
        frozen = self._manifest_matches
        return SourceIdentity(
            name="declaration-index",
            kind="declaration_index",
            corpus=(
                f"Mathlib {self._environment.mathlib_revision} sources / "
                f"manifest {self._environment.lake_manifest_sha256}"
                f"{'' if frozen else ' (NOT the project searched)'}"
            ),
            pinned=frozen,
        )

    @property
    def worst_case_seconds(self) -> float:
        return (
            DECLARATION_INDEX_WARM_SECONDS
            if self._index.read
            else DECLARATION_INDEX_COLD_SECONDS
        )

    def query_for(self, query: str) -> str:
        """The constant names in the shared query, which are all an index can use.

        `⊢ Nat.succ _ = _ + _` becomes `Nat.succ`: the turnstile and the
        operators are not identifiers and fall away on their own, the
        wildcards and keyword-shaped words are dropped, and what remains is
        deduplicated in order. Empty when the query is pure shape, and
        `search` then refuses rather than matching everything -- recorded in
        the provenance as this source not answering, with Loogle left to do
        what a name index cannot.
        """
        tokens = [
            token
            for token in DECLARATION_NAME.findall(query)
            if set(token) != {"_"} and token not in NOT_A_CONSTANT
        ]
        return " ".join(dict.fromkeys(tokens))

    def search(self, goal: str, limit: int) -> tuple[DeclarationRecord, ...]:
        if not goal.strip():
            raise RetrievalError(
                "the query names no constant for the declaration index to match; "
                "a pure-shape pattern is a question for Loogle"
            )
        return self._index.search(goal, limit)


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
        corpus_revision: str | None = None,
        fetch: Callable[[str, float], bytes] | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._timeout = timeout
        # A self-hosted instance served against a fixed Mathlib can say so, and
        # then a ranking it shaped *is* replayable. The endpoint was made
        # configurable for exactly that deployment, and hard-coding
        # `pinned=False` made the configuration pointless for the one reason
        # anyone would use it. Declared by the caller because nothing in the
        # protocol reports it -- an unverified claim, but the operator's rather
        # than Hardy's, and the revision it names travels in the corpus.
        if corpus_revision is not None and not IMMUTABLE_REVISION.fullmatch(corpus_revision):
            raise ValueError(
                "a Loogle corpus revision must be a git object name (7-40 hex characters); "
                "a branch or tag can be repointed under the identity that named it"
            )
        self._corpus_revision = corpus_revision
        self._fetch = fetch or _fetch_url

    @property
    def identity(self) -> SourceIdentity:
        return SourceIdentity(
            name="loogle",
            kind="loogle",
            corpus=(
                f"{self._endpoint} @ {self._corpus_revision}"
                if self._corpus_revision
                else self._endpoint
            ),
            pinned=self._corpus_revision is not None,
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

    def query_for(self, query: str) -> str:
        # Loogle reads `⊢ p` as a conclusion filter, so it takes the shared
        # query as it stands. Verified against the live service.
        return query

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
            # Strictly. `errors="replace"` turned undecodable bytes into `\ufffd`
            # and handed back a signature that reads as ordinary Lean while
            # naming something else -- silently altered data recorded as a
            # source that answered. JSON is required to be valid Unicode, so a
            # body that is not is a source that failed.
            payload = json.loads(body.decode("utf-8"))
        except UnicodeDecodeError as error:
            raise RetrievalError(f"Loogle response was not valid UTF-8: {error}") from error
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
        if len(name) > MAX_NAME_CHARACTERS or not DECLARATION_NAME.fullmatch(name):
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


def search_query(goal: str) -> str:
    """The one line a search can take, out of what Lean prints as a goal.

    `open_goals` comes back from a proof check in Lean's display form --
    hypotheses on their own lines, then a turnstile and the conclusion -- and
    that is the form a model has in hand when it wants to know what lemma it is
    missing. Refusing it made the tool unusable on its most natural input.

    Two mechanical steps, neither of them a guess about the mathematics:

    1. Take the conclusion. The first one, not the last: Lean prints the goal
       being worked on ahead of the ones queued behind it.
    2. Wildcard the locals. `⊢ n + m = m + n` sent as written gets `Unknown
       identifier ``n``` back from Loogle -- measured, not predicted -- because
       `n` names a hypothesis and means nothing outside this goal. The
       hypothesis lines say exactly which names are local, so replacing those
       with `_` is reading Lean's own binder display rather than interpreting
       it, and `⊢ _ + _ = _ + _` is the pattern both searches want.

    What is *not* attempted is turning `h : n < m` into a constraint on the
    pattern. That would be a guess about what the caller meant, and a wrong one
    produces a confidently wrong ranking. So the hypotheses inform which names
    are free and are otherwise dropped -- lossy in a way the caller can see,
    because the ranking reports the query beside the goal it came from.

    Nor is dot notation desugared. `xs.reverse` means `List.reverse xs`, and
    recovering that needs the type of `xs` and a model of how Lean elaborates
    projections, which is an elaborator living in the wrong module. Such a goal
    becomes a pattern the searches reject, and a rejected search is recorded as
    a source that did not answer -- the honest outcome, and a much better one
    than a ranking of whatever a mangled query happened to match.

    A conclusion Lean wrapped over several lines is rejoined before any of
    this. The pretty-printer breaks a long proposition onto indented
    continuation lines, and taking only the turnstile line searched a shorter,
    different proposition without saying so.

    Text with no hypothesis lines is already a pattern and passes through.
    """
    raw = goal.splitlines()
    lines = [line.strip() for line in raw if line.strip()]
    if not lines:
        return ""
    conclusion = " ".join(_conclusion_lines(raw)) or lines[0]
    # Longest first, so `xs` is not half-replaced by a shorter `x`.
    for name in sorted(_local_names(lines), key=len, reverse=True):
        # Outside string literals only. `⊢ x = "x"` is a statement about the
        # string "x", and rewriting the literal too turned it into a statement
        # about something else -- which both searches would then rank premises
        # for, perfectly confidently. Char literals need no such care: `'` is an
        # identifier character in Lean, so the quote already blocks the match.
        # Asymmetric on purpose. The lookbehind refuses `.` so the tail of a
        # qualified global -- `Foo.n` where `n` is also a local -- survives
        # whole. The lookahead allows it, because `xs.reverse` *is* the local
        # `xs` under projection and must become `_.reverse`; leaving it earns a
        # guaranteed `Unknown identifier`, which is worse than a pattern a
        # search might not like.
        # `(?!\s*:=)` keeps a label: in `{ field := field }` the first `field`
        # names the structure field and the second is the local. Rewriting both
        # produced `{ _ := _ }`, which is not a query any source accepts.
        conclusion = _substitute_outside_literals(
            conclusion, rf"(?<![\w'.!?]){re.escape(name)}(?![\w'!?])(?!\s*:=)"
        )
    return conclusion


# One binder name. Guillemets first, so a quoted name stays whole: splitting on
# whitespace turned `«foo bar»` into two tokens that matched nothing.
BINDER_TOKEN = re.compile(r"«[^»]*»|[^\s]+")
# How Lean displays a hypothesis it had to disambiguate -- `x✝`, `x✝¹`. Not an
# identifier, so nothing else here recognises it.
DISPLAYED_LOCAL = re.compile(r"[^\s:()]*✝[^\s:()]*")

# A Lean string literal, with backslash escapes: an escaped quote does not end
# it, so `"a \" x"` is one literal rather than two fragments and a stray `x`.
# The optional `s!`/`m!`/`f!` prefix is captured because those strings are not
# literal all the way through -- see `_substitute_outside_literals`.
STRING_LITERAL = re.compile(r'(s!|m!|f!)?"(?:[^"\\]|\\.)*"')
# What an interpolated string holds between its literal parts: an expression,
# in which a local means what it means everywhere else.
INTERPOLATION = re.compile(r"\{[^{}]*\}")


def _substitute_outside_literals(text: str, pattern: str) -> str:
    """Wildcard matches of `pattern`, leaving string literals untouched.

    Except inside interpolation. `s!"{x}"` is not literal text all the way
    through -- the braces hold an expression, and a local named there means
    exactly what it means outside the quotes. A plain `"{x}"` really is
    literal, braces and all, which is why the prefix is read rather than
    assumed: substituting into every braced group would undo the reason
    literals are skipped at all.
    """
    pieces = []
    read = 0
    for literal in STRING_LITERAL.finditer(text):
        pieces.append(re.sub(pattern, "_", text[read : literal.start()]))
        body = literal.group(0)
        if literal.group(1):
            body = INTERPOLATION.sub(lambda hole: re.sub(pattern, "_", hole.group(0)), body)
        pieces.append(body)
        read = literal.end()
    pieces.append(re.sub(pattern, "_", text[read:]))
    return "".join(pieces)


def _conclusion_lines(raw: Sequence[str]) -> list[str]:
    """The first goal's conclusion, including the lines Lean wrapped it onto.

    A continuation is indented; a blank line ends the goal, and an unindented
    line starts the next one. Empty when there is no turnstile at all, which is
    how a bare search pattern reaches the caller untouched.
    """
    start = next(
        (index for index, line in enumerate(raw) if line.strip().startswith(TURNSTILE)), None
    )
    if start is None:
        return []
    taken = [raw[start].strip()]
    for line in raw[start + 1 :]:
        if not line.strip() or not line[:1].isspace():
            break
        taken.append(line.strip())
    return taken


def _local_names(lines: Sequence[str]) -> set[str]:
    """The hypothesis names Lean bound above the turnstile.

    A hypothesis line reads `names... : type`, and several names can share one
    type (`n m : ℕ`). Anything before the first turnstile that does not look
    like a binding -- a wrapped type continuing onto its own line, say -- is
    skipped rather than guessed at.
    """
    names: set[str] = set()
    for line in lines:
        if line.startswith(TURNSTILE):
            break
        head, separator, _ = line.partition(" : ")
        if not separator:
            continue
        names.update(part for part in BINDER_TOKEN.findall(head) if _is_local(part))
    return names


def _is_local(token: str) -> bool:
    """Whether a binder token is a name Lean bound in this goal.

    Two forms beyond an ordinary identifier, both of which Lean prints and both
    of which went through to the search verbatim -- where they mean nothing, so
    every source failed on a goal that was otherwise perfectly ordinary.
    `x✝` is how a shadowed or unnamed hypothesis is displayed, and `«foo bar»`
    is a quoted name, which `head.split()` tore in half before it could even be
    recognised.
    """
    return bool(DECLARATION_NAME.fullmatch(token) or DISPLAYED_LOCAL.fullmatch(token))


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
        # And one ranking at a time. `rank` read the spend, ran its sources,
        # then wrote it back, so two MCP calls arriving together both admitted
        # against a figure the other was already spending -- a budget sized for
        # one paying for two. The staged transport gates its dispatch; the MCP
        # server does not, so the budget defends itself here. Serializing is the
        # right shape rather than a concession: the sources share one
        # declaration index, and two scans of it at once were never going to
        # be faster.
        self._admission = threading.Lock()

    @property
    def seconds_remaining(self) -> float:
        return max(0.0, self._limits.retrieval_seconds - self._spent)

    def rank(self, goal: str, limit: int = 10) -> PremiseRanking:
        if not 1 <= len(goal) <= MAX_GOAL_CHARACTERS:
            raise ValueError(f"a retrieval goal must be 1 to {MAX_GOAL_CHARACTERS} characters")
        query = search_query(goal)
        if not 1 <= len(query) <= MAX_QUERY_CHARACTERS:
            raise ValueError(
                f"no searchable line of at most {MAX_QUERY_CHARACTERS} characters could be "
                "taken from this goal"
            )
        if not 1 <= limit <= 50:
            raise ValueError("a premise ranking holds between 1 and 50 premises")

        # Depth is not derived from `limit`; see the note on fusion above.
        with self._admission:
            return self._ranked(goal, query, limit)

    def _ranked(self, goal: str, query: str, limit: int) -> PremiseRanking:
        depth = MAX_HITS
        started = self._clock()
        spent = 0.0
        exhausted = False
        outcomes: list[SourceOutcome] = []
        # name -> (rank per source, best record seen)
        found: dict[str, list[SourceRank]] = {}
        records: dict[str, DeclarationRecord] = {}
        local_signature: set[str] = set()

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
                        skipped_for_budget=True,
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
            asked = source.query_for(query)
            try:
                results = source.search(asked, depth)
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
                    query=asked,
                    returned=len(results),
                    seconds=seconds,
                    detail=detail,
                )
            )
            if detail is not None:
                continue
            seen: set[str] = set()
            position = 0
            for record in results:
                # A source that lists a name twice must not vote twice: two
                # entries from one search would inflate the score and leave
                # `ranks` reading as though two searches had agreed.
                if record.name in seen:
                    continue
                seen.add(record.name)
                # Counted after the skip, not by `enumerate` before it: a
                # source answering `[A, A, B]` put B second among the results
                # it usefully returned, and recording it third quietly lowered
                # its score for a duplicate that had already been discarded.
                position += 1
                found.setdefault(record.name, []).append(
                    SourceRank(source=identity.name, rank=position)
                )
                # The signature a model reads should be the one the environment
                # it will elaborate against actually holds, so the local search
                # wins over a remote service's rendering.
                #
                # Keyed on the *kind*, not on `pinned`. Tightening what pinning
                # requires -- a matching manifest -- quietly cost a
                # legitimately-unpinned local environment its say over
                # signatures, and handed the model a remote type for a
                # declaration its own Lean was about to elaborate. Whether a
                # ranking can be replayed is a question for `reproducible`.
                local = identity.kind in LOCAL_KINDS
                if record.name not in records or (local and record.name not in local_signature):
                    records[record.name] = record
                if local:
                    local_signature.add(record.name)

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

        prior = self._spent
        self._spent += spent
        provenance = RetrievalProvenance(
            premises_sha256=premises_digest(premises[:limit]),
            budget_seconds=self._limits.retrieval_seconds,
            prior_seconds_spent=prior,
            goal_sha256=hashlib.sha256(goal.encode("utf-8")).hexdigest(),
            query_sha256=hashlib.sha256(query.encode("utf-8")).hexdigest(),
            ranker=RANKER,
            sources=tuple(outcomes),
        )
        return PremiseRanking(
            goal=goal,
            query=query,
            premises=tuple(premises[:limit]),
            provenance=provenance,
            provenance_sha256=provenance.digest,
            complete=PremiseRanking._complete(provenance),
            reproducible=PremiseRanking._reproducible(provenance),
            seconds_spent=spent,
            run_seconds_remaining=self.seconds_remaining,
            budget_exhausted=exhausted,
        )


def build_retriever(
    service: object, limits: RunLimits, index: DeclarationIndex | None = None
) -> PremiseRetriever:
    """The default source set, in the order the budget should spend on them.

    The pinned local source goes first, which decides two things when the
    budget is tight: its rendering of a signature is the one a model reads,
    and the source dropped for want of time is the unpinned one.

    `index` lets a caller that also serves `search_declarations` share one
    index between the plain search and the ranking, so a session pays the
    one-time source scan once. Left out, the retriever builds its own over the
    same project.

    Lean's own `#find` is deliberately not a source any more: measured on the
    pinned toolchain it never answered while costing a full process timeout
    per ranking -- the finding is recorded in `declarations.py`.
    """
    if index is None:
        index = DeclarationIndex(getattr(service, "lean_project", None))
    return PremiseRetriever(
        sources=(
            DeclarationIndexSource(index, environment=service.environment),
            LoogleSource(),
        ),
        limits=limits,
    )
