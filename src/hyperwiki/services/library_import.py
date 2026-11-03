"""Import a library snapshot into the workspace database and report library status."""

import sqlite3
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, Field

from hyperwiki.adapters.snapshot import LoadedSnapshot, load_snapshot
from hyperwiki.domain.common import Contract, Problem
from hyperwiki.domain.evidence import EvidenceCoverage
from hyperwiki.domain.work import JobState
from hyperwiki.services.evidence import evidence_coverage
from hyperwiki.storage.job_repo import job_counts
from hyperwiki.storage.library_repo import (
    EntityCounts,
    count_entities,
    record_attachment_version,
    record_snapshot,
    stable_id,
    upsert_annotation,
    upsert_attachment,
    upsert_collection,
    upsert_item,
    upsert_library,
    upsert_note,
)

Completeness = Literal["complete", "partial"]


class ImportReport(Contract):
    library_id: str
    snapshot_id: str
    completeness: Completeness
    counts: EntityCounts
    problems: list[Problem]


class LibraryStatus(Contract):
    library_id: str
    adapter: str
    name: str
    last_snapshot_id: str
    last_completeness: Completeness
    last_imported_at: AwareDatetime
    counts: EntityCounts
    evidence: EvidenceCoverage


class StatusReport(Contract):
    libraries: list[LibraryStatus]
    jobs: dict[str, dict[JobState, int]] = Field(default_factory=dict)


def utc_now() -> datetime:
    return datetime.now(UTC)


def import_snapshot(
    conn: sqlite3.Connection,
    snapshot_dir: Path,
    *,
    linked_file_roots: Sequence[Path] = (),
    now: Callable[[], datetime] = utc_now,
) -> ImportReport:
    """Read a snapshot directory and import it (see ``import_loaded``)."""
    loaded = load_snapshot(snapshot_dir, linked_file_roots=linked_file_roots)
    return import_loaded(conn, loaded, now=now)


def import_loaded(
    conn: sqlite3.Connection, loaded: LoadedSnapshot, *, now: Callable[[], datetime] = utc_now
) -> ImportReport:
    """Insert or update every record of a loaded library in one transaction.

    Records a version for every available attachment file. Import never deletes or
    tombstones records missing from the snapshot; absence handling belongs to the M1 update
    engine.
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
    return ImportReport(
        library_id=library_id,
        snapshot_id=manifest.snapshot_id,
        completeness=manifest.completeness,
        counts=count_entities(conn, library_id),
        problems=loaded.problems,
    )


def library_status(conn: sqlite3.Connection) -> StatusReport:
    rows = conn.execute(
        """
        SELECT l.id, l.adapter, l.name, s.snapshot_id, s.completeness, s.imported_at
        FROM libraries AS l
        JOIN snapshots AS s ON s.library_id = l.id AND s.snapshot_id = l.last_snapshot_id
        ORDER BY l.name, l.id
        """
    ).fetchall()
    return StatusReport(
        libraries=[
            LibraryStatus(
                library_id=row["id"],
                adapter=row["adapter"],
                name=row["name"],
                last_snapshot_id=row["snapshot_id"],
                last_completeness=row["completeness"],
                last_imported_at=datetime.fromisoformat(row["imported_at"]),
                counts=count_entities(conn, row["id"]),
                evidence=evidence_coverage(conn, row["id"]),
            )
            for row in rows
        ],
        jobs=job_counts(conn),
    )
