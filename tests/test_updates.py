import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from engine_fakes import FakeClock, FakeFiles
from hyperwiki.adapters.zotero_profile import ZoteroLocation
from hyperwiki.adapters.zotero_watch import DatabaseWatcher
from hyperwiki.domain.updates import Selection, UpdateTrigger
from hyperwiki.errors import ErrorCode, HyperWikiError
from hyperwiki.services.library_import import SyncReport, import_snapshot
from hyperwiki.services.updates import (
    UpdateReport,
    run_engine,
    run_update,
    set_exclusions,
    watch_updates,
)
from hyperwiki.storage.job_repo import job_counts
from hyperwiki.storage.update_repo import (
    last_full_check,
    pending_items,
    record_full_check,
    set_update_settings,
)
from pdf_factory import make_pdf
from snapshot_mutations import add_file_attachment
from zotero_db_factory import sample_library

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
PDF = make_pdf([["Attention weights sum to one over the input tokens."]])


@pytest.fixture
def snapshot(bilingual_snapshot: Path) -> Path:
    """The bilingual fixture plus a PDF on IT2MIX22: three extractable attachments."""
    add_file_attachment(bilingual_snapshot, "ATPDFM22", "IT2MIX22", "mixed.pdf", PDF)
    return bilingual_snapshot


class Reads:
    """A library read from the snapshot directory, recording whether files were verified."""

    def __init__(self, conn: sqlite3.Connection, snapshot: Path, *, busy_first: int = 0) -> None:
        self.conn, self.snapshot, self.busy_first = conn, snapshot, busy_first
        self.verified: list[bool] = []

    def __call__(self, verify_files: bool) -> SyncReport:
        if self.busy_first:
            self.busy_first -= 1
            raise HyperWikiError(ErrorCode.ZOTERO_DATABASE_BUSY, "busy")
        self.verified.append(verify_files)
        return import_snapshot(self.conn, self.snapshot, now=lambda: NOW)


def queued(conn: sqlite3.Connection) -> int:
    return job_counts(conn).get("extract.text", {}).get("queued", 0)


def test_update_all_extracts_and_records_full_check(
    database: sqlite3.Connection, snapshot: Path
) -> None:
    reads = Reads(database, snapshot)
    report = run_update(database, reads, trigger="all", now=lambda: NOW)
    assert reads.verified == [True]
    assert report.sync is not None and report.sync.change_counts["item"] == {"upsert": 6}
    [plan] = report.plans.values()
    assert plan.queued == 3
    assert report.worker is not None and report.worker.done == 3
    assert (report.trigger, report.selected, report.paused) == ("all", None, False)
    assert last_full_check(database) == NOW


def test_automatic_update_does_not_verify_files_or_record_a_full_check(
    database: sqlite3.Connection, snapshot: Path
) -> None:
    reads = Reads(database, snapshot)
    run_update(database, reads, trigger="automatic", now=lambda: NOW)
    assert reads.verified == [False]
    assert last_full_check(database) is None


def test_update_selected_runs_only_selected_jobs_first(
    database: sqlite3.Connection, snapshot: Path
) -> None:
    report = run_update(
        database,
        Reads(database, snapshot),
        trigger="selected",
        selection=Selection(item_keys=frozenset({"IT2MIX22"})),
        now=lambda: NOW,
    )
    assert report.selected is not None
    [selected] = report.selected.values()
    assert (selected.already_queued, selected.no_file) == (1, 1)  # the PDF; AT2MIS22 is missing
    assert report.worker is not None and report.worker.done == 1
    assert queued(database) == 2  # the other papers wait in the queue


def test_update_selected_collection_includes_subcollections(
    database: sqlite3.Connection, snapshot: Path
) -> None:
    report = run_update(
        database,
        Reads(database, snapshot),
        trigger="selected",
        selection=Selection(collection_keys=frozenset({"CL2DEEP2"})),
        now=lambda: NOW,
    )
    assert report.worker is not None and report.worker.done == 3
    assert queued(database) == 0


def test_update_selected_force_reextracts(database: sqlite3.Connection, snapshot: Path) -> None:
    reads = Reads(database, snapshot)
    run_update(database, reads, trigger="all", now=lambda: NOW)
    selection = Selection(item_keys=frozenset({"IT2MIX22"}))
    again = run_update(database, reads, trigger="selected", selection=selection, now=lambda: NOW)
    assert again.worker is not None and again.worker.done == 0
    forced = run_update(
        database, reads, trigger="selected", selection=selection, force=True, now=lambda: NOW
    )
    assert forced.selected is not None
    assert [plan.queued for plan in forced.selected.values()] == [1]
    assert forced.worker is not None and forced.worker.done == 1


def test_selection_unknown_key_not_found(database: sqlite3.Connection, snapshot: Path) -> None:
    with pytest.raises(HyperWikiError) as caught:
        run_update(
            database,
            Reads(database, snapshot),
            trigger="selected",
            selection=Selection(
                item_keys=frozenset({"IT2MIX22", "ITMISS22"}),
                collection_keys=frozenset({"CLMISS22"}),
            ),
            now=lambda: NOW,
        )
    assert caught.value.code == ErrorCode.NOT_FOUND
    assert caught.value.details == {"item_keys": ["ITMISS22"], "collection_keys": ["CLMISS22"]}


def test_force_needs_a_selection(database: sqlite3.Connection, snapshot: Path) -> None:
    with pytest.raises(ValueError):
        run_update(database, Reads(database, snapshot), trigger="all", force=True)


def test_automatic_update_while_paused_plans_but_does_not_run(
    database: sqlite3.Connection, snapshot: Path
) -> None:
    set_update_settings(database, paused=True)
    report = run_update(database, Reads(database, snapshot), trigger="automatic", now=lambda: NOW)
    assert (report.paused, report.worker) == (True, None)
    assert queued(database) == 3
    assert [item.item_key for item in pending_items(database)] == [
        "IT2ENG22",
        "IT2MIX22",
        "IT2ZHN22",
    ]


def test_pause_stops_worker_between_jobs(database: sqlite3.Connection, snapshot: Path) -> None:
    calls = 0

    def pause_after_first_job() -> bool:
        """Another process pauses while the first job runs."""
        nonlocal calls
        calls += 1
        if calls == 2:
            set_update_settings(database, paused=True)
        return False

    reads = Reads(database, snapshot)
    report = run_update(
        database, reads, trigger="automatic", should_stop=pause_after_first_job, now=lambda: NOW
    )
    assert report.worker is not None and report.worker.done == 1
    assert queued(database) == 2
    manual = run_update(database, reads, trigger="all", now=lambda: NOW)
    assert manual.paused and manual.worker is not None and manual.worker.done == 2


class Engine:
    """Runs ``run_engine`` with a fake clock and database files and a snapshot library."""

    def __init__(self, conn: sqlite3.Connection, snapshot: Path, *, busy_first: int = 0) -> None:
        self.conn = conn
        self.clock, self.files = FakeClock(), FakeFiles()
        self.reads = Reads(conn, snapshot, busy_first=busy_first)
        self.reports: list[tuple[float, UpdateReport]] = []
        self.busy: list[float] = []
        self.script: dict[float, list[str]] = {}  # time -> actions: "write", "resume"

    def wall(self) -> datetime:
        return NOW + timedelta(seconds=self.clock.now)

    def update(self, trigger: UpdateTrigger) -> UpdateReport:
        return run_update(self.conn, self.reads, trigger=trigger, now=self.wall)

    def sleep(self, seconds: float) -> None:
        self.clock.sleep(seconds)
        for action in self.script.pop(self.clock.now, []):
            if action == "write":
                self.files.write()
            elif action == "resume":
                set_update_settings(self.conn, paused=False)

    def run(self, *, until: float, rescan_interval: float = 600) -> list[tuple[float, str]]:
        watcher = DatabaseWatcher(
            Path("Z"), quiet_period=3, max_wait=60, clock=self.clock, signature=self.files
        )
        run_engine(
            self.conn,
            self.update,
            watcher,
            on_report=lambda report: self.reports.append((self.clock.now, report)),
            on_busy=lambda error: self.busy.append(self.clock.now),
            interval=1,
            rescan_interval=rescan_interval,
            should_stop=lambda: self.clock.now >= until,
            sleep=self.sleep,
            clock=self.clock,
            now=self.wall,
        )
        return [(at, report.trigger) for at, report in self.reports]


def test_engine_syncs_at_start_then_on_change(database: sqlite3.Connection, snapshot: Path) -> None:
    set_update_settings(database, schedule="off")
    engine = Engine(database, snapshot)
    engine.script = {10: ["write"], 11: ["write"]}  # Zotero writes twice, then is quiet
    assert engine.run(until=60) == [(0, "automatic"), (14, "automatic")]


def test_engine_rescan_interval_forces_a_sync(database: sqlite3.Connection, snapshot: Path) -> None:
    set_update_settings(database, schedule="off")
    assert Engine(database, snapshot).run(until=25, rescan_interval=10) == [
        (0, "automatic"),
        (10, "automatic"),
        (20, "automatic"),
    ]


def test_engine_retries_busy_copy_next_tick(database: sqlite3.Connection, snapshot: Path) -> None:
    set_update_settings(database, schedule="off")
    engine = Engine(database, snapshot, busy_first=2)
    assert engine.run(until=10) == [(2, "automatic")]
    assert engine.busy == [0, 1]


def test_engine_runs_scheduled_check_when_due_and_not_before(
    database: sqlite3.Connection, snapshot: Path
) -> None:
    record_full_check(database, NOW - timedelta(days=1) + timedelta(seconds=30))
    engine = Engine(database, snapshot)
    assert engine.run(until=40) == [(0, "automatic"), (30, "scheduled")]
    assert engine.reads.verified == [False, True]
    assert last_full_check(database) == NOW + timedelta(seconds=30)


def test_engine_with_automatic_off_ignores_changes(
    database: sqlite3.Connection, snapshot: Path
) -> None:
    set_update_settings(database, automatic=False)
    engine = Engine(database, snapshot)
    engine.script = {5: ["write"]}
    assert engine.run(until=30) == [(0, "scheduled")]  # never checked before; then only changes


def test_engine_runs_queue_after_resume(database: sqlite3.Connection, snapshot: Path) -> None:
    set_update_settings(database, schedule="off", paused=True)
    engine = Engine(database, snapshot)
    engine.script = {5: ["resume"]}
    assert engine.run(until=10) == [(0, "automatic"), (5, "queue")]
    first, resumed = (report for _, report in engine.reports)
    assert (first.paused, first.worker) == (True, None)
    assert resumed.sync is None and resumed.worker is not None and resumed.worker.done == 3
    assert queued(database) == 0


def touch(path: Path) -> None:
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))


def test_watch_updates_end_to_end(database: sqlite3.Connection, tmp_path: Path) -> None:
    data_dir = tmp_path / "Zotero"
    sample_library(data_dir).close()
    location = ZoteroLocation(data_dir=data_dir, base_attachment_dir=None, profile_dir=None)
    clock = FakeClock()
    reports: list[UpdateReport] = []

    def sleep(seconds: float) -> None:
        clock.sleep(seconds)
        if clock.now == 5:
            conn = sqlite3.connect(data_dir / "zotero.sqlite")
            conn.execute("UPDATE items SET clientVersion = 40 WHERE key = 'ITCHIN22'")
            conn.commit()
            conn.close()
            # Zotero appends to its write-ahead log; this test database rewrites a page in
            # place, so make the modification time move however coarse the file system is.
            touch(data_dir / "zotero.sqlite")

    watch_updates(
        database,
        location,
        on_report=reports.append,
        interval=1,
        quiet_period=0,
        should_stop=lambda: len([r for r in reports if r.sync]) == 2 or clock.now > 30,
        sleep=sleep,
        clock=clock,
        now=lambda: NOW,
    )
    syncs = [r for r in reports if r.sync is not None]
    assert [r.trigger for r in syncs] == ["scheduled", "automatic"]
    assert [r.sync.change_counts.get("item") for r in syncs if r.sync] == [
        {"upsert": 4},
        {"upsert": 1},
    ]


def test_watch_updates_stops_on_other_errors(database: sqlite3.Connection, tmp_path: Path) -> None:
    location = ZoteroLocation(
        data_dir=tmp_path / "none", base_attachment_dir=None, profile_dir=None
    )
    with pytest.raises(HyperWikiError) as caught:
        watch_updates(database, location, on_report=lambda r: None, should_stop=lambda: False)
    assert caught.value.code == ErrorCode.NOT_FOUND


def test_exclusion_change_replans_at_once(database: sqlite3.Connection, snapshot: Path) -> None:
    import_snapshot(database, snapshot)
    set_update_settings(database, paused=True)
    run_update(database, Reads(database, snapshot), trigger="automatic", now=lambda: NOW)
    assert queued(database) == 3
    [plan] = set_exclusions(database, exclude=["CL2ATTN2"], now=lambda: NOW).values()
    assert (plan.cancelled, plan.excluded) == (2, 2)
    assert queued(database) == 1
    [plan] = set_exclusions(database, include=["CL2ATTN2"], now=lambda: NOW).values()
    assert plan.queued == 2
    assert queued(database) == 3
