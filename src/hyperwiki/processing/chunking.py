"""Cut a page's text into evidence spans (chunker version 1).

Spans never cross pages, hold at most ``max_chars`` characters, are trimmed of surrounding
whitespace, and do not overlap. A span ends at the last paragraph break in its window, else
the last line break, else the last Latin or Chinese sentence end, else the last whitespace;
only cut points in the second half of the window count. Without one, the span is cut at
``max_chars``.
"""

CHUNKER_VERSION = 1

_LATIN_SENTENCE_ENDS = (". ", "? ", "! ")
# Ideographic full stop and the fullwidth exclamation mark, question mark, and semicolon.
_CJK_SENTENCE_ENDS = ("\u3002", "\uff01", "\uff1f", "\uff1b")


def _cut(text: str, start: int, limit: int) -> int:
    """Where the span starting at ``start`` ends; ``limit`` is ``start + max_chars``."""
    low = start + (limit - start) // 2
    paragraph = text.rfind("\n\n", low, limit + 2)
    if paragraph > low:
        return paragraph
    line = text.rfind("\n", low, limit + 1)
    if line > low:
        return line
    sentence = max(
        max(text.rfind(end, low, limit + 1) for end in _LATIN_SENTENCE_ENDS),
        max(text.rfind(end, low, limit) for end in _CJK_SENTENCE_ENDS),
    )
    if sentence >= low:
        return sentence + 1
    for position in range(limit, low, -1):
        if text[position].isspace():
            return position
    return limit


def chunk_spans(text: str, *, max_chars: int) -> list[tuple[int, int]]:
    """Return ``(start, end)`` character offsets of each span, in order."""
    if max_chars < 2:
        raise ValueError("max_chars must be at least 2")
    spans: list[tuple[int, int]] = []
    length = len(text)
    position = 0
    while True:
        while position < length and text[position].isspace():
            position += 1
        if position >= length:
            return spans
        end = (
            length if length - position <= max_chars else _cut(text, position, position + max_chars)
        )
        trimmed = end
        while text[trimmed - 1].isspace():
            trimmed -= 1
        spans.append((position, trimmed))
        position = end
