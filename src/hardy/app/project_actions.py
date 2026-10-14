"""One strict structured action contract for session commands and the browser.

Only this adapter translates application inputs into named session operations.
The workflow owners authenticate exact references and acceptance independently.
"""
from __future__ import annotations

import dataclasses
import json
import shlex
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StrictInt, StringConstraints, create_model

from hardy.foundation.values import FrozenModel
from hardy.prompts import user as user_prompts
from hardy.workflows.interactive.live_records import ExploreOperationRequest, RepairOperationRequest
from hardy.workflows.interactive.project_application import SEMANTIC_REQUESTS
from hardy.workflows.ledger.contracts import Text
from hardy.workflows.research import ResearchRequest

Exact = Annotated[str, StringConstraints(strict=True, pattern=r"\A[A-Za-z0-9][A-Za-z0-9_.:-]*@[0-9a-f]{64}\z")]
Attempts = Annotated[StrictInt, Field(ge=1, le=10000)]


class Action(FrozenModel):
    operation: Text
    project: Text


class TargetAction(Action):
    operation: Literal["target"] = "target"
    identity: Text
    statement: Text
    formal: Text
    context: str | None = None
    supersedes: Exact | None = None


class ReviewAction(Action):
    operation: Literal["review"] = "review"
    target: Exact
    translation: Exact
    verdict: Literal["approved", "disputed", "unresolved"]
    reviewer: Text
    conditions: Text
    reason: Text
    scope: Exact | None = None


class BindAction(Action):
    operation: Literal["bind"] = "bind"
    target: Exact
    translation: Exact
    saved: Exact
    scope: Exact | None = None


class InspectAction(Action):
    operation: Literal["inspect"] = "inspect"
    target: Text


class ExportAction(Action):
    operation: Literal["export"] = "export"
    target: Text
    output: Text


class PublishAction(Action):
    operation: Literal["publish"] = "publish"
    item: Text
    scope: Text
    output: Text


class LinkAction(Action):
    operation: Literal["link"] = "link"
    source: Text
    kind: Literal["illustrates", "documents"]
    target: Text


class MarkAction(Action):
    operation: Literal["mark"] = "mark"
    item: Text
    visibility: Literal["internal", "public", "omitted"]


class WorkflowStatusAction(Action):
    operation: Literal["workflow-status", "intake-status", "referee-report", "catalog"]
    id: str | None = None


_LEGACY = {"target": TargetAction, "review": ReviewAction, "bind": BindAction,
           "inspect": InspectAction, "export": ExportAction, "publish": PublishAction,
           "link": LinkAction, "mark": MarkAction}
_NESTED = {"research": ResearchRequest, "explore": ExploreOperationRequest, "repair": RepairOperationRequest,
           **SEMANTIC_REQUESTS}
_READS = {"workflow-status", "intake-status", "referee-report", "catalog"}
PROJECT_VERBS = frozenset({*_LEGACY, *_NESTED, *_READS, "intake", "intake-review", "referee", "bundle", "referee-export", "action"})
LONG_ACTIONS = frozenset({"research", "explore", "repair", "intake", "referee", "admission-gates", "publish", "bundle"})


def action_model(operation: str) -> type[BaseModel]:
    if operation in _LEGACY:
        return _LEGACY[operation]
    if operation in _READS:
        return WorkflowStatusAction
    request_model = _NESTED.get(operation)
    if operation in {"intake", "intake-review", "referee"}:
        from hardy.workflows.interactive.manuscript_records import (
            IntakeRequest,
            IntakeReviewRequest,
            RefereeRunRequest,
        )
        request_model = {"intake": IntakeRequest, "intake-review": IntakeReviewRequest, "referee": RefereeRunRequest}[operation]
    if operation in {"bundle", "referee-export"}:
        from hardy.workflows.interactive.export_records import (
            RefereeExportRequest,
            ResultExportRequest,
        )
        request_model = {"bundle": ResultExportRequest, "referee-export": RefereeExportRequest}[operation]
    if request_model is None:
        raise ValueError("Unknown project operation.")
    fields = {"operation": (Literal[operation], operation), "request": (request_model, ...)}
    if operation in {"research", "repair"}:
        fields["max_attempts"] = (Attempts, 64 if operation == "research" else 8)
    return create_model(f"{operation.title().replace('-', '')}Action", __base__=Action, **fields)


def validate_action(data: dict[str, Any]) -> Action:
    if not isinstance(data, dict) or not isinstance(data.get("operation"), str):
        raise ValueError("Project operations require a named operation and structured fields.")
    # Strict JSON validation preserves JSON arrays for tuple-valued contracts,
    # while refusing boolean/int/string coercions recursively in nested models.
    return action_model(data["operation"]).model_validate_json(json.dumps(data), strict=True)


def execute_action(session, action: Action):
    """Run only a validated request; both adapters call this function."""
    action = validate_action(action.model_dump(mode="json"))
    loaded_project = getattr(session, "workspace", None)
    if loaded_project is None or action.project != str(loaded_project):
        raise ValueError("The loaded project changed or is unavailable. Reload the project before submitting this action.")
    operation = action.operation
    if operation in SEMANTIC_REQUESTS:
        method = getattr(session, "project_semantic", None)
        if method is None:
            raise ValueError("This session does not provide semantic project operations.")
        return method(operation, action.request)
    if operation == "catalog":
        method_name = "project_catalog"
    elif operation == "bundle":
        method_name = "project_export_bundle"
    elif operation == "referee-export":
        method_name = "project_export_referee"
    else:
        method_name = f"project_{operation.replace('-', '_')}"
    method = getattr(session, method_name, None)
    if method is None:
        raise ValueError(f"This session does not provide {operation}.")
    if operation in _LEGACY:
        values = {name: getattr(action, name) for name in type(action).model_fields
                  if name not in {"operation", "project"}}
        return method(**values)
    if operation in _READS:
        return method() if operation == "catalog" else method(action.id)
    options = {"max_attempts": action.max_attempts} if operation in {"research", "repair"} else {}
    return method(action.request, **options)


def action_result(result: Any) -> Any:
    """Transport records with owner-computed exact refs and journal digests."""
    from hardy.foundation.journal import record_digest

    if isinstance(result, BaseModel):
        data = result.model_dump(mode="json")
        if hasattr(result, "ref"):
            data["ref"] = result.ref.model_dump(mode="json")
        data["digest"] = getattr(result, "digest", record_digest(result))
        return data
    if isinstance(result, Path):
        return str(result)
    if dataclasses.is_dataclass(result):
        return action_result(dataclasses.asdict(result))
    if isinstance(result, dict):
        return {key: action_result(value) for key, value in result.items()}
    if isinstance(result, (tuple, list)):
        return [action_result(value) for value in result]
    return result


def provider_active(session) -> bool:
    """A fresh provider retains ownership through actual worker teardown."""
    calls = getattr(getattr(session, "live", None), "calls", None)
    return bool(calls is not None and calls.busy)


def project_stopper(session):
    from hardy.foundation import process

    pressed = False
    def stop() -> bool:
        nonlocal pressed
        method = getattr(session, "escalate" if pressed else "cancel", None)
        if method is None:
            (process.stop_children if pressed else process.interrupt_children)()
        elif pressed:
            method()
        else:
            method("user_pressed_escape")
        pressed = True
        return True
    return stop


def _exact(value: str) -> dict[str, str]:
    if "@" not in value:
        raise ValueError("This operation requires exact ID@FULL_SHA256 references.")
    identity, digest = value.rsplit("@", 1)
    return {"id": identity, "digest": digest}


def _options(parts: list[str]) -> dict[str, str]:
    if len(parts) % 2 or any(not key.startswith("--") for key in parts[::2]) or len(set(parts[::2])) != len(parts[::2]):
        raise ValueError("Options require unique --name value pairs.")
    return {key[2:].replace("-", "_"): value for key, value in zip(parts[::2], parts[1::2], strict=True)}


def parse_action_command(argument: str, *, project: str) -> Action:
    verb, _, tail = argument.strip().partition(" ")
    if verb == "action":
        operation, _, raw = tail.strip().partition(" ")
        # JSON is parsed directly, preserving mathematical quotes/backslashes.
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
            raw = raw[1:-1]
        try:
            request = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError(f"Invalid structured project request: {error.msg}") from error
        if not isinstance(request, dict):
            raise ValueError("A structured project request must be a JSON object.")
        data = {"operation": operation, "project": project}
        if operation in _LEGACY or operation in _READS:
            _reject_reserved(request)
        data.update(request if operation in _LEGACY or operation in _READS else {"request": request})
        return validate_action(data)
    words = [user_prompts.unquoted(word) for word in shlex.split(tail, posix=False)]
    data = {"operation": verb, "project": project}
    counts = {"target": 2, "review": 2, "bind": 2, "inspect": 1, "export": 1,
              "publish": 1, "link": 3, "mark": 2, "research": 2, "repair": 2,
              "explore": 1, "intake": 1, "referee": 1}
    if verb in _READS:
        if len(words) > 1:
            raise ValueError("Status operations take at most one record ID.")
        if words:
            data["id"] = words[0]
        return validate_action(data)
    count = counts.get(verb)
    if count is None or len(words) < count:
        raise ValueError("Use /project action OPERATION JSON for structured project requests.")
    positional, options = words[:count], _options(words[count:])
    if verb in _LEGACY:
        _reject_reserved(options)
        names = {"target": ("identity", "statement"), "review": ("target", "translation"),
                 "bind": ("target", "translation"), "inspect": ("target",), "export": ("target",),
                 "publish": ("item",), "link": ("source", "kind", "target"), "mark": ("item", "visibility")}[verb]
        return validate_action({**data, **dict(zip(names, positional, strict=True)), **options})
    request: dict[str, Any] = {"id": positional[0], **options}
    if verb == "research":
        request["target"] = positional[1]
    if verb == "repair":
        request["obligation"] = positional[1]
        if "original" in request:
            request["original_argument"] = request.pop("original")
    if verb == "intake":
        source = request.pop("source", "")
        if "@" not in source:
            raise ValueError("Intake requires --source PATH@FULL_SHA256 and --scope-id ID.")
        path, digest = source.rsplit("@", 1)
        selected = {"path": path, "digest": digest}
        if "retained" in request:
            selected["retained"] = _exact(request.pop("retained"))
        request["sources"] = [selected]
        if "target_papers" in request:
            request["target_papers"] = request["target_papers"].split(",")
        if "citation_depth" in request:
            request["citation_depth"] = int(request["citation_depth"])
        return validate_action({**data, "request": request})
    if verb == "referee":
        if "main" in request:
            request["main_results"] = request.pop("main")
        if "internal" in request:
            request["required_internal"] = request.pop("internal")
        for key in ("main_results", "required_internal", "original", "repaired"):
            if key in request:
                request[key] = [_exact(value) for value in request[key].split(",")]
        for key in ("review", "scope"):
            if key in request:
                request[key] = _exact(request[key])
        for key in ("citation_depth", "citation_max_nodes"):
            if key in request:
                request[key] = int(request[key])
        return validate_action({**data, "request": request})
    for key in ("target", "scope", "context", "obligation", "original_argument", "concept", "use_site"):
        if key in request:
            request[key] = _exact(request[key])
    if "max_attempts" in request:
        try:
            data["max_attempts"] = int(request.pop("max_attempts"))
        except ValueError as error:
            raise ValueError("max-attempts requires an integer.") from error
    return validate_action({**data, "request": request})


def _reject_reserved(fields: dict[str, Any]) -> None:
    if fields.keys() & {"operation", "project"}:
        raise ValueError("operation and project are reserved fields; use the command verb and currently loaded project.")
