"""HW-013 smoke test: the update engine against a real, running Zotero.

Part 1, throwaway library. Starts Zotero 10 on a throwaway profile and data directory with the
HW-006 harness and its test-only driver plugin (port 23124, never the user's 23119). Runs
``watch_updates`` with its default timings in a thread while Zotero applies changes, and
measures how long each change takes to reach the evidence store:
- a PDF imported into an item becomes current evidence;
- trashing the item makes the evidence ``trashed``, and restoring it makes it ``current``;
- excluding the item's collection makes it ``excluded``, and including it again ``current``;
- while paused, a new PDF is synced and queued but not extracted; after resuming, it is;
- emptying the trash tombstones the item and its attachments, whose evidence reads
  ``deleted``.
Writes ``results/summary.json`` (synthetic data only).

Part 2, real library, read-only (``--zotero-dir``). In an empty private workspace: update all
(sync and extract), an automatic update with nothing changed, a full check that rehashes every
file, planning alone, and an idle ``watch``. Prints counts and timings only, and checks that
Zotero's database files did not change.

    uv run --locked python experiments/hw013-update-engine/smoke.py \
        --private /path/outside/the/repo [--zotero-dir ~/Zotero]
"""

import argparse
import json
import os
import platform
import sqlite3
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hyperwiki.adapters.zotero_db import ZOTERO_DATABASE
from hyperwiki.adapters.zotero_profile import ZoteroLocation, discover_zotero
from hyperwiki.domain.updates import UpdateTrigger
from hyperwiki.services.evidence import evidence_coverage, plan_extraction, read_evidence
from hyperwiki.services.status import update_status
from hyperwiki.services.updates import (
    UpdateReport,
    set_exclusions,
    update_zotero,
    watch_updates,
)
from hyperwiki.storage.database import open_database
from hyperwiki.storage.library_repo import count_entities
from hyperwiki.storage.update_repo import pending_items, set_update_settings

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
DATABASE = "hyperwiki.sqlite3"
sys.path.insert(0, str(REPO_ROOT / "experiments" / "hw006-change-detection"))
from spike import Zotero, default_zotero, make_pdf  # noqa: E402

TEXT = "Update engine live test: attention weights sum to one over the inputs."


class Engine(threading.Thread):
    """Runs watch_updates in its own thread (SQLite connections stay in their thread)."""

    def __init__(self, workspace: Path, location: ZoteroLocation, **options: Any) -> None:
        super().__init__(daemon=True)
        self.workspace, self.location, self.options = workspace, location, options
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.reports: list[tuple[float, UpdateReport]] = []
        self.busy = 0
        self.error: BaseException | None = None

    def run(self) -> None:
        conn = open_database(self.workspace / DATABASE)
        try:
            watch_updates(
                conn,
                self.location,
                on_report=self.record,
                on_busy=self.count_busy,
                should_stop=self.stopping.is_set,
                **self.options,
            )
        except BaseException as error:
            self.error = error
        finally:
            conn.close()

    def record(self, report: UpdateReport) -> None:
        with self.lock:
            self.reports.append((time.time(), report))

    def count_busy(self, error: object) -> None:
        self.busy += 1


def wait_until(check: Callable[[], bool], timeout: float = 60) -> float | None:
    """Seconds until ``check`` holds, polling every 0.2 s, or None at the timeout."""
    start = time.time()
    while time.time() - start < timeout:
        if check():
            return round(time.time() - start, 1)
        time.sleep(0.2)
    return None


def evidence_of(conn: sqlite3.Connection, attachment_key: str) -> list[str]:
    rows = conn.execute(
        "SELECT e.id FROM evidence AS e JOIN attachments AS a ON a.id = e.attachment_id "
        "WHERE a.key = ? ORDER BY e.ordinal",
        (attachment_key,),
    )
    return [row[0] for row in rows]


def status_is(conn: sqlite3.Connection, evidence_id: str, status: str) -> Callable[[], bool]:
    return lambda: read_evidence(conn, evidence_id).status == status


def throwaway(private: Path, executable: Path, port: int, summary_path: Path) -> dict[str, Any]:
    run_dir = private / datetime.now(UTC).strftime("hw013-%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True)
    zotero = Zotero(executable, run_dir, port)
    zotero.prepare()
    files = run_dir / "files"
    for name in ("first", "second"):
        make_pdf(files / f"{name}.pdf", f"{TEXT} ({name})")
    workspace = run_dir / "workspace"
    workspace.mkdir()
    info = zotero.start()
    location = ZoteroLocation(
        data_dir=zotero.data, base_attachment_dir=zotero.base, profile_dir=None
    )
    engine = Engine(workspace, location)
    steps: dict[str, Any] = {}
    try:
        time.sleep(3)
        engine.start()
        if wait_until(lambda: bool(engine.reports), 60) is None:
            raise SystemExit(f"the engine never reported: {engine.error!r}")
        conn = open_database(workspace / DATABASE)

        collection = zotero.act("create_collection", name="Live 实时")["keys"][0]
        item = zotero.act(
            "create_item", fields={"title": "Update engine 更新"}, collectionKey=collection
        )["keys"][0]
        start = time.time()
        pdf = zotero.act("import_file", parentKey=item, path=str(files / "first.pdf"))["keys"][0]
        found = wait_until(lambda: bool(evidence_of(conn, pdf)))
        steps["add_pdf_to_evidence_s"] = None if found is None else round(time.time() - start, 1)
        evidence = evidence_of(conn, pdf)[0]
        steps["first_status"] = read_evidence(conn, evidence).status

        zotero.act("trash", keys=[item])
        steps["trash_s"] = wait_until(status_is(conn, evidence, "trashed"))
        zotero.act("restore", keys=[item])
        steps["restore_s"] = wait_until(status_is(conn, evidence, "current"))

        set_exclusions(conn, exclude=[collection])
        steps["excluded_status"] = read_evidence(conn, evidence).status
        second = zotero.act("import_file", parentKey=item, path=str(files / "second.pdf"))
        second_key = second["keys"][0]
        synced = wait_until(
            lambda: (
                conn.execute("SELECT 1 FROM attachments WHERE key = ?", (second_key,)).fetchone()
                is not None
            )
        )
        time.sleep(8)
        steps["excluded_pdf_synced_s"] = synced
        steps["excluded_pdf_evidence"] = len(evidence_of(conn, second_key))
        set_exclusions(conn, include=[collection])
        steps["include_to_evidence_s"] = wait_until(lambda: bool(evidence_of(conn, second_key)))

        set_update_settings(conn, paused=True)
        other = zotero.act("create_item", fields={"title": "Paused 暂停"})["keys"][0]
        third = zotero.act("import_file", parentKey=other, path=str(files / "first.pdf"))
        third_key = third["keys"][0]
        steps["paused_pdf_pending_s"] = wait_until(
            lambda: any(third_key in p.attachment_keys for p in pending_items(conn))
        )
        time.sleep(8)
        steps["paused_pdf_evidence"] = len(evidence_of(conn, third_key))
        set_update_settings(conn, paused=False)
        steps["resume_to_evidence_s"] = wait_until(lambda: bool(evidence_of(conn, third_key)))

        zotero.act("trash", keys=[item])
        zotero.act("empty_trash")
        steps["empty_trash_s"] = wait_until(status_is(conn, evidence, "deleted"))
        steps["deleted_status_second_pdf"] = read_evidence(
            conn, evidence_of(conn, second_key)[0]
        ).status
        library_id = conn.execute("SELECT id FROM libraries").fetchone()[0]
        steps["deleted_counts"] = count_entities(conn, library_id).deleted
        coverage = evidence_coverage(conn, library_id)
        steps["coverage_states"] = coverage.states
        steps["pending_jobs_at_end"] = update_status(conn).pending_jobs
        conn.close()
    finally:
        engine.stopping.set()
        engine.join(timeout=60)
        zotero.stop()

    with engine.lock:
        reports = list(engine.reports)
    summary = {
        "measured": datetime.now().date().isoformat(),
        "zotero": info["zoteroVersion"],
        "os": f"{platform.system()} {platform.release()} {platform.machine()}",
        "timings": "watch defaults: interval 2 s, quiet period 3 s, max wait 60 s",
        "steps": steps,
        "updates_by_trigger": {
            trigger: sum(1 for _, r in reports if r.trigger == trigger)
            for trigger in ("scheduled", "automatic", "queue", "all", "selected")
        },
        "busy": engine.busy,
        "engine_error": None if engine.error is None else repr(engine.error),
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=1, ensure_ascii=False) + "\n", "utf-8")
    return summary


def zotero_state(zotero_dir: Path) -> list[tuple[str, int, int]]:
    return sorted(
        (path.name, path.stat().st_size, path.stat().st_mtime_ns)
        for path in zotero_dir.glob(ZOTERO_DATABASE + "*")
    )


def timed_update(
    conn: sqlite3.Connection, location: ZoteroLocation, trigger: UpdateTrigger
) -> dict[str, Any]:
    start = time.perf_counter()
    report = update_zotero(conn, location, trigger=trigger)
    assert report.sync is not None
    return {
        "seconds": round(time.perf_counter() - start, 2),
        "sync_ms": report.sync.duration_ms,
        "changes": len(report.sync.changes),
        "planned": {
            name: sum(getattr(plan, name) for plan in report.plans.values())
            for name in ("queued", "already_done", "unsupported", "no_file", "trashed")
        },
        "worker": None if report.worker is None else report.worker.model_dump(),
    }


def real_library(private: Path, zotero_dir: Path, idle: float) -> dict[str, Any]:
    """Read-only on the user's library; counts and timings only."""
    before = zotero_state(zotero_dir)
    location = discover_zotero(
        data_dir=zotero_dir, home=Path.home(), environ=os.environ, platform=sys.platform
    )
    workspace = private / datetime.now(UTC).strftime("hw013-real-%Y%m%dT%H%M%SZ")
    workspace.mkdir(parents=True)
    conn = open_database(workspace / DATABASE)
    try:
        result: dict[str, Any] = {
            "update_all_first": timed_update(conn, location, "all"),
            "automatic_unchanged": timed_update(conn, location, "automatic"),
            "full_check": timed_update(conn, location, "all"),
        }
        start = time.perf_counter()
        plan_extraction(conn)
        result["plan_seconds"] = round(time.perf_counter() - start, 3)
        library_id = conn.execute("SELECT id FROM libraries").fetchone()[0]
        coverage = evidence_coverage(conn, library_id)
        result["coverage"] = {
            "states": coverage.states,
            "trashed": coverage.trashed,
            "excluded": coverage.excluded,
            "evidence": coverage.evidence,
        }
        result["deleted"] = count_entities(conn, library_id).deleted
    finally:
        conn.close()

    engine = Engine(workspace, location)
    cpu = time.process_time()
    engine.start()
    time.sleep(idle)
    engine.stopping.set()
    engine.join(timeout=60)
    result["idle_watch"] = {
        "seconds": idle,
        "updates": [r.trigger for _, r in engine.reports],
        "cpu_seconds": round(time.process_time() - cpu, 2),
        "error": None if engine.error is None else repr(engine.error),
    }
    result["zotero_database_unchanged"] = zotero_state(zotero_dir) == before
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--private", type=Path, required=True)
    parser.add_argument("--zotero", type=Path, default=default_zotero())
    parser.add_argument("--port", type=int, default=23124)
    parser.add_argument("--summary", type=Path, default=HERE / "results" / "summary.json")
    parser.add_argument("--zotero-dir", type=Path, help="also check this real library, read-only")
    parser.add_argument("--idle", type=float, default=30)
    parser.add_argument("--skip-throwaway", action="store_true")
    args = parser.parse_args()
    private: Path = args.private.expanduser().resolve()
    if private.is_relative_to(REPO_ROOT):
        raise SystemExit("the private directory must be outside the repository")
    if args.port == 23119:
        raise SystemExit("use a port other than 23119, so the user's own Zotero is never hit")
    if not args.skip_throwaway:
        print("throwaway library:", flush=True)
        summary = throwaway(private, args.zotero, args.port, args.summary)
        print(json.dumps(summary, indent=1, ensure_ascii=False))
    if args.zotero_dir is not None:
        print("real library (read-only):", flush=True)
        real = real_library(private, args.zotero_dir.expanduser().resolve(), args.idle)
        print(json.dumps(real, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
