import os
import sqlite3
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import JsonValue

from hyperwiki.domain.work import Job, WorkFingerprint
from hyperwiki.errors import ErrorCode, ErrorInfo
from hyperwiki.processing.worker import (
    Handler,
    JobContext,
    JobFailure,
    WorkerReport,
    new_owner_id,
    run_worker,
)
from hyperwiki.storage.job_repo import cancel, claim, enqueue, get_job

T0 = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
LEASE = timedelta(minutes=5)
OPERATION = "test.work"


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class Upper:
    """Uppercases the job subject and stores it in the ``outputs`` table."""

    def __init__(self, execute: Callable[[Job, JobContext], None] | None = None) -> None:
        self.before_return = execute
        self.stored: list[str] = []

    def execute(self, job: Job, context: JobContext) -> str:
        if self.before_return is not None:
            self.before_return(job, context)
        return job.subject.upper()

    def store(self, conn: sqlite3.Connection, job: Job, result: str) -> Mapping[str, JsonValue]:
        self.stored.append(result)
        conn.execute("INSERT INTO outputs VALUES (?, ?)", (job.id, result))
        return {"result": result}


@pytest.fixture
def conn(database: sqlite3.Connection) -> sqlite3.Connection:
    database.execute("CREATE TABLE outputs (job_id TEXT, value TEXT)")
    database.commit()
    return database


def spec(n: int, operation: str = OPERATION) -> WorkFingerprint:
    return WorkFingerprint(
        operation=operation, operation_version=1, inputs={"n": str(n)}, schema_version=1
    )


def queue(conn: sqlite3.Connection, count: int, *, max_attempts: int = 3) -> list[str]:
    return [
        enqueue(conn, spec(n), subject=f"s{n}", now=T0, max_attempts=max_attempts).job_id
        for n in range(count)
    ]


def run(
    conn: sqlite3.Connection, handler: Handler[str], clock: Clock, **kwargs: object
) -> WorkerReport:
    return run_worker(
        conn,
        {OPERATION: handler},
        owner="B",
        lease=LEASE,
        now=clock,
        **kwargs,  # type: ignore[arg-type]
    )


def count(conn: sqlite3.Connection, table: str) -> int:
    value: int = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return value


def test_worker_runs_queue_to_idle(conn: sqlite3.Connection) -> None:
    ids = queue(conn, 3)
    handler = Upper()
    assert run(conn, handler, Clock()) == WorkerReport(done=3, retried=0, failed=0, lost=0)
    assert sorted(handler.stored) == ["S0", "S1", "S2"]
    assert (count(conn, "outputs"), count(conn, "completed_work")) == (3, 3)
    assert {get_job(conn, job_id).state for job_id in ids} == {"done"}
    assert run(conn, handler, Clock()) == WorkerReport(done=0, retried=0, failed=0, lost=0)


def test_worker_resumes_abandoned_job_after_lease_expiry(conn: sqlite3.Connection) -> None:
    queue(conn, 3)
    abandoned = claim(conn, owner="crashed", now=T0, lease=LEASE)
    assert abandoned is not None
    clock = Clock()
    assert run(conn, Upper(), clock).done == 2
    assert get_job(conn, abandoned.id).state == "leased"

    clock.advance(LEASE.total_seconds())
    assert run(conn, Upper(), clock).done == 1
    resumed = get_job(conn, abandoned.id)
    assert (resumed.state, resumed.attempts) == ("done", 2)
    fingerprints = [row[0] for row in conn.execute("SELECT fingerprint FROM completed_work")]
    assert len(fingerprints) == len(set(fingerprints)) == 3
    assert count(conn, "outputs") == 3


def test_handler_exception_retries_then_fails(conn: sqlite3.Connection) -> None:
    [job_id] = queue(conn, 1, max_attempts=2)

    def explode(job: Job, context: JobContext) -> None:
        raise RuntimeError("cannot read file")

    clock = Clock()
    assert run(conn, Upper(explode), clock) == WorkerReport(done=0, retried=1, failed=0, lost=0)
    assert get_job(conn, job_id).state == "queued"
    clock.advance(30)
    assert run(conn, Upper(explode), clock) == WorkerReport(done=0, retried=0, failed=1, lost=0)
    job = get_job(conn, job_id)
    assert job.state == "failed"
    assert job.last_error == ErrorInfo(
        code=ErrorCode.JOB_FAILED, message="RuntimeError: cannot read file"
    )


def test_job_failure_not_retryable_fails_immediately(conn: sqlite3.Connection) -> None:
    [job_id] = queue(conn, 1)
    error = ErrorInfo(code=ErrorCode.NOT_FOUND, message="attachment file is gone")

    def give_up(job: Job, context: JobContext) -> None:
        raise JobFailure(error, retryable=False)

    assert run(conn, Upper(give_up), Clock()).failed == 1
    job = get_job(conn, job_id)
    assert (job.state, job.attempts, job.last_error) == ("failed", 1, error)


def test_lease_lost_during_execute_commits_nothing(conn: sqlite3.Connection) -> None:
    [job_id] = queue(conn, 1)

    def cancelled_meanwhile(job: Job, context: JobContext) -> None:
        cancel(conn, job.id, now=T0)
        context.heartbeat()

    handler = Upper(cancelled_meanwhile)
    assert run(conn, handler, Clock()) == WorkerReport(done=0, retried=0, failed=0, lost=1)
    assert handler.stored == []
    assert (count(conn, "outputs"), count(conn, "completed_work")) == (0, 0)
    assert get_job(conn, job_id).state == "cancelled"


def test_reclaimed_while_executing_commits_nothing(conn: sqlite3.Connection) -> None:
    [job_id] = queue(conn, 1)
    clock = Clock()

    def too_slow(job: Job, context: JobContext) -> None:
        clock.advance(LEASE.total_seconds())
        assert claim(conn, owner="other", now=clock(), lease=LEASE) is not None

    handler = Upper(too_slow)
    assert run(conn, handler, clock).lost == 1
    assert handler.stored == []
    assert count(conn, "outputs") == 0
    assert get_job(conn, job_id).lease_owner == "other"


def test_store_exception_rolls_back_and_retries(conn: sqlite3.Connection) -> None:
    [job_id] = queue(conn, 1)

    class BrokenStore(Upper):
        def store(self, conn: sqlite3.Connection, job: Job, result: str) -> Mapping[str, JsonValue]:
            super().store(conn, job, result)
            raise sqlite3.IntegrityError("constraint failed")

    assert run(conn, BrokenStore(), Clock()).retried == 1
    assert (count(conn, "outputs"), count(conn, "completed_work")) == (0, 0)
    job = get_job(conn, job_id)
    assert job.state == "queued"
    assert job.last_error is not None
    assert job.last_error.message == "IntegrityError: constraint failed"


def test_keyboard_interrupt_propagates_and_job_stays_leased(conn: sqlite3.Connection) -> None:
    [job_id] = queue(conn, 1)

    def interrupted(job: Job, context: JobContext) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run(conn, Upper(interrupted), Clock())
    job = get_job(conn, job_id)
    assert (job.state, job.lease_owner) == ("leased", "B")
    assert not conn.in_transaction


def test_max_jobs_and_should_stop(conn: sqlite3.Connection) -> None:
    queue(conn, 3)
    assert run(conn, Upper(), Clock(), max_jobs=2).done == 2
    assert run(conn, Upper(), Clock(), should_stop=lambda: True).done == 0
    assert run(conn, Upper(), Clock()).done == 1


def test_worker_claims_only_its_operations(conn: sqlite3.Connection) -> None:
    other = enqueue(conn, spec(1, "other.work"), subject="s", now=T0).job_id
    queue(conn, 1)
    assert run(conn, Upper(), Clock()).done == 1
    assert get_job(conn, other).state == "queued"


def test_heartbeat_extends_lease(conn: sqlite3.Connection) -> None:
    [job_id] = queue(conn, 1)
    clock = Clock()

    def long_running(job: Job, context: JobContext) -> None:
        clock.advance(200)
        context.heartbeat()
        assert get_job(conn, job.id).lease_expires_at == clock() + LEASE

    assert run(conn, Upper(long_running), clock).done == 1
    assert get_job(conn, job_id).state == "done"


def test_new_owner_id_is_unique_and_names_the_process() -> None:
    first, second = new_owner_id(), new_owner_id()
    assert first != second
    assert f":{os.getpid()}:" in first
