from hyperwiki.processing.text_cleanup import CleanText, clean_page_text, is_han


def test_crlf_becomes_lf() -> None:
    assert clean_page_text("first line\r\nsecond line").text == "first line\nsecond line"


def test_hyphen_marker_is_removed_with_following_break() -> None:
    assert clean_page_text("extrac￾tion").text == "extraction"
    assert clean_page_text("extrac￾\r\ntion").text == "extraction"
    assert clean_page_text("extrac￾\ntion").text == "extraction"


def test_c0_controls_removed_except_tab_and_newline() -> None:
    raw = "a\x00b\x02c\x03d\te\nf\rg\x1fh"
    assert clean_page_text(raw).text == "abcd\te\nfgh"


def test_line_break_between_han_is_joined() -> None:
    assert clean_page_text("中文\r\n论文").text == "中文论文"
    assert clean_page_text("中文\r\nPDF").text == "中文\nPDF"
    assert clean_page_text("中文\r\n\r\n论文").text == "中文\n\n论文"
    assert clean_page_text("（中文）\r\n论文").text == "（中文）\n论文"


def test_other_text_is_untouched() -> None:
    raw = "“Quotes” – ﬁ ligature, ２０２４, Ａ, é, 𝐀"
    assert clean_page_text(raw).text == raw


def test_sources_count_utf16_units() -> None:
    cleaned = clean_page_text("x 𝐀 y")
    assert cleaned == CleanText(text="x 𝐀 y", sources=(0, 1, 2, 4, 5))


def test_sources_skip_removed_characters() -> None:
    cleaned = clean_page_text("ab￾\r\ncd\r\n中\r\n文")
    assert cleaned.text == "abcd\n中文"
    assert cleaned.sources == (0, 1, 5, 6, 8, 9, 12)


def test_is_han_covers_adr_0005_ranges() -> None:
    assert all(is_han(c) for c in "中㐀䶿鿿豈﫿")
    assert not any(is_han(c) for c in "A。，ア한　")
