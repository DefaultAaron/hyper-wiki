"""Keep the workspace in step with a live Zotero library (ADR 0004).

A sync reads a byte copy of Zotero's database, compares it with what the workspace holds, and
reports the differences as change events before importing the read. Comparing with the
workspace, not with the previous read, means changes made while Hyper-Wiki was not running are
found at the next sync. Import applies upserts, trash, and restore; deleted records are
reported but kept until the update engine (HW-013) tombstones them.
"""

import sqlite3
import time
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import NamedTuple, Protocol, get_args

from hyperwiki.adapters.snapshot import LoadedSnapshot
from hyperwiki.adapters.zotero_db import read_zotero_library
from hyperwiki.adapters.zotero_profile import ZoteroLocation
from hyperwiki.adapters.zotero_watch import DatabaseWatcher
from hyperwiki.domain.changes import ChangeEvent, ChangeKind
from hyperwiki.domain.common import Contract, EntityKind
from hyperwiki.domain.library import KnownFile, LibraryManifest
from hyperwiki.errors import ErrorCode, HyperWikiError
from hyperwiki.services.library_import import (
    Completeness,
    ImportReport,
    import_loaded,
    utc_now,
)
from hyperwiki.storage.library_repo import known_attachment_files, library_id_for

_ENTITY_ORDER: tuple[EntityKind, ...] = get_args(EntityKind)
_KIND_ORDER: tuple[ChangeKind, ...] = ("trash", "restore", "upsert", "delete")
_TABLES: dict[EntityKind, str] = {
    "collection": "collections",
    "item": "items",
    "attachment": "attachments",
    "note": "notes",
    "annotation": "annotations",
}


class SyncReport(Contract):
    library_id: str
    snapshot_id: str
    completeness: Completeness
    change_counts: dict[EntityKind, dict[ChangeKind, int]]
    changes: list[ChangeEvent]
    import_report: ImportReport
    duration_ms: float


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


class _State(NamedTuple):
    version: int
    trashed: bool
    content: tuple[object, ...] = ()  # attachment file hash and availability


def _stored(conn: sqlite3.Connection, library_id: str, entity: EntityKind) -> dict[str, _State]:
    table = _TABLES[entity]
    if entity == "attachment":
        rows = conn.execute(
            "SELECT key, version, trashed, sha256, availability FROM attachments "
            "WHERE library_id = ?",
            (library_id,),
        )
        return {r[0]: _State(r[1], bool(r[2]), (r[3], r[4])) for r in rows}
    rows = conn.execute(
        f"SELECT key, version, trashed FROM {table} WHERE library_id = ?", (library_id,)
    )
    return {r[0]: _State(r[1], bool(r[2])) for r in rows}


def _read_state(loaded: LoadedSnapshot) -> dict[EntityKind, dict[str, _State]]:
    manifest = loaded.manifest
    state: dict[EntityKind, dict[str, _State]] = {
        "collection": {c.key: _State(c.version, c.trashed) for c in manifest.collections},
        "item": {i.key: _State(i.version, i.trashed) for i in manifest.items},
        "attachment": {},
        "note": {n.key: _State(n.version, n.trashed) for n in manifest.notes},
        "annotation": {a.key: _State(a.version, a.trashed) for a in manifest.annotations},
    }
    for attachment in manifest.attachments:
        file = loaded.files[attachment.key]
        state["attachment"][attachment.key] = _State(
            attachment.version, attachment.trashed, (file.sha256, file.availability)
        )
    return state


def diff_library(
    conn: sqlite3.Connection, library_id: str, loaded: LoadedSnapshot, *, now: datetime
) -> list[ChangeEvent]:
    """Changes between the workspace's copy of a library and a new read of it.

    Deletions are reported only from a complete read, and never for a key named in a read
    problem: such a record still exists in Zotero but could not be read.
    """
    unreadable = {(p.entity, p.key) for p in loaded.problems if p.key is not None}
    complete = loaded.manifest.completeness == "complete"
    found: list[tuple[EntityKind, str, ChangeKind, int]] = []
    for entity, read in _read_state(loaded).items():
        stored = _stored(conn, library_id, entity)
        for key, new in read.items():
            old = stored.get(key)
            if old is None:
                found.append((entity, key, "upsert", new.version))
                continue
            if new.trashed != old.trashed:
                found.append((entity, key, "trash" if new.trashed else "restore", new.version))
            if new.version != old.version or new.content != old.content:
                found.append((entity, key, "upsert", new.version))
        if complete:
            for key in stored.keys() - read.keys():
                if (entity, key) not in unreadable:
                    found.append((entity, key, "delete", stored[key].version))
    found.sort(key=lambda f: (_ENTITY_ORDER.index(f[0]), f[1], _KIND_ORDER.index(f[2])))
    return [
        ChangeEvent(entity=entity, key=key, change=change, version=version, observed_at=now)
        for entity, key, change, version in found
    ]


def _counts(changes: Sequence[ChangeEvent]) -> dict[EntityKind, dict[ChangeKind, int]]:
    counts: dict[EntityKind, dict[ChangeKind, int]] = defaultdict(dict)
    for event in changes:
        counts[event.entity][event.change] = counts[event.entity].get(event.change, 0) + 1
    return {
        entity: dict(sorted(counts[entity].items(), key=lambda kv: _KIND_ORDER.index(kv[0])))
        for entity in _ENTITY_ORDER
        if entity in counts
    }


def sync_zotero(
    conn: sqlite3.Connection,
    location: ZoteroLocation,
    *,
    linked_file_roots: Sequence[Path] = (),
    now: Callable[[], datetime] = utc_now,
    reader: LibraryReader = read_zotero_library,
) -> SyncReport:
    """Read the live library, report what changed since the workspace's copy, and import it.

    A read that fails (for example ``zotero_database_busy``) raises and changes nothing.
    """
    start = time.perf_counter()
    observed = now()

    def known(manifest: LibraryManifest) -> Mapping[str, KnownFile]:
        return known_attachment_files(conn, library_id_for(manifest.adapter, manifest.library))

    loaded = reader(
        location.data_dir,
        linked_file_roots=linked_file_roots,
        base_attachment_dir=location.base_attachment_dir,
        known_files=known,
        now=lambda: observed,
    )
    library_id = library_id_for(loaded.manifest.adapter, loaded.manifest.library)
    changes = diff_library(conn, library_id, loaded, now=observed)
    report = import_loaded(conn, loaded, now=lambda: observed)
    return SyncReport(
        library_id=library_id,
        snapshot_id=loaded.manifest.snapshot_id,
        completeness=loaded.manifest.completeness,
        change_counts=_counts(changes),
        changes=changes,
        import_report=report,
        duration_ms=round((time.perf_counter() - start) * 1000, 1),
    )


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
