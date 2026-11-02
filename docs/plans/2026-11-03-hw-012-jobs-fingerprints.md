# HW-012 Jobs, Leases, Fingerprints, Attachment Versions, and the Zotero Copy Reader

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the backend resumable, deduplicated processing (jobs with leases, keyed by fingerprints), permanent attachment versions, and a read-only reader that imports the user's real Zotero library from a copy of its database.

**Architecture:** Migration 0002 adds `attachment_versions`, `jobs`, and `completed_work`. Import records one version per distinct file hash. A fingerprint is a canonical hash of everything a unit of work depends on. A job queue in SQLite hands out time-limited leases; outputs and completion commit in one transaction, and only while the worker still holds the lease. A worker loop runs handlers to idle. The Zotero reader copies `zotero.sqlite` (never opening the original), reads the copy, and produces the same `LoadedSnapshot` the snapshot adapter produces, so import is shared.

**Tech Stack:** Python 3.12, sqlite3 (stdlib), pydantic 2, pytest, mypy strict, ruff. No new dependencies.

**Spec:** HW-012 in [BACKLOG.md](BACKLOG.md); "Jobs and concurrency" and "Source and evidence contracts" in [ADR 0001](../decisions/0001-core-architecture.md); sections 6 and 9 of [PRODUCT_DESIGN.md](../../PRODUCT_DESIGN.md). Decisions approved by the user in chat on 2026-11-03:
- Build HW-012 on a copy of the real library; agents never need Zotero running, because Hyper-Wiki searches its own workspace copy.
- Build the copy reader as a proper adapter (`adapters/zotero_db.py`), not a one-off script. ADR 0004 (HW-006) later decides how it combines with the Zotero local API.
- The two Chinese-titled books in the user's library are the Chinese samples.

**Branch:** `m1/hw-012-jobs-fingerprints` from `main` (2e4be84).

## Design decisions (builder's calls, recorded for review)

- **Attachment version identity** is the file content: one row per `(attachment, sha256)`, ID `uuid5(attachment_id/sha256)`. Replacing a PDF adds a version; restoring old bytes reuses the old version (so its committed work is reused). The current version is the row matching `attachments.sha256`; a missing file has no current version but keeps history.
- **Fingerprint** = operation, operation version, input versions, extractor, model (name, version, runtime), output schema version, and configuration, as canonical JSON hashed to `fp_` + 32 hex characters. Prompt versions go in configuration.
- **Dedup:** at most one open (queued or leased) job per fingerprint (partial unique index); enqueueing a fingerprint already in `completed_work` is a no-op unless `force`. A forced run replaces the stored result instead of adding one.
- **Leases:** claims use `BEGIN IMMEDIATE`. Attempts count at claim. An expired lease is reclaimable; when its attempts are used up, the job fails with `lease_expired`. Completion succeeds only if the job is still leased to the same owner (an expired but unclaimed lease still counts). Retry delay is 30 s × 2^(attempts−1), capped at 1 hour.
- **Timestamps** are stored as UTC ISO-8601 with microseconds so string order is time order; naive datetimes are rejected.
- **Migration race** (deferred M0 minor): each migration runs in `BEGIN IMMEDIATE` and re-reads `user_version` inside the transaction, because workers and the MCP server may open a new database at the same time.
- **Zotero copy reader:**
  - Copies `zotero.sqlite` plus `-wal`/`-journal` (if present) into a temporary directory, comparing size and mtime before and after. If they changed, it retries, then fails with `zotero_database_busy`. It never opens the original with SQLite and never creates files in the data directory.
  - Checks required tables and columns and runs `PRAGMA quick_check` on the copy; failures raise `unsupported_zotero_database`.
  - Reads the user library only (group libraries later). Library identity: adapter `zotero`, library ID `local:<localUserKey>` (from Zotero's `settings` table), kind `user`, name `My Library`.
  - Versions use Zotero 10's local `clientVersion` counter. Observed on Zotero 10.0.6: it advances for local edits even when the library is unsynced and `version` stays 0.
  - Titles use Zotero's base-field mapping (for example `caseName` → `title`). Dates drop Zotero's multipart prefix (`2020-06-00 June 2020` → `June 2020`). `Extra` lines of the form `Key: value` become entries; other lines go under `text`.
  - Collections in the trash (or under a trashed parent) are left out with their memberships. Items in `deletedItems` are `trashed`. Trashed annotations are left out.
  - Embedded-image attachments (link mode 4) are skipped silently; they are images inside notes, not papers. Notes are items of type `note` only (attachment notes are not separate notes).
  - Records that fail validation are skipped with a `Problem`, and so are their children, instead of failing the whole import.
  - Imported files resolve under `<data_dir>/storage` (`storage:NAME` → `KEY/NAME`); absolute linked files go through `--linked-root` as before. `attachments:`-relative linked paths need Zotero's base directory, which is not read yet; they are reported as `invalid_path`.

## Global Constraints

- All checks pass after every task: `uv run --locked ruff check`, `uv run --locked ruff format --check`, `uv run --locked mypy`, `uv run --locked pytest`, `uv run --locked python scripts/check_docs.py`.
- mypy strict covers `src`, `tests`, `scripts`; tests are fully annotated; test files are flat in `tests/` with unique names.
- Hyper-Wiki never writes to, locks, or modifies Zotero's database or storage. The real library, its database copy, and the private workspace stay outside Git.
- Close every SQLite connection (Windows cannot delete open files during tmp cleanup).
- English and Chinese text round-trips unchanged; files are read and written as UTF-8.
- Tests use an injected clock; no test sleeps to wait for a lease.

## Review Focus

1. **The Zotero data directory must be untouched by a read:** no new files (`-shm`, `-journal`), identical bytes and mtimes. Pinned by `test_read_leaves_zotero_data_dir_untouched` (Task 7).
2. **A worker whose lease expired and was reclaimed must not commit outputs:** its `store` never runs and `completed_work` keeps one row. Pinned by `test_complete_after_reclaim_is_rejected_and_writes_nothing` (Task 4) and `test_lease_lost_during_execute_commits_nothing` (Task 5).
3. **Two processes opening a new database at once** must not fail the migration. Pinned by `test_migration_skips_versions_applied_concurrently` (Task 1).
4. **Restoring a replaced PDF** must reuse the old version, not create a third. Pinned by `test_restored_bytes_reuse_old_version` (Task 2).
5. **One malformed Zotero record** (nameless creator, attachment without a path, unknown annotation type) must not block the rest of the library. Pinned by `test_invalid_records_are_skipped_with_problems` (Task 7).

---

### Task 1: Migration race fix and migration 0002

**Files:** Modify `src/hyperwiki/storage/database.py`; create `src/hyperwiki/storage/migrations/0002_jobs_and_versions.sql`; test `tests/test_storage.py`.

**Interfaces:** Produces tables `attachment_versions(id, attachment_id, sha256, size_bytes, first_seen_at, last_seen_at, UNIQUE(attachment_id, sha256))`, `jobs(id, operation, subject, fingerprint, fingerprint_json, state, priority, forced, attempts, max_attempts, lease_owner, lease_expires_at, not_before, last_error_json, created_at, updated_at)` with a CHECK that `state = 'leased'` exactly when owner and expiry are set, partial unique index `jobs_one_open_per_fingerprint ON jobs(fingerprint) WHERE state IN ('queued','leased')`, and `completed_work(fingerprint PK, operation, subject, job_id → jobs, summary_json, completed_at)`.

- [ ] Write `test_migration_skips_versions_applied_concurrently`: pass `migrate` a tuple subclass whose first `__iter__` opens and fully migrates the same file through a second connection (so it runs after the first read of `user_version`); assert `migrate` returns `latest_version()` with no exception and tables exist once. Write `test_migration_0002_creates_job_tables`: column names of the three tables match the interface; inserting a `leased` job with no owner raises `sqlite3.IntegrityError`; two `queued` jobs with one fingerprint raise `IntegrityError`, while a `done` and a `queued` job with one fingerprint are allowed.
- [ ] Run `uv run --locked pytest tests/test_storage.py -q`. Expected: the race test fails with `table libraries already exists`; the 0002 test fails on missing tables.
- [ ] Implement: per migration, `conn.execute("BEGIN IMMEDIATE")`, re-read `user_version`, and skip if it is already at or past this migration. Otherwise run the SQL one statement at a time and set `PRAGMA user_version`, then commit; roll back on any error. `executescript` cannot be used here because it commits any open transaction first. Split statements with `sqlite3.complete_statement`, which keeps trigger bodies whole. Add the 0002 SQL.
- [ ] Run the storage tests, then all checks. Expected: pass.
- [ ] Commit `feat(storage): add job and attachment-version tables; make migrations safe to run concurrently`.

### Task 2: Attachment versions

**Files:** Modify `src/hyperwiki/storage/library_repo.py`, `src/hyperwiki/services/library_import.py`; test `tests/test_attachment_versions.py`, `tests/test_storage.py` (EntityCounts gains a field).

**Interfaces:**
- `upsert_attachment(...) -> bool` (True when the row was inserted or updated; False when a newer version is already stored).
- `class AttachmentVersion(Contract)`: `id: str`, `attachment_id: str`, `sha256: Sha256Hex`, `size_bytes: NonNegativeInt`, `first_seen_at: AwareDatetime`, `last_seen_at: AwareDatetime`.
- `attachment_version_id(attachment_id: str, sha256: str) -> str` (uuid5 in `LIBRARY_NAMESPACE`).
- `record_attachment_version(conn, attachment_id: str, sha256: str, size_bytes: int, seen_at: datetime) -> str`.
- `current_attachment_version(conn, attachment_id: str) -> AttachmentVersion | None`; `list_attachment_versions(conn, attachment_id: str) -> list[AttachmentVersion]` ordered by `first_seen_at`, then `id`.
- `EntityCounts.attachment_versions: int`.
- `import_snapshot` records a version for every applied, available attachment.

- [ ] Tests: `test_import_records_one_version_per_available_attachment` (bilingual fixture: 2 versions; re-import keeps 2 and the same IDs, `last_seen_at` advances with the injected clock); `test_replaced_pdf_adds_version_and_becomes_current` (use `snapshot_mutations` replace helper with a higher attachment version: 2 versions, current is the new hash, old still listed); `test_restored_bytes_reuse_old_version` (replace then restore original bytes: still 2 versions, current ID equals the first version's ID); `test_missing_file_has_no_current_version_but_keeps_history`; `test_stale_attachment_version_records_nothing` (manifest with a lower attachment version and different bytes: no new version).
- [ ] Run; expect failures on missing functions. Implement. Run all checks. Commit `feat(storage): record permanent attachment versions on import`.

### Task 3: Work fingerprints

**Files:** Create `src/hyperwiki/domain/work.py`; test `tests/test_fingerprints.py`.

**Interfaces:**
- `OperationName` = `str` matching `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$`; `FingerprintId` = `^fp_[0-9a-f]{32}$`.
- `ConfigValue = StrictStr | StrictInt | StrictBool | FiniteFloat | None` (no NaN or infinity).
- `class Component(Contract)`: `name: NonEmptyStr`, `version: NonEmptyStr`, `runtime: str | None = None`.
- `class WorkFingerprint(Contract)`: `operation: OperationName`, `operation_version: PositiveInt`, `inputs: dict[NonEmptyStr, NonEmptyStr]` (at least one), `extractor: Component | None = None`, `model: Component | None = None`, `schema_version: PositiveInt`, `config: dict[NonEmptyStr, ConfigValue] = {}`.
- `fingerprint_id(fingerprint: WorkFingerprint) -> FingerprintId` (canonical JSON: sorted keys, compact separators, `ensure_ascii=False`, SHA-256, first 32 hex).

- [ ] Tests: `test_fingerprint_is_stable_across_key_order`; `test_fingerprint_changes_with_each_component` (parametrized over operation_version, inputs, extractor version, model revision, model runtime, schema_version, a config value, and adding a config key: every variant differs from the base and from each other); `test_fingerprint_rejects_bad_operation_and_empty_inputs`; `test_fingerprint_rejects_non_finite_config` (`float("nan")`); `test_chinese_config_values_round_trip` (`{"language": "中文"}` survives `model_dump_json` → `model_validate_json` with the same ID).
- [ ] Run; expect import errors. Implement. All checks. Commit `feat(domain): add work fingerprints covering inputs, extractor, model, schema, and configuration`.

### Task 4: Job queue with leases

**Files:** Modify `src/hyperwiki/errors.py` (codes `LEASE_EXPIRED = "lease_expired"`, `JOB_FAILED = "job_failed"`); extend `src/hyperwiki/domain/work.py`; create `src/hyperwiki/storage/job_repo.py`; test `tests/test_job_queue.py`.

**Interfaces:**
- `JobState = Literal["queued", "leased", "done", "failed", "cancelled"]`; `EnqueueOutcome = Literal["queued", "already_queued", "already_done"]`.
- `class Job(Contract)`: `id`, `operation`, `subject`, `fingerprint: FingerprintId`, `spec: WorkFingerprint`, `state: JobState`, `priority: int`, `forced: bool`, `attempts: NonNegativeInt`, `max_attempts: PositiveInt`, `lease_owner: str | None`, `lease_expires_at: AwareDatetime | None`, `not_before: AwareDatetime`, `last_error: ErrorInfo | None`, `created_at`, `updated_at`.
- `class EnqueueResult(Contract)`: `outcome: EnqueueOutcome`, `job_id: str | None`, `fingerprint: FingerprintId`.
- In `job_repo`: `enqueue(conn, spec, *, subject, now, priority=0, max_attempts=3, force=False) -> EnqueueResult`; `claim(conn, *, owner, now, lease: timedelta, operations: Collection[str] | None = None) -> Job | None`; `heartbeat(conn, job_id, *, owner, now, lease) -> bool`; `complete(conn, job_id, *, owner, now, store: Callable[[sqlite3.Connection], Mapping[str, JsonValue]]) -> bool`; `fail(conn, job_id, *, owner, now, error: ErrorInfo, retryable: bool) -> JobState | None`; `cancel(conn, job_id, *, now) -> bool`; `get_job(conn, job_id) -> Job` (`not_found`); `job_counts(conn) -> dict[str, dict[JobState, int]]`; `retry_delay(attempts: int) -> timedelta`.
- Every function runs its own `BEGIN IMMEDIATE` transaction and raises `RuntimeError` if the connection is already in a transaction.

- [ ] Tests: enqueue outcomes (`queued`, then `already_queued` with the same ID, `already_done` after completion, `queued` again with `force=True` and `forced` set, `queued` again after `failed` or `cancelled`); `test_claim_order_priority_then_age`; `test_claim_skips_not_before_in_future`; `test_claim_filters_operations`; `test_expired_lease_is_reclaimed_with_next_attempt`; `test_expired_lease_on_last_attempt_fails_with_lease_expired`; `test_heartbeat_extends_and_detects_loss`; `test_complete_writes_outputs_and_result_once`; `test_complete_after_reclaim_is_rejected_and_writes_nothing` (store callback records calls: zero for the stale owner); `test_store_error_rolls_back_everything` (job stays leased, no `completed_work`, no output rows); `test_forced_rerun_replaces_result`; `test_fail_retryable_requeues_with_backoff` (30 s then 60 s); `test_fail_permanent_and_exhausted_attempts`; `test_cancel_queued_and_leased`; `test_job_counts_by_operation_and_state`; `test_naive_datetime_rejected`; `test_open_transaction_rejected`.
- [ ] Run; expect failures. Implement. All checks. Commit `feat(jobs): add a SQLite job queue with leases, retries, and deduplicated results`.

### Task 5: Worker loop

**Files:** Create `src/hyperwiki/processing/__init__.py`, `src/hyperwiki/processing/worker.py`; test `tests/test_worker.py`.

**Interfaces:**
- `class JobFailure(Exception)`: `__init__(self, error: ErrorInfo, *, retryable: bool)`.
- `class LeaseLost(Exception)`.
- `class JobContext`: `job: Job`; `heartbeat(self) -> None` (extends the lease or raises `LeaseLost`).
- `Handler[R]`, a generic `Protocol`: `execute(self, job: Job, context: JobContext) -> R` (no database writes); `store(self, conn: sqlite3.Connection, job: Job, result: R) -> Mapping[str, JsonValue]` (runs inside the completion transaction).
- `class WorkerReport(Contract)`: `done`, `retried`, `failed`, `lost` (all `NonNegativeInt`).
- `run_worker(conn, handlers: Mapping[str, Handler[Any]], *, owner: str, lease: timedelta = DEFAULT_LEASE, now: Callable[[], datetime] = utc_now, max_jobs: int | None = None, should_stop: Callable[[], bool] = ...) -> WorkerReport` (claims only the handlers' operations; stops when nothing is claimable); `new_owner_id() -> str` (`host:pid:random`); `DEFAULT_LEASE = timedelta(minutes=5)`.
- Exceptions: `JobFailure` uses its own `retryable`; `LeaseLost` counts as lost; any other `Exception` is retryable with `job_failed` and `"{type}: {message}"`; `BaseException` (for example `KeyboardInterrupt`) propagates and leaves the lease to expire.

- [ ] Tests: `test_worker_runs_queue_to_idle`; `test_worker_resumes_abandoned_job_after_lease_expiry` (claim with owner A and abandon; worker B at now + lease + 1 s finishes all; one `completed_work` per fingerprint; the abandoned job shows 2 attempts); `test_handler_exception_retries_then_fails`; `test_job_failure_not_retryable_fails_immediately`; `test_lease_lost_during_execute_commits_nothing` (handler cancels its own job, then heartbeats); `test_store_exception_rolls_back_and_retries`; `test_keyboard_interrupt_propagates_and_job_stays_leased`; `test_max_jobs_and_should_stop`.
- [ ] Run; expect failures. Implement. All checks. Commit `feat(processing): add a worker loop that resumes abandoned jobs`.

### Task 6: Job counts in status

**Files:** Modify `src/hyperwiki/services/library_import.py` (`StatusReport.jobs: dict[str, dict[JobState, int]] = {}`), `src/hyperwiki/interfaces/cli.py`; test `tests/test_cli.py`.

- [ ] Test `test_status_reports_job_counts`: import the fixture through the CLI, enqueue one job directly in the workspace database, then `status` returns `result.jobs == {"<operation>": {"queued": 1, ...zeros}}`; the existing empty-workspace test gains `jobs == {}`.
- [ ] Run; expect failure. Implement. All checks. Commit `feat(cli): show job counts in status`.

### Task 7: Zotero copy reader

**Files:** Create `src/hyperwiki/adapters/zotero_db.py`, `tests/zotero_db_factory.py` (builds a synthetic Zotero database with the real table and column names the reader uses, plus a storage folder), `tests/test_zotero_db.py`; modify `src/hyperwiki/adapters/snapshot.py` (extract a public `resolve_attachment_files`), `src/hyperwiki/services/library_import.py` (`import_loaded(conn, loaded, *, now)`, used by `import_snapshot`), `src/hyperwiki/interfaces/cli.py` (`import-zotero DATA_DIR [--linked-root …]`), `src/hyperwiki/errors.py` (`ZOTERO_DATABASE_BUSY = "zotero_database_busy"`, `UNSUPPORTED_ZOTERO_DATABASE = "unsupported_zotero_database"`).

**Interfaces:**
- `read_zotero_library(data_dir: Path, *, linked_file_roots: Sequence[Path] = (), now: Callable[[], datetime] = utc_now, copy_attempts: int = 3) -> LoadedSnapshot`.
- `resolve_attachment_files(attachments: Sequence[Attachment], storage_root: Path, linked_file_roots: Sequence[Path]) -> tuple[dict[str, AttachmentFile], list[Problem]]`.
- `import_loaded(conn, loaded: LoadedSnapshot, *, now: Callable[[], datetime] = utc_now) -> ImportReport`.

- [ ] Tests (synthetic database with an English and a Chinese item, a `case` item whose title comes from `caseName`, multipart dates, a single-field creator, tags, nested collections with one trashed parent, a trashed item, imported and linked-URL attachments, an embedded image, a child and a standalone note, a highlight annotation, an attachment note):
  - `test_reads_items_with_zotero_field_mapping`;
  - `test_reads_collections_excluding_trashed_subtrees`;
  - `test_reads_attachments_notes_and_annotations`;
  - `test_uses_client_version_and_local_user_key`;
  - `test_read_leaves_zotero_data_dir_untouched` (listing, bytes, and mtimes identical; works with a `-wal` present);
  - `test_changing_database_raises_busy` (monkeypatched copy appends to the source each time);
  - `test_missing_database_is_not_found`; `test_missing_required_column_is_unsupported`; `test_not_a_database_is_unsupported`;
  - `test_invalid_records_are_skipped_with_problems`;
  - `test_import_zotero_end_to_end_and_reimport_is_stable` (through `import_loaded`: counts, attachment versions, same IDs on re-read);
  - `test_cli_import_zotero` (JSON envelope, Chinese title intact).
- [ ] Run; expect failures. Implement. All checks. Commit `feat(adapters): read a Zotero library from a copy of its database`.

### Task 8: Real-library smoke and documentation

**Files:** Create `experiments/hw012-real-library/smoke.py` and `README.md`; modify `docs/plans/BACKLOG.md`, `DEVELOPMENT_PLAN.md`, `README.md`, and this plan's results section.

- [ ] Smoke script (`uv run --locked python experiments/hw012-real-library/smoke.py --zotero-dir ~/Zotero --workspace <private dir outside Git>`):
  1. read and import the library (timed); report counts, problem counts by code, attachment versions, and items with Han characters in the title;
  2. read and import again; assert counts, version count, and item IDs unchanged;
  3. enqueue `smoke.verify_bytes` for every current PDF version (inputs: version ID and SHA-256; config `{"read_chunk_bytes": 1048576}`);
  4. run a worker in a subprocess with a 2 s lease and a handler that re-reads the file and checks its SHA-256; kill it after about a third complete; wait out the lease; run a worker to idle;
  5. assert one `completed_work` row per job, no open jobs, and every hash verified; record which jobs needed a second attempt;
  6. enqueue everything again and assert all `already_done`;
  7. print a JSON summary with counts and timings only (no titles or paths).
- [ ] Run it on the user's library; record the summary in this plan's results section and in the HW-012 backlog row; note the `clientVersion` observation on the HW-006 row; update the development plan and README status.
- [ ] All checks. Commit `docs: record HW-012 results on the real library`.

## Results

Measured 2026-11-03 on the user's Zotero 10.0.6 library (macOS, Apple M4 Pro, Zotero closed) with [`experiments/hw012-real-library/smoke.py`](../../experiments/hw012-real-library/smoke.py). Counts and timings only.

| Check | Result |
|---|---|
| Read from a copy of `zotero.sqlite`, including hashing 1.7 GB of attachments | 0.67 s; 0 problems |
| Imported | 296 items (1 trashed), 373 attachments (all available), 373 attachment versions, 13 collections (4 trashed collections left out), 52 notes, 23 annotations; 2 items with Chinese titles |
| Import again | counts and internal item IDs unchanged |
| Jobs queued (one per current PDF version) | 294 |
| Worker process killed | after 98 jobs completed, with 1 job leased |
| Worker run again after the 2 s lease expired | 196 done, 0 failed, 0 lost; the interrupted job finished on its second attempt |
| After both runs | 294 job rows, 294 committed results, 294 distinct fingerprints, 0 open jobs, 1,709.6 MB of PDF bytes re-verified against their recorded SHA-256 |
| Queue everything again | 294 `already_done`, no new jobs |
| Workspace database `PRAGMA integrity_check` | `ok` |
| Zotero's `zotero.sqlite*` files (size and modification time) | unchanged |

Observations for HW-006 and ADR 0004:
- The library is not synced: every `version` is 0. Zotero 10's local `clientVersion` counter is set on items, collections, and the library (558 for the library), so the copy reader uses it.
- The read copies the database instead of opening it. It was run with Zotero closed; reading while Zotero runs and writes (the retry path) is covered only by the synthetic tests, and Windows is untested.

Limitations: the job is a stand-in that re-hashes files; real extraction and embedding stages come with HW-014 and HW-015. Group libraries, `attachments:`-relative linked files, and the local API are not read yet.
