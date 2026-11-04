# HW-013 Update Engine and Modes

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the workspace in step with Zotero by itself. Changes are applied as they happen, deleted records are tombstoned, and the evidence work they need is planned and run. The user can pause processing, exclude collections, set the scheduled check, and update selected items or everything now.

**Architecture:**
- **One update step for every mode.** It reads the library, applies the differences with tombstones, and then plans processing for the whole workspace. Automatic, scheduled, update-all, and update-selected differ only in what starts the step, whether files are rehashed, and which jobs run afterwards.
- **One loop.** It replaces HW-011's `watch_zotero`:
  - it runs that step when Zotero's database changes, debounced, while automatic mode is on;
  - it runs a full check when the schedule says one is due;
  - between syncs, it runs queued work while processing is not paused.
- **Settings** live in the workspace database. Any process (CLI now, MCP and the plugin later) can change them, and the running loop picks them up at its next tick.

**Tech Stack:** Python 3.12, sqlite3, pydantic 2, pytest, mypy strict, ruff. No new dependency.

**Spec:** HW-013 in [BACKLOG.md](BACKLOG.md): "Automatic with debounce; scheduled daily/weekly/fortnightly/monthly/off; update selected (with force); update all; pause and pending list; collection exclusion; trash/restore/delete with tombstones; partial snapshots never delete". Sections 6.1–6.3 of [PRODUCT_DESIGN.md](../../PRODUCT_DESIGN.md) give the modes, controls, and lifecycle. [ADR 0004](../decisions/0004-zotero-change-detection.md) decisions 1–2 give detection and file reconciliation. [ADR 0001](../decisions/0001-core-architecture.md) says removed sources are tombstoned.

**Branch:** `m1/hw-013-update-engine`, from `main` (b639f89), whose CI passed.

## Design decisions (builder's calls, recorded for review)

- **Pause stops processing, not syncing.**
  - While paused, the loop still reads Zotero, applies changes, and plans work. Jobs stay queued and form the pending list.
  - The workspace therefore keeps mirroring Zotero. An item trashed or deleted while paused disappears from search at once, and the pending list is exact.
  - Pause applies to automatic and scheduled processing. Manual `sync` (update all or update selected) still processes, because the user asked for it.
  - A paused loop stops between jobs. A job already running finishes.
  - *Cost if wrong:* if the user expected pause to stop reading Zotero too, a "stop syncing" switch is a small addition.
- **Tombstones.**
  - A record that leaves a complete read gets `deleted_at` and stays stored for history. It is no longer counted, planned, or reported deleted again.
  - Evidence of a tombstoned attachment is kept. `read_evidence` reports it as `deleted`.
  - If the same key appears again, for example after the Zotero database is restored from a backup, the record comes back to life, even at a lower version.
  - Partial and failed reads never tombstone, as before. A snapshot `import` now applies deletions from complete snapshots too, so fixture libraries follow the same lifecycle as Zotero.
- **Evidence status.**
  - `EvidenceRecord` gains `status`: `current`, `superseded`, `trashed`, `deleted`, or `excluded`.
  - It is the first that applies in the order deleted, trashed, excluded, superseded. The source's state matters most when an agent decides whether a citation still stands.
  - `current` stays and equals `status == "current"`.
  - An attachment counts as trashed or deleted when it or its parent item is.
- **Collection exclusion** (product design: "items whose collections are all excluded are skipped entirely. Unfiled items are included").
  - Excluding a collection also excludes its subcollections.
  - An item is excluded when it is in at least one live collection and every one of them is excluded. Trashed collections count as memberships; tombstoned ones do not.
  - An attachment follows its parent item. Standalone attachments are never excluded, because their collection membership is not imported yet (noted as a limitation).
  - Excluded attachments are not planned. Their queued jobs are cancelled, and coverage counts them as `excluded`. Evidence already extracted stays stored, and `read_evidence` reports it as `excluded`.
  - Exclusions are stored by library and collection key, so they survive the collection being tombstoned and coming back.
- **Planning reconciles the queue.**
  - After every sync, extraction is planned for the whole workspace.
  - Queued `extract.text` jobs that are no longer wanted are cancelled. These are jobs for versions that are superseded, trashed, deleted, or excluded.
  - Leased (running) jobs are left to finish.
- **Debounce.**
  - The database watcher waits for Zotero's database to stay unchanged for a quiet period (3 s, as in HW-011). This way an item and a PDF saved moments later are read together.
  - New: it also syncs at the latest 60 s after the first unsynced change, even if writes continue. Typing in a Zotero note then cannot postpone a sync indefinitely, which keeps the product's "within about a minute".
  - The 10-minute rescan stays as the backstop for PDFs replaced outside Zotero, which do not touch its database.
  - These timings are command-line options of `watch`, not stored settings.
- **Scheduled check.**
  - A full check is a sync that rehashes every attachment file instead of trusting size and modification time, then plans and processes as usual. So it also catches content changed with size and time preserved.
  - Intervals are daily (1 day), weekly (7), fortnightly (14), and monthly (30 days), or off.
  - The loop runs a full check when none has run within the interval. This includes at start after Hyper-Wiki was closed, because the time of the last check is stored in the workspace.
  - Manual update all (`sync` without a selection) is the same full check, run now, and it resets the schedule.
  - With automatic mode off, the loop only runs scheduled checks and queued work.
- **Update selected.**
  - `sync --item KEY --collection KEY`:
    1. syncs normally;
    2. plans the whole workspace;
    3. re-plans the selected attachments at a higher priority, so they run ahead of the queue;
    4. runs only their jobs now.
  - Collections include their subcollections.
  - `--force` re-extracts the selection even if already done, and retries failures.
  - Unknown keys fail with `not_found`, after the sync.
  - Excluded and trashed items in a selection are skipped and counted as such.
- **Settings and state.** These are stored in a `workspace_settings` table: `automatic` (default on), `schedule` (default daily), `paused` (default off), and `last_full_check_at`.
- **CLI.**
  - `sync` and `watch` replace `import-zotero` and `import-zotero --watch`. HW-016 names `sync` as the CLI verb; the CLI is pre-release, with no users to keep compatible.
  - `settings`, `pause`, and `resume` are added.
  - `status` gains an `updates` section, and `status --pending` adds the pending list by item.
  - `import` (snapshots) now reports change counts and applies tombstones, but still does no processing.
  - `extract` stays for manual extraction and now respects exclusion.

## Global Constraints

- All checks pass after every task: `uv run --locked ruff check`, `uv run --locked ruff format --check`, `uv run --locked mypy`, `uv run --locked pytest`, `uv run --locked python scripts/check_docs.py`.
- mypy strict covers `src`, `tests`, and `scripts`. Tests are fully annotated and flat in `tests/`, with unique file names.
- **Zotero is read-only.** Hyper-Wiki only copies and reads its database and reads attachment files.
- **Tests don't depend on the machine:** they never sleep; loops take injected clocks, sleep functions, and stop conditions.
- **Read operations never write:** `status`, `read_evidence`, and coverage do not change the database.
- **Partial snapshots never delete.**

## Review Focus

1. **A partial or failed read must tombstone nothing.** Pinned by `test_partial_read_tombstones_nothing` (Task 1).
2. **A key that reappears after tombstoning must come back live, even at a lower version.** Pinned by `test_reappearing_key_is_live_again` (Task 1).
3. **Pause must stop the worker between jobs, and resuming must run the backlog without waiting for a Zotero change.** Pinned by `test_pause_stops_worker_between_jobs` and `test_engine_runs_queue_after_resume` (Task 4).
4. **Continuous writes must not postpone a sync indefinitely.** Pinned by `test_continuous_writes_ready_after_max_wait` (Task 4).
5. **Exclusion must cover subcollections and keep an item that is also in an included collection, and it must cancel queued work.** Pinned by `test_exclusion_covers_subcollections_keeps_multi_filed` (Task 2) and `test_excluded_attachments_are_not_planned_and_jobs_cancelled` (Task 3).

---

### Task 1: Tombstones (migration 0005, storage, sync, snapshot import)

**Files:**
- Create: `src/hyperwiki/storage/migrations/0005_update_engine.sql`
- Modify: `src/hyperwiki/storage/library_repo.py`, `src/hyperwiki/services/library_import.py`, `src/hyperwiki/services/zotero_sync.py`, `src/hyperwiki/interfaces/cli.py` (import output)
- Test: `tests/test_storage.py`, `tests/test_zotero_sync.py`, `tests/test_library_import.py`

**Interfaces:**
- Produces:
  - `tombstone(conn, library_id, entity: EntityKind, key, *, at: datetime) -> bool` in `library_repo`.
  - `EntityCounts.deleted: dict[EntityKind, int]`. The other counts cover live records only.
  - `import_loaded(conn, loaded, *, now, deletions: Sequence[ChangeEvent] = ())`.
  - `apply_read(conn, loaded, *, now: datetime) -> SyncReport`, which diffs, imports, and tombstones in one transaction.
  - `sync_zotero(..., verify_files: bool = False)`.
  - `sync_snapshot(conn, snapshot_dir, *, linked_file_roots=(), now=utc_now) -> SyncReport`.

**Steps:**
- [x] **Migration 0005** adds:
  - `deleted_at TEXT` to `collections`, `items`, `attachments`, `notes`, and `annotations`;
  - `workspace_settings(name TEXT PRIMARY KEY, value_json TEXT NOT NULL)`;
  - `excluded_collections(library_id REFERENCES libraries, collection_key, PRIMARY KEY (library_id, collection_key))`.

  `test_storage` expects latest version 5.
- [x] **Failing tests:**
  - `test_erased_records_are_tombstoned_once`: erase, sync, and the delete events are reported once, `deleted_at` is set, and the next sync reports nothing.
  - `test_partial_read_tombstones_nothing`.
  - `test_reappearing_key_is_live_again`: tombstone a key, then re-add it at a lower version; it is live again with the new data.
  - `test_counts_exclude_tombstoned`.
  - `test_snapshot_delete_tombstones`: `delete_item` on the fixture, then `sync_snapshot`; the item and its children are tombstoned.
  - `test_partial_snapshot_tombstones_nothing`.
  - `test_full_check_rehashes_unchanged_files`: `verify_files=True` hashes every available file.
- [x] **Implement:**
  - `_stored` ignores tombstoned rows.
  - Upserts set `deleted_at = NULL` and apply when `excluded.version >= version OR deleted_at IS NOT NULL`.
  - `tombstone` sets `deleted_at` only where it is NULL.
  - `known_attachment_files` skips tombstoned rows.
  - `apply_read` passes the delete events to `import_loaded`.
  - `sync_zotero` uses `apply_read`, and with `verify_files` passes no known files.
  - CLI `import` uses `sync_snapshot` and outputs the same shape as a sync (import report, `change_counts`, `duration_ms`).
- [x] **Verify:** run the tests, then all checks, then commit `feat(sync): tombstone deleted records (migration 0005)`.

### Task 2: Update settings, exclusion, and pending list (storage)

**Files:**
- Create: `src/hyperwiki/domain/updates.py`, `src/hyperwiki/storage/update_repo.py`
- Test: `tests/test_update_repo.py`

**Interfaces:**
- Produces from `domain/updates.py`:
  - `Schedule = Literal["daily", "weekly", "fortnightly", "monthly", "off"]`;
  - `SCHEDULE_DAYS = {"daily": 1, "weekly": 7, "fortnightly": 14, "monthly": 30}`;
  - `UpdateTrigger = Literal["automatic", "scheduled", "all", "selected", "queue"]`;
  - `UpdateSettings(automatic=True, schedule="daily", paused=False)`;
  - `Selection(item_keys: frozenset[ZoteroKey], collection_keys: frozenset[ZoteroKey])`;
  - `next_full_check(settings, last: datetime | None) -> datetime | None`, which is None when the schedule is off or no check has run;
  - `full_check_due(settings, last, now) -> bool`, which is true when the schedule is on and no check has run.
- Produces from `storage/update_repo.py`:
  - `get_update_settings(conn) -> UpdateSettings`;
  - `set_update_settings(conn, *, automatic=None, schedule=None, paused=None) -> UpdateSettings`;
  - `last_full_check(conn) -> datetime | None` and `record_full_check(conn, at)`;
  - `exclude_collection(conn, key) -> list[str]` (library IDs), which raises `not_found` for a key in no live collection;
  - `include_collection(conn, key) -> list[str]`;
  - `excluded_collections(conn) -> dict[str, list[str]]`;
  - `excluded_item_ids(conn, library_id, item_ids: Collection[str] | None = None) -> set[str]`;
  - `PendingItem(library_id, item_key | None, title | None, attachment_keys, jobs)` and `pending_items(conn) -> list[PendingItem]`, built from queued or leased jobs whose subject is an attachment version.

**Steps:**
- [x] **Failing tests:**
  - `test_settings_default_and_round_trip`;
  - `test_next_full_check_by_schedule`;
  - `test_exclusion_covers_subcollections_keeps_multi_filed`: on the bilingual fixture, excluding `CL2DEEP2` excludes IT2ENG22, IT2ZHN22, and IT2MIX22; IT2NAT22 (unfiled) and IT2DUP22 (`CL2UNRD2`) stay included; an item in both `CL2DEEP2` and `CL2UNRD2` stays included;
  - `test_exclude_unknown_collection_is_not_found`;
  - `test_tombstoned_collection_is_not_a_membership`;
  - `test_pending_items_group_jobs_by_item`.
- [x] **Implement:** exclusion is a recursive CTE over live collections, joining children by `parent_key` within the library.
- [x] **Verify:** run the tests and all checks, then commit `feat(storage): update settings, collection exclusion, pending list`.

### Task 3: Evidence lifecycle (planning, queue, status, coverage)

**Files:**
- Modify: `src/hyperwiki/storage/job_repo.py`, `src/hyperwiki/processing/worker.py`, `src/hyperwiki/storage/evidence_repo.py`, `src/hyperwiki/services/evidence.py`, `src/hyperwiki/domain/evidence.py`
- Test: `tests/test_job_queue.py`, `tests/test_evidence_service.py`

**Interfaces:**
- Consumes `excluded_item_ids` from Task 2 and `deleted_at` from Task 1.
- Produces:
  - in `job_repo`:
    - `claim(..., subjects: Collection[str] | None = None)`;
    - `enqueue` raises the priority of an already open job when it is asked with a higher one;
    - `cancel_queued(conn, job_ids, *, now) -> int`;
    - `has_runnable(conn, *, now, operations) -> bool` (read-only);
  - `run_worker(..., subjects=None)`;
  - `AttachmentState` gains `item_id`, a parent-aware `trashed`, and `deleted`; `VersionSource` gains `deleted` and parent-aware `trashed`; `queued_extractions(conn, library_id) -> list[tuple[str, str]]` (job ID and fingerprint);
  - `ExtractionPlan` gains `excluded` and `cancelled`;
  - `plan_extraction(..., only: Collection[str] | None = None, priority: int = 0)`, where `only` is attachment IDs; cancellation runs only when `only` is None;
  - `extract_evidence(..., subjects=None)`;
  - `EvidenceStatus` and `EvidenceRecord.status`, with a validator that keeps `current == (status == "current")`;
  - `EvidenceCoverage.excluded`. Deleted attachments are not counted.

**Steps:**
- [x] **Failing tests:**
  - `test_claim_filters_by_subject`;
  - `test_enqueue_raises_priority_of_open_job`;
  - `test_cancel_queued_leaves_leased`;
  - `test_excluded_attachments_are_not_planned_and_jobs_cancelled`;
  - `test_superseded_queued_job_is_cancelled_on_replan`;
  - `test_trashed_parent_counts_attachment_trashed`;
  - `test_evidence_status_follows_lifecycle` (current, then trashed, then current after restore, then excluded, then deleted after erase);
  - `test_superseded_evidence_status`;
  - `test_coverage_counts_excluded_not_deleted`.
- [x] **Implement** the steps above. `read_evidence` derives the status from `VersionSource` and the item's exclusion; it never writes.
- [x] **Verify:** run the tests and all checks, then commit `feat(evidence): plan with tombstones and exclusion; evidence status`.

### Task 4: Update service and engine loop

**Files:**
- Create: `src/hyperwiki/services/updates.py`
- Modify: `src/hyperwiki/adapters/zotero_watch.py` (`max_wait`), `src/hyperwiki/services/zotero_sync.py` (remove `run_watch`/`watch_zotero`)
- Test: `tests/test_zotero_watch.py` (watcher), new `tests/test_updates.py` (service and loop; the HW-011 loop tests move here)

**Interfaces:**
- Consumes Tasks 1–3.
- Produces:
  - `DatabaseWatcher(..., max_wait: float = 60.0)`;
  - `UpdateReport(trigger, sync: SyncReport | None, plans: dict[str, ExtractionPlan], selected: dict[str, ExtractionPlan] | None, worker: WorkerReport | None, paused: bool)`;
  - `resolve_selection(conn, selection) -> list[str]` (attachment IDs);
  - `run_update(conn, read: Callable[[bool], SyncReport] | None, *, trigger, selection=None, force=False, now=utc_now, should_stop=_never) -> UpdateReport`, where `read(verify_files)` performs the sync;
  - `update_zotero(conn, location, *, trigger="all", selection=None, force=False, linked_file_roots=(), now=utc_now, reader=read_zotero_library) -> UpdateReport`;
  - `full_check_due(conn, settings, now) -> bool`;
  - `run_engine(conn, update: Callable[[UpdateTrigger], UpdateReport], watcher, *, on_report, on_busy, interval, rescan_interval, should_stop, sleep, clock, now) -> int`;
  - `watch_updates(conn, location, *, on_report, on_busy, should_stop, interval=2.0, quiet_period=3.0, max_wait=60.0, rescan_interval=600.0, ...) -> int`.

**Steps:**
- [x] **Failing tests:**
  - `test_continuous_writes_ready_after_max_wait`;
  - `test_update_all_extracts_and_records_full_check`;
  - `test_update_selected_runs_only_selected_jobs_first`;
  - `test_update_selected_force_reextracts`;
  - `test_selection_unknown_key_not_found`;
  - `test_automatic_update_while_paused_plans_but_does_not_run`;
  - `test_pause_stops_worker_between_jobs`;
  - `test_engine_runs_queue_after_resume`;
  - `test_engine_runs_scheduled_check_when_due_and_not_before`;
  - `test_engine_with_automatic_off_ignores_changes`;
  - the HW-011 loop tests, ported: syncs at start then on change; rescan forces a sync; a busy copy is retried; other errors stop; end to end with the synthetic Zotero database.
- [x] **Implement:**
  - Each tick reads settings.
  - The trigger is `scheduled` if a full check is due; else `automatic` if automatic is on and (first tick, or watcher ready, or rescan due); else `queue` if not paused and a job is runnable.
  - The worker's stop condition checks `paused` before each job for the automatic, scheduled, and queue triggers.
  - `scheduled` and `all` rehash files and record the full check.
- [x] **Verify:** run the tests and all checks, then commit `feat(updates): update engine with modes, pause, and schedule`.

### Task 5: CLI

**Files:**
- Modify: `src/hyperwiki/interfaces/cli.py`, `src/hyperwiki/services/library_import.py` (status)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes Task 4.
- Produces:
  - `sync [DATA_DIR] [--base-dir] [--linked-root] [--item KEY]... [--collection KEY]... [--force] [--changes]`;
  - `watch [DATA_DIR] [--base-dir] [--linked-root] [--interval] [--quiet-period] [--max-wait] [--rescan-interval] [--changes]`, writing JSON Lines;
  - `settings [--automatic on|off] [--schedule …] [--exclude-collection KEY]... [--include-collection KEY]...`;
  - `pause` and `resume`;
  - `status [--pending]` with `updates: UpdateStatus(settings, excluded_collections, last_full_check_at, next_full_check_at, pending_jobs, pending_items)` and optional `pending: list[PendingItem]`.
  - `import-zotero` is removed.

**Steps:**
- [x] **Failing tests:**
  - `test_cli_sync_from_zotero_extracts` (synthetic Zotero database);
  - `test_cli_sync_selected_force`;
  - `test_cli_watch_writes_json_lines`;
  - `test_cli_settings_pause_resume_round_trip`;
  - `test_cli_exclude_unknown_collection_not_found`;
  - `test_cli_status_shows_updates_and_pending`;
  - `test_cli_import_reports_changes_and_tombstones`.
- [x] **Implement:**
  - `settings`, `pause`, and `resume` create the workspace database if needed.
  - `status` without a database shows the default settings.
- [x] **Verify:** run the tests and all checks, then commit `feat(cli): sync, watch, settings, pause, resume; update status`.

### Task 6: Live smoke and documentation

**Files:**
- Create: `experiments/hw013-update-engine/{smoke.py,README.md,results/summary.json}`
- Modify: README, BACKLOG, DEVELOPMENT_PLAN, this plan's Results, ADR 0004 status line, and `experiments/hw011-live-adapter/smoke.py` (if it imports the removed functions)

**Steps:**
- [x] **Throwaway Zotero** (HW-006 harness, port 23124): run `watch_updates` in a thread, then measure:
  - the time from adding a PDF item to its evidence being stored;
  - trash, then evidence status `trashed`;
  - restore, then `current`;
  - empty trash, then tombstoned and `deleted`;
  - pause, add a PDF, and see it queued and not extracted; resume, and see it extracted;
  - exclude a collection, and see its new PDF not planned.
- [x] **Real library, read-only, on a copy:**
  - first sync and extraction;
  - an unchanged automatic update;
  - a full check (rehash all files);
  - planning time;
  - Zotero's files unchanged.

  Record counts and timings only.
- [x] **Docs:** record results and rulings, update README usage, mark HW-013 done in BACKLOG, and set DEVELOPMENT_PLAN's next item to HW-015.
- [x] **Verify:** run all checks, then commit `docs: record HW-013 plan and results`.

## Results

Measured on 2026-11-04 on an Apple M4 Pro (macOS 26.6.2) with Python 3.12.13, CPU only.

**Checks:** all CI checks pass, with 365 tests passing and the opt-in live provider test skipped. Key tests were checked by breaking the code they cover and watching them fail:
- reviving a tombstoned record at a lower version;
- keeping the first deletion time;
- the all-excluded membership rule;
- subcollection exclusion;
- queue cancellation;
- trash inherited from the parent item;
- pause between jobs;
- running the queue after resume;
- the maximum wait.

**Live Zotero 10.0.7, throwaway library** ([`experiments/hw013-update-engine`](../../experiments/hw013-update-engine/README.md); synthetic data in [`results/summary.json`](../../experiments/hw013-update-engine/results/summary.json)). `watch` ran with its defaults: checks every 2 s, a 3 s quiet period, and a 60 s maximum wait.

| Change in Zotero | Seen in the evidence store after |
|---|---|
| PDF imported into an item | 5.9 s, as current evidence |
| Item trashed | 12.1 s, evidence `trashed`. HW-011 saw trash in 6.0 s. Not investigated; Zotero may still have been writing after the import, which restarts the quiet period |
| Item restored | 5.9 s, `current` |
| Collection excluded | at once, `excluded`. A PDF added while excluded was synced after 6.1 s and was still not extracted 8 s later |
| Collection included again | 2.0 s until that PDF was extracted (a queue run) |
| Processing paused, PDF added | in the pending list after 5.9 s; not extracted 8 s later |
| Processing resumed | 2.0 s until extracted |
| Trash emptied | 5.9 s, evidence `deleted`; 1 item and 2 attachments tombstoned |

The run made 1 scheduled check, 6 automatic syncs, and 2 queue runs, with no busy copies and no errors.

**The user's library, read-only** (counts and timings only; the library had gained one PDF since HW-014):

| Measure | Result |
|---|---|
| Update all into an empty workspace | 22.5 s: sync 0.96 s (765 changes), then 295 PDFs extracted, none failed; 78 unsupported, 2 trashed |
| Automatic update, nothing changed | 0.06 s (sync 46 ms); nothing queued |
| Full check (every file rehashed) | 0.71 s (sync 700 ms); no changes |
| Planning on its own | 8 ms |
| Idle `watch` for 30 s | one sync at start, then none; 0.07 s of CPU |
| Evidence | 34,887 spans from the current extractor and settings |
| Zotero | database files unchanged |

**Not tested:**
- Windows and Linux. CI runs the synthetic tests there, and the Windows runs for HW-006 and HW-011 are still pending.
- Group libraries.
- A library of about 5,000 items (M4).
- Very long runs of `watch` (days, so that a scheduled check falls due while it runs). The schedule logic is tested with a fake clock.

## Rulings made during execution

- **Where the diff lives:** diffing and applying a read moved from `zotero_sync` to `library_import` (`apply_read`), so snapshot import shares it without an import cycle. `import_snapshot` now returns a `SyncReport`. *Cost if wrong:* one module move.
- **Including a collection that is not excluded** raises `not_found` instead of doing nothing silently, which gives clearer CLI feedback. *Cost if wrong:* scripts that include repeatedly see an error.
- **Tombstoned attachments** are left out of `attachment_states`, together with children of tombstoned items, instead of carrying a `deleted` flag. Their queued jobs are cancelled as unwanted. *Cost if wrong:* none.
- **A test moved:** `test_trashed_attachment_is_not_queued` moved its PDF from a trashed item to one that is not trashed. Restoring an attachment whose parent is still trashed now, correctly, leaves it trashed. *Cost if wrong:* none.
- **A name:** the plan's `queued_extractions` became `queued_version_jobs(conn, library_id, operation)`, so storage does not import processing constants. *Cost if wrong:* a name.
- **Removing the HW-011 loop** waited until Task 5, when the CLI moved over, so every commit stays green. *Cost if wrong:* none.
- **`--item` also accepts a standalone attachment key,** since Zotero lets users select those. *Cost if wrong:* none.
- **`--force` needs a selection.** Forcing everything is `extract --force`, or a rebuild later. *Cost if wrong:* `sync --force` alone is a usage error.
- **Loop tests read the bilingual snapshot** (real apply, plan, and extract) instead of returning fake reports. The shared fakes are in `tests/engine_fakes.py`. *Cost if wrong:* none.
- **Status moved** to `services/status.py`, because it reads import, evidence, and update state. *Cost if wrong:* one module.
- **Output shapes:**
  - `sync` and `watch` put the former `import-zotero` result under `sync`, next to `trigger`, `plans`, `selected`, `worker`, and `paused`.
  - `status` leaves out `pending` unless `--pending` is given.
  - *Cost if wrong:* agents written for the old shape (there are none yet).
- **Usage errors:** invalid keys, and `--force` without a selection, are argparse usage errors (exit code 2), like other argument errors. *Cost if wrong:* those errors are not JSON.
- **Exclusion changes plan again at once** (`set_exclusions`). The smoke test showed that otherwise an included collection waits for the next Zotero change or the 10-minute rescan. *Cost if wrong:* none.

## Self-review

Final review: self-review (no subagent tool used, as the user asked). Two Important findings were fixed, each with a test that failed first:
- **Re-planning after a bad key:** `set_exclusions` did not plan again when a later key was unknown, although the keys before it had been applied.
- **Showing settings created a workspace:** `settings` with no changes created an empty workspace. Showing settings must not write.

**Deferred minors:**
- **Two processes syncing at once:** diffing and importing are separate statements. A key re-added between the two processes' reads can be tombstoned, and the next sync revives it.
- **Trash latency:** trash took 12.1 s in the live smoke test, against 6.0 s in HW-011. This was not investigated.
- **Standalone attachments are never excluded,** because their collection membership is not imported.
- **Excluded items in a selection:** an item chosen explicitly but only in excluded collections is skipped, not processed.
- **Manual sync rehashes every file.** This took 0.7 s here; a large library with a cold file cache will take longer.
- **No pruning:** old extractions and tombstoned records are kept.
