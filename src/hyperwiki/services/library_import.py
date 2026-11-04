"""Apply library reads to the workspace database.

Every read of a library, from a snapshot directory or a copy of Zotero's database, is compared
with what the workspace holds, and the differences are reported as change events. Comparing with
the workspace, not with the previous read, means changes made while Hyper-Wiki was not running
are found at the next read. Upserts, trash, and restore are applied; a record missing from a
complete read is tombstoned (HW-013). Partial reads never tombstone.
"""

import sqlite3
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, NamedTuple, get_args

from hyperwiki.adapters.snapshot import LoadedSnapshot, load_snapshot
from hyperwiki.domain.changes import ChangeEvent, ChangeKind
from hyperwiki.domain.common import Contract, EntityKind, Problem
from hyperwiki.storage.library_repo import (
    ENTITY_TABLES,
    EntityCounts,
    count_entities,
    library_id_for,
    record_attachment_version,
    record_snapshot,
    stable_id,
    tombstone,
    upsert_annotation,
    upsert_attachment,
    upsert_collection,
    upsert_item,
    upsert_library,
    upsert_note,
)

_ENTITY_ORDER: tuple[EntityKind, ...] = get_args(EntityKind)
_KIND_ORDER: tuple[ChangeKind, ...] = ("trash", "restore", "upsert", "delete")

Completeness = Literal["complete", "partial"]


class ImportReport(Contract):
    library_id: str
    snapshot_id: str
    completeness: Completeness
    counts: EntityCounts
    problems: list[Problem]


class SyncReport(Contract):
    library_id: str
    snapshot_id: str
    completeness: Completeness
    change_counts: dict[EntityKind, dict[ChangeKind, int]]
    changes: list[ChangeEvent]
    import_report: ImportReport
    duration_ms: float


def utc_now() -> datetime:
    return datetime.now(UTC)


def import_snapshot(
    conn: sqlite3.Connection,
    snapshot_dir: Path,
    *,
    linked_file_roots: Sequence[Path] = (),
    now: Callable[[], datetime] = utc_now,
) -> SyncReport:
    """Read a snapshot directory and apply it (see ``apply_read``)."""
    start = time.perf_counter()
    loaded = load_snapshot(snapshot_dir, linked_file_roots=linked_file_roots)
    return apply_read(conn, loaded, now=now(), started=start)


def import_loaded(
    conn: sqlite3.Connection,
    loaded: LoadedSnapshot,
    *,
    now: Callable[[], datetime] = utc_now,
    deletions: Sequence[ChangeEvent] = (),
) -> ImportReport:
    """Insert or update every record of a loaded library and tombstone ``deletions``, in one
    transaction.

    Records a version for every available attachment file. Records missing from the read are
    left alone; ``apply_read`` decides which are deletions.
    """
    manifest = loaded.manifest
    imported_at = now()
    with conn:
        library_id = upsert_library(conn, manifest.adapter, manifest.library)
        record_snapshot(conn, library_id, manifest, imported_at)
        for collection in manifest.collections:
            upsert_collection(conn, library_id, collection)
        for item in manifest.items:
            upsert_item(conn, library_id, item)
        for attachment in manifest.attachments:
            file = loaded.files[attachment.key]
            applied = upsert_attachment(
                conn,
                library_id,
                attachment,
                availability=file.availability,
                sha256=file.sha256,
                size_bytes=file.size_bytes,
                mtime_ns=file.mtime_ns,
                file_path=(
                    str(file.resolved_path)
                    if file.availability == "available" and file.resolved_path is not None
                    else None
                ),
            )
            if applied and file.sha256 is not None and file.size_bytes is not None:
                record_attachment_version(
                    conn,
                    stable_id(library_id, "attachment", attachment.key),
                    file.sha256,
                    file.size_bytes,
                    imported_at,
                )
        for note in manifest.notes:
            upsert_note(conn, library_id, note)
        for annotation in manifest.annotations:
            upsert_annotation(conn, library_id, annotation)
        for event in deletions:
            tombstone(conn, library_id, event.entity, event.key, at=imported_at)
    return ImportReport(
        library_id=library_id,
        snapshot_id=manifest.snapshot_id,
        completeness=manifest.completeness,
        counts=count_entities(conn, library_id),
        problems=loaded.problems,
    )


class _State(NamedTuple):
    version: int
    trashed: bool
    content: tuple[object, ...] = ()  # attachment file hash and availability


def _stored(conn: sqlite3.Connection, library_id: str, entity: EntityKind) -> dict[str, _State]:
    """Live (not tombstoned) records of one kind."""
    if entity == "attachment":
        rows = conn.execute(
            "SELECT key, version, trashed, sha256, availability FROM attachments "
            "WHERE library_id = ? AND deleted_at IS NULL",
            (library_id,),
        )
        return {r[0]: _State(r[1], bool(r[2]), (r[3], r[4])) for r in rows}
    rows = conn.execute(
        f"SELECT key, version, trashed FROM {ENTITY_TABLES[entity]} "
        "WHERE library_id = ? AND deleted_at IS NULL",
        (library_id,),
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
    """Changes between the workspace's live copy of a library and a new read of it.

    Deletions are reported only from a complete read, and never for a key named in a read
    problem: such a record still exists in the library but could not be read. A tombstoned
    key that appears again is reported as an upsert.
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


def apply_read(
    conn: sqlite3.Connection,
    loaded: LoadedSnapshot,
    *,
    now: datetime,
    started: float | None = None,
) -> SyncReport:
    """Report what changed since the workspace's copy of the library, then apply it.

    ``started`` is the ``time.perf_counter()`` value when the read began, for the duration.
    """
    start = time.perf_counter() if started is None else started
    library_id = library_id_for(loaded.manifest.adapter, loaded.manifest.library)
    changes = diff_library(conn, library_id, loaded, now=now)
    report = import_loaded(
        conn,
        loaded,
        now=lambda: now,
        deletions=[event for event in changes if event.change == "delete"],
    )
    return SyncReport(
        library_id=library_id,
        snapshot_id=loaded.manifest.snapshot_id,
        completeness=loaded.manifest.completeness,
        change_counts=_counts(changes),
        changes=changes,
        import_report=report,
        duration_ms=round((time.perf_counter() - start) * 1000, 1),
    )
