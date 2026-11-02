# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "docling==2.145.0",
#     "opencv-python-headless==4.13.0.92",
#     "torch==2.13.0",
#     "torchvision==0.28.0",
#     "transformers==5.22.1",
# ]
# ///
"""Run Docling (layout and table models, CPU, OCR off) over the sample PDFs.

Writes the same per-PDF JSON as extract.py to ``<private>/out/docling/``: page texts in
Docling's reading order, and the tables it found. Only the first ``--max-pages`` pages of
each PDF are converted, so books stay affordable; the scorer compares every extractor on
the same pages.

    HF_HOME=/path/to/model-cache uv run experiments/hw007-pdf-extraction/extract_docling.py \
        --private DIR
"""

import argparse
import json
import re
import resource
import sys
import time
from collections import defaultdict
from importlib.metadata import version
from pathlib import Path
from typing import Any


def build_converter(threads: int) -> Any:
    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions()
    options.do_ocr = False
    options.do_table_structure = True
    options.accelerator_options = AcceleratorOptions(
        num_threads=threads, device=AcceleratorDevice.CPU
    )
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )
    converter.initialize_pipeline(InputFormat.PDF)
    return converter


def convert(converter: Any, path: Path, max_pages: int) -> dict[str, Any]:
    from docling_core.types.doc import TableItem, TextItem

    result = converter.convert(path, page_range=(1, max_pages), raises_on_error=True)
    document = result.document
    page_count = len(document.pages)
    texts: dict[int, list[str]] = defaultdict(list)
    tables: list[dict[str, int]] = []
    for item, _level in document.iterate_items():
        if not item.prov:
            continue
        page = item.prov[0].page_no - 1
        if isinstance(item, TableItem):
            cells = item.data.table_cells
            tables.append(
                {
                    "page": page,
                    "rows": item.data.num_rows,
                    "cols": item.data.num_cols,
                    "filled_cells": sum(1 for cell in cells if cell.text.strip()),
                }
            )
            texts[page].append(item.export_to_markdown(doc=document))
        elif isinstance(item, TextItem):
            texts[page].append(item.text)
    pages = ["\n".join(texts.get(index, [])) for index in range(page_count)]
    return {"pages": pages, "labels": None, "tables": tables}


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
    parser.add_argument("--private", type=Path, required=True)
    parser.add_argument("--max-pages", type=int, default=40)
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    records = [
        r
        for r in json.loads((args.private / "pdfs.json").read_text(encoding="utf-8"))
        if r["sample"]
    ]
    out = args.private / "out" / "docling"
    out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    converter = build_converter(args.threads)
    load_seconds = time.perf_counter() - started
    started_all = time.perf_counter()
    for number, record in enumerate(records, start=1):
        target = out / f"{record['id']}.json"
        if target.exists():
            continue
        result: dict[str, Any] = {"id": record["id"], "extractor": "docling"}
        started = time.perf_counter()
        try:
            result.update(convert(converter, Path(record["path"]), args.max_pages))
            result["error"] = None
        except Exception as error:
            result.update(pages=[], labels=None, error=f"{type(error).__name__}: {error}"[:300])
        result["seconds"] = time.perf_counter() - started
        sanitize(result)
        result["max_pages"] = args.max_pages
        partial = target.with_suffix(".tmp")
        partial.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        partial.replace(target)  # atomic, so an interrupted write is never mistaken for done
        print(f"docling: {number}/{len(records)}", file=sys.stderr, flush=True)
    summary = {
        "extractor": "docling",
        "package": "docling",
        "version": version("docling"),
        "torch": version("torch"),
        "python": sys.version.split()[0],
        "pdfs": len(records),
        "max_pages": args.max_pages,
        "threads": args.threads,
        "device": "cpu",
        "load_seconds": load_seconds,
        "wall_seconds": time.perf_counter() - started_all,
        "peak_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6,
    }
    (out / "_run.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
