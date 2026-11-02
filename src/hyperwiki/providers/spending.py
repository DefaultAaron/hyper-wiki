"""Conservative token estimates and the spending cap enforced before every paid call."""

import math

from hyperwiki.domain.providers import ApiProvider
from hyperwiki.errors import ErrorCode, HyperWikiError


def estimate_tokens(text: str) -> int:
    """Over-estimate tokens: four ASCII characters per token, one token per other character."""
    ascii_chars = sum(1 for char in text if char.isascii())
    return math.ceil(ascii_chars / 4) + (len(text) - ascii_chars)


def estimate_cost_usd(provider: ApiProvider, input_tokens: int, output_tokens: int) -> float:
    return (
        input_tokens * provider.input_usd_per_million_tokens
        + output_tokens * provider.output_usd_per_million_tokens
    ) / 1_000_000


class SpendingGuard:
    """Refuses work whose estimated cost would take spending past the cap.

    Daily reset and persistence arrive with the M2 provider runtime.
    """

    def __init__(self, cap_usd: float, spent_usd: float = 0.0) -> None:
        self.cap_usd = cap_usd
        self.spent_usd = spent_usd

    def check(self, estimated_cost_usd: float) -> None:
        if self.spent_usd + estimated_cost_usd > self.cap_usd:
            raise HyperWikiError(
                ErrorCode.SPENDING_CAP_REACHED,
                "the spending cap would be exceeded",
                {
                    "cap_usd": self.cap_usd,
                    "spent_usd": self.spent_usd,
                    "estimated_usd": estimated_cost_usd,
                },
            )

    def record(self, cost_usd: float) -> None:
        self.spent_usd += cost_usd

    @property
    def remaining_usd(self) -> float:
        return self.cap_usd - self.spent_usd
