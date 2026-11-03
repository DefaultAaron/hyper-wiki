import itertools

import pytest

from hyperwiki.processing.chunking import chunk_spans


def _texts(text: str, max_chars: int) -> list[str]:
    return [text[start:end] for start, end in chunk_spans(text, max_chars=max_chars)]


def test_short_text_is_one_trimmed_span() -> None:
    text = "  \n Short page.\n\n "
    assert chunk_spans(text, max_chars=800) == [(4, 15)]


def test_spans_never_exceed_max_and_cover_all_text() -> None:
    words = [f"w{i}" + ("." if i % 7 == 0 else "") for i in range(600)]
    text = " ".join(words) + "\n\n" + "中文证据。" * 120 + "\n" + "x" * 950
    spans = chunk_spans(text, max_chars=200)
    assert all(0 < end - start <= 200 for start, end in spans)
    assert all(a_end <= b_start for (_, a_end), (b_start, _) in itertools.pairwise(spans))
    covered = {i for start, end in spans for i in range(start, end)}
    assert all(i in covered for i, char in enumerate(text) if not char.isspace())


def test_spans_prefer_paragraph_then_line_then_sentence_breaks() -> None:
    para = "a" * 60 + "\n\n" + "b" * 20 + "\n" + "c" * 10 + ". " + "d" * 40
    assert _texts(para, 100)[0] == "a" * 60
    line = "a" * 60 + "\n" + "b" * 20 + ". " + "c" * 40
    assert _texts(line, 100)[0] == "a" * 60
    sentence = "a" * 60 + ". " + "b" * 20 + " " + "c" * 40
    assert _texts(sentence, 100)[0] == "a" * 60 + "."


def test_break_in_first_half_is_ignored() -> None:
    text = "a" * 20 + "\n" + "b" * 60 + " " + "c" * 40
    assert _texts(text, 100)[0] == "a" * 20 + "\n" + "b" * 60


def test_chinese_sentence_end_is_a_break() -> None:
    text = "甲" * 70 + "。" + "乙" * 50
    assert _texts(text, 100) == ["甲" * 70 + "。", "乙" * 50]


def test_spans_are_trimmed_and_skip_blank_text() -> None:
    assert chunk_spans(" \n\t ", max_chars=100) == []
    assert chunk_spans("", max_chars=100) == []
    text = "a" * 70 + "   \n  " + "b" * 50
    assert _texts(text, 100) == ["a" * 70, "b" * 50]


def test_hard_cut_without_break() -> None:
    assert _texts("x" * 250, 100) == ["x" * 100, "x" * 100, "x" * 50]


def test_break_exactly_at_window_end_is_used() -> None:
    text = "a" * 100 + " " + "b" * 30
    assert _texts(text, 100) == ["a" * 100, "b" * 30]


def test_rejects_tiny_max() -> None:
    with pytest.raises(ValueError, match="max_chars"):
        chunk_spans("abc", max_chars=1)
