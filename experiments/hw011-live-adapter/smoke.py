"""HW-011 smoke test: the live Zotero adapter against a real, running Zotero.

Part 1, throwaway library. Starts Zotero 10 on a throwaway profile and data directory with
the HW-006 harness and its test-only driver plugin. Runs ``watch_updates`` with its default
interval and quiet period while Zotero applies changes, and checks that each change appears as
the expected change events, and how long that takes. Writes ``results/summary.json`` (synthetic
data only).

Part 2, real library, read-only (``--zotero-dir``). Discovers the data directory from
Zotero's settings, syncs into a private workspace twice, then watches for ``--idle`` seconds.
Prints counts and timings only. Zotero's files are never written.

    uv run --locked python experiments/hw011-live-adapter/smoke.py \
        --private /path/outside/the/repo [--zotero-dir ~/Zotero]
"""

import argparse
import json
import os
import platform
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hyperwiki.adapters.zotero_db import read_zotero_library
from hyperwiki.adapters.zotero_profile import ZoteroLocation, discover_zotero
from hyperwiki.services.library_import import SyncReport
from hyperwiki.services.updates import UpdateReport, watch_updates
from hyperwiki.services.zotero_sync import sync_zotero
from hyperwiki.storage.database import open_database

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "experiments" / "hw006-change-detection"))
from spike import Zotero, default_zotero, make_pdf  # noqa: E402

Event = tuple[str, str, str]  # entity, change, key


class Watch(threading.Thread):
    """Runs the watch loop in its own thread (SQLite connections stay in their thread).

    Since HW-013 the loop is the update engine (``watch_updates``), which also extracts
    evidence; only its syncs are recorded here.
    """

    def __init__(self, workspace: Path, location: ZoteroLocation, **options: Any) -> None:
        super().__init__(daemon=True)
        self.workspace, self.location, self.options = workspace, location, options
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.reports: list[tuple[float, SyncReport]] = []
        self.busy = 0
        self.error: BaseException | None = None

    def run(self) -> None:
        conn = open_database(self.workspace / "hyperwiki.sqlite3")
        try:
            watch_updates(
                conn,
                self.location,
                on_report=self.record_update,
                on_busy=self.count_busy,
                should_stop=self.stopping.is_set,
                **self.options,
            )
        except BaseException as error:
            self.error = error
        finally:
            conn.close()

    def record_update(self, report: UpdateReport) -> None:
        if report.sync is not None:
            self.record(report.sync)

    def record(self, report: SyncReport) -> None:
        with self.lock:
            self.reports.append((time.time(), report))

    def count_busy(self, error: object) -> None:
        self.busy += 1

    def events_since(self, start: float) -> list[tuple[float, Event]]:
        with self.lock:
            return [
                (t, (e.entity, e.change, e.key))
                for t, report in self.reports
                if t >= start
                for e in report.changes
            ]

    def wait_for(self, start: float, expected: set[Event], timeout: float) -> float | None:
        """Seconds from ``start`` until every expected event was reported, or None."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            seen = self.events_since(start)
            if expected <= {event for _, event in seen}:
                return max(t for t, event in seen if event in expected) - start
            time.sleep(0.1)
        return None


def throwaway(private: Path, executable: Path, port: int, summary_path: Path) -> dict[str, Any]:
    run_dir = private / datetime.now(UTC).strftime("hw011-%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True)
    zotero = Zotero(executable, run_dir, port)
    zotero.prepare()
    files = run_dir / "files"
    make_pdf(files / "paper.pdf", "HW-011 live adapter test")
    make_pdf(zotero.base / "sub" / "linked.pdf", "Linked file in the base directory")
    workspace = run_dir / "workspace"
    workspace.mkdir()
    info = zotero.start()
    location = ZoteroLocation(
        data_dir=zotero.data, base_attachment_dir=zotero.base, profile_dir=None
    )
    watch = Watch(workspace, location, rescan_interval=20.0)
    steps: list[dict[str, Any]] = []
    try:
        time.sleep(3)
        watch.start()
        deadline = time.time() + 60
        while not watch.reports and time.time() < deadline:  # the initial sync
            time.sleep(0.1)

        def step(name: str, apply: Any, expected: Any, timeout: float = 30) -> dict[str, Any]:
            start = time.time()
            result = apply()
            wanted = set(expected(result))
            latency = watch.wait_for(start, wanted, timeout)
            seen = {event for _, event in watch.events_since(start)}
            record = {
                "step": name,
                "expected": len(wanted),
                "all_seen": latency is not None,
                "latency_s": None if latency is None else round(latency, 1),
                "missing": sorted(f"{e}:{c}" for e, c, _ in wanted - seen),
                "kinds": sorted({f"{e}:{c}" for e, c, _ in seen}),
            }
            steps.append(record)
            print(f"  {name}: {record['latency_s']} s {'ok' if latency else record['missing']}")
            return dict(result)

        k: dict[str, str] = {}
        k["item"] = step(
            "create_item",
            lambda: zotero.act("create_item", fields={"title": "Live adapter 实时同步"}),
            lambda r: [("item", "upsert", r["keys"][0])],
        )["keys"][0]
        k["pdf"] = step(
            "import_pdf",
            lambda: zotero.act("import_file", parentKey=k["item"], path=str(files / "paper.pdf")),
            lambda r: [("attachment", "upsert", r["keys"][0])],
        )["keys"][0]
        k["linked"] = step(
            "link_pdf_in_base_dir",
            lambda: zotero.act(
                "link_file", parentKey=k["item"], path=str(zotero.base / "sub" / "linked.pdf")
            ),
            lambda r: [("attachment", "upsert", r["keys"][0])],
        )["keys"][0]
        step(
            "edit_title",
            lambda: zotero.act("edit_field", key=k["item"], field="title", value="Edited"),
            lambda r: [("item", "upsert", k["item"])],
        )
        k["note"] = step(
            "add_note",
            lambda: zotero.act("add_note", parentKey=k["item"], html="<p>笔记</p>"),
            lambda r: [("note", "upsert", r["keys"][0])],
        )["keys"][0]
        k["annotation"] = step(
            "add_annotation",
            lambda: zotero.act("add_annotation", attachmentKey=k["pdf"], text="evidence"),
            lambda r: [("annotation", "upsert", r["keys"][0])],
        )["keys"][0]
        k["collection"] = step(
            "create_collection",
            lambda: zotero.act("create_collection", name="Live"),
            lambda r: [("collection", "upsert", r["keys"][0])],
        )["keys"][0]
        children = [
            ("attachment", k["pdf"]),
            ("attachment", k["linked"]),
            ("note", k["note"]),
            ("annotation", k["annotation"]),
        ]
        step(
            "trash_item_with_children",
            lambda: zotero.act("trash", keys=[k["item"]]),
            lambda r: [("item", "trash", k["item"])] + [(e, "trash", key) for e, key in children],
        )
        step(
            "restore_item",
            lambda: zotero.act("restore", keys=[k["item"]]),
            lambda r: (
                [("item", "restore", k["item"])] + [(e, "restore", key) for e, key in children]
            ),
        )
        step(
            "trash_collection",
            lambda: zotero.act("trash_collection", key=k["collection"]),
            lambda r: [("collection", "trash", k["collection"])],
        )
        step(
            "erase_collection",
            lambda: zotero.act("erase_collection", key=k["collection"]),
            lambda r: [("collection", "delete", k["collection"])],
        )

        def replace_outside_zotero() -> dict[str, Any]:
            folder = zotero.data / "storage" / k["pdf"]
            target = next(p for p in folder.iterdir() if p.suffix == ".pdf")
            stat = target.stat()
            make_pdf(target, "Replaced outside Zotero")
            os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
            return {"keys": [k["pdf"]]}

        step(
            "replace_pdf_outside_zotero",
            replace_outside_zotero,
            lambda r: [("attachment", "upsert", k["pdf"])],
            timeout=40,
        )
        step(
            "trash_then_empty_trash",
            lambda: (zotero.act("trash", keys=[k["item"]]), zotero.act("empty_trash"))[1],
            lambda r: [("item", "delete", k["item"])] + [(e, "delete", key) for e, key in children],
        )
        step(
            "create_50_items",
            lambda: zotero.act("create_items_bulk", count=50, titlePrefix="Bulk"),
            lambda r: [("item", "upsert", key) for key in r["keys"]],
        )
        time.sleep(4)
    finally:
        watch.stopping.set()
        watch.join(timeout=30)
        zotero.stop()

    # The workspace now matches a fresh read of the library (apart from tombstones, HW-013).
    loaded = read_zotero_library(zotero.data, base_attachment_dir=zotero.base)
    conn = open_database(workspace / "hyperwiki.sqlite3")
    try:
        check = sync_zotero(conn, location)
    finally:
        conn.close()
    pending = [(e.entity, e.change) for e in check.changes]
    summary = {
        "measured": datetime.now().date().isoformat(),
        "zotero": info["zoteroVersion"],
        "os": f"{platform.system()} {platform.release()} {platform.machine()}",
        "interval_s": 2.0,
        "quiet_period_s": 3.0,
        "rescan_interval_s": 20.0,
        "steps": steps,
        "syncs": len(watch.reports),
        "busy": watch.busy,
        "watch_error": None if watch.error is None else repr(watch.error),
        "sync_ms": [r.duration_ms for _, r in watch.reports],
        "final_read_objects": len(loaded.manifest.items)
        + len(loaded.manifest.attachments)
        + len(loaded.manifest.notes)
        + len(loaded.manifest.annotations),
        "after_quit_pending": sorted({f"{e}:{c}" for e, c in pending}),
        "after_quit_pending_count": len(pending),
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=1, ensure_ascii=False) + "\n", "utf-8")
    return summary


def real_library(private: Path, zotero_dir: Path, idle: float) -> dict[str, Any]:
    """Read-only on the user's library; counts and timings only."""
    location = discover_zotero(home=Path.home(), environ=os.environ, platform=sys.platform)
    workspace = private / datetime.now(UTC).strftime("hw011-real-%Y%m%dT%H%M%SZ")
    workspace.mkdir(parents=True)
    result: dict[str, Any] = {
        "discovered_data_dir_matches": location.data_dir.resolve() == zotero_dir.resolve(),
        "base_dir_configured": location.base_attachment_dir is not None,
    }
    location = location.model_copy(update={"data_dir": zotero_dir})
    conn = open_database(workspace / "hyperwiki.sqlite3")
    try:
        first = sync_zotero(conn, location)
        second = sync_zotero(conn, location)
    finally:
        conn.close()
    result["first_sync"] = {
        "ms": first.duration_ms,
        "changes": len(first.changes),
        "counts": first.import_report.counts.model_dump(),
        "problems": len(first.import_report.problems),
    }
    result["second_sync"] = {"ms": second.duration_ms, "changes": len(second.changes)}
    watch = Watch(workspace, location)
    watch.start()
    time.sleep(idle)
    watch.stopping.set()
    watch.join(timeout=30)
    result["idle_watch"] = {
        "seconds": idle,
        "syncs": len(watch.reports),
        "changes": sum(len(r.changes) for _, r in watch.reports),
        "error": None if watch.error is None else repr(watch.error),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--private", type=Path, required=True)
    parser.add_argument("--zotero", type=Path, default=default_zotero())
    parser.add_argument("--port", type=int, default=23124)
    parser.add_argument("--summary", type=Path, default=HERE / "results" / "summary.json")
    parser.add_argument("--zotero-dir", type=Path, help="also check this real library, read-only")
    parser.add_argument("--idle", type=float, default=60)
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
        print(json.dumps({k: v for k, v in summary.items() if k != "steps"}, indent=1))
    if args.zotero_dir is not None:
        print("real library (read-only):", flush=True)
        real = real_library(private, args.zotero_dir.expanduser(), args.idle)
        print(json.dumps(real, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
