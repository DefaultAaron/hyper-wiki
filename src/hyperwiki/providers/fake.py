"""Deterministic generator for tests and provider-free runs."""

import hashlib
from collections.abc import Mapping

from hyperwiki.providers.base import GenerationRequest, GenerationResult
from hyperwiki.providers.spending import estimate_tokens


class FakeGenerator:
    def __init__(self, responses: Mapping[str, str] | None = None, model: str = "fake-1") -> None:
        self.responses = dict(responses or {})
        self.model = model
        self.calls: list[GenerationRequest] = []

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.calls.append(request)
        text = self.responses.get(request.prompt)
        if text is None:
            digest = hashlib.sha256(request.prompt.encode("utf-8")).hexdigest()[:12]
            text = f"[fake:{request.task}] {digest}"
        return GenerationResult(
            text=text,
            input_tokens=estimate_tokens(request.system + request.prompt),
            output_tokens=estimate_tokens(text),
            model=self.model,
            cost_usd=0.0,
        )
