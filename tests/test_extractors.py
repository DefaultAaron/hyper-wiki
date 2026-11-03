import pytest

from hyperwiki.domain.extraction import ExtractedPage
from hyperwiki.errors import ErrorCode, HyperWikiError
from hyperwiki.processing.extractors import (
    ExtractionSettings,
    extract_pdf,
    extract_plaintext,
    extractor_component,
)
from pdf_factory import PAGE_HEIGHT, PAGE_WIDTH, make_pdf

ENGLISH = [
    "Attention mechanisms weight every input token.",
    "The weights are normalized to sum to one.",
]
CHINESE = ["基于注意力机制的合成实验研究", "注意力权重经过归一化处理。"]
SETTINGS = ExtractionSettings()


def _span_texts(page: ExtractedPage) -> list[str]:
    return [page.text[span.char_start : span.char_end] for span in page.spans]


def test_extracts_english_and_chinese_pages_with_labels() -> None:
    data = make_pdf(
        [ENGLISH, CHINESE, ["Mixed 中英 text line with enough characters"]],
        page_labels="0 << /S /r >> 2 << /S /D /St 5 >>",
    )
    document = extract_pdf(data, SETTINGS)
    assert [page.index for page in document.pages] == [0, 1, 2]
    assert [page.label for page in document.pages] == ["i", "ii", "5"]
    assert document.pages[0].text == "\n".join(ENGLISH)
    assert document.pages[1].text == "基于注意力机制的合成实验研究注意力权重经过归一化处理。"
    assert document.pages[2].text == "Mixed 中英 text line with enough characters"
    assert all(page.has_text for page in document.pages)
    assert _span_texts(document.pages[1]) == [document.pages[1].text]
    assert all((page.width, page.height) == (PAGE_WIDTH, PAGE_HEIGHT) for page in document.pages)


def test_hyphenated_line_end_is_joined() -> None:
    document = extract_pdf(make_pdf([["Evidence extrac-", "tion keeps page numbers."]]), SETTINGS)
    assert document.pages[0].text == "Evidence extraction keeps page numbers."


def test_page_without_text_layer_has_no_spans() -> None:
    document = extract_pdf(make_pdf([[], ["12"], ENGLISH]), SETTINGS)
    assert [page.has_text for page in document.pages] == [False, False, True]
    assert [len(page.spans) for page in document.pages] == [0, 0, 1]
    assert document.pages[1].text == "12"


def test_constant_page_labels_are_dropped() -> None:
    data = make_pdf([ENGLISH, ENGLISH, ENGLISH], page_labels="0 << /P (Cover) >>")
    assert [page.label for page in extract_pdf(data, SETTINGS).pages] == [None, None, None]


def test_single_page_label_is_kept() -> None:
    data = make_pdf([ENGLISH], page_labels="0 << /S /D /St 7 >>")
    assert extract_pdf(data, SETTINGS).pages[0].label == "7"


def test_pdf_without_labels_has_none() -> None:
    document = extract_pdf(make_pdf([ENGLISH, ENGLISH]), SETTINGS)
    assert [page.label for page in document.pages] == [None, None]


def test_spans_have_boxes_inside_the_page() -> None:
    lines = [f"Line {row} carries several ordinary English words." for row in range(40)]
    document = extract_pdf(make_pdf([lines]), ExtractionSettings(chunk_chars=300))
    page = document.pages[0]
    assert len(page.spans) > 3
    for span in page.spans:
        assert span.bbox is not None
        assert 0 <= span.bbox.x0 <= span.bbox.x1 <= PAGE_WIDTH
        assert 0 <= span.bbox.y0 <= span.bbox.y1 <= PAGE_HEIGHT
    tops = [span.bbox.y1 for span in page.spans if span.bbox is not None]
    assert tops == sorted(tops, reverse=True)


def test_offsets_and_boxes_survive_non_bmp_text() -> None:
    first = "𝐀 " * 29 + "𝐀"
    second = "tail " * 11 + "tail"
    document = extract_pdf(make_pdf([[first, second]]), ExtractionSettings(chunk_chars=100))
    page = document.pages[0]
    assert page.text == first + "\n" + second
    assert _span_texts(page) == [first, second]
    top, bottom = page.spans
    assert top.bbox is not None and bottom.bbox is not None
    assert top.bbox.y0 > 755
    assert bottom.bbox.y1 < 756
    assert bottom.bbox.x1 > 150


def test_corrupt_pdf_raises_extraction_failed() -> None:
    with pytest.raises(HyperWikiError) as raised:
        extract_pdf(b"%PDF-1.7\nthis is not a PDF body", SETTINGS)
    assert raised.value.code == ErrorCode.EXTRACTION_FAILED


def test_on_page_called_per_page() -> None:
    seen: list[int] = []
    extract_pdf(make_pdf([ENGLISH, [], CHINESE]), SETTINGS, on_page=seen.append)
    assert seen == [0, 1, 2]


def test_plaintext_pages_split_on_form_feed() -> None:
    first = "第一页的中文内容，需要足够的字符才能形成证据。"
    data = (first + "\r\n\fSecond page in English with words.").encode()
    document = extract_plaintext(b"\xef\xbb\xbf" + data, SETTINGS)
    assert [page.text for page in document.pages] == [
        first + "\n",
        "Second page in English with words.",
    ]
    assert all(page.label is None and page.width is None for page in document.pages)
    assert all(span.bbox is None for page in document.pages for span in page.spans)
    assert [len(page.spans) for page in document.pages] == [1, 1]


def test_non_utf8_text_raises_extraction_failed() -> None:
    with pytest.raises(HyperWikiError) as raised:
        extract_plaintext("中文".encode("gb18030"), SETTINGS)
    assert raised.value.code == ErrorCode.EXTRACTION_FAILED


def test_extractor_components_by_content_type() -> None:
    pdf = extractor_component("application/pdf")
    assert pdf is not None and pdf.name == "pypdfium2" and pdf.runtime is not None
    assert pdf.runtime.startswith("pdfium ")
    text = extractor_component("text/plain; charset=utf-8")
    assert text is not None and text.name == "hyperwiki.plaintext"
    assert extractor_component("text/html") is None
    assert extractor_component("application/epub+zip") is None


def test_settings_reject_tiny_chunks() -> None:
    with pytest.raises(ValueError, match="chunk_chars"):
        ExtractionSettings(chunk_chars=10)
