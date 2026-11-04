"""Keep the workspace in step with a live Zotero library (ADR 0004).

A sync reads a byte copy of Zotero's database and applies it like any other library read
(``apply_read``): differences from the workspace are reported as change events, and records
erased from Zotero are tombstoned.
"""

import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Protocol

from hyperwiki.adapters.snapshot import LoadedSnapshot
from hyperwiki.adapters.zotero_db import read_zotero_library
from hyperwiki.adapters.zotero_profile import ZoteroLocation
from hyperwiki.adapters.zotero_watch import DatabaseWatcher
from hyperwiki.domain.library import KnownFile, LibraryManifest
from hyperwiki.errors import ErrorCode, HyperWikiError
from hyperwiki.services.library_import import SyncReport, apply_read, utc_now
from hyperwiki.storage.library_repo import known_attachment_files, library_id_for


class LibraryReader(Protocol):
    def __call__(
        self,
        data_dir: Path,
        *,
        linked_file_roots: Sequence[Path],
        base_attachment_dir: Path | None,
        known_files: Callable[[LibraryManifest], Mapping[str, KnownFile]],
        now: Callable[[], datetime],
    ) -> LoadedSnapshot: ...


def sync_zotero(
    conn: sqlite3.Connection,
    location: ZoteroLocation,
    *,
    linked_file_roots: Sequence[Path] = (),
    verify_files: bool = False,
    now: Callable[[], datetime] = utc_now,
    reader: LibraryReader = read_zotero_library,
) -> SyncReport:
    """Read the live library, report what changed since the workspace's copy, and apply it.

    Attachment files whose path, size, and modification time match the workspace are not
    hashed again, unless ``verify_files`` asks for every file to be hashed (the full check).
    A read that fails (for example ``zotero_database_busy``) raises and changes nothing.
    """
    start = time.perf_counter()
    observed = now()

    def known(manifest: LibraryManifest) -> Mapping[str, KnownFile]:
        if verify_files:
            return {}
        return known_attachment_files(conn, library_id_for(manifest.adapter, manifest.library))

    loaded = reader(
        location.data_dir,
        linked_file_roots=linked_file_roots,
        base_attachment_dir=location.base_attachment_dir,
        known_files=known,
        now=lambda: observed,
    )
    return apply_read(conn, loaded, now=observed, started=start)


def run_watch(
    sync: Callable[[], SyncReport],
    watcher: DatabaseWatcher,
    *,
    on_sync: Callable[[SyncReport], None],
    on_busy: Callable[[HyperWikiError], None],
    interval: float,
    rescan_interval: float,
    should_stop: Callable[[], bool],
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> int:
    """Sync at start, then whenever the watcher is ready or ``rescan_interval`` has passed.

    The rescan catches attachment files replaced outside Zotero, which do not touch its
    database. A busy database is retried at the next tick; other errors stop the loop.
    Returns the number of syncs.
    """
    syncs = 0
    last_sync: float | None = None
    while not should_stop():
        due = (
            last_sync is None
            or watcher.ready() is not None
            or clock() - last_sync >= rescan_interval
        )
        if due:
            signature = watcher.current()  # taken first: writes during the read sync again
            try:
                report = sync()
            except HyperWikiError as error:
                if error.code != ErrorCode.ZOTERO_DATABASE_BUSY:
                    raise
                on_busy(error)
            else:
                watcher.synced(signature)
                last_sync = clock()
                syncs += 1
                on_sync(report)
        sleep(interval)
    return syncs


def watch_zotero(
    conn: sqlite3.Connection,
    location: ZoteroLocation,
    *,
    on_sync: Callable[[SyncReport], None],
    on_busy: Callable[[HyperWikiError], None] = lambda error: None,
    should_stop: Callable[[], bool],
    interval: float = 2.0,
    quiet_period: float = 3.0,
    rescan_interval: float = 600.0,
    linked_file_roots: Sequence[Path] = (),
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = utc_now,
    reader: LibraryReader = read_zotero_library,
) -> int:
    """Keep the workspace in step with the library until ``should_stop`` returns true."""
    watcher = DatabaseWatcher(location.data_dir, quiet_period=quiet_period, clock=clock)
    return run_watch(
        lambda: sync_zotero(
            conn, location, linked_file_roots=linked_file_roots, now=now, reader=reader
        ),
        watcher,
        on_sync=on_sync,
        on_busy=on_busy,
        interval=interval,
        rescan_interval=rescan_interval,
        should_stop=should_stop,
        sleep=sleep,
        clock=clock,
    )
