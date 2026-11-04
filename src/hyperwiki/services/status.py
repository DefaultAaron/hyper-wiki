"""Workspace status: libraries, evidence coverage, jobs, and update state. Never writes."""

import sqlite3
from datetime import datetime

from pydantic import AwareDatetime, Field, NonNegativeInt

from hyperwiki.domain.common import Contract
from hyperwiki.domain.evidence import EvidenceCoverage
from hyperwiki.domain.updates import UpdateSettings, next_full_check
from hyperwiki.domain.work import JobState
from hyperwiki.services.evidence import evidence_coverage
from hyperwiki.services.library_import import Completeness
from hyperwiki.storage.job_repo import job_counts
from hyperwiki.storage.library_repo import EntityCounts, count_entities
from hyperwiki.storage.update_repo import (
    PendingItem,
    excluded_collections,
    get_update_settings,
    last_full_check,
    pending_items,
)


class LibraryStatus(Contract):
    library_id: str
    adapter: str
    name: str
    last_snapshot_id: str
    last_completeness: Completeness
    last_imported_at: AwareDatetime
    counts: EntityCounts
    evidence: EvidenceCoverage


class UpdateStatus(Contract):
    """Update settings and state. ``next_full_check_at`` is None when the schedule is off or
    no full check has run yet (the next ``watch`` then runs one at once)."""

    settings: UpdateSettings = Field(default_factory=UpdateSettings)
    excluded_collections: dict[str, list[str]] = Field(default_factory=dict)
    last_full_check_at: AwareDatetime | None = None
    next_full_check_at: AwareDatetime | None = None
    pending_jobs: NonNegativeInt = 0
    pending_items: NonNegativeInt = 0


class StatusReport(Contract):
    libraries: list[LibraryStatus]
    jobs: dict[str, dict[JobState, int]] = Field(default_factory=dict)
    updates: UpdateStatus = Field(default_factory=UpdateStatus)
    pending: list[PendingItem] | None = None


def update_status(conn: sqlite3.Connection) -> UpdateStatus:
    settings = get_update_settings(conn)
    last = last_full_check(conn)
    jobs = job_counts(conn)
    return UpdateStatus(
        settings=settings,
        excluded_collections=excluded_collections(conn),
        last_full_check_at=last,
        next_full_check_at=next_full_check(settings, last),
        pending_jobs=sum(states["queued"] + states["leased"] for states in jobs.values()),
        pending_items=len(pending_items(conn)),
    )


def library_status(conn: sqlite3.Connection, *, pending: bool = False) -> StatusReport:
    """Status of every library; ``pending`` adds the pending list by item."""
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
        updates=update_status(conn),
        pending=pending_items(conn) if pending else None,
    )
