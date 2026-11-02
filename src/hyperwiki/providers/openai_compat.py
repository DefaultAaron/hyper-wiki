"""Text generation through any OpenAI-compatible chat completions endpoint.

Covers metered APIs (``ApiProvider``, always under a spending guard) and local servers such as
Ollama or LM Studio (``LocalProvider``). The API key is held in memory only and never appears in
errors, details, or logs.
"""

import os
from collections.abc import Mapping
from typing import Any

import httpx

from hyperwiki.domain.providers import ApiProvider, LocalProvider
from hyperwiki.errors import ErrorCode, HyperWikiError
from hyperwiki.providers.base import GenerationRequest, GenerationResult
from hyperwiki.providers.spending import SpendingGuard, estimate_cost_usd, estimate_tokens

_BODY_EXCERPT_CHARS = 500


class OpenAICompatibleGenerator:
    def __init__(
        self,
        config: ApiProvider | LocalProvider,
        *,
        api_key: str | None = None,
        guard: SpendingGuard | None = None,
        client: httpx.Client | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        if isinstance(config, ApiProvider) and guard is None:
            raise HyperWikiError(
                ErrorCode.SPENDING_CAP_REQUIRED,
                "an api provider requires a spending guard",
                {"model": config.model},
            )
        self.config = config
        self.guard = guard
        self._api_key = api_key
        self._owns_client = client is None
        self._client = client if client is not None else httpx.Client()
        self._timeout_s = timeout_s
        self._url = str(config.base_url).rstrip("/") + "/chat/completions"

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _redact(self, text: str) -> str:
        return text.replace(self._api_key, "[redacted]") if self._api_key else text

    def _error(self, message: str, status_code: int | None, body: str = "") -> HyperWikiError:
        details: dict[str, Any] = {"status_code": status_code, "url": self._url}
        if body:
            details["body"] = self._redact(body)[:_BODY_EXCERPT_CHARS]
        return HyperWikiError(ErrorCode.PROVIDER_ERROR, message, details)

    def _record(self, cost_usd: float) -> None:
        if self.guard is not None:
            self.guard.record(cost_usd)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """Send one chat completion.

        Spending is recorded for every call the provider may have billed: from reported usage
        when the response has it, from the pre-call estimate when the outcome is unknown (a
        timeout or broken response after sending), and not at all when the request never left
        or the provider rejected it.
        """
        config = self.config
        estimated_cost = 0.0
        if isinstance(config, ApiProvider):
            estimated_input = estimate_tokens(request.system + request.prompt)
            estimated_cost = estimate_cost_usd(config, estimated_input, request.max_output_tokens)
            if self.guard is not None:
                self.guard.check(estimated_cost)

        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        payload = {
            "model": config.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.prompt},
            ],
            "max_tokens": request.max_output_tokens,
        }
        try:
            response = self._client.post(
                self._url, json=payload, headers=headers, timeout=self._timeout_s
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise self._error(f"could not reach the provider: {type(exc).__name__}", None) from exc
        except httpx.TimeoutException as exc:
            self._record(estimated_cost)
            raise self._error("the provider did not respond in time", None) from exc
        except httpx.HTTPError as exc:
            self._record(estimated_cost)
            raise self._error(f"the request failed: {type(exc).__name__}", None) from exc

        if not response.is_success:
            raise self._error(
                f"the provider returned HTTP {response.status_code}",
                response.status_code,
                response.text,
            )
        try:
            data = response.json()
        except ValueError as exc:
            self._record(estimated_cost)
            raise self._error(
                "the provider returned a malformed response", response.status_code, response.text
            ) from exc

        text = _message_text(data)
        usage = data.get("usage") if isinstance(data, dict) else None
        input_tokens = _usage_count(usage, "prompt_tokens")
        output_tokens = _usage_count(usage, "completion_tokens")
        if input_tokens is None:
            input_tokens = estimate_tokens(request.system + request.prompt)
        if output_tokens is None:
            output_tokens = request.max_output_tokens if text is None else estimate_tokens(text)
        cost_usd = 0.0
        if isinstance(config, ApiProvider):
            cost_usd = estimate_cost_usd(config, input_tokens, output_tokens)
        self._record(cost_usd)

        if text is None:
            raise self._error(
                "the provider response contained no text", response.status_code, response.text
            )
        model = data.get("model")
        return GenerationResult(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=model if isinstance(model, str) and model else config.model,
            cost_usd=cost_usd,
        )


def _message_text(data: object) -> str | None:
    """Return ``choices[0].message.content`` if the body has that shape and it is text."""
    choices = data.get("choices") if isinstance(data, dict) else None
    first = choices[0] if isinstance(choices, list) and choices else None
    message = first.get("message") if isinstance(first, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    return content if isinstance(content, str) else None


def _usage_count(usage: object, field: str) -> int | None:
    if not isinstance(usage, dict):
        return None
    value = usage.get(field)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def generator_from_config(
    config: ApiProvider | LocalProvider, environ: Mapping[str, str] | None = None
) -> OpenAICompatibleGenerator:
    """Build a generator, reading an API key from the environment variable the config names."""
    if isinstance(config, LocalProvider):
        return OpenAICompatibleGenerator(config)
    env = environ if environ is not None else os.environ
    api_key = env.get(config.api_key_env)
    if not api_key:
        raise HyperWikiError(
            ErrorCode.PROVIDER_NOT_CONFIGURED,
            f"set the {config.api_key_env} environment variable to the provider's API key",
            {"api_key_env": config.api_key_env},
        )
    return OpenAICompatibleGenerator(
        config, api_key=api_key, guard=SpendingGuard(config.daily_spend_cap_usd)
    )
