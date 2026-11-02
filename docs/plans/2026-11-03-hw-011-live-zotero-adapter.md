# HW-011 Live Read-Only Zotero Adapter

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the workspace in step with the user's live Zotero library. HW-011 reads Zotero's current state and reports what changed since the last read: upserts, trash, restore, and deletion. It does this without writing to or locking Zotero, whether or not Zotero is running.

**Architecture:** This implements [ADR 0004](../decisions/0004-zotero-change-detection.md).
- **Watcher:** a watcher checks the size and modification time of `zotero.sqlite` and its `-wal` and `-journal` files.
- **Sync:** after a quiet period, the existing copy reader reads a byte copy of the database. The result is diffed against the workspace's stored keys, versions, and trash flags, and then imported.
- **Attachment files:** only files whose size or modification time changed are hashed again.
- **Zotero's locations:** the data directory and the linked-attachment base directory come from Zotero's own profile settings, read only, or from explicit options.
- **Lifecycle:** applying deletions as tombstones, pausing, and modes belong to HW-013. HW-011 reports changes and imports upserts, trash, and restore, as import already does.

**Tech Stack:** Python 3.12, sqlite3, pydantic 2, pytest, mypy strict, ruff. No new dependencies.

**Spec:** HW-011 in [BACKLOG.md](BACKLOG.md); ADR 0004 decisions 1, 2, and 5; section 6 of [PRODUCT_DESIGN.md](../../PRODUCT_DESIGN.md), "Trash versus deletion" and the guarantees. The user's direction on 2026-11-03: Hyper-Wiki must work for agents without the Zotero application, so the database copy remains the source.

**Branch:** `m1/hw-011-live-zotero-adapter`, from `m0/hw-006-change-detection` (2bcc3e2), whose CI passed.

## Design decisions (builder's calls, recorded for review)

- **Trash belongs in the manifest; nothing that still exists in Zotero may disappear from a read.**
  - **Collections:** a trashed collection and its subcollections are read with `trashed: true`; previously they were left out. Items keep their membership in trashed collections. Scope rules (HW-015) must exclude trashed collections.
  - **Attachments and notes:** an attachment or note counts as trashed when it, or its parent item, is in Zotero's trash. This matches Zotero's own trash list, which shows children of a trashed item (observed in HW-006).
  - **Annotations:** an annotation is read with `trashed: true` when it, its attachment, or that attachment's parent is trashed. Previously trashed annotations were skipped.
  - **Contract changes:** `Collection.trashed` and `Annotation.trashed` are new optional fields defaulting to false, so schema version 1 manifests and fixtures stay valid.
  - **Storage:** migration 0003 adds a `trashed` column to `collections` and `annotations`.
- **Change detection compares the read with the workspace, not with the previous read.** Changes made while Hyper-Wiki was not running are therefore found at the next start. For each entity kind, it compares keys, versions, and trash flags:
  - **New key, or a different version:** `upsert`.
  - **Attachment whose file hash or availability changed at the same version:** `upsert`, because Zotero does not advance the version when a PDF is replaced on disk.
  - **Trash flag 0 to 1:** `trash`; **1 to 0:** `restore`. These come before a version check, since trashing does not advance Zotero's version.
  - **Stored key absent from the read:** `delete`, carrying the stored version. This applies only when the read is complete. A key named in a read problem (an unparseable record, or a child whose parent was skipped) is never reported deleted.
  - **Repeated deletions:** import still does not remove records, so a deleted key is reported again at every sync until HW-013 tombstones it. Each event says so through its kind; HW-013 makes this idempotent.
- **Attachment hashing:**
  - `AttachmentFile` gains `mtime_ns`, and migration 0003 adds `attachments.file_mtime_ns`.
  - A file is hashed only if it has no stored hash, or its Zotero path, size, or modification time changed; otherwise the stored hash is reused.
  - A real-library sync therefore costs stat calls, not 1.7 GB of reading, after the first.
- **Linked attachment base directory:**
  - Zotero stores linked files inside the base directory as `attachments:<relative path>`, using `/` separators.
  - The reader joins the relative path to the base directory after the same safety checks as `storage:` paths: no `..` segments and no absolute paths. The base directory is then treated as an allowed linked-file root.
  - If the base directory is unknown, the attachment is `invalid_path`, with a message naming Zotero's "Linked Attachment Base Directory" setting.
- **Zotero locations** (`adapters/zotero_profile.py`):
  - **Profile root:** macOS `~/Library/Application Support/Zotero`; Windows `%APPDATA%\Zotero\Zotero`; Linux `~/.zotero/zotero`.
  - **Choosing the profile:**
    - `profiles.ini` names the profiles; the reader uses the one marked `Default=1`, or the only one.
    - `prefs.js` is parsed only for `user_pref("name", value)` lines with JSON-compatible values.
    - Nothing is executed and nothing is written.
  - **Data directory:** an explicit argument, else `extensions.zotero.dataDir` when `useDataDir` is true, else `~/Zotero`.
  - **Base directory:** an explicit `--base-dir`, else the profile's `extensions.zotero.baseAttachmentPath`. The profile's value is used only when that profile's data directory is the one being read, so another profile's setting is never applied.
- **Watcher** (`adapters/zotero_watch.py`):
  - The signature is the size and modification time (ns) of the three database files.
  - A change is ready to sync once the signature has differed from the last synced one and stayed the same for the quiet period; the default is 3 s.
  - Polling defaults to every 2 s. A full sync also runs every 10 minutes by default, to catch attachment files replaced outside Zotero.
  - A busy copy (`zotero_database_busy`) is retried at the next tick.
  - The clock and the stat function are injectable for tests.
- **CLI:**
  - `hyperwiki import-zotero [DATA_DIR]`: `DATA_DIR` becomes optional, discovered as above. New options are `--base-dir` and `--changes`, which includes the event list.
  - The result is a sync report: change counts by entity and kind, the import report, and the time taken.
  - `--watch` keeps running and writes one JSON document per sync, as JSON Lines, until interrupted. The other new options are `--interval`, `--quiet-period`, and `--rescan-interval`.
- **Code layout:** the service (`services/zotero_sync.py`) owns sync and watch so the MCP server and workers can reuse them later; the CLI only parses and prints.

## Global Constraints

- All checks pass after every task: `uv run --locked ruff check`, `uv run --locked ruff format --check`, `uv run --locked mypy`, `uv run --locked pytest`, `uv run --locked python scripts/check_docs.py`.
- mypy strict covers `src`, `tests`, and `scripts`; tests are fully annotated, flat in `tests/`, with unique file names.
- **Zotero is read-only:** never write to, lock, or modify Zotero's database, storage, or profile. Read `prefs.js` and `profiles.ini` as text only.
- **Private data stays out of Git:** the real library, copies, and private workspaces.
- **Tests don't depend on the machine:** tests pass on macOS, Windows, and Linux. Platform, home directory, and environment are injected; tests never read the real profile.
- **No real waiting:** tests use injected clocks; no test sleeps.

## Review Focus

1. **A partial or failed read must never produce deletions.** This covers a manifest marked partial, a read raising `zotero_database_busy`, and a key that appears only in problems. Pinned by `test_problem_keys_are_never_reported_deleted` and `test_partial_read_reports_no_deletions` (Task 4), and `test_busy_copy_is_retried_next_tick` (Task 5).
2. **Trashing a parent must not make its children look deleted or live.** Pinned by `test_children_of_trashed_items_are_trashed` (Task 1) and `test_trash_and_restore_are_reported_without_version_change` (Task 4).
3. **An `attachments:` path must not escape the base directory.** Examples are `attachments:../x.pdf` and an absolute path after the prefix. Pinned by `test_base_relative_path_cannot_escape` (Task 2).
4. **Another profile's base directory must not be applied** to a data directory it does not belong to. Pinned by `test_base_dir_only_from_profile_using_this_data_dir` (Task 2).
5. **A replaced PDF with an unchanged Zotero version must still be rehashed and reported**, through its size or modification time. Pinned by `test_changed_file_is_rehashed_and_reported` (Task 3 and Task 4).

---

### Task 1: Trash completeness (contract, migration 0003, reader, repository)

**Files:** `domain/library.py`, `storage/migrations/0003_live_zotero.sql`, `storage/library_repo.py`, `adapters/zotero_db.py`; tests in `tests/test_zotero_db.py`, `tests/test_storage.py`, `tests/test_library_import.py`.

- [x] `Collection.trashed: bool = False` and `Annotation.trashed: bool = False`.
- [x] Migration 0003: add `trashed INTEGER NOT NULL DEFAULT 0 CHECK (trashed IN (0, 1))` to `collections` and `annotations`, and `file_mtime_ns INTEGER` to `attachments`. The latest version becomes 3.
- [x] `upsert_collection` and `upsert_annotation` store `trashed`.
- [x] Reader: include trashed collection subtrees with `trashed=True` and keep item memberships in them; trash inheritance for attachments, notes, and annotations as decided above.
- [x] Tests:
  - `test_trashed_collections_are_read_as_trashed` replaces the old test that left them out;
  - `test_children_of_trashed_items_are_trashed`;
  - `test_trashed_annotations_are_read_as_trashed`;
  - `test_import_stores_trash_flags_for_collections_and_annotations`;
  - the migration tests updated to version 3.

### Task 2: Zotero locations and base-directory linked files

**Files:** `adapters/zotero_profile.py` (new), `adapters/zotero_db.py`; tests in `tests/test_zotero_profile.py` (new) and `tests/test_zotero_db.py`.

**Produces:**
- `ZoteroLocation(data_dir: Path, base_attachment_dir: Path | None, profile_dir: Path | None)`.
- `discover_zotero(*, data_dir: Path | None = None, base_attachment_dir: Path | None = None, home: Path, environ: Mapping[str, str], platform: str) -> ZoteroLocation`.
- `read_prefs(path: Path) -> dict[str, object]`.
- `read_zotero_library(..., base_attachment_dir: Path | None = None)`.

- [x] Tests:
  - `test_discovers_data_dir_from_default_profile`, which covers the macOS, Windows, and Linux roots through injected `platform`;
  - `test_defaults_to_home_zotero_without_profile`;
  - `test_base_dir_only_from_profile_using_this_data_dir`;
  - `test_explicit_options_override_profile`;
  - `test_prefs_parser_ignores_non_pref_lines`;
  - `test_resolves_base_relative_linked_files`;
  - `test_base_relative_path_cannot_escape`;
  - `test_base_relative_without_base_dir_is_invalid_path`.

### Task 3: Hash only changed attachment files

**Files:** `adapters/snapshot.py`, `adapters/zotero_db.py`, `storage/library_repo.py`, `services/library_import.py`; tests in `tests/test_snapshot_adapter.py` and `tests/test_library_import.py`.

**Produces:**
- `KnownFile(path: str | None, size_bytes: int, mtime_ns: int, sha256: str)`.
- `known_attachment_files(conn, library_id) -> dict[str, KnownFile]`.
- `resolve_attachment_files(..., known: Mapping[str, KnownFile] | None = None)`.
- `read_zotero_library(..., known_files=...)`.
- `AttachmentFile.mtime_ns: int | None = None`, which the attachment upsert stores.

- [x] Tests:
  - `test_unchanged_file_is_not_rehashed`, which counts `_hash_file` calls;
  - `test_changed_file_is_rehashed_and_reported`, the first half, where a new mtime or size triggers a rehash;
  - `test_import_stores_file_mtime`.

### Task 4: Diff and sync service

**Files:** `services/zotero_sync.py` (new); test `tests/test_zotero_sync.py` (new).

**Produces:**
- `diff_library(conn, library_id, loaded, *, now) -> list[ChangeEvent]`.
- `SyncReport(library_id, snapshot_id, completeness, change_counts: dict[EntityKind, dict[ChangeKind, int]], changes: list[ChangeEvent], import_report: ImportReport, duration_ms: float)`.
- `sync_zotero(conn, location, *, linked_file_roots=(), now=utc_now, reader=read_zotero_library) -> SyncReport`.

- [x] Tests:
  - `test_first_sync_reports_every_record_as_upsert`;
  - `test_second_sync_without_changes_reports_nothing`;
  - `test_edit_reports_upsert`;
  - `test_trash_and_restore_are_reported_without_version_change`;
  - `test_erased_records_are_reported_deleted`;
  - `test_problem_keys_are_never_reported_deleted`;
  - `test_partial_read_reports_no_deletions`;
  - `test_changed_file_is_rehashed_and_reported`, the second half, reporting an attachment upsert at the same version;
  - `test_trashed_collection_is_trash_not_delete`.

### Task 5: Watcher, watch loop, and CLI

**Files:** `adapters/zotero_watch.py` (new), `services/zotero_sync.py`, `interfaces/cli.py`; tests in `tests/test_zotero_watch.py` (new) and `tests/test_cli.py`.

**Produces:**
- `database_signature(data_dir) -> Signature`.
- `DatabaseWatcher(data_dir, *, quiet_period, clock, signature)`, with `.ready() -> Signature | None`, `.current()`, and `.synced(signature)`.
- `run_watch(sync, watcher, *, on_sync, on_busy, interval, rescan_interval, should_stop, sleep, clock) -> int`, the loop, tested with fakes.
- `watch_zotero(conn, location, *, on_sync, on_busy, should_stop, interval=2, quiet_period=3, rescan_interval=600, ...)`, which composes them.

- [x] Tests:
  - `test_change_is_ready_only_after_quiet_period`;
  - `test_no_change_is_never_ready`;
  - `test_busy_copy_is_retried_next_tick`;
  - `test_watch_syncs_at_start_then_on_change`;
  - `test_rescan_interval_forces_a_sync`;
  - `test_cli_import_zotero_reports_changes`;
  - `test_cli_import_zotero_discovers_data_dir`;
  - `test_cli_watch_writes_json_lines`, which stops after N syncs through an injected stop condition.

### Task 6: Live smoke and documentation

**Files:** `experiments/hw011-live-adapter/smoke.py` and `README.md`; this plan's Results; BACKLOG, DEVELOPMENT_PLAN, and README status.

- [x] Throwaway Zotero, using the HW-006 harness and its driver plugin:
  - run the watch loop against the live data directory while Zotero applies changes: create, edit, attach, trash, restore, erase, collection trash, and a file replaced outside Zotero;
  - check that each change appears as the right events within seconds, and that the workspace matches.
- [x] Real library, read-only, Zotero running: first sync into a private workspace, a second sync with no changes, and 60 idle seconds of watching. Report counts and timings only.

## Results

Measured on 2026-11-03 on macOS (Apple M4 Pro, Darwin 25.6.0) with Zotero 10.0.7.

**Checks:** ruff, format, mypy (strict, 59 files), pytest (263 passed, 1 skipped: the opt-in live provider test), and the docs check all pass. New tests:
- `test_zotero_profile.py` (10);
- `test_zotero_sync.py` (12);
- `test_zotero_watch.py` (9);
- 22 more in `test_zotero_db.py`;
- the migration test in `test_storage.py`.

For the change detection and the watch loop, each safeguard was also removed on purpose to confirm a test fails:
- problem-key protection;
- the complete-read rule for deletions;
- the file-content comparison;
- the trash flag;
- the quiet period;
- the synced state;
- busy retry;
- the rescan.

**Throwaway library** ([`experiments/hw011-live-adapter`](../../experiments/hw011-live-adapter), `results/summary.json`). `watch_zotero` ran against a live Zotero with the defaults: check every 2 s, sync after 3 quiet seconds, and a 20 s rescan for this run.

| Change in Zotero | Events reported | Seconds until reported |
|---|---|---|
| Create item; import PDF; link PDF under the base directory | item upsert; attachment upsert (the base-relative file resolved and hashed) | 6.0 each |
| Edit title; add note; add annotation; create collection | the matching upsert | 6.0 each |
| Trash an item with a PDF, a linked file, a note, and an annotation | trash for the item and all 4 children | 6.0 |
| Restore it | restore for all 5, plus an item upsert, because Zotero advances the version on restore | 6.0 |
| Trash a collection; then erase it | collection trash (plus upsert: Zotero advances a collection's version on trash); then collection delete | 6.0; 6.0 |
| Replace the PDF's bytes outside Zotero | attachment upsert at the same version, at the next rescan | 20.1 |
| Trash the item again and empty the trash | delete for the item and all 4 children | 6.0 |
| Create 50 items in one transaction | 50 item upserts | 5.9 |

- **Sync cost:** 15 syncs took 22–33 ms each, with no busy copies and no watcher errors.
- **After Zotero quit,** a final sync found only the 6 deletions reported before, as designed: import keeps deleted records until HW-013 adds tombstones.

**Real library, read-only, Zotero running:**
- **Discovery:** the data directory was found from Zotero's profile and matched `~/Zotero`. No base directory is configured.
- **First sync:** 761 records (17 collections, 296 items, 373 attachments, 52 notes, 23 annotations) in 0.68 s, with all 373 files hashed and no problems.
- **Second sync:** 42 ms with no changes; no file was hashed again.
- **Idle:** a 60-second watch made only its initial sync and reported no changes.

**Not covered:**
- **Windows and Linux.** The Windows run of HW-006 decides whether copying works while Zotero runs there.
- **Synced and group libraries.**
- **Repeated deletions.** A deleted record is reported again at every sync until HW-013 tombstones it, so `--watch` output repeats deletions until then.
- **Trashed collections in scope:** queries must exclude them (HW-015).

**Final review:** a self-review of the whole branch. The user did not ask for a separate reviewer agent.
- **Fixed:** Ctrl-C ended any `import-zotero` run with exit code 0. Only an interrupted `--watch` is now a clean exit; an interrupted one-off sync propagates. Covered by `test_cli_interrupted_import_is_not_reported_as_success`, which failed before the fix.

**Deferred minors:**
- **Version regression:** if Zotero's database is restored from a backup, versions go back. The sync then reports those records as upserts at every run, but import keeps the higher stored version, which is the HW-004 rule against stale snapshots. HW-013 decides how a live source may lower versions.
- **Continuous writes:** while Zotero writes more often than the quiet period, as during a large import or a sync, a sync waits until the writes stop or the rescan interval (10 minutes) passes.
- **No heartbeat:** the watch loop writes nothing while idle, so a supervisor has nothing to watch.
- **Hidden option:** `--max-syncs` is hidden and used by tests.
