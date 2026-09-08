"""Extract structured objects from model replies without interpreting them."""
from __future__ import annotations


def json_object(text: str) -> str | None:
    """Find the one JSON object in a model's reply, fence or no fence.

    Here rather than in one caller because two of them need it and neither
    owns it: the staged runtime reads structured proof submissions, and the
    interactive session reads an independent reader's verdict. A model that
    wraps its answer in a fence or a sentence has still answered; one that
    returns no object at all has not, and both callers treat that as a
    failure rather than as a default.
    """
    if not text:
        return None
    stripped = text.strip()
    if stripped.startswith("```"):
        body = stripped.split("```")
        for part in body:
            candidate = part[4:] if part.startswith("json") else part
            found = _balanced_object(candidate)
            if found is not None:
                return found
    return _balanced_object(stripped)


def _balanced_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        character = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None
