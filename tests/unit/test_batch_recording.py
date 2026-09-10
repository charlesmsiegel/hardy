"""Batch evidence survives process death and binds the completed snapshot."""
import importlib
import json
import subprocess
import sys

import pytest
import test_recorded_runs as fixtures

from hardy.workflows.recorded import validate_batch_consistency


def recording():
    return importlib.import_module("hardy.workflows.batch_recording")


def test_abrupt_exit_keeps_observed_raw_usage_and_incomplete_identity(tmp_path):
    child = '''import os,sys
from pathlib import Path
sys.path.insert(0,"tests/unit")
import test_recorded_runs as fixtures
class Dying(fixtures._Runtime):
    def ask(self,text):
        self.context["observe"]({"type":"result","cost_usd":0.25,"usage":{"input_tokens":100,"output_tokens":20},"session_id":"crash-fixture"})
        os._exit(23)
fixtures._Runtime=Dying
fixtures._batch(Path(sys.argv[1]), [])
'''
    child_result = subprocess.run([sys.executable, "-c", child, str(tmp_path)],
                                  capture_output=True, text=True, timeout=15)
    assert child_result.returncode == 23, child_result.stderr
    output = tmp_path / "run"
    assert (output / "attempt.json").is_file()
    report = recording().read_attempt(output)
    assert report["status"] == "incomplete" and report["issues"] == []
    assert report["manifest"]["plan"]["request"]["declaration"] == "theorem HardyTarget : True"
    assert report["runtime"]["model"] == "fake-model@test"
    assert report["events"][-1]["usage"] == {"input_tokens": 100, "output_tokens": 20}
    assert report["events"][-1]["cost_usd"] == 0.25


def test_completed_attempt_materializes_every_tool_and_usage_event(tmp_path):
    output = fixtures._verified(tmp_path)
    trajectory = json.loads((output / "trajectory.json").read_text())
    assert "attempt_receipt" in trajectory
    report = recording().read_attempt(output)
    assert report["status"] == "complete" and report["issues"] == []
    assert report["events"] == trajectory["events"]
    assert any(e.get("name") == "submit_proof" for e in report["events"])
    assert any(e.get("usage", {}).get("input_tokens") == 5 for e in report["events"])
    assert validate_batch_consistency(output) == ()


@pytest.mark.parametrize("attack", ["missing", "removed_receipt", "erased_journal", "event", "manifest", "snapshot", "result", "tail"])
def test_completed_reader_rejects_missing_or_changed_declared_attempt(tmp_path, attack):
    output = fixtures._verified(tmp_path)
    journal = output / "attempt.jsonl"
    assert journal.exists()
    if attack == "missing":
        journal.unlink()
    elif attack == "event":
        journal.write_bytes(journal.read_bytes().replace(b'"input_tokens":5', b'"input_tokens":6'))
    elif attack == "manifest":
        path = output / "attempt.json"
        value = json.loads(path.read_text())
        value["plan"]["limits"]["max_turns"] = 99
        path.write_text(json.dumps(value))
    elif attack == "result":
        path = output / "result.json"
        value = json.loads(path.read_text())
        value["model_response"] = "altered"
        path.write_text(json.dumps(value))
    elif attack == "tail":
        with journal.open("ab") as stream:
            stream.write(b'{"torn":')
    else:
        path = output / "trajectory.json"
        value = json.loads(path.read_text())
        if attack in {"removed_receipt", "erased_journal"}:
            value.pop("attempt_receipt")
            if attack == "erased_journal":
                journal.unlink()
                (output / "attempt.json").unlink()
        else:
            value["limits"]["context_window"] += 1
        path.write_text(json.dumps(value))
    assert any("attempt" in issue for issue in validate_batch_consistency(output))


def test_reusing_an_attempt_directory_refuses_before_a_second_model_call(tmp_path, monkeypatch):
    fixtures._verified(tmp_path)

    class Unexpected(fixtures._Runtime):
        def ask(self, text):
            pytest.fail("an existing attempt bought another model call")

    monkeypatch.setattr(fixtures, "_Runtime", Unexpected)
    with pytest.raises((ValueError, FileExistsError), match="attempt|exists"):
        fixtures._batch(tmp_path, [])


def test_legacy_unjournaled_attempt_is_explicitly_unknown(tmp_path):
    output = fixtures._verified(tmp_path)
    trajectory_path = output / "trajectory.json"
    trajectory = json.loads(trajectory_path.read_text())
    trajectory.pop("attempt_receipt")
    trajectory["schema_version"] = 1
    trajectory_path.write_text(json.dumps(trajectory))
    (output / "attempt.json").unlink()
    (output / "attempt.jsonl").unlink()
    assert recording().read_attempt(output)["status"] == "unknown"
    assert validate_batch_consistency(output) == ()
