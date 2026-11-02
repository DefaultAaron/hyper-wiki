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
            details["body"] = self._redact(body[:_BODY_EXCERPT_CHARS])
        return HyperWikiError(ErrorCode.PROVIDER_ERROR, message, details)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        config = self.config
        if isinstance(config, ApiProvider) and self.guard is not None:
            estimated_input = estimate_tokens(request.system + request.prompt)
            self.guard.check(estimate_cost_usd(config, estimated_input, request.max_output_tokens))

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
        except httpx.TimeoutException as exc:
            raise self._error("the provider did not respond in time", None) from exc
        except httpx.HTTPError as exc:
            raise self._error(f"the request failed: {type(exc).__name__}", None) from exc

        if not response.is_success:
            raise self._error(
                f"the provider returned HTTP {response.status_code}",
                response.status_code,
                response.text,
            )
        try:
            data = response.json()
            text = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise self._error(
                "the provider returned a malformed response", response.status_code, response.text
            ) from exc
        if not isinstance(text, str):
            raise self._error("the provider returned no text", response.status_code, response.text)

        usage = data.get("usage") if isinstance(data, dict) else None
        input_tokens = _usage_count(usage, "prompt_tokens")
        output_tokens = _usage_count(usage, "completion_tokens")
        if input_tokens is None:
            input_tokens = estimate_tokens(request.system + request.prompt)
        if output_tokens is None:
            output_tokens = estimate_tokens(text)

        cost_usd = 0.0
        if isinstance(config, ApiProvider):
            cost_usd = estimate_cost_usd(config, input_tokens, output_tokens)
        if self.guard is not None:
            self.guard.record(cost_usd)
        model = data.get("model")
        return GenerationResult(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=model if isinstance(model, str) and model else config.model,
            cost_usd=cost_usd,
        )


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
