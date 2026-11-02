"""A SQLite job queue with leases, bounded retries, and one committed result per fingerprint.

Every function runs in its own ``BEGIN IMMEDIATE`` transaction, so claims and completions
from several processes never interleave. Outputs and completion commit together, and only
while the caller still holds the job's lease.
"""

import json
import sqlite3
import uuid
from collections.abc import Callable, Collection, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import get_args

from pydantic import JsonValue

from hyperwiki.domain.work import (
    EnqueueResult,
    Job,
    JobState,
    WorkFingerprint,
    fingerprint_id,
)
from hyperwiki.errors import ErrorCode, ErrorInfo, HyperWikiError
from hyperwiki.storage.database import from_db_time, to_db_time

RETRY_BASE = timedelta(seconds=30)
RETRY_CAP = timedelta(hours=1)

Store = Callable[[sqlite3.Connection], Mapping[str, JsonValue]]


def retry_delay(attempts: int) -> timedelta:
    """Wait before the next attempt: 30 s after the first, doubling, at most an hour."""
    delay: timedelta = RETRY_BASE * 2 ** max(attempts - 1, 0)
    return min(delay, RETRY_CAP)


@contextmanager
def _immediate(conn: sqlite3.Connection) -> Iterator[None]:
    if conn.in_transaction:
        raise RuntimeError("job queue operations need a connection with no open transaction")
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


def _job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        operation=row["operation"],
        subject=row["subject"],
        fingerprint=row["fingerprint"],
        spec=WorkFingerprint.model_validate_json(row["fingerprint_json"]),
        state=row["state"],
        priority=row["priority"],
        forced=bool(row["forced"]),
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        lease_owner=row["lease_owner"],
        lease_expires_at=(
            None if row["lease_expires_at"] is None else from_db_time(row["lease_expires_at"])
        ),
        not_before=from_db_time(row["not_before"]),
        last_error=(
            None
            if row["last_error_json"] is None
            else ErrorInfo.model_validate_json(row["last_error_json"])
        ),
        created_at=from_db_time(row["created_at"]),
        updated_at=from_db_time(row["updated_at"]),
    )


def _row(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    row: sqlite3.Row | None = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise HyperWikiError(ErrorCode.NOT_FOUND, "no such job", {"job_id": job_id})
    return row


def get_job(conn: sqlite3.Connection, job_id: str) -> Job:
    return _job(_row(conn, job_id))


def enqueue(
    conn: sqlite3.Connection,
    spec: WorkFingerprint,
    *,
    subject: str,
    now: datetime,
    priority: int = 0,
    max_attempts: int = 3,
    force: bool = False,
) -> EnqueueResult:
    """Queue work unless it is already queued, or already done and ``force`` is not set."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    fingerprint = fingerprint_id(spec)
    stamp = to_db_time(now)
    with _immediate(conn):
        open_job = conn.execute(
            "SELECT id FROM jobs WHERE fingerprint = ? AND state IN ('queued', 'leased')",
            (fingerprint,),
        ).fetchone()
        if open_job is not None:
            return EnqueueResult(
                outcome="already_queued", job_id=open_job["id"], fingerprint=fingerprint
            )
        if not force:
            done = conn.execute(
                "SELECT job_id FROM completed_work WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
            if done is not None:
                return EnqueueResult(
                    outcome="already_done", job_id=done["job_id"], fingerprint=fingerprint
                )
        job_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO jobs (id, operation, subject, fingerprint, fingerprint_json, state,
                priority, forced, attempts, max_attempts, not_before, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, 0, ?, ?, ?, ?)
            """,
            (
                job_id,
                spec.operation,
                subject,
                fingerprint,
                spec.model_dump_json(),
                priority,
                int(force),
                max_attempts,
                stamp,
                stamp,
                stamp,
            ),
        )
    return EnqueueResult(outcome="queued", job_id=job_id, fingerprint=fingerprint)


def claim(
    conn: sqlite3.Connection,
    *,
    owner: str,
    now: datetime,
    lease: timedelta,
    operations: Collection[str] | None = None,
) -> Job | None:
    """Lease the next runnable job: highest priority, then oldest.

    Jobs whose lease expired are runnable again. One whose last attempt expired fails with
    ``lease_expired`` instead.
    """
    if operations is not None and not operations:
        return None
    stamp = to_db_time(now)
    expires = to_db_time(now + lease)
    names = sorted(operations) if operations is not None else []
    operation_filter = f"AND operation IN ({', '.join('?' * len(names))})" if names else ""
    with _immediate(conn):
        while True:
            row = conn.execute(
                f"""
                SELECT * FROM jobs
                WHERE ((state = 'queued' AND not_before <= ?)
                    OR (state = 'leased' AND lease_expires_at <= ?))
                    {operation_filter}
                ORDER BY priority DESC, created_at, rowid
                LIMIT 1
                """,
                (stamp, stamp, *names),
            ).fetchone()
            if row is None:
                return None
            if row["state"] == "leased" and row["attempts"] >= row["max_attempts"]:
                error = ErrorInfo(
                    code=ErrorCode.LEASE_EXPIRED,
                    message="the worker stopped responding on the job's last attempt",
                    details={"attempts": row["attempts"], "lease_owner": row["lease_owner"]},
                )
                conn.execute(
                    """
                    UPDATE jobs SET state = 'failed', lease_owner = NULL,
                        lease_expires_at = NULL, last_error_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (error.model_dump_json(), stamp, row["id"]),
                )
                continue
            conn.execute(
                """
                UPDATE jobs SET state = 'leased', lease_owner = ?, lease_expires_at = ?,
                    attempts = attempts + 1, updated_at = ?
                WHERE id = ?
                """,
                (owner, expires, stamp, row["id"]),
            )
            return _job(_row(conn, row["id"]))


def heartbeat(
    conn: sqlite3.Connection, job_id: str, *, owner: str, now: datetime, lease: timedelta
) -> bool:
    """Extend the lease; False if the job is no longer leased to ``owner``."""
    with _immediate(conn):
        cursor = conn.execute(
            """
            UPDATE jobs SET lease_expires_at = ?, updated_at = ?
            WHERE id = ? AND state = 'leased' AND lease_owner = ?
            """,
            (to_db_time(now + lease), to_db_time(now), job_id, owner),
        )
        return cursor.rowcount == 1


def complete(
    conn: sqlite3.Connection, job_id: str, *, owner: str, now: datetime, store: Store
) -> bool:
    """Run ``store`` and mark the job done in one transaction, if ``owner`` holds the lease.

    Returns False, without calling ``store``, when the lease was lost. If ``store`` raises,
    nothing is written and the job stays leased.
    """
    stamp = to_db_time(now)
    with _immediate(conn):
        row = _row(conn, job_id)
        if row["state"] != "leased" or row["lease_owner"] != owner:
            return False
        summary = json.dumps(dict(store(conn)), ensure_ascii=False, sort_keys=True, allow_nan=False)
        conn.execute(
            """
            INSERT INTO completed_work
                (fingerprint, operation, subject, job_id, summary_json, completed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (fingerprint) DO UPDATE SET
                operation = excluded.operation,
                subject = excluded.subject,
                job_id = excluded.job_id,
                summary_json = excluded.summary_json,
                completed_at = excluded.completed_at
            """,
            (row["fingerprint"], row["operation"], row["subject"], job_id, summary, stamp),
        )
        conn.execute(
            """
            UPDATE jobs SET state = 'done', lease_owner = NULL, lease_expires_at = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (stamp, job_id),
        )
    return True


def fail(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    owner: str,
    now: datetime,
    error: ErrorInfo,
    retryable: bool,
) -> JobState | None:
    """Record a failed attempt: requeue with backoff if allowed, else fail the job.

    Returns the new state, or None if ``owner`` no longer holds the lease.
    """
    stamp = to_db_time(now)
    with _immediate(conn):
        row = _row(conn, job_id)
        if row["state"] != "leased" or row["lease_owner"] != owner:
            return None
        state: JobState
        if retryable and row["attempts"] < row["max_attempts"]:
            state = "queued"
            not_before = to_db_time(now + retry_delay(row["attempts"]))
        else:
            state = "failed"
            not_before = row["not_before"]
        conn.execute(
            """
            UPDATE jobs SET state = ?, lease_owner = NULL, lease_expires_at = NULL,
                not_before = ?, last_error_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (state, not_before, error.model_dump_json(), stamp, job_id),
        )
    return state


def cancel(conn: sqlite3.Connection, job_id: str, *, now: datetime) -> bool:
    """Cancel a queued or running job; a running worker's completion is then rejected."""
    with _immediate(conn):
        cursor = conn.execute(
            """
            UPDATE jobs SET state = 'cancelled', lease_owner = NULL, lease_expires_at = NULL,
                updated_at = ?
            WHERE id = ? AND state IN ('queued', 'leased')
            """,
            (to_db_time(now), job_id),
        )
        return cursor.rowcount == 1


def job_counts(conn: sqlite3.Connection) -> dict[str, dict[JobState, int]]:
    """Number of jobs per operation and state, every state present."""
    counts: dict[str, dict[JobState, int]] = {}
    for operation, state, count in conn.execute(
        "SELECT operation, state, COUNT(*) FROM jobs GROUP BY operation, state ORDER BY operation"
    ):
        counts.setdefault(operation, dict.fromkeys(get_args(JobState), 0))[state] = count
    return counts
