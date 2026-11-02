"""Retrieval requests and results returned to agents."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from hyperwiki.domain.common import Contract, VersionedDocument, ZoteroKey
from hyperwiki.domain.evidence import EvidenceId, EvidenceLocator, make_evidence_id
from hyperwiki.domain.scope import Scope

RetrievalMode = Literal["source", "wiki", "hybrid", "relations"]


class RetrievalRequest(Contract):
    query: Annotated[str, Field(min_length=1)]
    scope: Scope = Scope()
    mode: RetrievalMode = "hybrid"
    max_results: Annotated[int, Field(ge=1, le=100)] = 10
    evidence_token_budget: Annotated[int, Field(ge=1)] = 8000


class RetrievalHit(Contract):
    evidence_id: EvidenceId
    item_key: ZoteroKey
    locator: EvidenceLocator
    text: str
    score: float
    signals: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_evidence_id(self) -> Self:
        if self.evidence_id != make_evidence_id(self.locator):
            raise ValueError("evidence_id does not match the locator")
        return self


class RetrievalResult(VersionedDocument):
    request: RetrievalRequest
    hits: list[RetrievalHit]
    truncated: bool
    warnings: list[str] = Field(default_factory=list)
