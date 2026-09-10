"""Independent proof attempts share ceilings, but never provider contexts.

The coordinator alone chooses a winner after fresh formal verification. Every
attempt owns its files and cancellation handle; teardown drains all attempts
before collecting usage. A loser can cost money even when it produced no proof.
Named runtime operations must honor cancellation and bound in-flight calls.
Python worker threads are orchestration, not process isolation.
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import PurePosixPath
from queue import Empty, Queue
from threading import Event

from hardy.agents.usage import Usage
from hardy.formal.budget import BudgetExhausted, CheckBudget, ReservedBudget
from hardy.formal.verifier import VerificationResult
from hardy.workflows.contracts import ProofSubmission, RunPhase
from hardy.workflows.storage import RunStore
from hardy.workflows.strategies.contracts import ProofOutcome, ProofTask, Strategy, run_strategy


class AttemptCancelled(Exception):
    """One branch stopped; this does not cancel another provider context."""


@dataclass(frozen=True)
class AttemptContext:
    name: str
    store: RunStore
    budget: CheckBudget | ReservedBudget
    check_cancelled: Callable[[], None]


@dataclass(frozen=True)
class RaceAttempt:
    """An independently opened provider context and its bounded teardown.

    ``usage`` returns final cumulative Usage for this context after it drains,
    or None when even its exchange count is unavailable. ``cancel`` must be
    branch-local. Factories wire all tools/strategies to the supplied budget.
    """

    context_id: str
    strategy: Strategy
    cancel: Callable[[], None]
    usage: Callable[[], Usage | None]


class RaceStrategy:
    def __init__(
        self, *, names: Sequence[str],
        open_attempt: Callable[[AttemptContext], RaceAttempt],
        verify: Callable[[ProofTask, ProofSubmission, RunStore], VerificationResult],
        store: RunStore, budget: CheckBudget | ReservedBudget | None = None,
        check_cancelled: Callable[[], None] = lambda: None,
        active_elapsed: Callable[[], float] = lambda: 0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 2 <= len(names) <= 16 or len(set(names)) != len(names):
            raise ValueError("a race requires 2 to 16 distinct approach names")
        if any(not isinstance(name, str) or not name.strip() for name in names):
            raise ValueError("approach names must be nonempty text")
        self._names = tuple(names)
        self._open = open_attempt
        self._verify = verify
        self._store = store
        self._budget = budget
        self._cancelled = check_cancelled
        self._elapsed = active_elapsed
        self._clock = monotonic
        self.last_verification: VerificationResult | None = None

    def run(self, task: ProofTask) -> ProofOutcome:
        race_path = self._store.path / "race"
        race_path.mkdir(parents=True, exist_ok=False)
        budget = self._budget or CheckBudget(
            official_checks=task.limits.official_checks,
            active_seconds=task.limits.active_seconds,
            proof_seconds=task.limits.proof_seconds,
            active_elapsed=self._elapsed, monotonic=self._clock,
        )
        self.last_verification = None
        budget.validate_limits(official_checks=task.limits.official_checks,
                               active_seconds=task.limits.active_seconds,
                               proof_seconds=task.limits.proof_seconds)
        attempts: dict[str, RaceAttempt] = {}
        stopped: dict[str, Event] = {}
        records: dict[str, dict] = {}
        futures: dict[Future, str] = {}
        completed: Queue[Future] = Queue()
        winner = None
        outcome = ProofOutcome(task=task, status="exhausted", detail="No attempt verified.")
        pool = ThreadPoolExecutor(max_workers=len(self._names), thread_name_prefix="hardy-proof")

        def stop(name: str) -> None:
            if stopped[name].is_set():
                return
            stopped[name].set()
            try:
                attempts[name].cancel()
            except Exception as error:
                records[name]["cancellation_error"] = f"{type(error).__name__}: {error}"

        def collect(future: Future, name: str) -> ProofOutcome | None:
            try:
                result = future.result()
            except AttemptCancelled:
                records[name]["status"] = "cancelled"
            except BaseException as error:
                records[name].update(status="failed", error=f"{type(error).__name__}: {error}")
            else:
                records[name].update(status=result.status, outcome=result.model_dump(mode="json"))
                return result
            return None

        try:
            self._cancelled()
            budget.ensure()
            for index, name in enumerate(self._names):
                self._cancelled()
                budget.ensure()
                signal = Event()
                stopped[name] = signal

                def check(signal=signal):
                    self._cancelled()
                    if signal.is_set():
                        raise AttemptCancelled

                child = RunStore.open(race_path / "attempts" / str(index),
                                      run_id=self._store.run_id)
                records[name] = {"status": "opening", "context_id": None,
                                 "artifacts": child.path.relative_to(self._store.path).as_posix()}
                try:
                    attempt = self._open(AttemptContext(name, child, budget.reserved(checks=1), check))
                except BaseException as error:
                    records[name].update(status="open_failed", error=f"{type(error).__name__}: {error}")
                    raise
                attempts[name] = attempt
                records[name].update(status="opened", context_id=attempt.context_id)
                if (not isinstance(attempt.context_id, str) or not attempt.context_id.strip() or sum(
                        item.context_id == attempt.context_id for item in attempts.values()) != 1
                        or sum(item.strategy is attempt.strategy for item in attempts.values()) != 1):
                    raise ValueError("race attempts require independent provider contexts and strategies")
            # Open and validate every context before any starts generating work.
            for name, attempt in attempts.items():
                self._cancelled()
                budget.ensure()
                future = pool.submit(run_strategy, attempt.strategy, task)
                futures[future] = name
                future.add_done_callback(completed.put)
            pending = set(futures)
            while pending:
                self._cancelled()
                budget.ensure()
                try:
                    future = completed.get(timeout=0.05)
                except Empty:
                    continue
                pending.remove(future)
                name = futures[future]
                candidate = collect(future, name)
                if candidate is None or candidate.submission is None:
                    continue
                outcome = ProofOutcome(task=task, status="partial", submission=candidate.submission,
                                       detail="Attempt retained without final acceptance.")
                if candidate.status != "submitted":
                    continue
                check_number = budget.acquire()
                self._store.write_json(PurePosixPath("race/candidate.json"), candidate)
                result = VerificationResult.model_validate(
                    self._verify(task, candidate.submission, self._store).model_dump())
                self._store.write_json(PurePosixPath(f"race/checks/{check_number}.json"),
                                       {"attempt": name, "result": result.model_dump(mode="json")})
                records[name]["final_verification"] = result.model_dump(mode="json")
                self._cancelled()
                if result.verified:
                    outcome = ProofOutcome(task=task, status="submitted",
                                           submission=candidate.submission, evidence=result.evidence)
                    self.last_verification = result
                    winner = name
                    break
        except BudgetExhausted:
            outcome = outcome.model_copy(update={"status": "exhausted",
                "evidence": None, "detail": "Shared race budget exhausted; retained partial attempt."})
        except BaseException as error:
            outcome = outcome.model_copy(update={
                "status": "cancelled" if isinstance(error, (KeyboardInterrupt, AttemptCancelled)) else "partial",
                "evidence": None, "detail": f"Race stopped: {type(error).__name__}: {error}"})
            raise
        finally:
            for name in attempts:
                if name != winner:
                    stop(name)
            pool.shutdown(wait=True)
            for future, name in futures.items():
                if "outcome" not in records[name] and records[name]["status"] not in {"failed", "cancelled"}:
                    collect(future, name)
            usages = [None for name in records if name not in attempts]
            for name, attempt in attempts.items():
                try:
                    usage = attempt.usage()
                    if usage is not None:
                        usage = Usage.from_dict(usage.as_dict()) if isinstance(usage, Usage) else None
                        if usage is None:
                            records[name]["usage_error"] = "Malformed usage report; accounting unknown."
                except Exception as error:
                    records[name]["usage_error"] = f"{type(error).__name__}: {error}"
                    usage = None
                records[name]["usage"] = usage.as_dict() if usage else None
                usages.append(usage)
            report = {"winner": winner, "outcome": outcome.model_dump(mode="json"),
                      "attempts": records, "usage": _total_usage(usages),
                      "official_checks_used": budget.checks}
            self._store.write_json(PurePosixPath("race/outcome.json"), report)
            self._store.append("race.outcome", report, phase=RunPhase.PROVING)
        return outcome


def _total_usage(usages: Sequence[Usage | None]) -> dict:
    """Sum separate context totals; do not difference unrelated session reports."""
    totals = {"unknown_attempts": sum(usage is None for usage in usages)}
    for field in ("cost_usd", *Usage.COUNTERS):
        known = [usage for usage in usages if usage is not None]
        stated = [usage for usage in known if usage.reports.get(field, 0) > 0
                  and isinstance(getattr(usage, field), (int, float))
                  and not isinstance(getattr(usage, field), bool)
                  and math.isfinite(getattr(usage, field)) and getattr(usage, field) >= 0]
        totals[field] = {
            "value": sum(getattr(usage, field) for usage in stated) if stated else None,
            "reported": sum(usage.reports[field] for usage in stated),
            "exchanges": sum(usage.turns for usage in known),
        }
    return totals
