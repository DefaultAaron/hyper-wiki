"""HW-014 smoke test: extract evidence from a real Zotero library.

Syncs the library from a copy of its database into an empty private workspace, plans and runs
text extraction, and checks every stored span:
- its text equals the slice of its stored page text;
- its locator names the extraction, the attachment version's SHA-256, and its own offsets;
- its ID is derived from its locator;
- its bounding box lies on its page.

It then plans again (expecting no new work), forces a full re-extraction (expecting identical
evidence IDs), and confirms Zotero's database files did not change.

Prints and writes aggregates only: no titles, paths, keys, or text. The workspace must be
outside the repository and empty, because it holds a private copy of the library.

    uv run --locked python experiments/hw014-evidence/smoke.py \
        --zotero-dir ~/Zotero --workspace /path/outside/the/repo
"""

import argparse
import json
import os
import re
import sqlite3
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import pypdfium2.version as pdfium_version

from hyperwiki.adapters.zotero_db import ZOTERO_DATABASE
from hyperwiki.adapters.zotero_profile import discover_zotero
from hyperwiki.domain.evidence import EvidenceLocator, make_evidence_id
from hyperwiki.services.evidence import (
    evidence_coverage,
    extract_evidence,
    plan_extraction,
    read_evidence,
)
from hyperwiki.services.zotero_sync import sync_zotero
from hyperwiki.storage.database import open_database

REPO_ROOT = Path(__file__).resolve().parents[2]
DATABASE = "hyperwiki.sqlite3"
HAN = re.compile("[㐀-䶿一-鿿豈-﫿]")
BOX_TOLERANCE = 1.0


def zotero_state(zotero_dir: Path) -> list[tuple[str, int, int]]:
    return sorted(
        (path.name, path.stat().st_size, path.stat().st_mtime_ns)
        for path in zotero_dir.glob(ZOTERO_DATABASE + "*")
    )


def timed(label: str, timings: dict[str, float], started: float) -> None:
    timings[label] = round(time.perf_counter() - started, 2)


def evidence_ids(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT id FROM evidence")}


def check_spans(conn: sqlite3.Connection) -> dict[str, Any]:
    pages = {
        (row["extraction_id"], row["page_index"]): row
        for row in conn.execute("SELECT * FROM extraction_pages")
    }
    versions = dict(
        conn.execute(
            """
            SELECT x.id, v.sha256 FROM extractions AS x
            JOIN attachment_versions AS v ON v.id = x.attachment_version_id
            """
        ).fetchall()
    )
    lengths: list[int] = []
    quote_mismatch = locator_mismatch = id_mismatch = 0
    no_box = box_off_page = han_spans = 0
    for row in conn.execute("SELECT * FROM evidence"):
        page = pages[(row["extraction_id"], row["page_index"])]
        text = row["text"]
        lengths.append(len(text))
        han_spans += bool(HAN.search(text))
        if page["text"][row["char_start"] : row["char_end"]] != text:
            quote_mismatch += 1
        locator = EvidenceLocator.model_validate_json(row["locator_json"])
        if (
            locator.extractor != row["extraction_id"]
            or locator.attachment_sha256 != versions[row["extraction_id"]]
            or (locator.page_index, locator.char_start, locator.char_end)
            != (row["page_index"], row["char_start"], row["char_end"])
        ):
            locator_mismatch += 1
        if make_evidence_id(locator) != row["id"]:
            id_mismatch += 1
        box = locator.bbox
        if box is None:
            no_box += 1
        elif page["width"] is not None and (
            box.x0 < -BOX_TOLERANCE
            or box.y0 < -BOX_TOLERANCE
            or box.x1 > page["width"] + BOX_TOLERANCE
            or box.y1 > page["height"] + BOX_TOLERANCE
        ):
            box_off_page += 1
    lengths.sort()
    return {
        "spans": len(lengths),
        "span_chars": {
            "median": statistics.median(lengths) if lengths else 0,
            "p95": lengths[int(0.95 * (len(lengths) - 1))] if lengths else 0,
            "max": lengths[-1] if lengths else 0,
        },
        "spans_with_han": han_spans,
        "quote_mismatches": quote_mismatch,
        "locator_mismatches": locator_mismatch,
        "id_mismatches": id_mismatch,
        "spans_without_box": no_box,
        "boxes_off_page": box_off_page,
    }


def page_facts(conn: sqlite3.Connection) -> dict[str, Any]:
    labelled = conn.execute(
        "SELECT COUNT(DISTINCT extraction_id) FROM extraction_pages WHERE page_label IS NOT NULL"
    ).fetchone()[0]
    mostly_han = 0
    for (text,) in conn.execute(
        "SELECT group_concat(text, '') FROM extraction_pages GROUP BY extraction_id"
    ):
        letters = [char for char in text or "" if char.isalpha()]
        if letters and sum(bool(HAN.match(char)) for char in letters) > len(letters) / 2:
            mostly_han += 1
    return {
        "extractions_with_page_labels": labelled,
        "extractions_mostly_han": mostly_han,
        "pages_with_han": conn.execute(
            "SELECT COUNT(*) FROM extraction_pages WHERE text GLOB '*[一-鿿]*'"
        ).fetchone()[0],
    }


def main(args: argparse.Namespace) -> int:
    workspace: Path = args.workspace.resolve()
    if workspace.is_relative_to(REPO_ROOT):
        raise SystemExit("the workspace must be outside the repository")
    if workspace.exists() and any(workspace.iterdir()):
        raise SystemExit("the workspace must be empty")
    zotero_dir: Path = args.zotero_dir.expanduser().resolve()
    zotero_before = zotero_state(zotero_dir)
    location = discover_zotero(
        data_dir=zotero_dir, home=Path.home(), environ=os.environ, platform=sys.platform
    )
    timings: dict[str, float] = {}
    summary: dict[str, Any] = {
        "extractor": f"pypdfium2 {pdfium_version.PYPDFIUM_INFO}, "
        f"pdfium {pdfium_version.PDFIUM_INFO.build}"
    }
    conn = open_database(workspace / DATABASE)
    try:
        started = time.perf_counter()
        sync = sync_zotero(conn, location)
        timed("sync_seconds", timings, started)
        library_id = sync.library_id
        summary["library"] = {
            "attachments": sync.import_report.counts.attachments,
            "attachments_by_availability": sync.import_report.counts.attachments_by_availability,
        }

        started = time.perf_counter()
        plan = plan_extraction(conn)[library_id]
        timed("plan_seconds", timings, started)
        started = time.perf_counter()
        worker = extract_evidence(conn)
        timed("extract_seconds", timings, started)
        coverage = evidence_coverage(conn, library_id)
        summary["plan"] = plan.model_dump(mode="json")
        summary["worker"] = worker.model_dump(mode="json")
        summary["coverage"] = coverage.model_dump(mode="json", exclude={"failures"})
        summary["coverage"]["failures_by_code"] = Counter(
            failure.code.value for failure in coverage.failures
        )
        if coverage.pages:
            summary["ms_per_page"] = round(1000 * timings["extract_seconds"] / coverage.pages, 2)

        started = time.perf_counter()
        summary["spans"] = check_spans(conn)
        timed("check_seconds", timings, started)
        summary["pages"] = page_facts(conn)
        started = time.perf_counter()
        ids = evidence_ids(conn)
        summary["read_evidence_all_current"] = all(
            read_evidence(conn, evidence_id).current for evidence_id in ids
        )
        timed("read_all_seconds", timings, started)

        replan = plan_extraction(conn)[library_id]
        summary["replan_queued"] = replan.queued
        started = time.perf_counter()
        forced = plan_extraction(conn, force=True)[library_id]
        rerun = extract_evidence(conn)
        timed("forced_rerun_seconds", timings, started)
        summary["forced_rerun"] = {
            "queued": forced.queued,
            "done": rerun.done,
            "failed": rerun.failed,
            "evidence_ids_identical": evidence_ids(conn) == ids,
        }
    finally:
        conn.close()
    database_bytes = sum(
        path.stat().st_size for path in workspace.glob(DATABASE + "*") if path.is_file()
    )
    summary["workspace_database_mb"] = round(database_bytes / 1e6, 1)
    summary["zotero_database_unchanged"] = zotero_state(zotero_dir) == zotero_before
    summary["timings"] = timings

    text = json.dumps(summary, ensure_ascii=False, indent=2)
    print(text)
    if args.summary is not None:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--zotero-dir", type=Path, default=Path("~/Zotero"))
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--summary", type=Path, help="also write the summary JSON here")
    raise SystemExit(main(parser.parse_args()))
