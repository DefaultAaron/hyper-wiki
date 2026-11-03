"""Build small synthetic PDFs whose text PDFium extracts exactly, with no font files.

Every line is drawn with one Type0 font using Identity-H encoding. Each distinct character
gets a two-byte code, and a ToUnicode map gives its text, so English, Chinese, and characters
outside the Basic Multilingual Plane all extract without any glyph data. ASCII characters are
half an em wide and all others a full em; lines are 14 points apart from the top of a US Letter
page.
"""

from collections.abc import Sequence

PAGE_WIDTH = 612
PAGE_HEIGHT = 792
LEFT = 72
TOP_BASELINE = 760
LINE_GAP = 14
FONT_SIZE = 10


def _codes(pages: Sequence[Sequence[str]]) -> dict[str, int]:
    chars = sorted({char for lines in pages for line in lines for char in line})
    codes: dict[str, int] = {}
    private = 0xE000
    for char in chars:
        code = ord(char)
        if code > 0xFFFF or 0xD800 <= code <= 0xDFFF or 0xE000 <= code <= 0xF8FF:
            codes[char] = private
            private += 1
        else:
            codes[char] = code
    return codes


def _utf16_hex(char: str) -> str:
    return char.encode("utf-16-be").hex().upper()


def _to_unicode(codes: dict[str, int]) -> bytes:
    lines = [
        "/CIDInit /ProcSet findresource begin",
        "12 dict begin",
        "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /Adobe-Identity-UCS def",
        "/CMapType 2 def",
        "1 begincodespacerange",
        "<0000> <FFFF>",
        "endcodespacerange",
    ]
    entries = sorted(codes.items(), key=lambda item: item[1])
    for offset in range(0, len(entries), 100):
        block = entries[offset : offset + 100]
        lines.append(f"{len(block)} beginbfchar")
        lines.extend(f"<{code:04X}> <{_utf16_hex(char)}>" for char, code in block)
        lines.append("endbfchar")
    lines += ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]
    return "\n".join(lines).encode("ascii")


def _stream(data: bytes) -> bytes:
    return b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream"


def make_pdf(pages: Sequence[Sequence[str]], *, page_labels: str | None = None) -> bytes:
    """Return a PDF with one page per entry, each a list of text lines.

    ``page_labels`` is the content of the catalog's ``/PageLabels /Nums`` array, for example
    ``"0 << /S /r >> 2 << /S /D >>"``.
    """
    codes = _codes(pages)
    objects: dict[int, bytes] = {}
    catalog = "<< /Type /Catalog /Pages 2 0 R"
    if page_labels is not None:
        catalog += f" /PageLabels << /Nums [{page_labels}] >>"
    objects[1] = (catalog + " >>").encode("ascii")
    objects[3] = (
        b"<< /Type /Font /Subtype /Type0 /BaseFont /HyperWikiTest /Encoding /Identity-H "
        b"/DescendantFonts [4 0 R] /ToUnicode 5 0 R >>"
    )
    objects[4] = (
        b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /HyperWikiTest "
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
        b"/DW 1000 /W [32 126 500] /CIDToGIDMap /Identity >>"
    )
    objects[5] = _stream(_to_unicode(codes))
    kids = []
    number = 6
    for lines in pages:
        operations = []
        for row, line in enumerate(lines):
            text = "".join(f"{codes[char]:04X}" for char in line)
            baseline = TOP_BASELINE - row * LINE_GAP
            operations.append(f"BT /F1 {FONT_SIZE} Tf {LEFT} {baseline} Td <{text}> Tj ET")
        objects[number] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            f"/Contents {number + 1} 0 R /Resources << /Font << /F1 3 0 R >> >> >>"
        ).encode("ascii")
        objects[number + 1] = _stream("\n".join(operations).encode("ascii"))
        kids.append(f"{number} 0 R")
        number += 2
    objects[2] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>".encode()

    out = bytearray(b"%PDF-1.7\n")
    offsets: dict[int, int] = {}
    for key in sorted(objects):
        offsets[key] = len(out)
        out += b"%d 0 obj\n" % key + objects[key] + b"\nendobj\n"
    xref = len(out)
    size = max(objects) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % size
    for key in range(1, size):
        out += b"%010d 00000 n \n" % offsets[key]
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, xref)
    return bytes(out)
