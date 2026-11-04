import json
import sqlite3
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import JsonValue

from hyperwiki.domain.work import WorkFingerprint, fingerprint_id
from hyperwiki.errors import ErrorCode, ErrorInfo, HyperWikiError
from hyperwiki.storage.job_repo import (
    cancel,
    cancel_queued,
    claim,
    complete,
    enqueue,
    fail,
    get_job,
    has_runnable,
    heartbeat,
    job_counts,
    retry_delay,
)

T0 = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
LEASE = timedelta(minutes=5)
ERROR = ErrorInfo(code=ErrorCode.JOB_FAILED, message="boom")


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def spec(n: int = 1, operation: str = "test.work") -> WorkFingerprint:
    return WorkFingerprint(
        operation=operation, operation_version=1, inputs={"subject": f"s{n}"}, schema_version=1
    )


def nothing(conn: sqlite3.Connection) -> Mapping[str, JsonValue]:
    return {}


def summary(**values: JsonValue) -> Callable[[sqlite3.Connection], Mapping[str, JsonValue]]:
    def store(conn: sqlite3.Connection) -> Mapping[str, JsonValue]:
        return values

    return store


def queued_id(
    conn: sqlite3.Connection, n: int = 1, *, priority: int = 0, max_attempts: int = 3
) -> str:
    result = enqueue(
        conn, spec(n), subject=f"s{n}", now=T0, priority=priority, max_attempts=max_attempts
    )
    assert result.outcome == "queued"
    return result.job_id


def claimed(conn: sqlite3.Connection, owner: str = "A", now: datetime = T0) -> str:
    job = claim(conn, owner=owner, now=now, lease=LEASE)
    assert job is not None
    return job.id


def completed_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM completed_work ORDER BY completed_at").fetchall()


@pytest.fixture
def outputs(database: sqlite3.Connection) -> sqlite3.Connection:
    database.execute("CREATE TABLE outputs (job_id TEXT, value TEXT)")
    database.commit()
    return database


def test_enqueue_outcomes(database: sqlite3.Connection) -> None:
    first = enqueue(database, spec(1), subject="s1", now=T0)
    assert first.outcome == "queued"
    assert first.fingerprint == fingerprint_id(spec(1))

    again = enqueue(database, spec(1), subject="s1", now=at(1))
    assert (again.outcome, again.job_id) == ("already_queued", first.job_id)

    job_id = claimed(database)
    assert complete(database, job_id, owner="A", now=at(2), store=nothing)
    done = enqueue(database, spec(1), subject="s1", now=at(3))
    assert (done.outcome, done.job_id) == ("already_done", first.job_id)

    forced = enqueue(database, spec(1), subject="s1", now=at(4), force=True)
    assert forced.outcome == "queued"
    assert forced.job_id != first.job_id
    assert get_job(database, forced.job_id).forced


def test_enqueue_again_after_failed_or_cancelled(database: sqlite3.Connection) -> None:
    failed_id = queued_id(database, 1, max_attempts=1)
    claimed(database)
    assert fail(database, failed_id, owner="A", now=T0, error=ERROR, retryable=True) == "failed"
    assert enqueue(database, spec(1), subject="s1", now=at(1)).outcome == "queued"

    cancelled_id = queued_id(database, 2)
    assert cancel(database, cancelled_id, now=T0)
    assert enqueue(database, spec(2), subject="s2", now=at(1)).outcome == "queued"


def test_job_round_trips_spec(database: sqlite3.Connection) -> None:
    job = get_job(database, queued_id(database, 7, priority=3))
    assert job.spec == spec(7)
    assert job.fingerprint == fingerprint_id(spec(7))
    assert (job.operation, job.subject, job.state, job.priority) == ("test.work", "s7", "queued", 3)
    assert (job.attempts, job.max_attempts, job.forced) == (0, 3, False)
    assert (job.created_at, job.not_before, job.lease_owner) == (T0, T0, None)


def test_claim_order_priority_then_age(database: sqlite3.Connection) -> None:
    low_first = enqueue(database, spec(1), subject="s1", now=T0).job_id
    low_second = enqueue(database, spec(2), subject="s2", now=T0).job_id
    high = enqueue(database, spec(3), subject="s3", now=at(1), priority=5).job_id
    order = [claimed(database, now=at(2)) for _ in range(3)]
    assert order == [high, low_first, low_second]
    assert claim(database, owner="A", now=at(2), lease=LEASE) is None


def test_claim_skips_not_before_in_future(database: sqlite3.Connection) -> None:
    job_id = queued_id(database)
    claimed(database)
    assert fail(database, job_id, owner="A", now=T0, error=ERROR, retryable=True) == "queued"
    assert claim(database, owner="A", now=at(29), lease=LEASE) is None
    assert claimed(database, now=at(30)) == job_id


def test_claim_filters_operations(database: sqlite3.Connection) -> None:
    enqueue(database, spec(1, "a.x"), subject="s", now=T0)
    wanted = enqueue(database, spec(1, "b.y"), subject="s", now=at(1)).job_id
    job = claim(database, owner="A", now=at(2), lease=LEASE, operations={"b.y"})
    assert job is not None and job.id == wanted
    assert claim(database, owner="A", now=at(2), lease=LEASE, operations=()) is None


def test_expired_lease_is_reclaimed_with_next_attempt(database: sqlite3.Connection) -> None:
    job_id = queued_id(database)
    claimed(database, owner="A")
    assert claim(database, owner="B", now=at(299), lease=LEASE) is None
    reclaimed = claim(database, owner="B", now=at(300), lease=LEASE)
    assert reclaimed is not None
    assert (reclaimed.id, reclaimed.attempts, reclaimed.lease_owner) == (job_id, 2, "B")
    assert reclaimed.lease_expires_at == at(600)


def test_expired_lease_on_last_attempt_fails_with_lease_expired(
    database: sqlite3.Connection,
) -> None:
    job_id = queued_id(database, max_attempts=1)
    claimed(database, owner="A")
    assert claim(database, owner="B", now=at(301), lease=LEASE) is None
    job = get_job(database, job_id)
    assert job.state == "failed"
    assert job.lease_owner is None
    assert job.last_error is not None
    assert job.last_error.code is ErrorCode.LEASE_EXPIRED


def test_heartbeat_extends_and_detects_loss(database: sqlite3.Connection) -> None:
    queued_id(database)
    job_id = claimed(database)
    assert heartbeat(database, job_id, owner="A", now=at(200), lease=LEASE)
    assert get_job(database, job_id).lease_expires_at == at(500)
    assert claim(database, owner="B", now=at(400), lease=LEASE) is None
    assert claimed(database, owner="B", now=at(500)) == job_id
    assert not heartbeat(database, job_id, owner="A", now=at(501), lease=LEASE)


def test_complete_writes_outputs_and_result_once(outputs: sqlite3.Connection) -> None:
    job_id = queued_id(outputs)
    claimed(outputs)
    calls: list[str] = []

    def store(conn: sqlite3.Connection) -> Mapping[str, JsonValue]:
        calls.append(job_id)
        conn.execute("INSERT INTO outputs VALUES (?, ?)", (job_id, "x"))
        return {"value": "x", "中文": "证据"}

    assert complete(outputs, job_id, owner="A", now=at(5), store=store)
    assert not complete(outputs, job_id, owner="A", now=at(6), store=store)
    assert calls == [job_id]
    assert outputs.execute("SELECT COUNT(*) FROM outputs").fetchone()[0] == 1
    [row] = completed_rows(outputs)
    assert (row["fingerprint"], row["job_id"], row["subject"]) == (
        fingerprint_id(spec(1)),
        job_id,
        "s1",
    )
    assert json.loads(row["summary_json"]) == {"value": "x", "中文": "证据"}
    job = get_job(outputs, job_id)
    assert (job.state, job.lease_owner, job.lease_expires_at) == ("done", None, None)


def test_complete_after_reclaim_is_rejected_and_writes_nothing(
    outputs: sqlite3.Connection,
) -> None:
    job_id = queued_id(outputs)
    claimed(outputs, owner="A")
    claimed(outputs, owner="B", now=at(300))
    calls: list[str] = []

    def store(conn: sqlite3.Connection) -> Mapping[str, JsonValue]:
        calls.append("called")
        conn.execute("INSERT INTO outputs VALUES (?, ?)", (job_id, "stale"))
        return {}

    assert not complete(outputs, job_id, owner="A", now=at(301), store=store)
    assert calls == []
    assert complete(outputs, job_id, owner="B", now=at(302), store=summary(owner="B"))
    assert outputs.execute("SELECT COUNT(*) FROM outputs").fetchone()[0] == 0
    [row] = completed_rows(outputs)
    assert json.loads(row["summary_json"]) == {"owner": "B"}


def test_store_error_rolls_back_everything(outputs: sqlite3.Connection) -> None:
    job_id = queued_id(outputs)
    claimed(outputs)

    def store(conn: sqlite3.Connection) -> Mapping[str, JsonValue]:
        conn.execute("INSERT INTO outputs VALUES (?, ?)", (job_id, "partial"))
        raise RuntimeError("disk full")

    with pytest.raises(RuntimeError, match="disk full"):
        complete(outputs, job_id, owner="A", now=at(1), store=store)
    assert not outputs.in_transaction
    assert outputs.execute("SELECT COUNT(*) FROM outputs").fetchone()[0] == 0
    assert completed_rows(outputs) == []
    job = get_job(outputs, job_id)
    assert (job.state, job.lease_owner) == ("leased", "A")


def test_forced_rerun_replaces_result(database: sqlite3.Connection) -> None:
    first = queued_id(database)
    claimed(database)
    assert complete(database, first, owner="A", now=at(1), store=summary(run=1))
    second = enqueue(database, spec(1), subject="s1", now=at(2), force=True).job_id
    claimed(database, now=at(3))
    assert complete(database, second, owner="A", now=at(4), store=summary(run=2))
    [row] = completed_rows(database)
    assert (row["job_id"], json.loads(row["summary_json"])) == (second, {"run": 2})


def test_fail_retryable_requeues_with_backoff_until_attempts_run_out(
    database: sqlite3.Connection,
) -> None:
    job_id = queued_id(database, max_attempts=3)
    claimed(database, now=T0)
    assert fail(database, job_id, owner="A", now=T0, error=ERROR, retryable=True) == "queued"
    assert get_job(database, job_id).not_before == at(30)
    claimed(database, now=at(30))
    assert fail(database, job_id, owner="A", now=at(30), error=ERROR, retryable=True) == "queued"
    assert get_job(database, job_id).not_before == at(90)
    claimed(database, now=at(90))
    assert fail(database, job_id, owner="A", now=at(90), error=ERROR, retryable=True) == "failed"
    job = get_job(database, job_id)
    assert (job.state, job.attempts, job.last_error) == ("failed", 3, ERROR)


def test_fail_permanent_and_by_wrong_owner(database: sqlite3.Connection) -> None:
    job_id = queued_id(database)
    claimed(database)
    assert fail(database, job_id, owner="B", now=T0, error=ERROR, retryable=False) is None
    assert fail(database, job_id, owner="A", now=T0, error=ERROR, retryable=False) == "failed"
    assert fail(database, job_id, owner="A", now=T0, error=ERROR, retryable=False) is None


def test_retry_delay_doubles_and_caps() -> None:
    assert [retry_delay(n) for n in (1, 2, 3)] == [
        timedelta(seconds=30),
        timedelta(seconds=60),
        timedelta(seconds=120),
    ]
    assert retry_delay(20) == timedelta(hours=1)


def test_cancel_queued_and_leased(database: sqlite3.Connection) -> None:
    queued = queued_id(database, 1)
    leased = queued_id(database, 2)
    assert claimed(database) == queued
    assert cancel(database, leased, now=at(1))
    assert cancel(database, queued, now=at(1))
    assert not cancel(database, queued, now=at(2))
    assert not complete(database, queued, owner="A", now=at(3), store=nothing)
    assert get_job(database, queued).state == "cancelled"


def test_job_counts_by_operation_and_state(database: sqlite3.Connection) -> None:
    assert job_counts(database) == {}
    enqueue(database, spec(1, "a.x"), subject="s1", now=T0)
    enqueue(database, spec(2, "a.x"), subject="s2", now=at(1))
    enqueue(database, spec(1, "b.y"), subject="s1", now=at(2))
    claimed(database, now=at(3))
    zero = {"queued": 0, "leased": 0, "done": 0, "failed": 0, "cancelled": 0}
    assert job_counts(database) == {
        "a.x": {**zero, "queued": 1, "leased": 1},
        "b.y": {**zero, "queued": 1},
    }


def test_naive_datetime_rejected(database: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="timezone"):
        enqueue(database, spec(), subject="s", now=datetime(2026, 11, 3))


def test_open_transaction_rejected(database: sqlite3.Connection) -> None:
    database.execute("BEGIN")
    try:
        with pytest.raises(RuntimeError, match="transaction"):
            enqueue(database, spec(), subject="s", now=T0)
    finally:
        database.rollback()


def test_unknown_job_is_not_found(database: sqlite3.Connection) -> None:
    with pytest.raises(HyperWikiError) as caught:
        get_job(database, "missing")
    assert caught.value.code is ErrorCode.NOT_FOUND


def test_claim_filters_by_subject(database: sqlite3.Connection) -> None:
    first = queued_id(database, 1, priority=5)
    second = queued_id(database, 2)
    job = claim(database, owner="A", now=T0, lease=LEASE, subjects={"s2"})
    assert job is not None and job.id == second
    assert claim(database, owner="A", now=T0, lease=LEASE, subjects={"s9"}) is None
    assert claim(database, owner="A", now=T0, lease=LEASE, subjects=set()) is None
    assert claimed(database) == first


def test_enqueue_raises_priority_of_open_job(database: sqlite3.Connection) -> None:
    job_id = queued_id(database, 1)
    other = queued_id(database, 2, priority=5)
    again = enqueue(database, spec(1), subject="s1", now=at(1), priority=10)
    assert (again.outcome, again.job_id) == ("already_queued", job_id)
    assert get_job(database, job_id).priority == 10
    lower = enqueue(database, spec(1), subject="s1", now=at(2), priority=0)
    assert lower.outcome == "already_queued"
    assert get_job(database, job_id).priority == 10
    assert claimed(database) == job_id
    assert get_job(database, other).priority == 5


def test_cancel_queued_leaves_leased(database: sqlite3.Connection) -> None:
    leased = queued_id(database, 1)
    queued = queued_id(database, 2)
    assert claimed(database) == leased
    assert cancel_queued(database, [leased, queued, "no-such-job"], now=at(1)) == 1
    assert get_job(database, leased).state == "leased"
    assert get_job(database, queued).state == "cancelled"
    assert cancel_queued(database, [], now=at(1)) == 0


def test_has_runnable_is_read_only(database: sqlite3.Connection) -> None:
    assert not has_runnable(database, now=T0, operations={"test.work"})
    queued_id(database, 1)
    assert has_runnable(database, now=T0, operations={"test.work"})
    assert not has_runnable(database, now=T0, operations={"other.work"})
    assert get_job(database, claimed(database)).state == "leased"
    assert not has_runnable(database, now=at(1), operations={"test.work"})
    assert has_runnable(database, now=T0 + LEASE, operations={"test.work"})  # lease expired
    assert not database.in_transaction
