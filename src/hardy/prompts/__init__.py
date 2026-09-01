"""Every instruction Hardy sends a model, and the loader that renders them.

The templates live beside this module as `.md.j2` files so that prompt text is
edited as prose rather than as Python string literals. They are Jinja2, with
undefined variables an error: a prompt that quietly lost its claim hash would
ask a model to prove nothing while looking entirely ordinary.

The staged prompt set is hashed and the hash is recorded in every run manifest,
so a result can be traced to the exact instructions that produced it. Editing
any staged template below changes that hash, which is the intended effect.
"""

from __future__ import annotations

import hashlib
import json
from functools import cache
from importlib import resources

from jinja2 import Environment, StrictUndefined, TemplateNotFound
from jinja2 import UndefinedError as _UndefinedError
from jinja2.loaders import BaseLoader

from ..domain import FrozenClaim

# Bumped whenever a staged template changes. The hash below identifies the text
# exactly; this names the revision a human can talk about.
PROMPT_SET_VERSION = "2026-09-01.1"

SUFFIX = ".md.j2"


class PromptError(RuntimeError):
    """A template is missing, malformed, or was rendered without a variable."""


class _PackageLoader(BaseLoader):
    """Reads templates through importlib.resources.

    Jinja's own PackageLoader reaches for a filesystem path, which a zipped
    install does not have. `resources` is the interface that answers for both.
    """

    def get_source(self, environment: Environment, template: str):
        parts = (template + SUFFIX).split("/")
        anchor = resources.files(__package__)
        for part in parts[:-1]:
            anchor = anchor / part
        try:
            text = (anchor / parts[-1]).read_text(encoding="utf-8")
        except (FileNotFoundError, NotADirectoryError, ModuleNotFoundError) as error:
            raise TemplateNotFound(template) from error
        # No auto-reload: an installed package's templates cannot change under a
        # running process, and `source` is cached on the same assumption.
        return text, None, lambda: True


_environment = Environment(loader=_PackageLoader(), undefined=StrictUndefined, keep_trailing_newline=False, autoescape=False)


@cache
def source(name: str) -> str:
    """The raw template text, as written. This is what the hash covers."""
    try:
        text, _, _ = _environment.loader.get_source(_environment, name)
    except TemplateNotFound as error:
        raise PromptError(f"no prompt template named {name!r}") from error
    return text.strip()


def render(name: str, /, **variables: object) -> str:
    """Render one template. Every variable it uses must be supplied."""
    try:
        return _environment.get_template(name).render(**variables).strip()
    except TemplateNotFound as error:
        raise PromptError(f"no prompt template named {name!r}") from error
    except _UndefinedError as error:
        raise PromptError(f"prompt {name!r} is missing a variable: {error}") from error


# The names the rest of Hardy imports. Rendered once at import: none of these
# take variables, and their text is fixed for the life of the process.
BASE_INSTRUCTIONS = render("staged/base")
DEVELOPER_INSTRUCTIONS = render("staged/developer")
FORMALIZATION_PROMPT = render("staged/formalization")


def writeup_prompt(*, verified: bool) -> str:
    """The writeup stage's instructions, carrying the verification outcome.

    The model on the proving thread is told about a rejection and never about
    an acceptance -- the verifier's yes ends the loop -- so a writeup asked
    for without this said "no acceptance is claimed here" under a heading
    reading kernel verified. The grade is Hardy's, not the model's; the
    prompt says which it will be.
    """
    return render("staged/writeup", verified=verified)
STRUCTURE_INSTRUCTION = "\n\n" + render("staged/structure") + "\n"
CHAT_SYSTEM_PROMPT = render("chat")
# What the search façade tells the model about an empty `inspect_declarations`
# batch, and about a `search_modules` query that named a concept rather than a
# module. Model-facing text, so it lives here rather than as a string constant
# in `search_tools.py` -- the same reason every other prompt in this file does.
SPELLINGS_HINT = render("spellings_hint") + "\n"
CONCEPT_HINT = "\n" + render("concept_hint")
# Appended only when a CAS backend was actually discovered, so a session with
# no kernel never describes tools it does not have.
def chat_cas_prompt(backend: str) -> str:
    return render("chat_cas", backend=backend)


def chat_project_context_prompt(*, name: str, text: str, truncated: bool, shown: int, total: int) -> str:
    """The user's own project instructions, framed as input rather than orders.

    Deliberately outside `_prompt_set_payload`, so this text is *not* folded
    into `PROMPT_SET_SHA256`. That hash identifies the instructions a staged
    run was given, and staged runs never see this: an unattended graded run
    whose prompt came partly from a project-local file is not comparable to
    another run. Interactive work wants intent; benchmarking wants a fixed
    condition.
    """
    return render("chat_project_context", name=name, text=text, truncated=truncated, shown=shown, total=total)


def cas_spill_note(*, artifact: str | None, capture_truncated: bool) -> str:
    """What the model is told when an answer was too big to hand back."""
    return render("cas_spill", artifact=artifact, capture_truncated=capture_truncated)


BATCH_SYSTEM_PROMPT = render("batch/system")


def claim_signature(claim: FrozenClaim) -> str:
    """The Lean line a frozen claim states, as every surface must quote it."""
    binders = f" {claim.proposal.binders.strip()}" if claim.proposal.binders.strip() else ""
    return (
        f"theorem {claim.proposal.theorem_name}{binders} : "
        f"{claim.proposal.proposition.strip()}"
    )


def proof_prompt(claim: FrozenClaim) -> str:
    return render("staged/proof", claim_hash=claim.content_hash, signature=claim_signature(claim))


def faithfulness_prompt(claim: FrozenClaim) -> str:
    """The independent reader's question: the claim and the Lean, and nothing else.

    Deliberately not given the proposal's restatement, domains or
    interpretation choices. Those are the formalizer's gloss on its own work,
    and a reader handed them is reading the translation through the account
    that produced it -- which is the shared context this gate exists to
    defeat. What is left is the two texts that have to say the same thing.
    """
    text = claim.original_text.strip()
    signature = claim_signature(claim)
    return render(
        "staged/faithfulness",
        fence=_fence(text, signature),
        claim=text,
        signature=signature,
    )


def _fence(*texts: str) -> str:
    """A quoting marker none of `texts` contains, derived from them.

    A fixed terminator is one a quoted text can write. Both texts here are
    untrusted -- the claim is the user's and the Lean is a model's -- and a
    Lean block comment holding a line equal to the terminator would have ended
    its own fence, putting whatever followed where the reader reads
    instructions. Deriving the marker from the bytes it has to survive removes
    that move: to close the fence early a text would have to contain a digest
    of itself.

    Deterministic, because `prompt_sha256` must identify the question that was
    actually asked and a random marker would make the same claim hash
    differently on every run. Lengthened rather than trusted if the improbable
    happens, so this cannot collide even in principle.
    """
    joined = "\u0000".join(texts).encode("utf-8")
    digest = hashlib.sha256(joined).hexdigest()
    for length in range(8, len(digest) + 1):
        marker = "===HARDY-" + digest[:length] + "==="
        if all(marker not in text for text in texts):
            return marker
    raise PromptError("could not derive a quoting marker the claim does not contain")


def batch_task_prompt(informal_claim: str, declaration: str, imports: tuple[str, ...]) -> str:
    return render("batch/task", informal_claim=informal_claim, declaration=declaration, imports=", ".join(imports))


def _prompt_set_payload() -> dict[str, str]:
    """What the recorded hash is taken over: the staged templates as written.

    Deliberately the template source rather than any rendered output, so the
    hash describes the instructions themselves and not one claim's rendering.

    Every template under `staged/` belongs here, `structure` included: it is
    appended to each staged turn, so an edit to the response contract changed
    what a run was told while its manifest kept the same hash. A test
    enumerates the directory rather than trusting this list.

    The other templates stay out, and that is a decision rather than an
    oversight. `chat`, `chat_cas` and `cas_spill` serve an interactive session,
    which is not a comparable experimental unit and writes no manifest.
    `batch/system` and `batch/task` do govern graded runs, but the batch runner
    records no prompt-set hash at all (`runner.py` writes `provenance()`, which
    is model and endpoint), so there is no field for their absence to falsify;
    folding them in here would instead churn the staged hash for edits no
    staged run ever saw. If a batch record ever carries a prompt-set hash, it
    must cover `batch/*` -- as its own hash, not by widening this one.

    One asymmetry this does not close, recorded so it is not mistaken for
    coverage: the Codex backend hands the response schema to the SDK as
    `output_schema` (`codex_runtime.py:117`) and never appends
    `STRUCTURE_INSTRUCTION`, so a `--backend codex` run records a hash over a
    template it was not sent, and `RunManifest` carries no backend field to
    tell the two apart. That is the conservative direction of the same error --
    identical instructions can now hash differently across a `structure` edit,
    where before different instructions hashed identically -- but it is still
    wrong, and closing it means a per-backend hash plus a manifest that says
    which backend ran -- a `schema_version` bump, deliberately not made here.
    """
    return {
        "base": source("staged/base"),
        "developer": source("staged/developer"),
        "faithfulness": source("staged/faithfulness"),
        "formalization": source("staged/formalization"),
        "proof": source("staged/proof"),
        "structure": source("staged/structure"),
        "version": PROMPT_SET_VERSION,
        "writeup": source("staged/writeup"),
    }


def _prompt_set_hash() -> str:
    canonical = json.dumps(_prompt_set_payload(), separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(canonical).hexdigest()


PROMPT_SET_SHA256 = _prompt_set_hash()
