"""Ownership contracts independent of a model, Lean, or the terminal."""

from hardy.workflows.interactive.record import SessionRecord


def test_record_snapshots_cannot_mutate_durable_state(tmp_path):
    record = SessionRecord(tmp_path)
    record.load()
    record.state["names"].append({"formal_name": "original"})
    snapshot = record.snapshot()
    snapshot["names"][0]["formal_name"] = "changed"
    assert record.snapshot()["names"] == [{"formal_name": "original"}]
    record._save_state()
    reopened = SessionRecord(tmp_path)
    reopened.load()
    assert reopened.snapshot() == record.snapshot()


def test_record_recovers_result_tail_only_once(tmp_path):
    record = SessionRecord(tmp_path)
    record.load()
    record._record({"type": "result", "cost_usd": 0.25})
    recovered = record._recover_spend()
    assert recovered == record._recover_spend()
    assert record.local["usage_recovered_turns"] == 1
