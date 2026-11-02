"""Score the HW-007 extractor outputs and write aggregate results.

Reads the private outputs of extract.py and extract_docling.py and writes only aggregates
(no paper text, titles, or per-PDF rows) to ``results/summary.json`` and ``results.md`` next
to this script.

Reading order is scored against the user's own Zotero metadata: each paper's abstract should
appear intact, in order, in the first three extracted pages. The score is the share of the
abstract's word 4-grams found there (Han characters count as single tokens). A Zotero
abstract can differ from the printed one, which lowers every extractor equally; the
"within 0.05 of the best" share compares extractors on the same paper.

    python3 experiments/hw007-pdf-extraction/score.py --private DIR
"""

import argparse
import json
import re
import statistics
import unicodedata
from collections.abc import Iterable
from itertools import combinations
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
LIGHT = ("pypdf", "pypdfium2", "pdfminer", "pdfplumber")
ALL = (*LIGHT, "docling")
HAN_CLASS = "㐀-䶿一-鿿豈-﫿"
HAN = re.compile(f"[{HAN_CLASS}]")
WORD = re.compile(r"[^\W_]+")
HAN_OR_OTHER = re.compile(f"[{HAN_CLASS}]|[^{HAN_CLASS}]+")
# A line-break hyphen: "-", a soft hyphen, or U+FFFE (PDFium's marker for a hyphen it
# found at a line end), then the line break.
LINE_HYPHEN = re.compile("(\\w)[-\u00ad\ufffe][ \t]*\n[ \t]*(\\w)")
SOFT_HYPHEN = re.compile("[\u00ad\ufffe]")
CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
CID = re.compile(r"\(cid:\d+\)")
PUA = re.compile("[-]")
RADICAL = re.compile("[⺀-⻿⼀-⿟]")
LIGATURE = re.compile("[ﬀ-ﬆ]")
LONG_WORD = re.compile(r"[A-Za-z]{26,}")
CAPTION = re.compile(r"(?mi)^\s*table\s+([0-9]+|[ivx]+)\b|^\s*表\s*([0-9]+)")
EMPTY_PAGE_CHARS = 20
ORDER_PAGES = 3
COMPARE_PAGES = 40


def normalize(text: str) -> str:
    """Unify line endings, join words split by a line-break hyphen, NFKC, casefold."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = SOFT_HYPHEN.sub("", LINE_HYPHEN.sub(r"\1\2", text))
    return unicodedata.normalize("NFKC", text).casefold()


def tokens(text: str) -> list[str]:
    out: list[str] = []
    for word in WORD.findall(normalize(text)):
        out.extend(HAN_OR_OTHER.findall(word))
    return out


def ngrams(items: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(items[i : i + n]) for i in range(len(items) - n + 1)}


def recall(reference: str, text: str, n: int) -> float | None:
    wanted = ngrams(tokens(reference), n)
    if not wanted:
        return None
    return len(wanted & ngrams(tokens(text), n)) / len(wanted)


def per_10k(pattern: re.Pattern[str], text: str) -> float:
    return 1e4 * len(pattern.findall(text)) / max(len(text), 1)


def mean(values: Iterable[float]) -> float | None:
    values = list(values)
    return round(statistics.fmean(values), 3) if values else None


def quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 3)


def load(private: Path, extractor: str) -> dict[str, dict[str, Any]]:
    folder = private / "out" / extractor
    if not folder.is_dir():
        return {}
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8")) for path in folder.glob("p*.json")
    }


def run_info(private: Path, extractor: str) -> dict[str, Any]:
    path = private / "out" / extractor / "_run.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def coverage(
    outputs: dict[str, dict[str, Any]],
    reference_pages: dict[str, int],
    timing: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Errors, empty pages, defects, and speed. ``timing`` holds outputs of an idle rerun."""
    ok = [o for o in outputs.values() if o["error"] is None]
    pages = [page for o in ok for page in o["pages"]]
    text = "".join(pages)
    empty = [sum(len(p.strip()) < EMPTY_PAGE_CHARS for p in o["pages"]) for o in ok]
    timed = timing if timing else outputs
    seconds = [timed[o["id"]]["seconds"] for o in ok if o["id"] in timed]
    return {
        "pdfs": len(outputs),
        "errors": sum(
            o["error"] is not None and "timeout" not in o["error"] for o in outputs.values()
        ),
        "timeouts": sum(
            o["error"] is not None and "timeout" in o["error"] for o in outputs.values()
        ),
        "error_kinds": sorted({o["error"].split(":")[0] for o in outputs.values() if o["error"]}),
        "pages": len(pages),
        "page_count_mismatches": sum(
            len(o["pages"]) != reference_pages.get(o["id"], len(o["pages"]))
            and len(o["pages"]) < o.get("max_pages", 10**9)
            for o in ok
        ),
        "empty_pages": sum(empty),
        "pdfs_with_empty_pages": sum(e > 0 for e in empty),
        "pdfs_mostly_empty": sum(e > len(o["pages"]) / 2 for e, o in zip(empty, ok, strict=True)),
        "total_seconds": round(sum(seconds), 1),
        "seconds_per_page": round(sum(seconds) / max(len(pages), 1), 4),
        "median_seconds_per_pdf": quantile(seconds, 0.5),
        "p95_seconds_per_pdf": quantile(seconds, 0.95),
        "max_seconds_per_pdf": round(max(seconds), 2) if seconds else None,
        "chars_per_page": round(len(text) / max(len(pages), 1)),
        "pdfs_with_lone_surrogates": sum(o.get("lone_surrogates", 0) > 0 for o in ok),
        "per_10k_chars": {
            "replacement_char": round(per_10k(re.compile("�"), text), 3),
            "cid_codes": round(per_10k(CID, text), 3),
            "private_use": round(per_10k(PUA, text), 3),
            "cjk_radicals": round(per_10k(RADICAL, text), 3),
            "ligatures": round(per_10k(LIGATURE, text), 3),
            "control_chars": round(per_10k(CONTROL, text), 3),
            "glued_words_26_letters": round(per_10k(LONG_WORD, text), 3),
        },
    }


def order_scores(
    outputs: dict[str, dict[str, dict[str, Any]]],
    records: list[dict[str, Any]],
    extractors: tuple[str, ...],
) -> dict[str, Any]:
    scores: dict[str, dict[str, float]] = {e: {} for e in extractors}
    titles: dict[str, list[float]] = {e: [] for e in extractors}
    for record in records:
        if record["chinese"] or record["abstract_words"] < 40:
            continue
        if not all(
            record["id"] in outputs[e] and outputs[e][record["id"]]["error"] is None
            for e in extractors
        ):
            continue
        for e in extractors:
            pages = outputs[e][record["id"]]["pages"]
            first = "\n".join(pages[:ORDER_PAGES])
            value = recall(record["abstract"], first, 4)
            if value is not None:
                scores[e][record["id"]] = value
            title = recall(record["title"], "\n".join(pages[:2]), 2)
            if title is not None:
                titles[e].append(title)
    common = set.intersection(*(set(s) for s in scores.values())) if scores else set()
    types = {r["id"]: r["item_type"] for r in records}
    result: dict[str, Any] = {"pdfs": len(common)}
    for e in extractors:
        values = [scores[e][i] for i in common]
        best = [max(scores[x][i] for x in extractors) for i in common]
        by_type: dict[str, float | None] = {}
        for item_type in sorted({types[i] for i in common}):
            by_type[item_type] = mean(scores[e][i] for i in common if types[i] == item_type)
        result[e] = {
            "abstract_4gram_recall_mean": mean(values),
            "abstract_intact_share": mean(v >= 0.9 for v in values),
            "abstract_broken_share": mean(v < 0.5 for v in values),
            "within_005_of_best_share": mean(
                scores[e][i] >= b - 0.05 for i, b in zip(common, best, strict=True)
            ),
            "by_item_type": by_type,
            "title_2gram_recall_mean": mean(titles[e]),
        }
    return result


def tables(
    outputs: dict[str, dict[str, dict[str, Any]]], sample: list[dict[str, Any]]
) -> dict[str, Any]:
    captions_total = 0
    detected: dict[str, int] = {"pdfplumber": 0, "docling": 0}
    matched: dict[str, int] = {"pdfplumber": 0, "docling": 0}
    extra: dict[str, int] = {"pdfplumber": 0, "docling": 0}
    filled: dict[str, list[int]] = {"pdfplumber": [], "docling": []}
    pdfs = 0
    for record in sample:
        reference = outputs["pypdfium2"].get(record["id"])
        if record["chinese"] or reference is None or reference["error"]:
            continue
        if any(record["id"] not in outputs[e] for e in detected):
            continue
        text = "\n".join(reference["pages"][:COMPARE_PAGES])
        captions = len({m[0] or m[1] for m in CAPTION.findall(text)})
        captions_total += captions
        pdfs += 1
        for e in detected:
            found = [
                t for t in outputs[e][record["id"]].get("tables", []) if t["page"] < COMPARE_PAGES
            ]
            detected[e] += len(found)
            matched[e] += min(len(found), captions)
            extra[e] += max(len(found) - captions, 0)
            filled[e].extend(t["filled_cells"] for t in found)
    return {
        "pdfs": pdfs,
        "table_captions": captions_total,
        **{
            e: {
                "tables_detected": detected[e],
                "captions_matched_by_count": matched[e],
                "detected_beyond_captions": extra[e],
                "median_filled_cells": quantile([float(x) for x in filled[e]], 0.5),
            }
            for e in detected
        },
    }


def chinese(
    outputs: dict[str, dict[str, dict[str, Any]]],
    records: list[dict[str, Any]],
    extractors: tuple[str, ...],
) -> dict[str, Any]:
    books = [r for r in records if r["chinese"]]
    result: dict[str, Any] = {"pdfs": len(books), "pages_compared": COMPARE_PAGES}
    texts: dict[str, str] = {}
    for e in extractors:
        han = total = empty = 0
        artifacts = {"replacement_char": 0, "cid_codes": 0, "private_use": 0, "cjk_radicals": 0}
        title_scores: list[float] = []
        joined = []
        for book in books:
            output = outputs[e].get(book["id"])
            if output is None or output["error"]:
                continue
            pages = output["pages"][:COMPARE_PAGES]
            text = "\n".join(pages)
            joined.append(text)
            han += len(HAN.findall(text))
            total += len(re.sub(r"\s", "", text))
            empty += sum(len(p.strip()) < EMPTY_PAGE_CHARS for p in pages)
            artifacts["replacement_char"] += text.count("�")
            artifacts["cid_codes"] += len(CID.findall(text))
            artifacts["private_use"] += len(PUA.findall(text))
            artifacts["cjk_radicals"] += len(RADICAL.findall(text))
            title = recall(book["title"], "\n".join(pages[:5]), 3)
            if title is not None:
                title_scores.append(title)
        texts[e] = normalize("\n".join(joined))
        result[e] = {
            "han_chars": han,
            "han_share_of_non_space": round(han / max(total, 1), 3),
            "empty_pages": empty,
            "artifact_counts": artifacts,
            "title_char_3gram_recall_mean": mean(title_scores),
        }
    bigrams = {e: ngrams(HAN.findall(texts[e]), 2) for e in extractors}
    result["han_bigram_jaccard"] = {
        f"{a}~{b}": round(len(bigrams[a] & bigrams[b]) / max(len(bigrams[a] | bigrams[b]), 1), 3)
        for a, b in combinations(extractors, 2)
    }
    return result


def page_labels(outputs: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    with_labels = [o for o in outputs["pypdf"].values() if o.get("has_page_labels")]
    agree = 0
    for output in with_labels:
        other = outputs["pypdfium2"].get(output["id"])
        if other and other.get("labels") and other["labels"] == output["labels"]:
            agree += 1
    return {
        "pdfs_with_page_labels": len(with_labels),
        "pypdf_and_pypdfium2_labels_identical": agree,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--private", type=Path, required=True)
    parser.add_argument(
        "--timing", type=Path, help="private directory of a rerun on an otherwise idle machine"
    )
    args = parser.parse_args()
    records = json.loads((args.private / "pdfs.json").read_text(encoding="utf-8"))
    sample = [r for r in records if r["sample"]]
    outputs = {e: load(args.private, e) for e in ALL}
    reference_pages = {
        i: len(o["pages"]) for i, o in outputs["pypdfium2"].items() if not o["error"]
    }
    sample_ids = {r["id"] for r in sample}

    def restricted(e: str) -> dict[str, dict[str, Any]]:
        return {
            i: {**o, "pages": o["pages"][:COMPARE_PAGES], "max_pages": COMPARE_PAGES}
            for i, o in outputs[e].items()
            if i in sample_ids
        }

    timing = {e: load(args.timing, e) for e in ALL} if args.timing else {e: {} for e in ALL}
    present = tuple(e for e in ALL if outputs[e])
    light = tuple(e for e in LIGHT if outputs[e])
    summary = {
        "library": {
            "pdfs": len(records),
            "with_abstract_40_words": sum(
                r["abstract_words"] >= 40 and not r["chinese"] for r in records
            ),
            "sample": len(sample),
            "chinese_books": sum(r["chinese"] for r in records),
        },
        "runs": {
            e: run_info(args.timing, e) if timing[e] else run_info(args.private, e) for e in present
        },
        "timing_rerun_on_idle_machine": {e: bool(timing[e]) for e in present},
        "all_pdfs": {
            "coverage": {e: coverage(outputs[e], reference_pages, timing[e]) for e in light},
            "reading_order": order_scores(outputs, records, light),
            "page_labels": page_labels(outputs),
        },
        "sample_first_40_pages": {
            "coverage": {e: coverage(restricted(e), reference_pages, timing[e]) for e in present},
            "reading_order": order_scores({e: restricted(e) for e in present}, sample, present),
            "tables": tables({e: restricted(e) for e in present}, sample)
            if "docling" in present
            else None,
            "chinese": chinese({e: restricted(e) for e in present}, records, present),
        },
    }
    for run in summary["runs"].values():
        for key in ("wall_seconds", "peak_rss_mb", "load_seconds"):
            if key in run:
                run[key] = round(run[key], 1)
    results = HERE / "results"
    results.mkdir(exist_ok=True)
    (results / "summary.json").write_text(
        json.dumps(summary, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
