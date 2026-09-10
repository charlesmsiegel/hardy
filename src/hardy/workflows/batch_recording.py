"""Durable batch observations, with a completed snapshot bound to their journal.

A crash leaves an attributed incomplete attempt. A completed receipt binds the
same observations and final result; it does not authenticate a provider's model
weights or turn an unverified mathematical result into a verified one.
"""
from __future__ import annotations

import json
import os
import platform
import sys
import threading
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from hardy.foundation.files import WriteGuard
from hardy.foundation.values import json_digest

MANIFEST = "attempt.json"
JOURNAL = "attempt.jsonl"
RUNTIME_FIELDS = ("model", "backend", "endpoint", "output_limit")


@lru_cache(maxsize=1)
def worker_identity() -> dict[str, Any]:
    """Record local launcher/interpreter metadata; runtime closure remains incomplete."""
    return {"python": sys.version, "platform": platform.platform(),
            "executable_sha256": sha256(Path(sys.executable).read_bytes()).hexdigest(),
            "provider_model_revision": "not established", "provider_sdk_identity": "not established"}


def _copy(value: Any) -> Any:
    # Own every recorded value, preserving raw usage fields exactly. The
    # caller must supply its existing observation payload, not credentials.
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


class BatchRecorder:
    def __init__(self, directory: Path, plan: dict[str, Any]):
        self.guard = WriteGuard(directory, create=True)
        if any(self.guard.path(name).exists() for name in (MANIFEST, JOURNAL, "trajectory.json", "result.json")):
            raise ValueError("this output directory already contains an attempt; choose a fresh directory")
        from hardy.evals.identity import run_source_digest_of

        self.manifest = {"schema": "hardy.batch-attempt/v1", "attempt_id": str(uuid4()),
                         "plan": _copy(plan), "source_sha256": run_source_digest_of(),
                         "worker": worker_identity()}
        self.manifest_digest = json_digest(self.manifest)
        self.events: list[dict[str, Any]] = []
        self.runtime: dict[str, Any] | None = None
        self.sequence = 0
        self.previous: str | None = None
        self.closed = False
        self.lock = threading.RLock()
        # Exclusive creation claims this attempt before any capability work;
        # another process cannot initialize the same directory concurrently.
        with self.guard.open(MANIFEST, "x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(self.manifest, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        self._append("start", {"manifest_digest": self.manifest_digest})

    def _append(self, kind: str, payload: dict[str, Any]) -> None:
        if self.closed:
            raise ValueError("the batch attempt journal is closed")
        event = {"sequence": self.sequence, "previous": self.previous, "kind": kind,
                 "attempt_id": self.manifest["attempt_id"], "payload": _copy(payload)}
        digest = json_digest(event)
        try:
            with self.guard.open(JOURNAL, "a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps({**event, "digest": digest}, ensure_ascii=False,
                                        separators=(",", ":"), allow_nan=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            self.closed = True
            raise
        self.previous = digest
        self.sequence += 1

    def append(self, event: dict[str, Any]) -> None:
        with self.lock:
            owned = _copy(event)
            self._append("observation", owned)
            self.events.append(owned)

    def bind_runtime(self, provenance: dict[str, Any]) -> None:
        with self.lock:
            if self.runtime is not None:
                raise ValueError("the batch attempt already names its runtime")
            self.runtime = _copy({key: provenance[key] for key in RUNTIME_FIELDS if key in provenance})
            self._append("runtime", self.runtime)

    def finish(self, trajectory: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if self.runtime is None or trajectory["events"] != self.events:
                raise ValueError("the batch snapshot does not describe its recorded attempt")
            self._append("finish", {"trajectory_digest": json_digest(trajectory),
                                    "result_digest": json_digest(result)})
            self.closed = True
            with self.guard.open(JOURNAL, "rb") as stream:
                digest = sha256(stream.read()).hexdigest()
            return {"schema": "hardy.batch-attempt-receipt/v1", "attempt_id": self.manifest["attempt_id"],
                    "manifest_digest": self.manifest_digest, "journal_sha256": digest}


def read_attempt(directory: Path) -> dict[str, Any]:
    """Read complete evidence or the intact prefix of an interrupted attempt."""
    report: dict[str, Any] = {"status": "unknown", "issues": [], "events": [], "runtime": None,
                              "manifest": None}
    try:
        guard = WriteGuard(directory)
        trajectory = None
        if guard.path("trajectory.json").exists():
            with guard.open("trajectory.json", encoding="utf-8") as stream:
                trajectory = json.load(stream)
        receipt = trajectory.get("attempt_receipt") if isinstance(trajectory, dict) else None
        declared = isinstance(trajectory, dict) and trajectory.get("schema_version", 1) == 2
        if not any(guard.path(name).exists() for name in (MANIFEST, JOURNAL)) and receipt is None and not declared:
            return report
        report["status"] = "incomplete"
        with guard.open(MANIFEST, encoding="utf-8") as stream:
            manifest = json.load(stream)
        if manifest["schema"] != "hardy.batch-attempt/v1":
            raise ValueError("unknown manifest schema")
        report["manifest"] = manifest
        if not guard.path(JOURNAL).exists() and receipt is None and not declared:
            return report
        with guard.open(JOURNAL, "rb") as stream:
            raw = stream.read()
        lines = raw.splitlines(keepends=True)
        torn = bool(lines and not lines[-1].endswith(b"\n"))
        if torn:
            lines.pop()
        previous = None
        finish = None
        for sequence, line in enumerate(lines):
            event = json.loads(line)
            if set(event) != {"sequence", "previous", "kind", "attempt_id", "payload", "digest"}:
                raise ValueError("invalid journal envelope")
            value = {key: val for key, val in event.items() if key != "digest"}
            if (event["sequence"] != sequence or event["previous"] != previous
                    or event["attempt_id"] != manifest["attempt_id"] or event["digest"] != json_digest(value)):
                raise ValueError("journal identity, sequence or content mismatch")
            previous = event["digest"]
            kind, payload = event["kind"], event["payload"]
            if finish is not None:
                raise ValueError("journal contains events after completion")
            if sequence == 0:
                if kind != "start" or payload != {"manifest_digest": json_digest(manifest)}:
                    raise ValueError("journal does not bind its initial manifest")
            elif kind == "observation":
                report["events"].append(payload)
            elif kind == "runtime":
                if report["runtime"] is not None:
                    raise ValueError("journal repeats its runtime identity")
                report["runtime"] = payload
            elif kind == "finish":
                finish = payload
            else:
                raise ValueError("unknown journal event")
        if finish is None:
            if receipt is not None:
                raise ValueError("completed snapshot names an incomplete journal")
            return report
        if torn:
            raise ValueError("completed journal has a torn trailing event")
        expected = {"schema": "hardy.batch-attempt-receipt/v1", "attempt_id": manifest["attempt_id"],
                    "manifest_digest": json_digest(manifest), "journal_sha256": sha256(raw).hexdigest()}
        if receipt != expected:
            raise ValueError("completed snapshot has a missing or changed attempt receipt")
        snapshot = {key: val for key, val in trajectory.items() if key != "attempt_receipt"}
        if json_digest(snapshot) != finish["trajectory_digest"] or snapshot["events"] != report["events"]:
            raise ValueError("completed snapshot differs from recorded observations")
        if {key: snapshot[key] for key in RUNTIME_FIELDS if key in snapshot} != report["runtime"]:
            raise ValueError("completed snapshot names another runtime")
        plan = manifest["plan"]
        if any(snapshot.get(key) != plan.get(key) for key in ("request", "toolchain", "lean_command", "lean_project")):
            raise ValueError("completed snapshot differs from its initial plan")
        if any(snapshot["limits"].get(key) != val for key, val in plan["limits"].items()):
            raise ValueError("completed limits differ from their initial plan")
        with guard.open("result.json", encoding="utf-8") as stream:
            result = json.load(stream)
        if json_digest(result) != finish["result_digest"]:
            raise ValueError("completed result differs from the journal")
        report["status"] = "complete"
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        report["status"] = "invalid"
        report["issues"].append(f"attempt journal: {error}")
    return report


def attempt_record_issues(directory: Path) -> tuple[str, ...]:
    report = read_attempt(directory)
    if report["status"] == "incomplete":
        return ("attempt journal is incomplete",)
    return tuple(report["issues"])
