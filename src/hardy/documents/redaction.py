"""What an export removes before anything is written (#83).

An export is made to leave the machine, so what it can carry is bounded and
stated: `prepare` removes values under key names that mean a credential, by
the same rule a trajectory is written under, and `redact` removes the common
token shapes in `SECRETS` from free text. Neither is a proof that a transcript
holds no secret -- nothing here can be -- and the page says so where a reader
will see it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

# The key-name rule a trajectory is already written under. Imported rather than
# restated so one list decides what counts as a credential for both.
from hardy.workflows.storage import SECRET_KEY, SECRET_NAME, SECRET_PREFIX
from hardy.workflows.storage import _redact as redact_payload

#: Token shapes worth removing from free text. Deliberately narrow: a pattern
#: broad enough to catch "anything that looks random" would eat the sha256
#: digests Hardy records on purpose, and an export missing its own provenance
#: is a worse artifact than one that names the limits of its redaction.
#: Each entry is (pattern, replacement, structural). `structural` marks a rule
#: that reads a `name: value` PAIR rather than recognising a token by its own
#: shape -- those are the ones `redact(keys=False)` drops for audited source,
#: because Lean writes a type ascription with the same colon. See `redact`.
SECRETS: tuple[tuple[re.Pattern[str], str, bool], ...] = (
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), "[REDACTED-KEY]", False),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"), "[REDACTED-KEY]", False),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "[REDACTED-KEY]", False),
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}"), "[REDACTED-KEY]", False),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED-KEY]", False),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"), "[REDACTED-KEY]", False),
    # A short token is still a token, and length was never the right question:
    # 16 let `Bearer abc123` through, 8 let the same six-character example
    # through while a comment right here claimed otherwise, and no floor at all
    # redacts "the bearer of bad news". What separates a credential from an
    # English word is not how long it is but what it is made of -- a digit or a
    # separator, which "of" and "news" do not have and `abc123`, `sk-...` and
    # every base64 fragment do. A long run of letters is taken too: a
    # hexadecimal token can be all letters, and no word here is sixteen.
    # The alphabet is base64's, not an identifier's: `Bearer abc+123/==` is an
    # ordinary encoded credential and a class of `[A-Za-z0-9._-]` stopped at the
    # `+`, leaving most of it on the page. JWTs and base64url add `~` and `=`.
    (
        re.compile(
            r"(?i)\bbearer\s+(?:"
            r"(?=[A-Za-z0-9._~+/=\-]*[0-9._~+/=\-])[A-Za-z0-9._~+/=\-]{3,}"
            r"|[A-Za-z0-9._~+/=\-]{16,})"
        ),
        "Bearer [REDACTED-KEY]",
        # A shape, not a pair: `Bearer <token>` names no key. It stays on for
        # audited source too, where it cannot occur by accident.
        False,
    ),
    # `Authorization: Basic dXNlcjpwYXNz` -- a pasted header, and the shape the
    # generic rule below gets exactly backwards: its unquoted alternative
    # matches `\S+`, which is the SCHEME, so it redacted the word "Basic" and
    # left the base64 credential standing. The scheme is kept (it is not a
    # secret and it tells a reader what was there) and what follows it goes.
    # The same header written as a quoted value, which is how it arrives when
    # somebody pastes a JSON payload or a log line. The closing quote is a
    # backreference to the opening one, and an escaped character is consumed
    # whole, so `"Digest username=\"x\", response=\"...\""` ends where the
    # value ends rather than at the first quote inside it -- which is where a
    # rule matching `"[^"]*"` stopped, leaving the response field standing.
    (
        re.compile(
            r"(?i)(?<![A-Za-z0-9_.-])(" + SECRET_PREFIX + r"authorization)"
            r"([\"\']?\s*[:=]\s*)([\"\'])"
            r"((?:basic|bearer|digest|negotiate|token|apikey)\s+)"
            r"(?:\\.|[^\r\n])*?\3"
        ),
        r"\1\2\3\4[REDACTED-KEY]\3",
        True,
    ),
    #
    # The value runs to the end of the line rather than to the next quote or
    # space. `Authorization: Digest username="Mufasa", realm="private",
    # nonce="...", response="..."` is one credential written as fields, and a
    # tail that stopped at the first quote redacted `username=` and left every
    # field after it standing. A header is a line, so the line is the value.
    #
    # Deliberately not matching a quoted value: `{"Authorization": "Digest
    # ..."}` has a quote between the separator and the scheme, so it falls
    # through to the generic rule below, which takes the whole quoted string
    # and does not run past the end of it into the rest of the payload.
    (
        re.compile(
            r"(?i)(?<![A-Za-z0-9_.-])(" + SECRET_PREFIX + r"authorization)(\s*[:=]\s*)"
            r"(basic|bearer|digest|negotiate|token|apikey)\s+[^\r\n]+"
        ),
        r"\1\2\3 [REDACTED-KEY]",
        True,
    ),
    # `api_key = "..."`, `password: ...`, `authorization=...` in prose or in a
    # pasted config. The key names are `storage.SECRET_KEY`'s, so one list
    # decides what counts as a credential for both the trajectory and this.
    #
    # The optional quote after the key name is what makes this cover the shape
    # a credential is actually pasted in. `{"api_key": "hunter2"}` is the
    # common case and the first version missed it outright: it required the
    # separator to follow the bare name, so a JSON snippet inside a message
    # went through untouched -- and the structural redactor cannot help there,
    # because the snippet is one string rather than a key of its own.
    (
        re.compile(
            r"(?i)(?<![A-Za-z0-9_.-])"
            # Only authorization headers have schemes. Earlier rules have
            # already removed their values; keep that scheme and placeholder.
            r"(?!" + SECRET_PREFIX
            + r"authorization[\"']?\s*[:=]\s*[\"']?"
            r"(?:basic|bearer|digest|negotiate|token|apikey)\s+\[REDACTED)"
            r"(" + SECRET_NAME + r")"
            # Not a value some earlier pattern has already replaced: the
            # scheme rule above leaves `Authorization: Basic [REDACTED-KEY]`,
            # and without this the generic rule matched it again and ate the
            # scheme the earlier rule had deliberately kept.
            r"([\"']?\s*[:=]\s*)(?!\[REDACTED)"
            # Escapes consumed whole, as the authorization rule above does:
            # `{"api_key": "he\\"re"}` ended the value at the escaped quote,
            # so the redaction replaced the prefix and left the rest of the
            # credential standing in the page.
            #
            # Unquoted, the value runs to the END OF THE LINE rather than to
            # the first space. A passphrase is allowed spaces and an unquoted
            # YAML scalar keeps them, so `password: correct horse battery
            # staple` lost one word and published the other three -- under a
            # `[REDACTED]` that told the reader it had been handled.
            #
            # The cost is real and is accepted deliberately: `the password:
            # hunter2 was rotated on Tuesday` now loses the rest of that line
            # too. Over-redaction announces itself -- `[REDACTED]` is right
            # there on the page and the reader knows something was removed --
            # while a surviving credential is invisible and cannot be undone
            # once the file is sent. Quoted values are matched first, so JSON
            # and a quoted YAML scalar still stop at their closing quote and
            # keep whatever follows on the line.
            # An unquoted value starts at its first non-space character;
            # separator whitespace remains outside the replaced value.
            r"(\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s\r\n][^\r\n]*)"
        ),
        r"\1\2[REDACTED]",
        True,
    ),
)

def redact(text: str, *, keys: bool = True) -> str:
    """Remove the credential shapes `SECRETS` names. See the module docstring.

    `keys=False` drops the rules that read a `name: value` pair, keeping only
    the ones that recognise a token by its own shape. That is the difference
    between free text and AUDITED SOURCE. Lean spells a type ascription with a
    colon, so `theorem secret : Nat` and `password : String` are ordinary Lean
    that the key/value rule rewrote to `[REDACTED]` -- and both `_results` and
    `_sources` print audited code, so the page displayed and badged a statement
    that was not the one the kernel checked. A page whose whole purpose is to
    say what was proved may not alter what was proved.

    The shape rules stay on everywhere: `sk-...`, `ghp_...` and the rest cannot
    occur in valid Lean by accident, so keeping them costs the source nothing.
    """
    for pattern, replacement, structural in SECRETS:
        if structural and not keys:
            continue
        text = pattern.sub(replacement, text)
    return text


#: Material keyed by a name the WORKSPACE chose -- a declaration, a module --
#: rather than by a field name. `storage.SECRET_KEY` matches a key exactly, so
#: a theorem called `secret` or a module called `Password` collides with it,
#: and the structural pass then replaces the thing itself. That has been found
#: three times now at three different maps: the statement was replaced, then
#: the automation tactic, then the whole audit RECORD -- which is a string
#: where a mapping is expected, so `/export` raised instead of writing a page.
#: Listed by the property they share rather than one at a time, because the
#: next map keyed by a workspace name will have it too.
#:
#: Not `tex`: its keys are paths, which the anchored rule cannot match, and
#: keeping it out keeps this list about key naming rather than about content.
#: What is rendered verbatim is a separate decision -- see `redact`.
NAMED_BY_WORKSPACE = frozenset(
    {"theorems", "lean", "audit", "automation", "shared", "shared_sources"}
)

#: The same problem one level down: a report event carries `statements`, keyed
#: by theorem name, and it is the only durable copy of what was reported.
NESTED_BY_WORKSPACE = frozenset({"statements"})


def prepare(material: Mapping[str, Any]) -> dict[str, Any]:
    """`material` with credential-shaped values removed from every nested key.

    Runs before the page is built, so the key-name rule that governs a
    trajectory (`storage._redact`) governs an export too. The text-level pass
    is `redact`, applied by every escaper on the way out.

    `NAMED_BY_WORKSPACE` is exempt, for the reason stated there: those maps are
    keyed by the names of the things they carry, so a theorem or a module that
    happens to be called `secret` is not a secret.
    """
    return {
        key: (
            _redact_around_names(value)
            if isinstance(value, (dict, list, tuple)) and key not in NAMED_BY_WORKSPACE
            else value
        )
        for key, value in material.items()
    }


def _redact_around_names(value: Any) -> Any:
    """`storage._redact`, stepping over the name-keyed maps nested inside.

    A walker of its own rather than a flag on the shared one: the trajectory
    that `storage` redacts has no such maps, and teaching the rule Hardy's
    whole record depends on about an exception it does not need would be the
    wrong place to carry this. What it does between those maps is the shared
    rule, called directly.
    """
    if isinstance(value, dict):
        return {
            str(key): (
                item
                if str(key) in NESTED_BY_WORKSPACE
                # `SECRET_KEY` itself, imported rather than restated: the rule
                # for what counts as a credential key has one definition, and
                # this walker exists to make an exception to WHERE it runs, not
                # to what it says.
                else "[REDACTED]"
                if SECRET_KEY.match(str(key))
                else _redact_around_names(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_around_names(item) for item in value]
    return redact_payload(value)
