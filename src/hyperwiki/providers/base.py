"""The text-generation interface every provider implements."""

from typing import Annotated, Protocol

from pydantic import Field

from hyperwiki.domain.common import Contract
from hyperwiki.domain.providers import TaskName


class GenerationRequest(Contract):
    task: TaskName
    system: str
    prompt: str
    max_output_tokens: Annotated[int, Field(gt=0)]


class GenerationResult(Contract):
    text: str
    input_tokens: int
    output_tokens: int
    model: str
    cost_usd: float


class TextGenerator(Protocol):
    def generate(self, request: GenerationRequest) -> GenerationResult: ...
