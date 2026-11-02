"""Evidence locators and the stable evidence IDs derived from them."""

import hashlib
import json
from typing import Annotated, Literal, Self

from pydantic import Field, NonNegativeInt, StringConstraints, model_validator

from hyperwiki.domain.common import Contract, Sha256Hex, ZoteroKey

EvidenceId = Annotated[str, StringConstraints(pattern=r"^ev_[0-9a-f]{32}$")]
LocatorPrecision = Literal["page", "section", "span", "bbox"]


class BoundingBox(Contract):
    x0: float
    y0: float
    x1: float
    y1: float

    @model_validator(mode="after")
    def _check_order(self) -> Self:
        if self.x0 > self.x1 or self.y0 > self.y1:
            raise ValueError("bounding box requires x0 <= x1 and y0 <= y1")
        return self


class EvidenceLocator(Contract):
    """Where a piece of evidence lives in one version of one attachment."""

    attachment_key: ZoteroKey
    attachment_sha256: Sha256Hex
    extractor: Annotated[str, Field(min_length=1)]
    precision: LocatorPrecision
    page_index: NonNegativeInt | None = None
    page_label: str | None = None
    section: str | None = None
    char_start: int | None = None
    char_end: int | None = None
    bbox: BoundingBox | None = None

    @model_validator(mode="after")
    def _check_precision(self) -> Self:
        if (self.char_start is None) != (self.char_end is None):
            raise ValueError("char_start and char_end must be set together")
        if (
            self.char_start is not None
            and self.char_end is not None
            and not 0 <= self.char_start < self.char_end
        ):
            raise ValueError("character offsets require 0 <= char_start < char_end")
        if self.precision == "page" and self.page_index is None:
            raise ValueError("page precision requires page_index")
        if self.precision == "section" and self.section is None:
            raise ValueError("section precision requires section")
        if self.precision == "span" and self.char_start is None:
            raise ValueError("span precision requires char_start and char_end")
        if self.precision == "bbox" and (self.bbox is None or self.page_index is None):
            raise ValueError("bbox precision requires bbox and page_index")
        return self


def make_evidence_id(locator: EvidenceLocator) -> EvidenceId:
    """Derive a stable ID from the full locator, including attachment version and extractor."""
    canonical = json.dumps(
        locator.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return "ev_" + hashlib.sha256(canonical).hexdigest()[:32]
