"""Typed models of the leanprover-community/repl JSON wire protocol.

The repl speaks camelCase JSON (endPos, proofState); attributes here are
snake_case and parse via alias generator. extra="ignore" keeps us tolerant
of protocol additions in newer repl versions.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ReplModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="ignore"
    )


class Pos(ReplModel):
    line: int
    column: int


class Message(ReplModel):
    severity: Literal["error", "warning", "info", "information"]
    pos: Pos
    end_pos: Pos | None = None
    data: str


class Sorry(ReplModel):
    pos: Pos
    end_pos: Pos | None = None
    goal: str
    proof_state: int | None = None


class CommandResponse(ReplModel):
    env: int | None = None
    messages: list[Message] = []
    sorries: list[Sorry] = []
    # Fatal repl-level error (e.g. "unknown environment") — no env is returned.
    message: str | None = None


class TacticResponse(ReplModel):
    proof_state: int | None = None
    goals: list[str] = []
    messages: list[Message] = []
    message: str | None = None
