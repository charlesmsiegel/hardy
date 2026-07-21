from hardy.lean.feedback import ProofVerdict, failure_verdict, verdict
from hardy.lean.messages import CommandResponse


def resp(**kwargs) -> CommandResponse:
    return CommandResponse.model_validate(kwargs)


MSG_ERROR = {
    "severity": "error",
    "pos": {"line": 1, "column": 0},
    "data": "type mismatch",
}
MSG_WARNING = {
    "severity": "warning",
    "pos": {"line": 1, "column": 0},
    "data": "unused variable `h`",
}
SORRY = {"pos": {"line": 2, "column": 2}, "goal": "⊢ True", "proofState": 0}


def test_clean_response_is_complete():
    v = verdict(resp(env=1))
    assert v.complete
    assert v.errors == [] and v.sorries == [] and v.failure is None


def test_warnings_do_not_block_completeness():
    v = verdict(resp(env=1, messages=[MSG_WARNING]))
    assert v.complete
    assert v.warnings[0].data == "unused variable `h`"


def test_error_blocks_completeness():
    v = verdict(resp(env=1, messages=[MSG_ERROR]))
    assert not v.complete
    assert v.errors[0].data == "type mismatch"


def test_sorry_blocks_completeness():
    v = verdict(resp(env=1, sorries=[SORRY]))
    assert not v.complete
    assert v.sorries[0].goal == "⊢ True"


def test_fatal_message_becomes_error():
    v = verdict(resp(message="unknown environment 99"))
    assert not v.complete
    assert any("unknown environment 99" in e.data for e in v.errors)


def test_missing_env_blocks_completeness():
    v = verdict(resp())
    assert not v.complete


def test_failure_verdicts():
    for kind in ("timeout", "crash"):
        v = failure_verdict(kind)
        assert not v.complete
        assert v.failure == kind
