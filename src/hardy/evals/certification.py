"""Prospective fixed-scope declarations and read-only observed-first-k reports.

A certificate concerns the named verifier-call cap and the existing recorded
Lean/canonical trust boundary. It is not an independent kernel replay, an IID
estimator, a total Lean-process cap, or a guarantee about provider billing.
Every declared slot survives interruption; missing evidence cannot reduce n.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from collections import Counter
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any, Self
from uuid import uuid4

from pydantic import Field, model_validator

from hardy.corpus.catalog import load_corpus, manifest_digest
from hardy.evals.contracts import Row, Scoreboard
from hardy.evals.scoreboard import (
    _canonical_issues,
    _condition_issues,
    _entry_issues,
    _nested_run,
    batch_row,
    scoreboard_self_issues,
    staged_row,
)
from hardy.evals.selection import select
from hardy.evals.sweep import Baseline
from hardy.foundation.files import WriteGuard
from hardy.foundation.values import FrozenModel, json_digest
from hardy.workflows.attempt_context import EvaluationAttempt

DECLARATION = "certification.json"
JOURNAL = "certification.jsonl"


class CertificationInputsChanged(ValueError):
    """Exact declared inputs are unavailable; no reduced-universe rate is safe."""


class CertificationBudget(FrozenModel):
    independent_verifier_calls: int = Field(ge=1, strict=True)
    ks: tuple[Annotated[int, Field(ge=1, strict=True)], ...] = (1,)
    hard_tokens: int | None = Field(default=None, ge=0, strict=True)
    hard_cost_usd: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def valid_ks(self) -> Self:
        if not self.ks or any(type(k) is not int or k < 1 for k in self.ks) or len(set(self.ks)) != len(self.ks):
            raise ValueError("k must be distinct positive integers")
        return self


def _plan(board: Scoreboard, entries: Any, budget: CertificationBudget) -> dict[str, Any]:
    return {"schema": "hardy.certification/v1", "declaration_id": str(uuid4()),
            "budget": budget.model_dump(mode="json"), "condition": board.condition.model_dump(mode="json"),
            "environment": board.environment.model_dump(mode="json"), "host": board.host,
            "problems_sha256": board.problems_sha256, "baseline_sha256": board.baseline_sha256,
            "entries": [entry.model_dump(mode="json") for entry in entries],
            "slots": [{"id": entry.id, "repeat": repeat,
                "mode": "batch" if entry.expected == "false" else board.condition.mode,
                "run_dir": f"runs/{entry.id}/{'batch' if entry.expected == 'false' else board.condition.mode}-{repeat}"}
                for entry in entries for repeat in range(board.condition.repeats)]}


def _files(directory: Path) -> dict[str, str]:
    files = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("certification artifacts cannot be symbolic links")
        if path.is_file():
            path.resolve().relative_to(directory.resolve())
            files[path.relative_to(directory).as_posix()] = sha256(path.read_bytes()).hexdigest()
    return files


def _contained(directory: Path, relative: str) -> Path:
    path = directory / relative
    path.resolve().relative_to(directory.resolve())
    return path


def _invalid_row(entry: Any, tier: int, slot: dict[str, Any]) -> Row:
    return Row(**slot, tier=tier, twin_of=entry.twin_of, expected=entry.expected, outcome="invalid",
               terminal_reason=None, cost_usd=None, exchanges=None, turns=None, wall_seconds=None,
               lean_checks=0, search_calls=0)


class CertificationRecorder:
    """Owned by run_set: seal before executors, record slots before/after IO."""
    def __init__(self, directory: Path, board: Scoreboard, entries: Any, budget: CertificationBudget, workers: int):
        budget = CertificationBudget.model_validate(budget.model_dump())
        if max(budget.ks) > board.condition.repeats:
            raise ValueError("k exceeds the declared repeat count")
        self.guard = WriteGuard(directory)
        self.plan = _plan(board, entries, budget)
        self.plan["workers"] = workers
        self.lock = threading.Lock()
        self.started = time.monotonic()
        self.sequence, self.previous = 0, None
        self.contexts = [EvaluationAttempt(declaration_sha256=json_digest(self.plan), slot=index,
            nonce=str(uuid4()), problem_id=slot["id"], repeat=slot["repeat"],
            statement_sha256=next(e.statement_digest() for e in entries if e.id == slot["id"]),
            prompt_sha256=next(e.prompt_digest() for e in entries if e.id == slot["id"]))
            for index, slot in enumerate(self.plan["slots"])]
        with self.guard.open(DECLARATION, "x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(self.plan, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        self._append("declaration", {"sha256": json_digest(self.plan)})

    def _append(self, kind: str, payload: dict[str, Any]) -> None:
        with self.lock:
            event = {"sequence": self.sequence, "previous": self.previous,
                     "kind": kind, "seconds": time.monotonic() - self.started, "payload": payload}
            event["digest"] = json_digest(event)
            with self.guard.open(JOURNAL, "a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            self.sequence += 1
            self.previous = event["digest"]

    def start_attempt(self, index: int) -> None:
        slot = self.plan["slots"][index]
        if _contained(self.guard.directory, slot["run_dir"]).exists():
            raise ValueError("a prospective attempt requires a fresh artifact directory")
        self._append("start_attempt", {"slot": index, "context": self.contexts[index].model_dump(mode="json")})

    def finish_attempt(self, index: int, error: str | None) -> None:
        self._append("finish_attempt", {"slot": index, "error": error,
            "artifacts": _files(_contained(self.guard.directory, self.plan["slots"][index]["run_dir"]))})

    def finish(self, interrupted: bool) -> None:
        self._append("finish", {"interrupted": interrupted,
            "scoreboard_sha256": sha256(self.guard.path("scoreboard.json").read_bytes()).hexdigest()})


def _journal(directory: Path, plan: dict[str, Any]) -> tuple[dict[int, dict[str, Any]], dict[str, Any] | None]:
    raw = (directory / JOURNAL).read_bytes()
    if not raw.endswith(b"\n"):
        raise ValueError("certification journal has an incomplete tail")
    events = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
    previous, elapsed, finished = None, 0.0, None
    attempts: dict[int, dict[str, Any]] = {}
    for number, event in enumerate(events):
        if not isinstance(event, dict) or set(event) != {"sequence", "previous", "kind", "seconds", "payload", "digest"}:
            raise ValueError("invalid certification event")
        value = {k: v for k, v in event.items() if k != "digest"}
        seconds = event["seconds"]
        if (type(event["sequence"]) is not int or event["sequence"] != number or event["previous"] != previous
                or event["digest"] != json_digest(value) or finished is not None
                or not isinstance(seconds, (int, float)) or isinstance(seconds, bool)
                or not math.isfinite(seconds) or seconds < elapsed):
            raise ValueError("certification sequence/content/time mismatch")
        previous, elapsed = event["digest"], seconds
        payload, kind = event["payload"], event["kind"]
        if not isinstance(payload, dict):
            raise ValueError("invalid certification payload")
        if number == 0:
            if kind != "declaration" or payload != {"sha256": json_digest(plan)}:
                raise ValueError("certification declaration differs from its start seal")
        elif kind in {"start_attempt", "finish_attempt"}:
            expected_keys = {"slot", "context"} if kind == "start_attempt" else {"slot", "error", "artifacts"}
            if set(payload) != expected_keys:
                raise ValueError("certification attempt payload has unexpected fields")
            index = payload.get("slot")
            if type(index) is not int or not 0 <= index < len(plan["slots"]):
                raise ValueError("certification event names an undeclared slot")
            if kind == "start_attempt":
                if index in attempts:
                    raise ValueError("certification repeats an attempt start")
                context = EvaluationAttempt.model_validate(payload.get("context"))
                slot = plan["slots"][index]
                if (context.declaration_sha256 != json_digest(plan) or context.slot != index
                        or context.problem_id != slot["id"] or context.repeat != slot["repeat"]):
                    raise ValueError("prospective context differs from declared slot")
                if sum("finish" not in attempt for attempt in attempts.values()) >= plan["workers"]:
                    raise ValueError("certification records more active attempts than declared workers")
                if context.nonce in {attempt["context"]["nonce"] for attempt in attempts.values()}:
                    raise ValueError("certification repeats a prospective nonce")
                attempts[index] = {"start": seconds, "context": context.model_dump(mode="json")}
            else:
                if index not in attempts or "finish" in attempts[index]:
                    raise ValueError("certification finish lacks one preceding start")
                attempts[index].update(finish=seconds, **payload)
        elif kind == "finish":
            if set(payload) != {"interrupted", "scoreboard_sha256"}:
                raise ValueError("certification finish payload has unexpected fields")
            if payload.get("scoreboard_sha256") != sha256((directory / "scoreboard.json").read_bytes()).hexdigest():
                raise ValueError("certification final scoreboard differs from its seal")
            if type(payload.get("interrupted")) is not bool:
                raise ValueError("certification finish lacks an interruption status")
            if not payload["interrupted"] and (len(attempts) != len(plan["slots"])
                    or any("finish" not in attempt for attempt in attempts.values())):
                raise ValueError("completed certification lacks declared attempt receipts")
            finished = {**payload, "seconds": seconds}
        else:
            raise ValueError("unknown certification event")
    if not events:
        raise ValueError("empty certification journal")
    return attempts, finished


def _statistics(attempts: list[dict[str, Any]], entries: list[dict[str, Any]], ks: tuple[int, ...]) -> dict[str, Any]:
    result = {}
    for k in ks:
        groups = [[a for a in attempts if a["id"] == entry["id"] and a["repeat"] < k]
                  for entry in entries if entry["expected"] == "true"]
        solved = sum(any(a["authenticated"] and a["outcome"] == "solved" for a in group) for group in groups)
        possible = sum(any(a["authenticated"] and a["outcome"] == "solved" for a in group)
                       or len(group) < k or any(not a["authenticated"] for a in group) for group in groups)
        eligible = all(len(group) == k and all(a["eligible"] for a in group) for group in groups)
        result[str(k)] = {"problems": len(groups), "solved": solved,
            "observed_lower": solved / len(groups) if groups else None,
            "observed_upper": possible / len(groups) if groups else None,
            "certified_value": solved / len(groups) if groups and eligible else None}
    return result


def certify(directory: Path, *, problems_path: Path, baseline_path: Path) -> dict[str, Any]:
    """Read only. A missing prospective declaration always yields provisional data."""
    from hardy.evals.compare import _row, _usage_totals

    directory = Path(directory)
    board = Scoreboard.model_validate_json((directory / "scoreboard.json").read_bytes())
    if (manifest_digest(problems_path) != board.problems_sha256
            or sha256(baseline_path.read_bytes()).hexdigest() != board.baseline_sha256):
        raise CertificationInputsChanged("exact recorded corpus/baseline inputs changed; original declared denominator is required")
    corpus = load_corpus(problems_path)
    baseline = Baseline.model_validate_json(baseline_path.read_bytes())
    selected = select(corpus, baseline, only=board.condition.selection.get("only"),
                      tiers=board.condition.selection.get("tiers"), twins=board.condition.selection.get("twins", True))
    issues = list(scoreboard_self_issues(directory, problems_path=problems_path, baseline_path=baseline_path))
    prospective = (directory / DECLARATION).exists()
    budget = CertificationBudget(independent_verifier_calls=1)
    plan = _plan(board, selected, budget)
    receipts, finished = {}, None
    if prospective:
        try:
            plan = json.loads((directory / DECLARATION).read_bytes())
            budget = CertificationBudget.model_validate(plan["budget"])
            expected = _plan(board, selected, budget)
            if (plan.get("entries") != expected["entries"] or plan.get("problems_sha256") != expected["problems_sha256"]
                    or plan.get("baseline_sha256") != expected["baseline_sha256"]):
                raise CertificationInputsChanged("prospective input universe differs; no reduced-universe statistic is reported")
            if any(plan.get(key) != value for key, value in expected.items() if key != "declaration_id"):
                raise ValueError("certification universe, slots or conditions differ from recorded inputs")
            if type(plan.get("workers")) is not int or plan["workers"] < 1:
                raise ValueError("certification requires a positive worker count")
            if max(budget.ks) > board.condition.repeats:
                raise ValueError("k exceeds declared repeats")
            receipts, finished = _journal(directory, plan)
            if finished is not None and finished["interrupted"] != board.interrupted:
                raise ValueError("certification interruption differs from its scoreboard")
        except CertificationInputsChanged:
            raise
        except (OSError, ValueError, KeyError, TypeError) as error:
            issues.append(str(error))
    elif (directory / JOURNAL).exists():
        issues.append("certification journal lacks its declaration")
    # Reconstruct slots from the authenticated board selection even when the
    # declaration was edited: a forged list must not erase a failed attempt.
    slots = _plan(board, selected, budget)["slots"]
    attempts = []
    identities: dict[str, int] = {}
    for index, slot in enumerate(slots):
        entry = corpus.by_id(slot["id"])
        path = directory / slot["run_dir"]
        receipt = receipts.get(index, {})
        reasons = list(issues)
        sealed = prospective and not issues and "finish" in receipt
        safe_path = True
        try:
            _contained(directory, slot["run_dir"])
        except ValueError:
            safe_path, sealed = False, False
            reasons.append("attempt artifact directory escapes the scoreboard")
        if not prospective:
            reasons.append("no prospective declaration")
        if "finish" not in receipt:
            execution = "incomplete" if "start" in receipt else "not_started"
            reasons.append("attempt lacks a sealed completion")
        else:
            execution = "failed" if receipt.get("error") is not None else "complete"
            try:
                if not safe_path or receipt.get("artifacts") != _files(path):
                    raise ValueError("attempt artifacts differ from their completion seal")
            except (OSError, ValueError) as error:
                reasons.append(str(error))
                sealed = False
            if receipt.get("error") is not None:
                reasons.append("executor failed: " + str(receipt["error"]))
        row = (_invalid_row(entry, baseline.entries[entry.id].tier, slot) if not safe_path else
               batch_row(entry, baseline.entries[entry.id].tier, path, directory, repeat=slot["repeat"])
               if slot["mode"] == "batch" else staged_row(entry, baseline.entries[entry.id].tier, path, directory, repeat=slot["repeat"]))
        artifact_issues = []
        if row.outcome != "invalid":
            artifact_issues.extend(_entry_issues(entry, row, path))
            artifact_issues.extend(_condition_issues(row, path, board.condition, board.environment, row.run_dir))
            if row.mode == "staged":
                artifact_issues.extend(_canonical_issues(entry, path, row.run_dir, condition=board.condition))
        authenticated = row.outcome != "invalid" and not artifact_issues and not issues
        data = _row(directory, row, authenticated=authenticated)
        reasons.extend(artifact_issues)
        if not authenticated:
            reasons.append("outcome artifacts are not authenticated")
        data.update(execution=execution, provisional_reasons=reasons, attempt_id=None, claim_sha256=None,
                    provider_budget=None, family=None, domain=entry.msc[0][:2], sealed=sealed,
                    input_statement_sha256=entry.statement_digest(), input_prompt_sha256=entry.prompt_digest(),
                    artifact_seal_sha256=json_digest(receipt["artifacts"]) if receipt.get("artifacts") is not None else None)
        if authenticated:
            if row.mode == "staged":
                nested = _nested_run(path)
                manifest = json.loads((nested / "manifest.json").read_bytes())
                data["attempt_id"], data["claim_sha256"] = manifest["run_id"], manifest["claim_sha256"]
                data["recorded_limits"] = manifest["limits"]
                events = [json.loads(line) for line in (nested / "trajectory.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
                contexts = [e.get("payload") for e in events if e.get("kind") == "workflow.evaluation_attempt"]
                context_path = nested / "evaluation-attempt.json"
                if (not receipt.get("context") or contexts != [receipt["context"]]
                        or not context_path.is_file()
                        or manifest["artifacts"].get("evaluation-attempt.json") != sha256(context_path.read_bytes()).hexdigest()
                        or json.loads(context_path.read_bytes()) != receipt["context"]
                        or receipt["context"]["statement_sha256"] != entry.statement_digest()
                        or receipt["context"]["prompt_sha256"] != entry.prompt_digest()):
                    reasons.append("run lacks its authenticated prospective attempt context")
                    data["sealed"] = False
                context_positions = [i for i, e in enumerate(events) if e.get("kind") == "workflow.evaluation_attempt"]
                work_positions = [i for i, e in enumerate(events) if e.get("kind", "").startswith(("claude.", "codex."))]
                if context_positions and work_positions and context_positions[0] >= min(work_positions):
                    reasons.append("prospective context was recorded after provider work")
                    data["sealed"] = False
                if (manifest["limits"].get("official_checks") != budget.independent_verifier_calls
                        or data["independent_verifier_calls"] is None
                        or data["independent_verifier_calls"] > budget.independent_verifier_calls):
                    reasons.append("independent_verifier_calls budget lacks matching complete enforcement evidence")
            else:
                trajectory = json.loads((path / "trajectory.json").read_bytes())
                data["attempt_id"] = (trajectory.get("attempt_receipt") or {}).get("attempt_id")
                data["claim_sha256"] = json_digest(trajectory["request"])
                data["provider_budget"] = trajectory.get("provider_budget")
                from hardy.workflows.batch_recording import read_attempt

                attempt = read_attempt(path)
                if not receipt.get("context") or (attempt.get("manifest") or {}).get("plan", {}).get("evaluation_attempt") != receipt["context"]:
                    reasons.append("run lacks its authenticated prospective attempt context")
                    data["sealed"] = False
                reasons.append("batch records do not establish an independent_verifier_calls cap")
            if data["attempt_id"] is None:
                reasons.append("attempt identity is missing")
            elif data["attempt_id"] in identities:
                reasons.append("attempt identity is reused")
                earlier = attempts[identities[data["attempt_id"]]]
                earlier["eligible"] = False
                earlier["provisional_reasons"].append("attempt identity is reused")
            else:
                identities[data["attempt_id"]] = index
        if authenticated and safe_path and board.condition.exposure is not None:
            from hardy.evals.exposure import read_exposure

            exposure = read_exposure(path, plan=board.condition.exposure, problem_id=entry.id,
                repeat=row.repeat, run_procedure_digest=board.condition.run_procedure_digest, expected_digest=row.exposure_sha256)
            if not exposure.get("issues") and exposure.get("cohort") != "unknown":
                data["family"] = next(a.family for a in board.condition.exposure.assignments if a.problem_id == entry.id)
        for name in ("hard_tokens", "hard_cost_usd"):
            if getattr(budget, name) is not None:
                reasons.append(f"{name}: existing receipts do not establish a hard provider cap")
        data["eligible"] = not reasons
        attempts.append(data)
    entries = [e.model_dump(mode="json") for e in selected]
    measured = finished is not None and not finished["interrupted"] and not issues and all(a["sealed"] for a in attempts)
    elapsed = finished["seconds"] if measured else None
    occupied = sum(r["finish"] - r["start"] for r in receipts.values()) if measured else None
    costs = [(a.get("provider_budget") or {}).get("derived_cost_usd") for a in attempts]
    known_costs = [Decimal(value) for value in costs if value is not None]
    return {"schema": "hardy.certification-report/v1", "prospective": prospective, "issues": issues,
        "budget": budget.model_dump(mode="json") if prospective else None, "attempts": attempts,
        "declaration_sha256": json_digest(plan) if prospective and isinstance(plan, dict) else None,
        "scoreboard_sha256": sha256((directory / "scoreboard.json").read_bytes()).hexdigest(),
        "condition": board.condition.model_dump(mode="json"), "environment": board.environment.model_dump(mode="json"),
        "host": board.host, "canonical_entries": entries,
        "statistic": "observed success within first k declared attempts per problem; no IID estimate or confidence interval",
        "scope": "per-attempt frozen-claim independent verifier calls; not all Lean processes or provider spend",
        "trust": "existing recorded Lean verification and canonical review; no independent kernel replay",
        "statistics": _statistics(attempts, entries, budget.ks),
        "domains": {domain: _statistics([a for a in attempts if a["domain"] == domain],
            [e for e in entries if e["msc"][0][:2] == domain], budget.ks) for domain in sorted({a["domain"] for a in attempts})},
        "family_counts": dict(Counter(next((a["family"] for a in attempts if a["id"] == e["id"] and a["family"]), "unknown")
            for e in entries if e["expected"] == "true")),
        "failures": dict(Counter(a["terminal_reason"] or a["execution"] for a in attempts if a["outcome"] != "solved")),
        "measurements": {"proof_usage": _usage_totals(attempts, "proof_usage"),
            "canonical_review_usage": _usage_totals([a for a in attempts if a["mode"] == "staged"], "canonical_review_usage"),
            "lean_cpu_seconds": None, "scheduler_makespan_seconds": elapsed,
            "derived_tariff_cost_usd": {"value": str(sum(known_costs)) if known_costs else None,
                "complete_rows": len(known_costs), "missing_rows": len(attempts) - len(known_costs)},
            "scheduler_worker_utilization": occupied / (elapsed * plan["workers"]) if elapsed else None,
            "timing_scope": "producer start through board seal; worker occupancy is executor time, not CPU utilization"}}
