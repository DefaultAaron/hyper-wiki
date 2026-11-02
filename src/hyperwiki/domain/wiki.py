"""Wiki page frontmatter: identity, provenance, and freshness of a Markdown page."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from hyperwiki.domain.common import (
    Contract,
    ProviderKind,
    Sha256Hex,
    VersionedDocument,
    ZoteroKey,
)
from hyperwiki.domain.evidence import EvidenceId

PageType = Literal["paper", "concept", "topic", "synthesis", "index", "note"]
PageStatus = Literal["current", "stale", "pending"]
WikiLanguage = Literal["en", "zh"]


class GeneratorFingerprint(Contract):
    provider_kind: ProviderKind
    model: Annotated[str, Field(min_length=1)]
    prompt_version: Annotated[str, Field(min_length=1)]


class WikiFrontmatter(VersionedDocument):
    id: UUID
    page_type: PageType
    title: Annotated[str, Field(min_length=1)]
    language: WikiLanguage
    source_item_keys: list[ZoteroKey] = Field(default_factory=list)
    evidence_ids: list[EvidenceId] = Field(default_factory=list)
    revision: Annotated[int, Field(ge=1)]
    status: PageStatus
    generated_by: GeneratorFingerprint | None = None
    generated_sha256: Sha256Hex | None = None
    last_verified_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _check_generation(self) -> Self:
        if self.page_type == "note":
            if self.generated_by is not None or self.generated_sha256 is not None:
                raise ValueError("personal notes are never generated")
        elif self.status != "pending" and (
            self.generated_by is None or self.generated_sha256 is None
        ):
            raise ValueError("generated pages require generated_by and generated_sha256")
        return self
