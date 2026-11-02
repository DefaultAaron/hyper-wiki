import json
import os
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from hyperwiki.domain.providers import ApiProvider, LocalProvider
from hyperwiki.errors import ErrorCode, HyperWikiError
from hyperwiki.providers.base import GenerationRequest
from hyperwiki.providers.openai_compat import OpenAICompatibleGenerator, generator_from_config
from hyperwiki.providers.spending import SpendingGuard, estimate_cost_usd, estimate_tokens

API = ApiProvider.model_validate(
    {
        "kind": "api",
        "base_url": "https://api.example.com/v1",
        "model": "m",
        "api_key_env": "HW_TEST_KEY",
        "daily_spend_cap_usd": 1.0,
        "input_usd_per_million_tokens": 1.0,
        "output_usd_per_million_tokens": 2.0,
    }
)
LOCAL = LocalProvider.model_validate(
    {"kind": "local", "base_url": "http://localhost:11434/v1", "model": "qwen3:8b"}
)
REQUEST = GenerationRequest(
    task="page_writing", system="You write wiki pages.", prompt="Summarize.", max_output_tokens=100
)
OK_BODY: dict[str, Any] = {
    "choices": [{"message": {"content": "注意力"}}],
    "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 500_000},
}

Handler = Callable[[httpx.Request], httpx.Response]


def client(handler: Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def api_generator(handler: Handler, cap_usd: float = 10.0) -> OpenAICompatibleGenerator:
    return OpenAICompatibleGenerator(
        API, api_key="test-key", guard=SpendingGuard(cap_usd=cap_usd), client=client(handler)
    )


def assert_no_key(error: HyperWikiError) -> None:
    assert "test-key" not in str(error)
    assert "test-key" not in json.dumps(error.details)


def test_sends_openai_compatible_request() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=OK_BODY)

    api_generator(handler).generate(REQUEST)
    [request] = seen
    assert request.method == "POST"
    assert request.url.path.endswith("/v1/chat/completions")
    assert request.headers["Authorization"] == "Bearer test-key"
    assert json.loads(request.content) == {
        "model": "m",
        "messages": [
            {"role": "system", "content": "You write wiki pages."},
            {"role": "user", "content": "Summarize."},
        ],
        "max_tokens": 100,
    }


def test_parses_response_and_computes_cost() -> None:
    guard = SpendingGuard(cap_usd=10.0)
    generator = OpenAICompatibleGenerator(
        API,
        api_key="test-key",
        guard=guard,
        client=client(lambda _: httpx.Response(200, json=OK_BODY)),
    )
    result = generator.generate(REQUEST)
    assert result.text == "注意力"
    assert (result.input_tokens, result.output_tokens) == (1_000_000, 500_000)
    assert result.cost_usd == pytest.approx(2.0)
    assert guard.remaining_usd == pytest.approx(8.0)


def test_missing_usage_falls_back_to_estimates() -> None:
    body = {"choices": [{"message": {"content": "abcd" * 3}}]}
    result = api_generator(lambda _: httpx.Response(200, json=body)).generate(REQUEST)
    assert result.output_tokens == 3
    assert result.input_tokens > 0


def test_spending_cap_blocks_before_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("the request must not be sent when the cap would be exceeded")

    with pytest.raises(HyperWikiError) as caught:
        api_generator(handler, cap_usd=0.000001).generate(REQUEST)
    assert caught.value.code is ErrorCode.SPENDING_CAP_REACHED


def test_api_provider_requires_guard() -> None:
    with pytest.raises(HyperWikiError) as caught:
        OpenAICompatibleGenerator(API, api_key="test-key")
    assert caught.value.code is ErrorCode.SPENDING_CAP_REQUIRED


def test_http_error_does_not_leak_key() -> None:
    body = {"error": {"message": "Incorrect API key provided: test-key"}}
    with pytest.raises(HyperWikiError) as caught:
        api_generator(lambda _: httpx.Response(401, json=body)).generate(REQUEST)
    assert caught.value.code is ErrorCode.PROVIDER_ERROR
    assert caught.value.details["status_code"] == 401
    assert_no_key(caught.value)


def test_timeout_is_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(HyperWikiError) as caught:
        api_generator(handler).generate(REQUEST)
    assert caught.value.code is ErrorCode.PROVIDER_ERROR
    assert_no_key(caught.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": [{"message": {"content": None}}]}),
    ],
)
def test_malformed_body_is_provider_error(response: httpx.Response) -> None:
    with pytest.raises(HyperWikiError) as caught:
        api_generator(lambda _: response).generate(REQUEST)
    assert caught.value.code is ErrorCode.PROVIDER_ERROR
    assert caught.value.details["status_code"] == 200


def test_local_provider_sends_no_auth_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=OK_BODY)

    result = OpenAICompatibleGenerator(LOCAL, client=client(handler)).generate(REQUEST)
    assert "Authorization" not in seen[0].headers
    assert seen[0].url == httpx.URL("http://localhost:11434/v1/chat/completions")
    assert result.cost_usd == 0.0


def test_missing_api_key_env_is_provider_not_configured() -> None:
    with pytest.raises(HyperWikiError) as caught:
        generator_from_config(API, environ={})
    assert caught.value.code is ErrorCode.PROVIDER_NOT_CONFIGURED
    assert "HW_TEST_KEY" in json.dumps(caught.value.details)


def test_generator_from_config_applies_cap() -> None:
    generator = generator_from_config(API, environ={"HW_TEST_KEY": "test-key"})
    assert generator.guard is not None
    assert generator.guard.cap_usd == 1.0


def test_key_cut_by_truncation_is_still_redacted() -> None:
    key = "sk-live-0123456789abcdef"
    generator = OpenAICompatibleGenerator(
        API,
        api_key=key,
        guard=SpendingGuard(cap_usd=10.0),
        client=client(lambda _: httpx.Response(401, text="x" * 490 + key)),
    )
    with pytest.raises(HyperWikiError) as caught:
        generator.generate(REQUEST)
    assert "sk-live" not in json.dumps(caught.value.details)


def test_billed_response_without_text_is_recorded() -> None:
    guard = SpendingGuard(cap_usd=10.0)
    body = {
        "choices": [{"message": {"content": None}}],
        "usage": {"prompt_tokens": 400_000, "completion_tokens": 0},
    }
    generator = OpenAICompatibleGenerator(
        API,
        api_key="test-key",
        guard=guard,
        client=client(lambda _: httpx.Response(200, json=body)),
    )
    with pytest.raises(HyperWikiError):
        generator.generate(REQUEST)
    assert guard.spent_usd == pytest.approx(0.4)


def raise_read_timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timed out", request=request)


@pytest.mark.parametrize(
    "handler", [raise_read_timeout, lambda _: httpx.Response(200, text="not json")]
)
def test_unknown_outcome_records_the_estimate(handler: Handler) -> None:
    guard = SpendingGuard(cap_usd=10.0)
    generator = OpenAICompatibleGenerator(
        API, api_key="test-key", guard=guard, client=client(handler)
    )
    with pytest.raises(HyperWikiError):
        generator.generate(REQUEST)
    expected = estimate_cost_usd(
        API, estimate_tokens(REQUEST.system + REQUEST.prompt), REQUEST.max_output_tokens
    )
    assert expected > 0
    assert guard.spent_usd == pytest.approx(expected)


def test_unsent_request_records_nothing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    guard = SpendingGuard(cap_usd=10.0)
    generator = OpenAICompatibleGenerator(
        API, api_key="test-key", guard=guard, client=client(handler)
    )
    with pytest.raises(HyperWikiError):
        generator.generate(REQUEST)
    assert guard.spent_usd == 0.0


@pytest.mark.live
@pytest.mark.skipif(
    "HYPERWIKI_SMOKE_BASE_URL" not in os.environ, reason="live provider not configured"
)
def test_live_openai_compatible_smoke() -> None:
    config = LocalProvider(
        kind="local",
        base_url=os.environ["HYPERWIKI_SMOKE_BASE_URL"],
        model=os.environ["HYPERWIKI_SMOKE_MODEL"],
    )
    result = OpenAICompatibleGenerator(config).generate(
        GenerationRequest(
            task="extraction", system="Reply with one word.", prompt="Say ok.", max_output_tokens=8
        )
    )
    assert result.text.strip()
