"""OpenAI-compatible transports use Hardy's tool loop and actual provider receipts."""
import json
import threading
from types import SimpleNamespace as NS

import pytest

from hardy.agents.api import ApiRuntime
from hardy.agents.loop import Message, ToolCall
from hardy.agents.openai_api import OpenAIProvider, as_messages, usage
from hardy.app import config, wiring
from hardy.foundation.values import ToolResult


class Client:
    base_url = "https://gateway.example/v1"

    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []
        self.chat = NS(completions=self)

    def create(self, **request):
        self.requests.append(request)
        value = next(self.replies)
        if isinstance(value, BaseException):
            raise value
        return value


def reply(text="done", *, calls=None, finish="stop", reported=None, **extra):
    return NS(choices=[NS(message=NS(content=text, tool_calls=calls, **extra), finish_reason=finish)], usage=reported)


def test_messages_preserve_parallel_calls_and_tool_results():
    messages = as_messages([Message("user", text="prove"), Message("assistant", text="checking",
        tool_calls=(ToolCall("a", "check", {"source": "by rfl"}), ToolCall("b", "search", {"query": "rfl"}))),
        Message("tool_result", call_id="a", name="check", ok=False, text="rejected"),
        Message("tool_result", call_id="b", name="search", ok=True, text="found")])
    assert messages[1]["content"] == "checking"
    assert [call["id"] for call in messages[1]["tool_calls"]] == ["a", "b"]
    assert messages[1]["tool_calls"][0]["function"]["arguments"] == '{"source": "by rfl"}'
    assert messages[2] == {"role": "tool", "tool_call_id": "a", "content": "rejected"}
    assert messages[3] == {"role": "tool", "tool_call_id": "b", "content": "found"}


def test_actual_tool_loop_and_stop_translation():
    call = NS(id="c1", type="function", function=NS(name="check", arguments='{"source":"by rfl"}'))
    client = Client([reply(None, calls=[call], finish="tool_calls"), reply("accepted")])
    actions = []
    provider = OpenAIProvider("fixture", client=client, backend="openrouter")
    runtime = ApiRuntime("fixture", system_prompt="Exact statement", specs=[{"type": "function", "function": {
        "name": "check", "parameters": {"type": "object", "properties": {}}}}],
        dispatch=lambda name, args: (actions.append((name, args)), ToolResult(True, "checked"))[1], provider=provider)
    assert runtime.ask("prove") == "accepted"
    assert actions == [("check", {"source": "by rfl"})]
    assert client.requests[0]["messages"][0] == {"role": "system", "content": "Exact statement"}
    assert client.requests[1]["messages"][-1]["role"] == "tool"
    assert runtime.backend == "openrouter"
    assert runtime.prompt_caching is False
    assert "gateway.example" in runtime.endpoint


@pytest.mark.parametrize("finish,expected", [("length", "max_tokens"), ("content_filter", "content_filter")])
def test_nonterminal_finish_is_not_reported_as_a_complete_answer(finish, expected):
    provider = OpenAIProvider("fixture", client=Client([reply(finish=finish)]))
    assert provider.complete(system="", messages=[], specs=[]).stop_reason == expected


@pytest.mark.parametrize("arguments", ["{broken", "[]", "null"])
def test_malformed_tool_arguments_never_reach_dispatch(arguments):
    call = NS(id="c1", type="function", function=NS(name="check", arguments=arguments))
    provider = OpenAIProvider("fixture", client=Client([reply(calls=[call], finish="tool_calls")]))
    with pytest.raises(ValueError, match="arguments"):
        provider.complete(system="", messages=[], specs=[])


def test_usage_counts_cached_input_once_and_keeps_missing_fields_unknown():
    assert usage(None) is None
    assert usage({}) is None
    assert usage({"prompt_tokens": 10, "completion_tokens": 3, "prompt_tokens_details": {"cached_tokens": 4}}) == {
        "input_tokens": 6, "output_tokens": 3, "cache_read_input_tokens": 4}
    assert usage({"prompt_tokens": 10}) == {"input_tokens": 10}
    assert usage({"prompt_tokens": True, "completion_tokens": -1}) is None


@pytest.mark.parametrize("backend,key", [("openai", "OPENAI_API_KEY"), ("openrouter", "OPENROUTER_API_KEY")])
def test_normal_factory_selects_provider_and_safe_endpoint_before_first_call(tmp_path, monkeypatch, backend, key):
    monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://secret:password@gateway.example/private/token?key=hidden")
    settings = config.load(tmp_path / "config.toml", root=tmp_path, backend=backend, model="fixture")
    make = wiring.session_runtime(settings)
    runtime = make(system_prompt="", specs=[], dispatch=lambda *_: ToolResult(True, "ok"))
    assert runtime.backend == ("openai-api" if backend == "openai" else "openrouter")
    assert "password" not in runtime.endpoint and "private/token" not in runtime.endpoint and "hidden" not in runtime.endpoint
    if backend == "openrouter":
        assert "openrouter.ai" in runtime.endpoint
    with pytest.raises(RuntimeError, match=key):
        runtime.ask("hello")


@pytest.mark.parametrize("backend", ["openai", "openrouter"])
def test_config_accepts_explicit_compatible_backend_and_model(tmp_path, backend):
    value = config.load(tmp_path / "config.toml", root=tmp_path, backend=backend, model="provider/model")
    assert value.backend == backend and value.model == "provider/model"


def test_no_tools_reader_and_reasoning_metadata_continuation():
    from hardy.agents.compaction import estimate_tokens
    details = [{"type": "reasoning.encrypted", "data": "opaque-provider-signature" * 1000}]
    client = Client([reply("answer", reasoning_details=details)])
    provider = OpenAIProvider("fixture", client=client)
    turn = provider.complete(system="reader", messages=[Message("user", text="read")], specs=[])
    assert "tools" not in client.requests[0]
    assert client.requests[0]["max_completion_tokens"] == provider.max_tokens
    assert turn.thinking and "opaque-provider-signature" not in turn.text
    continued = as_messages([Message("assistant", text=turn.text, blocks=turn.blocks)])
    assert continued[0]["reasoning_details"] == details
    assert "reasoning_details" not in Message("assistant", text=turn.text, blocks=turn.blocks).as_dict()
    assert estimate_tokens([Message("assistant", text=turn.text, reasoning=turn.reasoning)]) > 20000


def test_deadline_bounds_the_whole_request_and_retains_budget_liability(tmp_path, monkeypatch):
    from hardy.agents.spend_budget import SpendBudget, SpendLimitReached, SpendPolicy
    entered, release = threading.Event(), threading.Event()
    class Slow(Client):
        def create(self, **request):
            entered.set()
            release.wait()
            return reply()
    workers = []
    start = threading.Thread.start
    def start_and_wait_for_sdk_entry(worker):
        start(worker)
        workers.append(worker)
        assert entered.wait(10), "the provider worker did not enter the SDK call"
    # This case needs a sent request. Real journal I/O and thread scheduling
    # must not consume its admission precondition; the real worker still
    # waits in create and _within still joins it for the original 0.2 seconds.
    monkeypatch.setattr(threading.Thread, "start", start_and_wait_for_sdk_entry)
    budget = SpendBudget(tmp_path / "budget.jsonl", SpendPolicy(id="bound", models=("fixture",), token_limit=10000))
    provider = OpenAIProvider("fixture", client=Slow([]), spend_budget=budget, clock=lambda: 0.0)
    try:
        with pytest.raises(TimeoutError):
            provider.complete(system="", messages=[], specs=[], timeout=.2)
        assert entered.is_set()
        assert budget.summary()["actual_tokens"] is None
        with pytest.raises(SpendLimitReached):
            provider.complete(system="", messages=[], specs=[])
    finally:
        release.set()
        for worker in workers:
            worker.join(10)
            assert not worker.is_alive()


def test_admission_that_consumes_the_deadline_never_sends_and_settles_at_zero(tmp_path):
    from hardy.agents.contracts import ProviderRequestTimeout
    from hardy.agents.spend_budget import SpendBudget, SpendPolicy
    clock = [0.0]
    # Keep durable reservation and settlement real; control only elapsed time.
    class SlowAdmission(SpendBudget):
        def reserve(self, request):
            reservation = super().reserve(request)
            clock[0] = 0.3
            return reservation
    budget = SlowAdmission(tmp_path / "budget.jsonl", SpendPolicy(id="bound", models=("fixture",), token_limit=10000))
    client = Client([reply()])
    provider = OpenAIProvider("fixture", client=client, spend_budget=budget, clock=lambda: clock[0])

    with pytest.raises(ProviderRequestTimeout, match="before sending") as expired:
        provider.complete(system="", messages=[], specs=[], timeout=.2)

    assert expired.value.provider_called is False
    assert client.requests == []
    summary = budget.summary()
    assert summary["reservations"] == 1 and summary["pending"] == 0
    assert summary["actual_tokens"] == 0
    events = [json.loads(line) for line in budget.path.read_text(encoding="utf-8").splitlines()]
    assert [event["kind"] for event in events] == ["start", "reserve", "settle"]
    assert events[-1]["payload"]["tokens"] == 0
    assert events[-1]["payload"]["reported"] == {
        "input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}


@pytest.mark.parametrize("backend,key", [("openai", "OPENAI_API_KEY"), ("openrouter", "OPENROUTER_API_KEY")])
def test_factory_budget_doctor_and_setup_use_selected_credentials(tmp_path, monkeypatch, backend, key):
    import sys

    from hardy.agents import openai_api
    from hardy.agents.spend_budget import bind_spend_budget
    from hardy.app import doctor, setup
    requests = []
    class Sdk:
        __version__ = "controlled"
        class OpenAI(Client):
            def __init__(self, **options):
                requests.append(options)
                super().__init__([reply(reported={"prompt_tokens": 10, "completion_tokens": 2,
                    "prompt_tokens_details": {"cached_tokens": 4, "cache_write_tokens": 0}})])
    monkeypatch.setitem(sys.modules, "openai", Sdk)
    monkeypatch.setattr(openai_api, "load_sdk", lambda: Sdk)
    monkeypatch.setenv(key, "canary-credential-never-record")
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"id": "fixture", "models": ["fixture"], "token_limit": 20000}))
    settings = config.load(tmp_path / "config.toml", root=tmp_path, backend=backend,
        model="fixture", provider_budget=str(policy))
    make = bind_spend_budget(wiring.session_runtime(settings), tmp_path / "budget.jsonl")
    runtime = make(system_prompt="", specs=[], dispatch=lambda *_: ToolResult(True, "ok"))
    assert runtime.ask("hello") == "done"
    assert make.spend_budget.summary()["actual_tokens"] == 12
    assert requests[0]["api_key"] == "canary-credential-never-record" and requests[0]["max_retries"] == 0
    assert "canary-credential" not in (tmp_path / "budget.jsonl").read_text()
    checks = doctor._backend_checks(backend)
    assert all(check.ok for check in checks)
    assert not any("canary-credential" in check.detail for check in checks)
    assert setup.backend_probe(backend)()[0]
    monkeypatch.delenv(key)
    assert not doctor._backend_checks(backend)[-1].ok
    assert not setup.backend_probe(backend)()[0]


@pytest.mark.parametrize("backend", ["openai", "openrouter"])
def test_new_transports_require_explicit_models(tmp_path, backend):
    with pytest.raises(ValueError, match="explicit model"):
        config.load(tmp_path / "config.toml", root=tmp_path, backend=backend)


def test_openrouter_reported_usd_cost_survives_repeated_exchanges_and_missing_reports():
    from hardy.agents.usage import Usage
    client = Client([reply(reported={"prompt_tokens": 3, "completion_tokens": 1, "cost": .1}),
        reply(reported={"prompt_tokens": 2, "completion_tokens": 1}),
        reply(reported={"prompt_tokens": 2, "completion_tokens": 1, "cost": .2})])
    events = []
    runtime = ApiRuntime("fixture", system_prompt="", specs=[], dispatch=lambda *_: ToolResult(True, "ok"),
        provider=OpenAIProvider("fixture", client=client, backend="openrouter"), observe=events.append)
    for _ in range(3):
        assert runtime.ask("hello") == "done"
    reports = [event for event in events if event["type"] == "result"]
    assert reports[0]["cost_usd"] == .1
    assert reports[1]["cost_usd"] is None
    assert reports[2]["cost_usd"] == pytest.approx(.3)
    measured = Usage()
    for event in reports:
        measured = measured.record(event)
    assert measured.cost_usd == pytest.approx(.3)
    assert measured.summary()["reported"]["cost_usd"] == 2


@pytest.mark.parametrize("cost", [True, -1, float("inf"), float("nan"), "0.25"])
def test_invalid_or_unspecified_cost_units_remain_unreported(cost):
    from hardy.agents.loop import AgentLoop, ProviderTurn
    provider = OpenAIProvider("fixture", client=Client([reply(reported={"cost": cost})]), backend="openrouter")
    assert provider.complete(system="", messages=[], specs=[]).usage is None
    generic = OpenAIProvider("fixture", client=Client([reply(reported={"cost": .25})]))
    assert generic.complete(system="", messages=[], specs=[]).usage is None
    events = []
    loop = AgentLoop(NS(complete=lambda **_: ProviderTurn(text="done", usage={"cost_usd": cost})),
        system_prompt="", specs=[], dispatch=lambda *_: ToolResult(True, "ok"), observe=events.append)
    list(loop.run("hello"))
    assert events[-1]["cost_usd"] is None


def test_missing_optional_sdk_is_reported_by_selected_backend(monkeypatch):
    import sys

    from hardy.agents.openai_api import load_sdk
    from hardy.app import doctor, setup
    monkeypatch.setitem(sys.modules, "openai", None)
    with pytest.raises(RuntimeError, match="hardy-prover"):
        load_sdk()
    assert not doctor._backend_checks("openrouter")[0].ok
    assert not setup.backend_probe("openrouter")()[0]
