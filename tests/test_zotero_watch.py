import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hyperwiki.adapters.zotero_profile import ZoteroLocation
from hyperwiki.adapters.zotero_watch import DatabaseWatcher, Signature, database_signature
from hyperwiki.errors import ErrorCode, HyperWikiError
from hyperwiki.services.zotero_sync import SyncReport, run_watch, watch_zotero
from zotero_db_factory import sample_library

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeFiles:
    """Stands in for stat() on zotero.sqlite and its write-ahead log."""

    def __init__(self) -> None:
        self.writes = 0

    def write(self) -> None:
        self.writes += 1

    def __call__(self, data_dir: Path) -> Signature:
        return ((4096, self.writes), (0, 0), None)


def test_database_signature_reads_sizes_and_mtimes(tmp_path: Path) -> None:
    (tmp_path / "zotero.sqlite").write_bytes(b"x" * 10)
    first = database_signature(tmp_path)
    assert first[0] is not None and first[0][0] == 10
    assert first[1] is None and first[2] is None  # no -wal, no -journal
    (tmp_path / "zotero.sqlite-wal").write_bytes(b"w")
    assert database_signature(tmp_path) != first


def test_change_is_ready_only_after_quiet_period() -> None:
    clock, files = FakeClock(), FakeFiles()
    watcher = DatabaseWatcher(Path("Z"), quiet_period=3, clock=clock, signature=files)
    watcher.synced(files(Path("Z")))
    files.write()
    assert watcher.ready() is None  # just changed
    clock.sleep(2)
    files.write()  # Zotero is still writing: the quiet period starts again
    assert watcher.ready() is None
    clock.sleep(2.9)
    assert watcher.ready() is None
    clock.sleep(0.1)
    assert watcher.ready() == files(Path("Z"))


def test_no_change_is_never_ready() -> None:
    clock, files = FakeClock(), FakeFiles()
    watcher = DatabaseWatcher(Path("Z"), quiet_period=3, clock=clock, signature=files)
    watcher.synced(files(Path("Z")))
    for _ in range(10):
        clock.sleep(5)
        assert watcher.ready() is None


def test_change_back_to_synced_state_is_not_ready() -> None:
    clock, files = FakeClock(), FakeFiles()
    watcher = DatabaseWatcher(Path("Z"), quiet_period=1, clock=clock, signature=files)
    watcher.synced(files(Path("Z")))
    files.write()
    watcher.ready()
    files.writes -= 1
    clock.sleep(5)
    assert watcher.ready() is None


def make_report(number: int) -> SyncReport:
    return SyncReport.model_validate(
        {
            "library_id": "lib",
            "snapshot_id": f"zotero-{number}",
            "completeness": "complete",
            "change_counts": {},
            "changes": [],
            "import_report": {
                "library_id": "lib",
                "snapshot_id": f"zotero-{number}",
                "completeness": "complete",
                "counts": {
                    "collections": 0,
                    "items": 0,
                    "items_trashed": 0,
                    "attachments": 0,
                    "attachments_by_availability": {},
                    "attachment_versions": 0,
                    "notes": 0,
                    "annotations": 0,
                },
                "problems": [],
            },
            "duration_ms": 1.0,
        }
    )


class Harness:
    """Runs ``run_watch`` with a fake clock, fake files, and a scripted sync."""

    def __init__(self, *, busy_first: int = 0) -> None:
        self.clock, self.files = FakeClock(), FakeFiles()
        self.synced_at: list[float] = []
        self.busy: list[float] = []
        self.busy_first = busy_first
        self.script: dict[float, int] = {}  # time -> number of writes then

    def sync(self) -> SyncReport:
        if self.busy_first:
            self.busy_first -= 1
            raise HyperWikiError(ErrorCode.ZOTERO_DATABASE_BUSY, "busy")
        self.synced_at.append(self.clock.now)
        return make_report(len(self.synced_at))

    def sleep(self, seconds: float) -> None:
        self.clock.sleep(seconds)
        for _ in range(self.script.pop(self.clock.now, 0)):
            self.files.write()

    def run(self, *, until: float, rescan_interval: float = 600) -> list[float]:
        watcher = DatabaseWatcher(Path("Z"), quiet_period=3, clock=self.clock, signature=self.files)
        run_watch(
            self.sync,
            watcher,
            on_sync=lambda report: None,
            on_busy=lambda error: self.busy.append(self.clock.now),
            interval=1,
            rescan_interval=rescan_interval,
            should_stop=lambda: self.clock.now >= until,
            sleep=self.sleep,
            clock=self.clock,
        )
        return self.synced_at


def test_watch_syncs_at_start_then_on_change() -> None:
    harness = Harness()
    harness.script = {10: 1, 11: 1}  # Zotero writes twice, then is quiet
    # Start: sync at 0. Last write at 11, quiet for 3 s -> sync at 14. Nothing after.
    assert harness.run(until=60) == [0, 14]


def test_rescan_interval_forces_a_sync() -> None:
    assert Harness().run(until=25, rescan_interval=10) == [0, 10, 20]


def test_busy_copy_is_retried_next_tick() -> None:
    harness = Harness(busy_first=2)
    assert harness.run(until=10) == [2]
    assert harness.busy == [0, 1]


def zotero_sql(data_dir: Path, sql: str) -> None:
    conn = sqlite3.connect(data_dir / "zotero.sqlite")
    try:
        conn.execute(sql)
        conn.commit()
    finally:
        conn.close()


def test_watch_zotero_end_to_end(database: sqlite3.Connection, tmp_path: Path) -> None:
    data_dir = tmp_path / "Zotero"
    sample_library(data_dir).close()
    location = ZoteroLocation(data_dir=data_dir, base_attachment_dir=None, profile_dir=None)
    clock = FakeClock()
    reports: list[SyncReport] = []

    def sleep(seconds: float) -> None:
        clock.sleep(seconds)
        if clock.now == 5:
            zotero_sql(data_dir, "UPDATE items SET clientVersion = 40 WHERE key = 'ITCHIN22'")
            # Zotero appends to its write-ahead log; this test database rewrites a page in
            # place, so make the modification time move however coarse the file system is.
            stat = (data_dir / "zotero.sqlite").stat()
            os.utime(data_dir / "zotero.sqlite", ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))

    watch_zotero(
        database,
        location,
        on_sync=reports.append,
        interval=1,
        quiet_period=0,
        should_stop=lambda: len(reports) == 2 or clock.now > 30,
        sleep=sleep,
        clock=clock,
        now=lambda: NOW,
    )
    assert [r.change_counts.get("item") for r in reports] == [{"upsert": 4}, {"upsert": 1}]
    assert [(e.key, e.version) for e in reports[1].changes] == [("ITCHIN22", 40)]


def test_watch_stops_on_other_errors(database: sqlite3.Connection, tmp_path: Path) -> None:
    location = ZoteroLocation(
        data_dir=tmp_path / "none", base_attachment_dir=None, profile_dir=None
    )
    with pytest.raises(HyperWikiError) as caught:
        watch_zotero(database, location, on_sync=lambda r: None, should_stop=lambda: False)
    assert caught.value.code == ErrorCode.NOT_FOUND
