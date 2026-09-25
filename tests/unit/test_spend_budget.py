"""Provider admission accounting, tested with scripted actual API calls."""
import importlib
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from hardy.agents.api import AnthropicProvider, ApiRuntime
from hardy.foundation.values import ToolResult


@pytest.fixture
def budget_module():
    try:
        return importlib.import_module("hardy.agents.spend_budget")
    except ModuleNotFoundError:
        pytest.fail("X5 spend budget is not implemented")


def policy(module, **changes):
    return module.SpendPolicy.model_validate({"id": "scripted estimate", "models": ["fixture"],
        "token_limit": 200, "input_characters_per_token": "4", "input_overhead_tokens": 0, **changes})


def request():
    return {"model": "fixture", "max_tokens": 10, "system": "", "messages": [], "tools": []}


def usage(tokens=1):
    return {"input_tokens": tokens, "output_tokens": 1,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}


def quote():
    """What `request()` reserves under `policy`: its characters over four, plus
    its output cap."""
    import math
    return math.ceil(len(json.dumps(request(), ensure_ascii=False, sort_keys=True)) / 4) + 10


class Client:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.messages = self
        self.requests = []

    def create(self, **value):
        self.requests.append(value)
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return SimpleNamespace(content=[], usage=response, stop_reason="end_turn")


def test_reservations_are_durable_shared_and_settle_exactly_once(tmp_path, budget_module):
    module = budget_module
    owner = module.SpendBudget(tmp_path / "budget.jsonl", policy(module, token_limit=70))
    with ThreadPoolExecutor(max_workers=4) as executor:
        reservations = list(executor.map(lambda _: attempt_reserve(module, owner), range(4)))
    kept = [r for r in reservations if r is not None]
    assert len(kept) == 2
    assert owner.summary()["pending"] == 2
    assert len((tmp_path / "budget.jsonl").read_text().splitlines()) >= 3
    owner.settle(kept[0], usage(4))
    owner.settle(kept[0], usage(4))
    assert owner.summary()["reported_tokens"] == 5 and owner.summary()["actual_tokens"] is None
    with pytest.raises(ValueError, match="different"):
        owner.settle(kept[0], usage(5))


def attempt_reserve(module, owner):
    try:
        return owner.reserve(request())
    except module.SpendLimitReached:
        return None


def test_restart_does_not_release_unsettled_liability(tmp_path, budget_module):
    """A reservation nobody settled -- a crashed process, or another live one
    -- stays charged at its quote after a restart. It is liability, not a
    reason to refuse everything: later calls are admitted while the headroom
    covers them, and refused on the limit once it does not (#190)."""
    module = budget_module
    value = policy(module, token_limit=3 * quote())
    owner = module.SpendBudget(tmp_path / "budget.jsonl", value)
    owner.reserve(request())
    resumed = module.SpendBudget(tmp_path / "budget.jsonl", value)
    resumed.reserve(request())
    resumed.reserve(request())
    with pytest.raises(module.SpendLimitReached, match="token_limit"):
        resumed.reserve(request())
    assert resumed.summary()["pending"] == 3
    assert resumed.summary()["actual_tokens"] is None


@pytest.mark.parametrize("reported", [None, {"input_tokens": 2}], ids=["missing", "partial"])
def test_missing_and_partial_usage_is_charged_at_the_reservation(tmp_path, budget_module, reported):
    """A call whose usage is unknown or incomplete keeps its reservation
    charged. It used to refuse every later call as `unknown_usage` -- one 429,
    or a gateway omitting the cache counters, disabled the model for the
    workspace for good (#190)."""
    module = budget_module
    value = policy(module, token_limit=2 * quote())
    owner = module.SpendBudget(tmp_path / "budget.jsonl", value)
    owner.settle(owner.reserve(request()), reported)
    owner.settle(owner.reserve(request()), reported)
    with pytest.raises(module.SpendLimitReached, match="token_limit"):
        owner.reserve(request())
    assert owner.summary()["ending_limit"] == "token_limit"
    assert owner.summary()["actual_tokens"] is None
    restarted = module.SpendBudget(tmp_path / "budget.jsonl", value)
    with pytest.raises(module.SpendLimitReached, match="token_limit"):
        restarted.reserve(request())


def test_a_partial_report_larger_than_its_quote_is_charged_what_it_reported(tmp_path, budget_module):
    """Never count less than was spent: where the counters that did arrive
    already exceed the quote, they are what is charged."""
    module = budget_module
    value = policy(module, token_limit=quote() + 149)
    owner = module.SpendBudget(tmp_path / "budget.jsonl", value)
    owner.settle(owner.reserve(request()), {"input_tokens": 150})
    with pytest.raises(module.SpendLimitReached, match="token_limit"):
        owner.reserve(request())


def test_a_reported_overrun_still_refuses_later_calls(tmp_path, budget_module):
    module = budget_module
    owner = module.SpendBudget(tmp_path / "budget.jsonl", policy(module, token_limit=10000))
    owner.settle(owner.reserve(request()), usage(500))
    with pytest.raises(module.SpendLimitReached, match="reservation_overrun"):
        owner.reserve(request())
    restarted = module.SpendBudget(tmp_path / "budget.jsonl", policy(module, token_limit=10000))
    with pytest.raises(module.SpendLimitReached, match="reservation_overrun"):
        restarted.reserve(request())


def test_actual_api_call_is_reserved_and_denied_call_is_not_a_provider_turn(tmp_path, budget_module):
    module = budget_module
    owner = module.SpendBudget(tmp_path / "budget.jsonl", policy(module, token_limit=0))
    client = Client([usage()])
    provider = AnthropicProvider("fixture", client=client, max_tokens=10)
    runtime = ApiRuntime("fixture", system_prompt="", specs=[], dispatch=lambda *_: ToolResult(True, ""),
                         provider=provider, spend_budget=owner)
    with pytest.raises(module.SpendLimitReached):
        runtime.ask("Hello")
    assert not client.requests and runtime.turns == 0


def test_api_failure_records_unknown_and_never_refunds(tmp_path, budget_module):
    """A transport failure may have been billed: it settles as unknown and is
    charged at its reservation, which admission keeps counting -- a later call
    is admitted while there is headroom, and refused on the limit after."""
    module = budget_module
    value = policy(module, token_limit=2 * quote())
    owner = module.SpendBudget(tmp_path / "budget.jsonl", value)
    client = Client([TimeoutError("transport"), usage(), usage()])
    provider = AnthropicProvider("fixture", client=client, max_tokens=10, spend_budget=owner)
    with pytest.raises(TimeoutError):
        provider.complete(system="", messages=[], specs=[])
    provider.complete(system="", messages=[], specs=[])
    with pytest.raises(module.SpendLimitReached, match="token_limit"):
        provider.complete(system="", messages=[], specs=[])
    assert len(client.requests) == 2 and owner.summary()["actual_tokens"] is None
    restarted = AnthropicProvider("fixture", client=client, max_tokens=10,
                                  spend_budget=module.SpendBudget(tmp_path / "budget.jsonl", value))
    with pytest.raises(module.SpendLimitReached, match="token_limit"):
        restarted.complete(system="", messages=[], specs=[])
    assert len(client.requests) == 2


def test_late_timeout_reply_cannot_refund_unknown_liability(tmp_path, budget_module):
    from threading import Event
    module = budget_module
    import math
    # The abandoned call's request carries its timeout, so its quote is a little larger.
    timed = math.ceil(len(json.dumps({**request(), "timeout": 0.02}, ensure_ascii=False, sort_keys=True)) / 4) + 10
    owner = module.SpendBudget(tmp_path / "budget.jsonl", policy(module, token_limit=timed + quote() + 1))
    started, release, finished = Event(), Event(), Event()
    class DelayedClient:
        def __init__(self):
            self.messages = self
        def create(self, **kwargs):
            started.set()
            assert release.wait(2)
            finished.set()
            return SimpleNamespace(content=[], usage=usage(), stop_reason="end_turn")
    provider = AnthropicProvider("fixture", client=DelayedClient(), max_tokens=10, spend_budget=owner)
    try:
        with pytest.raises(TimeoutError):
            provider.complete(system="", messages=[], specs=[], timeout=0.02)
        assert started.wait(1)
        before = owner.path.read_bytes()
    finally:
        release.set()
    assert finished.wait(1)
    assert owner.path.read_bytes() == before and owner.summary()["actual_tokens"] is None
    # The abandoned call stays charged at its quote: one more fits, a third does not.
    provider.complete(system="", messages=[], specs=[])
    with pytest.raises(module.SpendLimitReached, match="token_limit"):
        provider.complete(system="", messages=[], specs=[])
    assert owner.summary()["actual_tokens"] is None


def test_exact_token_boundary_and_cost_only_admission(tmp_path, budget_module):
    import math
    from decimal import Decimal
    module = budget_module
    quoted = math.ceil(len(json.dumps(request(), ensure_ascii=False, sort_keys=True)) / 4) + 10
    owner = module.SpendBudget(tmp_path / "tokens.jsonl", policy(module, token_limit=quoted))
    first = owner.reserve(request())
    with pytest.raises(module.SpendLimitReached, match="token_limit"):
        owner.reserve(request())
    owner.settle(first, {name: 0 for name in usage()})
    owner.reserve(request())
    tariff = {"id": "one dollar per token", "input_per_million": "1000000",
              "output_per_million": "1000000", "cache_read_per_million": "1000000",
              "cache_write_per_million": "1000000"}
    priced = module.SpendBudget(tmp_path / "cost.jsonl", policy(module, token_limit=None,
        cost_limit_usd=str(Decimal(quoted) - Decimal("0.01")), tariff=tariff))
    with pytest.raises(module.SpendLimitReached, match="cost_limit"):
        priced.reserve(request())
    assert priced.summary()["reservations"] == 0


@pytest.mark.parametrize("event", [None, [], 42, "bad"])
def test_malformed_journal_is_a_reader_issue_not_an_exception(tmp_path, budget_module, event):
    module = budget_module
    owner = module.SpendBudget(tmp_path / "provider-budget.jsonl", policy(module))
    record = owner.summary()
    with owner.path.open("a") as stream:
        stream.write(json.dumps(event) + "\n")
    assert module.budget_record_issues(tmp_path, record)


def test_read_only_budget_audit_does_not_create_files(tmp_path, budget_module):
    module = budget_module
    owner = module.SpendBudget(tmp_path / "provider-budget.jsonl", policy(module))
    record = owner.summary()
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert module.budget_record_issues(tmp_path, record) == ()
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_failed_settlement_write_keeps_the_call_charged_in_same_owner(tmp_path, monkeypatch, budget_module):
    """A settlement that could not be written leaves the reservation pending,
    and no longer this owner's to vouch for: it stays charged at its quote."""
    module = budget_module
    owner = module.SpendBudget(tmp_path / "budget.jsonl", policy(module, token_limit=2 * quote()))
    reservation = owner.reserve(request())
    append = owner._append
    def fail(events, kind, payload):
        if kind == "settle":
            raise OSError("disk full")
        append(events, kind, payload)
    monkeypatch.setattr(owner, "_append", fail)
    with pytest.raises(OSError):
        owner.settle(reservation, usage())
    owner.reserve(request())
    with pytest.raises(module.SpendLimitReached, match="token_limit"):
        owner.reserve(request())
    assert owner.summary()["actual_tokens"] is None


def test_overrun_is_recorded_even_when_no_later_call_is_requested(tmp_path, budget_module):
    module = budget_module
    owner = module.SpendBudget(tmp_path / "budget.jsonl", policy(module, token_limit=10000))
    reservation = owner.reserve(request())
    owner.settle(reservation, usage(80))
    assert owner.summary()["reservation_overruns"] == 1
    assert owner.summary()["actual_tokens"] == 81


def test_budget_and_runtime_consume_the_same_single_provider_usage_snapshot(tmp_path, budget_module):
    module = budget_module
    owner = module.SpendBudget(tmp_path / "budget.jsonl", policy(module))
    class Report:
        calls = 0
        def model_dump(self):
            self.calls += 1
            return usage(4 if self.calls == 1 else 100)
    report = Report()
    client = Client([report])
    provider = AnthropicProvider("fixture", client=client, max_tokens=10, spend_budget=owner)
    turn = provider.complete(system="", messages=[], specs=[])
    assert report.calls == 1
    assert turn.usage["input_tokens"] == 4 and owner.summary()["actual_tokens"] == 5


def test_usage_read_failure_leaves_unknown_liability(tmp_path, budget_module):
    module = budget_module
    owner = module.SpendBudget(tmp_path / "budget.jsonl", policy(module, token_limit=2 * quote()))
    class Report:
        def model_dump(self):
            raise ValueError("unreadable report")
    client = Client([Report(), usage(), usage()])
    provider = AnthropicProvider("fixture", client=client, max_tokens=10, spend_budget=owner)
    with pytest.raises(ValueError, match="unreadable"):
        provider.complete(system="", messages=[], specs=[])
    provider.complete(system="", messages=[], specs=[])
    with pytest.raises(module.SpendLimitReached, match="token_limit"):
        provider.complete(system="", messages=[], specs=[])
    assert len(client.requests) == 2 and owner.summary()["actual_tokens"] is None


def test_tool_continuations_and_compacted_input_each_reserve_actual_request(tmp_path, budget_module):
    from test_api_runtime import Block, FakeClient, Reply

    from hardy.agents.loop import Message
    module = budget_module
    class JsonBlock(Block):
        def model_dump(self):
            return vars(self)
    owner = module.SpendBudget(tmp_path / "budget.jsonl", policy(module, token_limit=10000))
    client = FakeClient([Reply([JsonBlock(type="tool_use", id="one", name="check", input={})], usage=usage()),
                         Reply([JsonBlock(type="text", text="done")], usage=usage(3))])
    provider = AnthropicProvider("fixture", client=client, max_tokens=10)
    calls = []
    def compact(messages):
        return [Message("user", "short input")] if len(messages) == 1 else None
    runtime = ApiRuntime("fixture", system_prompt="system", specs=[], provider=provider, spend_budget=owner,
        compact=compact, dispatch=lambda name, args: calls.append(name) or ToolResult(True, "checked"))
    assert runtime.ask("long " * 500) == "done"
    assert calls == ["check"] and len(client.sent) == 2 and owner.summary()["actual_tokens"] == 6
    reserved = [json.loads(line)["payload"] for line in owner.path.read_text().splitlines()
                if json.loads(line)["kind"] == "reserve"]
    from hashlib import sha256
    assert [r["request_sha256"] for r in reserved] == [sha256(json.dumps(r, ensure_ascii=False, sort_keys=True).encode()).hexdigest() for r in client.sent]
    assert "long " not in json.dumps(client.sent[0])


def test_tariff_cost_is_explicit_derived_spend_and_not_an_invoice(tmp_path, budget_module):
    module = budget_module
    value = policy(module, cost_limit_usd="0.1", tariff={"id": "fixture tariff", "input_per_million": "2",
        "output_per_million": "4", "cache_read_per_million": "1", "cache_write_per_million": "3"})
    owner = module.SpendBudget(tmp_path / "budget.jsonl", value)
    reservation = owner.reserve(request())
    owner.settle(reservation, usage(4))
    assert owner.summary()["derived_cost_usd"] == "0.000012"
    assert owner.summary()["provider_cost_usd"] is None
    assert owner.summary()["policy"] == value.model_dump(mode="json")


@pytest.mark.parametrize("changes", [{"token_limit": True}, {"token_limit": -1}, {"cost_limit_usd": "NaN"},
    {"input_characters_per_token": "0"}, {"cost_limit_usd": "1"}])
def test_invalid_or_unpriced_policy_is_refused(budget_module, changes):
    with pytest.raises(ValueError):
        policy(budget_module, **changes)


def test_policy_changes_and_truncated_journal_cannot_replenish_budget(tmp_path, budget_module):
    module = budget_module
    path = tmp_path / "budget.jsonl"
    module.SpendBudget(path, policy(module))
    with pytest.raises(ValueError, match="policy"):
        module.SpendBudget(path, policy(module, token_limit=999))
    with path.open("a") as stream:
        stream.write(json.dumps({"unfinished": True}))
    with pytest.raises(ValueError):
        module.SpendBudget(path, policy(module))


def test_config_binds_main_auxiliary_and_replacement_runtimes_to_one_owner(tmp_path, monkeypatch, budget_module):
    from hardy.app import config, wiring
    from hardy.workflows.interactive.session import MathematicsSession
    module = budget_module
    configured = policy(module, token_limit=100000)
    policy_path = tmp_path / "budget-policy.json"
    policy_path.write_text(configured.model_dump_json(), encoding="utf-8")
    settings = config.load(tmp_path / "config.toml", root=tmp_path, backend="api", provider_budget=str(policy_path))
    assert settings.provider_budget == configured
    client = Client([usage(), usage(), usage()])
    monkeypatch.setattr(AnthropicProvider, "client", lambda _: client)
    factory = wiring.runtime_factory("fixture", "api", spend_policy=settings.provider_budget)
    # Real session composition; no Lean or model process runs during construction.
    session = MathematicsSession(tmp_path / "workspace", factory, ("missing-lean",), ("missing-tex",), lambda _: False,
                                 project_context=False)
    owner = session._make_runtime.spend_budget
    session.runtime.ask("first")
    session._review_assumption(paper="fixture", reference="1", paper_text="True", formal_name="T",
                               lean_statement="True", informal_statement="True")
    session.switch_model("fixture")
    session.runtime.ask("third")
    assert owner.summary()["reservations"] == 3
    assert all(r["model"] == "fixture" for r in client.requests)
    reopened = MathematicsSession(tmp_path / "workspace", factory, ("missing-lean",), ("missing-tex",), lambda _: False,
                                  project_context=False)
    assert reopened._make_runtime.spend_budget.summary()["reservations"] == 3


def test_unsupported_sdk_and_unbound_factory_cannot_ignore_declared_budget(budget_module):
    from hardy.app.wiring import runtime_factory
    value = policy(budget_module)
    with pytest.raises(ValueError, match="API"):
        runtime_factory("fixture", "claude", spend_policy=value)
    factory = runtime_factory("fixture", "api", spend_policy=value)
    with pytest.raises(ValueError, match="bind"):
        factory(system_prompt="", specs=[], dispatch=lambda *_: None)


def test_unsupported_commands_refuse_before_constructing_capabilities(tmp_path, budget_module, capsys):
    from hardy.app.evals import run_set_command, run_todo
    from hardy.app.wiring import build_prove_workflow
    config = SimpleNamespace(provider_budget=policy(budget_module))
    # No other args/config fields exist: refusal must precede any work.
    assert run_todo(SimpleNamespace(), config) == 2
    assert run_set_command(SimpleNamespace(), config) == 2
    assert "unsupported" in capsys.readouterr().err
    with pytest.raises(ValueError, match="unsupported"):
        build_prove_workflow(config, tmp_path / "config.toml")


def test_existing_session_budget_survives_removing_config_declaration(tmp_path, budget_module):
    from hardy.app.wiring import runtime_factory
    module = budget_module
    path = tmp_path / "provider-budget.jsonl"
    value = policy(module, token_limit=quote())
    configured = module.bind_spend_budget(runtime_factory("fixture", "api", spend_policy=value), path)
    configured.spend_budget.reserve(request())
    restored = module.bind_spend_budget(runtime_factory("fixture", "api"), path)
    with pytest.raises(module.SpendLimitReached, match="token_limit"):
        restored.spend_budget.reserve(request())
    with pytest.raises(ValueError, match="API"):
        module.bind_spend_budget(runtime_factory("fixture", "claude"), path)


def test_real_batch_budget_refusal_has_zero_calls_and_authenticated_artifacts(tmp_path, monkeypatch, budget_module):
    import sys

    from test_recorded_runs import FAKE_LEAN, IDENTITY

    from hardy.app.wiring import runtime_factory
    from hardy.formal.contracts import Request
    from hardy.formal.lean import LeanTools
    from hardy.workflows import batch
    from hardy.workflows.acceptance import validate_batch_consistency
    value = policy(budget_module, token_limit=0)
    client = Client([usage()])
    monkeypatch.setattr(AnthropicProvider, "client", lambda _: client)
    request = Request("theorem HardyTarget : True", "True", ("Mathlib",))
    output = tmp_path / "run"
    result = batch.run(request, runtime_factory("fixture", "api", spend_policy=value),
        LeanTools(request, (sys.executable, str(FAKE_LEAN))), output, toolchain=IDENTITY)
    assert result.terminal_reason == "provider_budget_limit" and result.turns == 0
    assert result.usage["exchanges"] == 0 and not client.requests
    assert validate_batch_consistency(output) == ()
    journal = output / "provider-budget.jsonl"
    journal.write_bytes(journal.read_bytes()[:-1])
    assert any("budget" in issue for issue in validate_batch_consistency(output))


class Rejected(Exception):
    """Shaped like `anthropic.APIStatusError`: a status and the response."""

    def __init__(self, status):
        super().__init__(f"status {status}")
        self.status_code, self.response = status, SimpleNamespace(status_code=status)


@pytest.mark.parametrize("status", [429, 529])
def test_an_explicit_rate_or_overload_rejection_settles_at_zero(tmp_path, budget_module, status):
    """Owner decision 4: a 429 or 529 is the provider refusing before doing
    any work, and bills nothing -- the one failure that settles at zero."""
    module = budget_module
    owner = module.SpendBudget(tmp_path / "budget.jsonl", policy(module, token_limit=quote()))
    client = Client([Rejected(status), usage()])
    provider = AnthropicProvider("fixture", client=client, max_tokens=10, spend_budget=owner)
    with pytest.raises(Rejected):
        provider.complete(system="", messages=[], specs=[])
    settled = [json.loads(line)["payload"] for line in owner.path.read_text().splitlines()
               if json.loads(line)["kind"] == "settle"]
    assert settled[0]["reported"] == {name: 0 for name in usage()} and settled[0]["tokens"] == 0
    provider.complete(system="", messages=[], specs=[])
    assert owner.summary()["actual_tokens"] == 2


@pytest.mark.parametrize("status", [400, 500, 502, 503])
def test_any_other_status_failure_is_charged_at_its_reservation(tmp_path, budget_module, status):
    """Everything else may have done work before it failed, so it is charged
    under "never count less than was spent"."""
    module = budget_module
    owner = module.SpendBudget(tmp_path / "budget.jsonl", policy(module, token_limit=quote()))
    client = Client([Rejected(status), usage()])
    provider = AnthropicProvider("fixture", client=client, max_tokens=10, spend_budget=owner)
    with pytest.raises(Rejected):
        provider.complete(system="", messages=[], specs=[])
    with pytest.raises(module.SpendLimitReached, match="token_limit"):
        provider.complete(system="", messages=[], specs=[])
    assert owner.summary()["actual_tokens"] is None


def test_a_journal_from_before_charging_still_audits_clean(tmp_path, budget_module):
    """The journal schema and the summary shape are unchanged, so a record
    written with unknown settlements still matches its journal."""
    module = budget_module
    owner = module.SpendBudget(tmp_path / "provider-budget.jsonl", policy(module))
    owner.settle(owner.reserve(request()), None)
    owner.settle(owner.reserve(request()), {"input_tokens": 2})
    owner.reserve(request())
    record = owner.summary()
    assert record["actual_tokens"] is None and record["derived_cost_usd"] is None
    assert module.budget_record_issues(tmp_path, record) == ()
