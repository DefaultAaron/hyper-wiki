# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "fonttools==4.63.0",
#     "pdfminer.six==20260107",
#     "pdfplumber==0.11.10",
#     "pypdf[fonts]==6.19.0",
#     "pypdfium2==5.14.0",
# ]
# ///
"""Run one lightweight PDF extractor over the PDFs listed by prepare.py.

Writes one JSON file per PDF (page texts, page labels, tables for pdfplumber, timing,
errors) to ``<private>/out/<extractor>/``. Outputs contain paper text and stay private.
Existing outputs are kept, so an interrupted run resumes.

    uv run experiments/hw007-pdf-extraction/extract.py pypdfium2 --private DIR [--sample]
"""

import argparse
import json
import re
import resource
import signal
import sys
import time
from collections.abc import Callable
from importlib.metadata import version
from pathlib import Path
from types import FrameType
from typing import Any

TIME_LIMIT_SECONDS = 300

Result = dict[str, Any]


class TimeLimit(Exception):
    pass


def _alarm(signum: int, frame: FrameType | None) -> None:
    raise TimeLimit


def run_pypdf(path: Path) -> Result:
    from pypdf import PdfReader

    reader = PdfReader(path)
    pages = [page.extract_text() or "" for page in reader.pages]
    has_labels = "/PageLabels" in reader.trailer["/Root"]
    return {"pages": pages, "labels": list(reader.page_labels), "has_page_labels": has_labels}


def run_pypdfium2(path: Path) -> Result:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(path)
    try:
        pages: list[str] = []
        labels: list[str] | None = [] if hasattr(pdf, "get_page_label") else None
        for index in range(len(pdf)):
            page = pdf[index]
            textpage = page.get_textpage()
            pages.append(textpage.get_text_range())
            if labels is not None:
                labels.append(pdf.get_page_label(index) or "")
            textpage.close()
            page.close()
    finally:
        pdf.close()
    return {"pages": pages, "labels": labels}


def run_pdfminer(path: Path) -> Result:
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LAParams, LTTextContainer

    pages = [
        "".join(e.get_text() for e in layout if isinstance(e, LTTextContainer))
        for layout in extract_pages(path, laparams=LAParams())
    ]
    return {"pages": pages, "labels": None}


def run_pdfplumber(path: Path) -> Result:
    import pdfplumber

    pages: list[str] = []
    tables: list[dict[str, int]] = []
    table_seconds = 0.0
    with pdfplumber.open(path) as pdf:
        for index, page in enumerate(pdf.pages):
            pages.append(page.extract_text() or "")
            started = time.perf_counter()
            for table in page.find_tables():
                rows = table.extract()
                tables.append(
                    {
                        "page": index,
                        "rows": len(rows),
                        "cols": max((len(row) for row in rows), default=0),
                        "filled_cells": sum(1 for row in rows for cell in row if cell),
                    }
                )
            table_seconds += time.perf_counter() - started
            page.close()
    return {"pages": pages, "labels": None, "tables": tables, "table_seconds": table_seconds}


EXTRACTORS: dict[str, tuple[str, Callable[[Path], Result]]] = {
    "pypdf": ("pypdf", run_pypdf),
    "pypdfium2": ("pypdfium2", run_pypdfium2),
    "pdfminer": ("pdfminer.six", run_pdfminer),
    "pdfplumber": ("pdfplumber", run_pdfplumber),
}


SURROGATE = re.compile("[\ud800-\udfff]")


def sanitize(result: dict[str, Any]) -> None:
    """Replace lone surrogates (invalid text some extractors emit) and count them."""
    count = 0
    pages = []
    for page in result.get("pages", []):
        count += len(SURROGATE.findall(page))
        pages.append(SURROGATE.sub("\ufffd", page))
    result["pages"] = pages
    result["lone_surrogates"] = count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("extractor", choices=sorted(EXTRACTORS))
    parser.add_argument("--private", type=Path, required=True)
    parser.add_argument("--sample", action="store_true", help="only the marked sample")
    args = parser.parse_args()
    package, run = EXTRACTORS[args.extractor]
    records = json.loads((args.private / "pdfs.json").read_text(encoding="utf-8"))
    if args.sample:
        records = [r for r in records if r["sample"]]
    out = args.private / "out" / args.extractor
    out.mkdir(parents=True, exist_ok=True)
    signal.signal(signal.SIGALRM, _alarm)
    started_all = time.perf_counter()
    for number, record in enumerate(records, start=1):
        target = out / f"{record['id']}.json"
        if target.exists():
            continue
        result: Result = {"id": record["id"], "extractor": args.extractor}
        started = time.perf_counter()
        signal.alarm(TIME_LIMIT_SECONDS)
        try:
            result.update(run(Path(record["path"])))
            result["error"] = None
        except TimeLimit:
            result.update(pages=[], labels=None, error=f"timeout after {TIME_LIMIT_SECONDS} s")
        except Exception as error:
            result.update(pages=[], labels=None, error=f"{type(error).__name__}: {error}"[:300])
        finally:
            signal.alarm(0)
        result["seconds"] = time.perf_counter() - started
        sanitize(result)
        partial = target.with_suffix(".tmp")
        partial.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        partial.replace(target)  # atomic, so an interrupted write is never mistaken for done
        if number % 25 == 0:
            print(f"{args.extractor}: {number}/{len(records)}", file=sys.stderr, flush=True)
    summary = {
        "extractor": args.extractor,
        "package": package,
        "version": version(package),
        "python": sys.version.split()[0],
        "pdfs": len(records),
        "wall_seconds": time.perf_counter() - started_all,
        "peak_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6,
    }
    (out / "_run.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
