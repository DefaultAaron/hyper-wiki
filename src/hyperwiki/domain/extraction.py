"""Extracted pages and evidence spans, as extractors produce them and storage records them."""

from pydantic import NonNegativeInt, PositiveInt

from hyperwiki.domain.common import Contract
from hyperwiki.domain.evidence import BoundingBox


class ExtractedSpan(Contract):
    char_start: NonNegativeInt
    char_end: PositiveInt
    bbox: BoundingBox | None = None


class ExtractedPage(Contract):
    index: NonNegativeInt
    label: str | None
    width: float | None
    height: float | None
    text: str
    has_text: bool
    spans: list[ExtractedSpan]


class ExtractedDocument(Contract):
    pages: list[ExtractedPage]
