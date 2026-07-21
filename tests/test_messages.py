from hardy.lean.messages import CommandResponse, TacticResponse

RAW_COMMAND = {
    "env": 1,
    "messages": [
        {
            "severity": "error",
            "pos": {"line": 1, "column": 8},
            "endPos": {"line": 1, "column": 13},
            "data": "unknown identifier 'sqrt2'",
        }
    ],
    "sorries": [
        {
            "pos": {"line": 2, "column": 2},
            "endPos": {"line": 2, "column": 7},
            "goal": "⊢ 2 + 2 = 4",
            "proofState": 0,
        }
    ],
}


def test_command_response_parses_camel_case():
    resp = CommandResponse.model_validate(RAW_COMMAND)
    assert resp.env == 1
    assert resp.messages[0].severity == "error"
    assert resp.messages[0].pos.line == 1
    assert resp.messages[0].end_pos.column == 13
    assert resp.messages[0].data == "unknown identifier 'sqrt2'"
    assert resp.sorries[0].proof_state == 0
    assert resp.sorries[0].goal == "⊢ 2 + 2 = 4"


def test_command_response_defaults_empty():
    resp = CommandResponse.model_validate({"env": 0})
    assert resp.messages == []
    assert resp.sorries == []
    assert resp.message is None


def test_fatal_repl_level_message():
    # The repl answers e.g. {"message": "unknown environment 99"} with no env.
    resp = CommandResponse.model_validate({"message": "unknown environment 99"})
    assert resp.env is None
    assert resp.message == "unknown environment 99"


def test_tactic_response():
    resp = TacticResponse.model_validate({"proofState": 3, "goals": ["⊢ True"]})
    assert resp.proof_state == 3
    assert resp.goals == ["⊢ True"]


def test_unknown_fields_ignored():
    # Forward-compat: newer repl versions may add fields; we must not explode.
    resp = CommandResponse.model_validate({"env": 0, "somethingNew": 42})
    assert resp.env == 0
