"""Real SDK requests remain offline and use only synthetic credentials."""
import os
from unittest.mock import patch

import httpx2 as httpx
import pytest

from hardy.agents.loop import Message
from hardy.agents.openai_api import OpenAIProvider


def intercepted_completion(backend, observed, *, retry=False):
    def respond(request):
        observed.append(request)
        if retry and len(observed) == 1:
            return httpx.Response(429, json={"error": {"message": "controlled retry", "type": "rate_limit"}})
        return httpx.Response(200, json={"id": "fixture", "object": "chat.completion", "created": 0,
            "model": "fixture", "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
    provider = OpenAIProvider("fixture", backend=backend, sleep=lambda _: None)
    original = provider.client()
    with httpx.Client(transport=httpx.MockTransport(respond), trust_env=False) as transport:
        provider._client = original.with_options(http_client=transport)
        original.close()
        try:
            assert provider.complete(system="fixture", messages=[Message("user", text="hello")], specs=[]).text == "ok"
        finally:
            provider.client().close()


@pytest.mark.parametrize("authorization_names", [(), ("Authorization",), ("authorization",), ("AUTHORIZATION",),
    ("Authorization", "authorization", "AUTHORIZATION")])
@pytest.mark.parametrize("router_url", [None, "https://router.example/v1"])
@pytest.mark.parametrize("retry", [False, True])
def test_openrouter_real_sdk_excludes_ambient_openai_headers(authorization_names, router_url, retry):
    headers = [f"{name}: Bearer ambient-{index}" for index, name in enumerate(authorization_names)]
    headers.extend(["X-Gateway-Token: gateway-canary", "openai-organization: header-org-canary",
        "OPENAI-PROJECT: header-project-canary"])
    environment = {"OPENROUTER_API_KEY": "router-canary", "OPENAI_API_KEY": "openai-canary",
        "OPENAI_BASE_URL": "https://openai.example/v1", "OPENAI_CUSTOM_HEADERS": "\n".join(headers),
        "OPENAI_ORG_ID": "org-canary", "OPENAI_PROJECT_ID": "project-canary",
        "OPENAI_ADMIN_KEY": "admin-canary"}
    if router_url:
        environment["OPENROUTER_BASE_URL"] = router_url
    observed = []
    with patch.dict(os.environ, environment, clear=True):
        intercepted_completion("openrouter", observed, retry=retry)
        assert dict(os.environ) == environment, "Client construction must not change the process environment"
    assert len(observed) == (2 if retry else 1)
    for request in observed:
        assert str(request.url) == (router_url or "https://openrouter.ai/api/v1") + "/chat/completions"
        assert request.headers.get_list("authorization") == ["Bearer router-canary"]
        assert "openai-organization" not in request.headers
        assert "openai-project" not in request.headers
        assert "x-gateway-token" not in request.headers
        assert "admin-canary" not in str(request.headers)


def test_openai_real_sdk_preserves_intentional_ambient_configuration():
    observed = []
    environment = {"OPENAI_API_KEY": "openai-canary", "OPENROUTER_API_KEY": "router-canary",
        "OPENAI_BASE_URL": "https://openai.example/v1", "OPENAI_ORG_ID": "org-canary",
        "OPENAI_PROJECT_ID": "project-canary",
        "OPENAI_CUSTOM_HEADERS": "authorization: Bearer gateway-canary\nX-Gateway-Token: custom-canary"}
    with patch.dict(os.environ, environment, clear=True):
        intercepted_completion("openai", observed)
        assert dict(os.environ) == environment
    request, = observed
    assert str(request.url) == "https://openai.example/v1/chat/completions"
    assert request.headers.get_list("authorization") == ["Bearer gateway-canary"]
    assert request.headers["openai-organization"] == "org-canary"
    assert request.headers["openai-project"] == "project-canary"
    assert request.headers["x-gateway-token"] == "custom-canary"
