"""A worker loop that runs queued jobs through handlers until nothing is runnable.

A handler does its slow work in ``execute`` without touching the database, then writes its
outputs in ``store``, which runs inside the transaction that marks the job done. If the
worker loses its lease in between, ``store`` never runs. If the process dies, the lease
expires and the next worker resumes the job.
"""

import os
import socket
import sqlite3
import uuid
from collections.abc import Callable, Collection, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from pydantic import JsonValue, NonNegativeInt

from hyperwiki.domain.common import Contract
from hyperwiki.domain.work import Job, JobState
from hyperwiki.errors import ErrorCode, ErrorInfo, HyperWikiError
from hyperwiki.storage.job_repo import Store, claim, complete, fail, heartbeat

DEFAULT_LEASE = timedelta(minutes=5)


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_owner_id() -> str:
    """Identify this worker in leases: host, process, and a random suffix."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


class JobFailure(Exception):
    """Raised by a handler to fail a job with a specific error and retry decision."""

    def __init__(self, error: ErrorInfo, *, retryable: bool) -> None:
        super().__init__(error.message)
        self.error = error
        self.retryable = retryable


class LeaseLost(Exception):
    """The job was cancelled or taken over by another worker; stop working on it."""


class JobContext:
    """What a handler may do while it runs: renew its lease."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        job: Job,
        *,
        owner: str,
        lease: timedelta,
        now: Callable[[], datetime],
    ) -> None:
        self.job = job
        self._conn = conn
        self._owner = owner
        self._lease = lease
        self._now = now

    def heartbeat(self) -> None:
        """Extend the lease; raise ``LeaseLost`` if this worker no longer holds it."""
        if not heartbeat(
            self._conn, self.job.id, owner=self._owner, now=self._now(), lease=self._lease
        ):
            raise LeaseLost(self.job.id)


class Handler[R](Protocol):
    def execute(self, job: Job, context: JobContext) -> R:
        """Do the work without writing to the database; may call ``context.heartbeat()``."""
        ...

    def store(self, conn: sqlite3.Connection, job: Job, result: R) -> Mapping[str, JsonValue]:
        """Write outputs inside the completion transaction and return a short summary."""
        ...


class WorkerReport(Contract):
    done: NonNegativeInt
    retried: NonNegativeInt
    failed: NonNegativeInt
    lost: NonNegativeInt


def _never() -> bool:
    return False


def _error_info(error: Exception) -> ErrorInfo:
    if isinstance(error, HyperWikiError):
        return error.to_info()
    return ErrorInfo(code=ErrorCode.JOB_FAILED, message=f"{type(error).__name__}: {error}")


def _store_for[R](handler: Handler[R], job: Job, result: R) -> Store:
    def store(conn: sqlite3.Connection) -> Mapping[str, JsonValue]:
        return handler.store(conn, job, result)

    return store


def run_worker(
    conn: sqlite3.Connection,
    handlers: Mapping[str, Handler[Any]],
    *,
    owner: str,
    lease: timedelta = DEFAULT_LEASE,
    now: Callable[[], datetime] = utc_now,
    max_jobs: int | None = None,
    should_stop: Callable[[], bool] = _never,
    subjects: Collection[str] | None = None,
) -> WorkerReport:
    """Run jobs for the handlers' operations until none is runnable or a limit is reached.

    ``subjects`` limits the run to jobs on those subjects, for "update selected".

    Handler exceptions fail the attempt (retryable unless a ``JobFailure`` says otherwise).
    ``BaseException`` such as ``KeyboardInterrupt`` propagates and leaves the lease to expire.
    """
    done = retried = failed = lost = 0
    processed = 0
    while not should_stop() and (max_jobs is None or processed < max_jobs):
        job = claim(
            conn,
            owner=owner,
            now=now(),
            lease=lease,
            operations=handlers.keys(),
            subjects=subjects,
        )
        if job is None:
            break
        processed += 1
        handler = handlers[job.operation]
        context = JobContext(conn, job, owner=owner, lease=lease, now=now)
        outcome: JobState | None
        try:
            result = handler.execute(job, context)
        except LeaseLost:
            lost += 1
            continue
        except JobFailure as failure:
            outcome = fail(
                conn,
                job.id,
                owner=owner,
                now=now(),
                error=failure.error,
                retryable=failure.retryable,
            )
        except Exception as error:
            outcome = fail(
                conn, job.id, owner=owner, now=now(), error=_error_info(error), retryable=True
            )
        else:
            try:
                committed = complete(
                    conn, job.id, owner=owner, now=now(), store=_store_for(handler, job, result)
                )
            except Exception as error:
                outcome = fail(
                    conn, job.id, owner=owner, now=now(), error=_error_info(error), retryable=True
                )
            else:
                if committed:
                    done += 1
                else:
                    lost += 1
                continue
        if outcome is None:
            lost += 1
        elif outcome == "queued":
            retried += 1
        else:
            failed += 1
    return WorkerReport(done=done, retried=retried, failed=failed, lost=lost)
