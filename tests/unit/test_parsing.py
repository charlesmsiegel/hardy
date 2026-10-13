import pytest

from hardy.agents.parsing import json_object


@pytest.mark.parametrize('text', [
    '```lean\nexample : {x : Nat // x > 0} := sorry\n```\n```json\n{"a": 1}\n```',
    'The set {x | x > 0 is open.\n{"a": 1}',
    r'LaTeX \{ x \} then {"a": 1}',
    'prose {"a": 1} after',
    '```JSON  \n{"a": 1}\n```',
    '```json\n{"a": 1}',
])
def test_finds_json_after_mathematical_braces(text):
    assert json_object(text) == '{"a": 1}'


def test_json_fence_takes_priority_over_prose_objects():
    assert json_object('{"wrong": 0}\n```json\n{"a": 1}\n```') == '{"a": 1}'


def test_preserves_braces_and_escaped_quotes_in_strings():
    text = r'{"source": "{x : Nat // x > 0}", "quote": "\""}'
    assert json_object(text) == text


def test_no_json_object():
    assert json_object('no answer {x : Nat // x > 0}') is None
