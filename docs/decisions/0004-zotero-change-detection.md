# ADR 0004: Zotero change detection

Date: 2026-11-03. Status: accepted for macOS (HW-006). Windows and Linux are unresolved until the same harness runs there. HW-011 implemented decisions 1, 2, and 5 ([plan and results](../plans/2026-11-03-hw-011-live-zotero-adapter.md)). HW-013 added tombstones for deletions, a 60-second maximum wait to the quiet period, and scheduled full checks that rehash every file ([plan and results](../plans/2026-11-04-hw-013-update-engine.md)).

## Context

The [product design](../../PRODUCT_DESIGN.md) makes automatic updates the default:
- **Detection time:** changes in Zotero are detected within about a minute by the backend, or instantly once the Zotero plugin is installed.
- **Scheduled check:** a full reconciliation catches whatever automatic mode missed.

Constraints:
- **Hyper-Wiki only reads Zotero.** It never writes to, locks, or modifies Zotero's database or storage.
- **No Zotero required.** It must also work for agents on machines where Zotero is not running.
- **Lifecycle rules:**
  - Trash is restorable and hides content.
  - Permanent deletion tombstones evidence.
  - Absence from a partial snapshot is never deletion.

The [change-event contract](../../src/hyperwiki/domain/changes.py) has four kinds: upsert, trash, restore, and delete.

## Candidates

1. **Local API polling.** Zotero 7 and later serve a read-only copy of the web API on `localhost:23119` when "Allow other applications on this computer to communicate with Zotero" is on.
   - In Zotero 10, versions are the local `clientVersion`, so they also advance in unsynced libraries.
   - `If-Modified-Since-Version` returns 304 when nothing changed.
   - There is no `/deleted` endpoint.
2. **Database copy.** Watch the size and modification time of `zotero.sqlite` and its `-wal` and `-journal` files. When they change:
   - copy them byte for byte;
   - read the copy with the [Zotero copy reader](../../src/hyperwiki/adapters/zotero_db.py) added in HW-012;
   - diff the result by key and version.
3. **Plugin notifications.** A Zotero plugin registers a `Zotero.Notifier` observer. This needs the M5 plugin.
4. **Attachment file watching.** Check the size and modification time of attachment files. This complements the others; it does not replace them.

## Evidence

The harness is in [`experiments/hw006-change-detection`](../../experiments/hw006-change-detection) and the full tables are in [results.md](../../experiments/hw006-change-detection/results.md). It was measured on 2026-11-03 on macOS (Apple M4 Pro, Darwin 25.6.0) with Zotero 10.0.7.

`spike.py` ran a real Zotero on a throwaway profile and data directory. A test-only plugin applied 25 changes through Zotero's own JavaScript API: English and Chinese items, imported and linked PDFs, notes, an annotation, collections, tags, a rename, trash and restore, permanent deletion, and a 50-item transaction. Each candidate was watched for 4 seconds after each change. `observe.py` then read the user's real library while Zotero ran, read-only, recording counts and timings only.

| | Local API polling (0.25 s) | Database copy (1 s) | Plugin notifier | File watching (1 s) |
|---|---|---|---|---|
| **Changes seen, of 25** | 20 | 23 | 23 | 6 (file changes only) |
| **Median latency** | 135 ms | 860 ms, mostly the poll interval (34–130 ms right after a poll) | 15 ms | 757 ms |
| **Trash** | **not seen**: trashing does not advance `clientVersion`, so the poll answers 304. A separate trash-list poll sees it, children included. | seen, parent only (`deletedItems`) | seen, parent only | — |
| **Permanent deletion** | only by diffing the full key list; an erased collection that was in the trash is not seen | seen: key gone, and key in `syncDeleteLog` | seen, with keys | storage folder removed |
| **Hyper-Wiki was offline** | edit, add, and erase caught (erase by diff); trash caught only because other changes advanced the version | all four caught | events lost unless the plugin persists them; none while the plugin is not loaded | — |
| **Completeness on the user's library** | 735 of 744 objects: 9 child notes of computer-program items are missing from every listing, though each loads by key | 744 of 744 | not measured | — |
| **PDF replaced outside Zotero** | not seen | not seen | not seen | seen |
| **Zotero not running** | unavailable | works | unavailable | works |
| **Cost on the user's library** (3.9 MB database, 735 keys) | 15 ms per 304 poll; full key list 34 ms | copy 0.8 ms, read 16.5 ms, only after a file change; no file changes in 60 idle seconds | none | depends on the number of files |
| **Setup** | the user must enable the local API | none | install the plugin | none |

**Safety while Zotero runs (macOS):**
- **Direct open fails.** Opening the live database directly, read-only, failed with `database is locked`: Zotero holds an exclusive SQLite lock in WAL mode. Reading a copy is therefore required, not just cautious.
- **Copying under heavy writes:**
  - In 20 s, the copy reader read 385 copies while Zotero committed about 31 transactions per second.
  - No copy was refused or failed to read. 18% needed one retry.
  - All of Zotero's writes succeeded, and the reader created no files in Zotero's data directory.

**Attachment paths:**
- **Resolved:** the copy reader resolves `storage:` paths, including a Chinese file name, and absolute linked paths.
- **Not resolved:** it does not resolve linked files that Zotero stores relative to the "Linked Attachment Base Directory" (`attachments:`). That directory is a preference in Zotero's profile (`extensions.zotero.baseAttachmentPath`), not in the data directory. Zotero and the local API resolve these paths.

## Decision

1. **Backend detection is the database copy watcher.** HW-011 builds it on the existing copy reader:
   - **Detecting a change:** check the size and modification time of `zotero.sqlite`, `-wal`, and `-journal` every 2 seconds by default. On a change, wait for a quiet period (the debounce in the product design), then copy, read, and diff against the last imported state.
   - **Diff by full key set and version:**
     - A key that disappears from a complete read is a deletion.
     - `syncDeleteLog` only corroborates deletions, because Zotero clears it after a sync upload.
     - A key absent from a partial or failed read is never a deletion.
   - **Trash semantics:**
     - An attachment, note, or annotation whose parent is trashed counts as trashed, which matches Zotero's own trash list.
     - A trashed collection is trashed, not deleted. It is deleted only when its key leaves the database.
   - **Never open the live database.** Retry a copy when a file changes during it, as now.
   - **Why this one:** it is the only candidate that saw every metadata, trash, and deletion change, works with Zotero closed, needs no setup, and reads every object.
2. **Attachment files are reconciled separately.**
   - **When:** after any change that touches attachments, and in the scheduled check.
   - **How:** compare the size and modification time of each attachment file, and hash only files that changed. HW-012's attachment versions record new bytes.
   - **Why:** only file watching saw a PDF replaced outside Zotero, and hashing every file on each change does not scale.
3. **The local API is optional, never the source of truth.**
   - **Why not:** it misses trash, cannot list permanent deletions or erased collections, omitted 9 objects of the user's library, needs a setting the user may not have enabled, and exists only while Zotero runs.
   - **Allowed use:** a later task may use it as a fast wake-up hint when it is reachable. That use must be measured first.
4. **The M5 Zotero plugin's notifier events are a wake-up signal.**
   - **Use:** they trigger an immediate copy-and-diff, giving near-instant updates.
   - **Not the source of truth:** events cover only parents on trash, are not raised for changes made while the plugin is not loaded, and are lost unless persisted.
5. **Linked files relative to the base directory.** HW-011 reads `extensions.zotero.baseAttachmentPath` from the Zotero profile's `prefs.js`, read-only. The profile is located through `profiles.ini`, and the user can set the directory explicitly. If the base directory is unknown, such files are reported as unresolved, as now. They are never guessed.

## Consequences and open items

- **Latency:**
  - The backend detects changes within the poll interval plus the debounce: seconds, well inside the product's "about a minute".
  - Read cost grows with database size. Under heavy writes, reading copies of a test library that grew to 12,653 objects took a median 39 ms. The 5,000-item check in M4 confirms this on a large real library.
- **No new dependency or Zotero setting.** Agents can use Hyper-Wiki on a copied or synced Zotero data directory without Zotero running.
- **Windows unresolved.** Zotero's source says that outside macOS it opens the database with an exclusive lock. Whether another process can still copy the file while Zotero runs is unmeasured. This is the main risk to the decision.
  - **If copying works:** the decision holds.
  - **If copying is blocked:** the watcher still runs while Zotero is closed. While Zotero runs on Windows, the fallback is local API polling plus a trash-list poll and a full key-list diff, with the gaps listed above. The fallback is chosen only after the measurement.
  - **To measure:** run [`spike.py`](../../experiments/hw006-change-detection/README.md#run) and `observe.py` on Windows. Linux is lower priority.
- **Not tested:**
  - synced libraries, including whether a sync clears `syncDeleteLog` between reads;
  - group libraries and feeds;
  - data directories on network or cloud-synced drives;
  - the long-run CPU cost of polling.
- **Unexplained:** why Zotero 10.0.7's local API omits the 9 child notes, and why trashing does not advance `clientVersion`. Both may be Zotero bugs, so recheck them on Zotero updates.
- **Process:** the spike's test plugin writes only to throwaway libraries and is not part of Hyper-Wiki. A fresh Zotero profile tries to install its Word and LibreOffice plugins, so the harness turns that off.

## Sources

- **Zotero 10.0.7 source,** read from the installed application (`omni.ja`):
  - local API, `chrome/content/zotero/xpcom/server/server_localAPI.js`: versions, `since`, `If-Modified-Since-Version`, no `/deleted` endpoint;
  - database locking and WAL, `xpcom/db.js`;
  - trash, `xpcom/data/items.js` (`Zotero.Items.trash`, which does not update `clientVersion`);
  - delete log, `xpcom/sync/syncEventListeners.js`;
  - command-line data directory, `xpcom/dataDirectory.js` (`-datadir`);
  - server endpoint conventions, `xpcom/server/server.js`.
- Zotero's local API notes at the top of `server_localAPI.js`, and the [Zotero web API v3 documentation](https://www.zotero.org/support/dev/web_api/v3/basics) it mirrors.
