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
PROMPT_SET_VERSION = "2026-08-27.1"

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
WRITEUP_PROMPT = render("staged/writeup")
STRUCTURE_INSTRUCTION = "\n\n" + render("staged/structure") + "\n"
CHAT_SYSTEM_PROMPT = render("chat")
# Appended only when a CAS backend was actually discovered, so a session with
# no kernel never describes tools it does not have.
def chat_cas_prompt(backend: str) -> str:
    return render("chat_cas", backend=backend)


def cas_spill_note(*, artifact: str | None, capture_truncated: bool) -> str:
    """What the model is told when an answer was too big to hand back."""
    return render("cas_spill", artifact=artifact, capture_truncated=capture_truncated)


BATCH_SYSTEM_PROMPT = render("batch/system")


def proof_prompt(claim: FrozenClaim) -> str:
    binders = f" {claim.proposal.binders.strip()}" if claim.proposal.binders.strip() else ""
    signature = (
        f"theorem {claim.proposal.theorem_name}{binders} : "
        f"{claim.proposal.proposition.strip()}"
    )
    return render("staged/proof", claim_hash=claim.content_hash, signature=signature)


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
    """
    return {
        "base": source("staged/base"),
        "developer": source("staged/developer"),
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
