"""Turn raw REPL responses into a structured pass/fail verdict.

`complete` is the M0 notion of "kernel-checked": an environment came back,
with no errors, no sorries, and no fatal repl-level message. (The full
anti-cheat suite — axiom audits, statement immutability — is M2.)
"""

from typing import Literal

from pydantic import BaseModel

from .messages import CommandResponse, Message, Pos, Sorry


class ProofVerdict(BaseModel):
    complete: bool
    errors: list[Message] = []
    warnings: list[Message] = []
    sorries: list[Sorry] = []
    # Set when the worker never produced a response at all.
    failure: Literal["timeout", "crash"] | None = None


def verdict(resp: CommandResponse) -> ProofVerdict:
    errors = [m for m in resp.messages if m.severity == "error"]
    warnings = [m for m in resp.messages if m.severity != "error"]
    if resp.message is not None:
        errors.append(
            Message(severity="error", pos=Pos(line=0, column=0), data=resp.message)
        )
    complete = resp.env is not None and not errors and not resp.sorries
    return ProofVerdict(
        complete=complete, errors=errors, warnings=warnings, sorries=resp.sorries
    )


def failure_verdict(kind: Literal["timeout", "crash"]) -> ProofVerdict:
    return ProofVerdict(complete=False, failure=kind)
