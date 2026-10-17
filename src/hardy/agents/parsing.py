"""Extract structured objects from model replies."""
from __future__ import annotations

import json
import re

#: Opening braces tried per candidate before giving up on it. Mathematical
#: prose is full of braces that open no JSON, and each try is a decode from
#: that point, so an unbounded scan of a malformed reply is quadratic.
BRACES_PER_CANDIDATE = 200


def json_object(text: str) -> str | None:
    """Return the first valid object, preferring JSON fences, then other fences.

    Mathematical braces are skipped. Each candidate examines at most
    `BRACES_PER_CANDIDATE` brace positions to bound work on malformed replies.
    Unclosed fences are accepted.
    """
    fences = list(re.finditer(r'```([^\n`]*)\n(.*?)(?:```|\Z)', text, re.DOTALL))
    candidates = [match[2] for match in fences if match[1].strip().lower() == 'json']
    candidates.extend(match[2] for match in fences if match[1].strip().lower() != 'json')
    candidates.append(text)
    for candidate in candidates:
        found = _balanced_object(candidate)
        if found is not None:
            return found
    return None


def _balanced_object(text: str) -> str | None:
    """The text of the first JSON object that decodes whole from one of the
    first `BRACES_PER_CANDIDATE` opening braces in `text`, or None."""
    decoder = json.JSONDecoder()
    for attempt, match in enumerate(re.finditer(r'\{', text)):
        if attempt >= BRACES_PER_CANDIDATE:
            break
        try:
            value, end = decoder.raw_decode(text, match.start())
        except ValueError:
            continue
        if isinstance(value, dict):
            return text[match.start():end]
    return None
