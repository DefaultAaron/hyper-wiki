"""Page text extractors: PDF through pypdfium2 (ADR 0006) and UTF-8 plain text.

Both return each page's cleaned text (cleanup version 1), whether the page has a text layer,
and its evidence spans as character offsets into that text. PDF spans also get a bounding box
in PDF user space (points, origin at the bottom left, not adjusted for page rotation). An
input that cannot be read raises ``HyperWikiError`` with ``extraction_failed``.
"""

from collections.abc import Callable
from typing import Annotated, Any

import pypdfium2
import pypdfium2.raw as pdfium_c
import pypdfium2.version as pdfium_version
from pydantic import Field, NonNegativeInt, PositiveInt

from hyperwiki.domain.common import Contract
from hyperwiki.domain.evidence import BoundingBox
from hyperwiki.domain.work import Component
from hyperwiki.errors import ErrorCode, HyperWikiError
from hyperwiki.processing.chunking import chunk_spans
from hyperwiki.processing.text_cleanup import CleanText, clean_page_text

PDF_TYPE = "application/pdf"
TEXT_TYPE = "text/plain"
PLAINTEXT_EXTRACTOR_VERSION = "1"


class ExtractionSettings(Contract):
    """Settings that shape the output; all of them are part of the extraction fingerprint."""

    chunk_chars: Annotated[int, Field(ge=100)] = 800
    min_page_chars: Annotated[int, Field(ge=0)] = 20


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


def _media_type(content_type: str) -> str:
    return content_type.split(";", 1)[0].strip().lower()


def extractor_component(content_type: str) -> Component | None:
    """The extractor for a content type, or None if the type is not supported."""
    media_type = _media_type(content_type)
    if media_type == PDF_TYPE:
        return Component(
            name="pypdfium2",
            version=str(pdfium_version.PYPDFIUM_INFO),
            runtime=f"pdfium {pdfium_version.PDFIUM_INFO.build}",
        )
    if media_type == TEXT_TYPE:
        return Component(name="hyperwiki.plaintext", version=PLAINTEXT_EXTRACTOR_VERSION)
    return None


def _has_text(cleaned: CleanText, settings: ExtractionSettings) -> bool:
    visible = sum(1 for char in cleaned.text if not char.isspace())
    return visible > 0 and visible >= settings.min_page_chars


def _units(char: str) -> int:
    return 2 if ord(char) > 0xFFFF else 1


def _bbox(textpage: Any, first_unit: int, last_unit: int) -> BoundingBox | None:
    """Union of PDFium's text rectangles from the first to the last text unit, inclusive."""
    first = int(pdfium_c.FPDFText_GetCharIndexFromTextIndex(textpage.raw, first_unit))
    last = int(pdfium_c.FPDFText_GetCharIndexFromTextIndex(textpage.raw, last_unit))
    if first < 0 or last < first:
        return None
    count = int(textpage.count_rects(first, last - first + 1))
    if count <= 0:
        return None
    rects = [tuple(float(value) for value in textpage.get_rect(i)) for i in range(count)]
    return BoundingBox(
        x0=min(min(r[0], r[2]) for r in rects),
        y0=min(min(r[1], r[3]) for r in rects),
        x1=max(max(r[0], r[2]) for r in rects),
        y1=max(max(r[1], r[3]) for r in rects),
    )


def _page_labels(document: Any, count: int) -> list[str | None]:
    """Labels the PDF defines, unless they are one constant value on every page."""
    labels = [str(document.get_page_label(index) or "") for index in range(count)]
    if not any(labels) or (count > 1 and len(set(labels)) == 1):
        return [None] * count
    return [label or None for label in labels]


def _pdf_page(
    document: Any, index: int, label: str | None, settings: ExtractionSettings
) -> ExtractedPage:
    page = document[index]
    try:
        width, height = (float(value) for value in page.get_size())
        textpage = page.get_textpage()
        try:
            cleaned = clean_page_text(str(textpage.get_text_range(errors="replace")))
            spans: list[ExtractedSpan] = []
            has_text = _has_text(cleaned, settings)
            if has_text:
                for start, end in chunk_spans(cleaned.text, max_chars=settings.chunk_chars):
                    last_unit = cleaned.sources[end - 1] + _units(cleaned.text[end - 1]) - 1
                    bbox = _bbox(textpage, cleaned.sources[start], last_unit)
                    spans.append(ExtractedSpan(char_start=start, char_end=end, bbox=bbox))
        finally:
            textpage.close()
    finally:
        page.close()
    return ExtractedPage(
        index=index,
        label=label,
        width=width,
        height=height,
        text=cleaned.text,
        has_text=has_text,
        spans=spans,
    )


def extract_pdf(
    data: bytes,
    settings: ExtractionSettings,
    *,
    on_page: Callable[[int], None] | None = None,
) -> ExtractedDocument:
    """Extract every page of a PDF; ``on_page`` is called after each page."""
    try:
        document = pypdfium2.PdfDocument(data)
    except pypdfium2.PdfiumError as error:
        raise HyperWikiError(
            ErrorCode.EXTRACTION_FAILED, f"PDFium could not open the PDF: {error}"
        ) from error
    try:
        count = len(document)
        labels = _page_labels(document, count)
        pages: list[ExtractedPage] = []
        for index in range(count):
            pages.append(_pdf_page(document, index, labels[index], settings))
            if on_page is not None:
                on_page(index)
    except pypdfium2.PdfiumError as error:
        raise HyperWikiError(
            ErrorCode.EXTRACTION_FAILED, f"PDFium could not read the PDF: {error}"
        ) from error
    finally:
        document.close()
    return ExtractedDocument(pages=pages)


def extract_plaintext(data: bytes, settings: ExtractionSettings) -> ExtractedDocument:
    """Extract UTF-8 text; a form feed separates pages."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise HyperWikiError(
            ErrorCode.EXTRACTION_FAILED,
            "text file is not valid UTF-8",
            {"byte_offset": error.start},
        ) from error
    pages: list[ExtractedPage] = []
    for index, raw in enumerate(text.split("\f")):
        cleaned = clean_page_text(raw)
        has_text = _has_text(cleaned, settings)
        spans = (
            [
                ExtractedSpan(char_start=start, char_end=end)
                for start, end in chunk_spans(cleaned.text, max_chars=settings.chunk_chars)
            ]
            if has_text
            else []
        )
        pages.append(
            ExtractedPage(
                index=index,
                label=None,
                width=None,
                height=None,
                text=cleaned.text,
                has_text=has_text,
                spans=spans,
            )
        )
    return ExtractedDocument(pages=pages)
