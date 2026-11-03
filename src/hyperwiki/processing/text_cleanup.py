"""Cleanup of extracted page text (cleanup version 1, ADR 0006).

Only four things are rewritten, so that quotes stay faithful to the PDF:
1. ``\\r\\n`` becomes ``\\n``.
2. U+FFFE, PDFium's marker for a hyphen at a line end, is removed together with a line break
   directly after it. PDFium usually joins the two lines itself (``extrac\\uFFFEtion``).
3. C0 control characters other than tab and newline are removed; they come from glyphs
   without a Unicode mapping.
4. A single line break between two Han characters is removed, since Chinese has no spaces.

Each cleaned character keeps its position in the raw text in UTF-16 units, the unit PDFium
uses for text indices, so spans can be mapped back to character boxes.
"""

from hyperwiki.domain.common import Contract

CLEANUP_VERSION = 1

_HYPHEN_MARKER = "￾"


class CleanText(Contract):
    text: str
    sources: tuple[int, ...]


def is_han(char: str) -> bool:
    """CJK Unified Ideographs, Extension A, and the compatibility block (ADR 0005)."""
    code = ord(char)
    return 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF or 0xF900 <= code <= 0xFAFF


def _units(char: str) -> int:
    return 2 if ord(char) > 0xFFFF else 1


def clean_page_text(raw: str) -> CleanText:
    kept: list[tuple[str, int]] = []
    unit = 0
    index = 0
    length = len(raw)
    while index < length:
        char = raw[index]
        if char == _HYPHEN_MARKER:
            unit += 1
            index += 1
            if raw.startswith("\r\n", index):
                unit += 2
                index += 2
            elif index < length and raw[index] == "\n":
                unit += 1
                index += 1
            continue
        if char == "\r" and raw.startswith("\n", index + 1):
            unit += 1
            index += 1
            continue
        if ord(char) < 0x20 and char not in "\t\n":
            unit += 1
            index += 1
            continue
        kept.append((char, unit))
        unit += _units(char)
        index += 1

    joined: list[tuple[str, int]] = []
    for position, (char, source) in enumerate(kept):
        if (
            char == "\n"
            and joined
            and position + 1 < len(kept)
            and is_han(joined[-1][0])
            and is_han(kept[position + 1][0])
        ):
            continue
        joined.append((char, source))
    return CleanText(
        text="".join(char for char, _ in joined),
        sources=tuple(source for _, source in joined),
    )
