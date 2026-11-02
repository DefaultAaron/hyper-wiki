"""HW-012 smoke test on a real Zotero library.

Imports the library twice from a copy of its database, queues one job per current PDF
version, kills the worker process part-way, restarts it, and checks that every job finished
exactly once. The job re-reads each PDF and checks its SHA-256 against the recorded
attachment version; a short pause per job stands in for slower real processing so the kill
lands mid-run.

Prints counts and timings only: no titles, paths, or keys. The workspace must be outside
the repository and empty; it holds a private copy of library metadata.

    uv run --locked python experiments/hw012-real-library/smoke.py \
        --zotero-dir ~/Zotero --workspace /path/outside/the/repo
"""

import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any

from pydantic import JsonValue

from hyperwiki.adapters.zotero_db import ZOTERO_DATABASE, read_zotero_library
from hyperwiki.domain.work import Job, WorkFingerprint
from hyperwiki.processing.worker import JobContext, new_owner_id, run_worker, utc_now
from hyperwiki.services.library_import import import_loaded, library_status
from hyperwiki.storage.database import open_database
from hyperwiki.storage.job_repo import enqueue

REPO_ROOT = Path(__file__).resolve().parents[2]
DATABASE = "hyperwiki.sqlite3"
OPERATION = "smoke.verify_bytes"
CHUNK_BYTES = 1024 * 1024
HAN = re.compile("[㐀-䶿一-鿿豈-﫿]")


class VerifyBytes:
    """Re-read an attachment version's file and compare its SHA-256 with the record."""

    def __init__(self, conn: sqlite3.Connection, storage_root: Path, pace_seconds: float) -> None:
        self.paths = {
            row["id"]: storage_root / row["path"]
            for row in conn.execute(
                """
                SELECT v.id, a.path FROM attachment_versions AS v
                JOIN attachments AS a ON a.id = v.attachment_id
                """
            )
        }
        self.pace_seconds = pace_seconds

    def execute(self, job: Job, context: JobContext) -> dict[str, JsonValue]:
        version_id = job.spec.inputs["attachment_version"]
        digest = hashlib.sha256()
        size = 0
        with self.paths[version_id].open("rb") as handle:
            while chunk := handle.read(CHUNK_BYTES):
                digest.update(chunk)
                size += len(chunk)
        time.sleep(self.pace_seconds)
        return {"verified": digest.hexdigest() == job.spec.inputs["sha256"], "bytes": size}

    def store(
        self, conn: sqlite3.Connection, job: Job, result: dict[str, JsonValue]
    ) -> Mapping[str, JsonValue]:
        return result


def zotero_state(zotero_dir: Path) -> list[tuple[str, int, int]]:
    return sorted(
        (path.name, path.stat().st_size, path.stat().st_mtime_ns)
        for path in zotero_dir.glob(ZOTERO_DATABASE + "*")
    )


def count(conn: sqlite3.Connection, sql: str) -> int:
    value: int = conn.execute(sql).fetchone()[0]
    return value


def worker_main(args: argparse.Namespace) -> int:
    conn = open_database(args.workspace / DATABASE)
    try:
        handler = VerifyBytes(conn, args.zotero_dir / "storage", args.pace_ms / 1000)
        run_worker(
            conn,
            {OPERATION: handler},
            owner=new_owner_id(),
            lease=timedelta(seconds=args.lease_seconds),
        )
    finally:
        conn.close()
    return 0


def main(args: argparse.Namespace) -> int:
    workspace: Path = args.workspace.resolve()
    if workspace.is_relative_to(REPO_ROOT):
        raise SystemExit("the workspace must be outside the repository")
    if workspace.exists() and any(workspace.iterdir()):
        raise SystemExit("the workspace must be empty")
    zotero_dir: Path = args.zotero_dir.expanduser().resolve()
    zotero_before = zotero_state(zotero_dir)
    summary: dict[str, Any] = {}

    started = time.perf_counter()
    loaded = read_zotero_library(zotero_dir)
    read_seconds = time.perf_counter() - started
    conn = open_database(workspace / DATABASE)
    try:
        started = time.perf_counter()
        first = import_loaded(conn, loaded)
        import_seconds = time.perf_counter() - started
        item_ids = conn.execute("SELECT id FROM items ORDER BY id").fetchall()
        second = import_loaded(conn, read_zotero_library(zotero_dir))
        summary["import"] = {
            "read_seconds": round(read_seconds, 2),
            "import_seconds": round(import_seconds, 2),
            "counts": first.counts.model_dump(mode="json"),
            "problems": Counter(f"{p.code.value}/{p.entity}" for p in first.problems),
            "items_with_han_title": sum(bool(HAN.search(i.title)) for i in loaded.manifest.items),
            "reimport_counts_equal": second.counts == first.counts,
            "reimport_item_ids_equal": conn.execute("SELECT id FROM items ORDER BY id").fetchall()
            == item_ids,
        }

        pdf_versions = conn.execute(
            """
            SELECT v.id, v.sha256 FROM attachment_versions AS v
            JOIN attachments AS a ON a.id = v.attachment_id AND a.sha256 = v.sha256
            WHERE a.content_type = 'application/pdf'
            ORDER BY v.id
            """
        ).fetchall()

        def specs() -> list[WorkFingerprint]:
            return [
                WorkFingerprint(
                    operation=OPERATION,
                    operation_version=1,
                    inputs={"attachment_version": row["id"], "sha256": row["sha256"]},
                    schema_version=1,
                    config={"read_chunk_bytes": CHUNK_BYTES},
                )
                for row in pdf_versions
            ]

        outcomes = Counter(
            enqueue(conn, spec, subject=spec.inputs["attachment_version"], now=utc_now()).outcome
            for spec in specs()
        )
        total = len(pdf_versions)
        kill_at = max(1, int(total * args.kill_fraction))

        worker = subprocess.Popen(
            [
                sys.executable,
                __file__,
                "--worker",
                "--zotero-dir",
                str(zotero_dir),
                "--workspace",
                str(workspace),
                "--lease-seconds",
                str(args.lease_seconds),
                "--pace-ms",
                str(args.pace_ms),
            ]
        )
        started = time.perf_counter()
        while count(conn, "SELECT COUNT(*) FROM completed_work") < kill_at:
            if worker.poll() is not None:
                break
            time.sleep(0.01)
        worker.kill()
        worker.wait()
        done_at_kill = count(conn, "SELECT COUNT(*) FROM completed_work")
        leased_at_kill = count(conn, "SELECT COUNT(*) FROM jobs WHERE state = 'leased'")
        first_run_seconds = time.perf_counter() - started

        time.sleep(args.lease_seconds + 0.5)
        started = time.perf_counter()
        resumed = run_worker(
            conn,
            {OPERATION: VerifyBytes(conn, zotero_dir / "storage", 0.0)},
            owner=new_owner_id(),
            lease=timedelta(seconds=args.lease_seconds),
        )
        resume_seconds = time.perf_counter() - started

        summaries = [
            json.loads(row[0]) for row in conn.execute("SELECT summary_json FROM completed_work")
        ]
        again = Counter(
            enqueue(conn, spec, subject=spec.inputs["attachment_version"], now=utc_now()).outcome
            for spec in specs()
        )
        status = library_status(conn)
        summary["jobs"] = {
            "pdf_versions_queued": total,
            "enqueue_outcomes": outcomes,
            "killed_after_completed": done_at_kill,
            "leased_when_killed": leased_at_kill,
            "first_worker_seconds": round(first_run_seconds, 2),
            "resumed_worker": resumed.model_dump(),
            "resume_seconds": round(resume_seconds, 2),
            "completed_work_rows": len(summaries),
            "distinct_fingerprints": count(
                conn, "SELECT COUNT(DISTINCT fingerprint) FROM completed_work"
            ),
            "jobs_rows": count(conn, "SELECT COUNT(*) FROM jobs"),
            "open_jobs": count(
                conn, "SELECT COUNT(*) FROM jobs WHERE state IN ('queued', 'leased')"
            ),
            "jobs_with_second_attempt": count(conn, "SELECT COUNT(*) FROM jobs WHERE attempts > 1"),
            "all_bytes_verified": all(s["verified"] for s in summaries),
            "verified_megabytes": round(sum(s["bytes"] for s in summaries) / 1e6, 1),
            "enqueue_again_outcomes": again,
            "status_jobs": status.jobs,
            "integrity_check": conn.execute("PRAGMA integrity_check").fetchone()[0],
        }
    finally:
        conn.close()
    summary["zotero_files_unchanged"] = zotero_state(zotero_dir) == zotero_before
    text = json.dumps(summary, indent=2, sort_keys=True)
    (workspace / "summary.json").write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--zotero-dir", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--lease-seconds", type=float, default=2.0)
    parser.add_argument("--pace-ms", type=float, default=20.0)
    parser.add_argument("--kill-fraction", type=float, default=1 / 3)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    raise SystemExit(worker_main(arguments) if arguments.worker else main(arguments))
