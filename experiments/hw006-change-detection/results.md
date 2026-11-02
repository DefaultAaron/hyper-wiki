# HW-006 spike results

Measured on 2026-11-03 on macOS: Apple M4 Pro, Darwin 25.6.0 (arm64), Python 3.12.13, Zotero 10.0.7. Windows and Linux have not been run yet.

There are two parts:
- **Throwaway library:** `spike.py` scripted changes in a throwaway Zotero profile and library. Aggregates are in `results/summary.json`.
- **Real library:** `observe.py` read the user's library (735 local API keys, 744 database objects) while Zotero ran. It is read-only and reports counts and timings only.

## How each candidate saw each change

The test driver applied each change through Zotero's own JavaScript API, then each detector was watched for 4 seconds.

| Detector | How it works | Poll interval |
|---|---|---|
| **Notifier** | The test plugin's `Zotero.Notifier` observer writes each event to a log. | Pushed, no polling. |
| **Local API (versions)** | `GET /api/users/0/items?format=versions&includeTrashed=1` with `If-Modified-Since-Version`, plus `/items/trash` and `/collections` when the version changes. | 0.25 s |
| **Local API (trash list)** | `GET /items/trash?format=keys` on every tick, whatever the version. | 1 s |
| **Database copy** | Checks the size and modification time of `zotero.sqlite` and its `-wal` and `-journal` files. When they change, it copies them, reads the copy with Hyper-Wiki's reader, and diffs by key and version; the `syncDeleteLog` table is also read. | 1 s |
| **Storage** | Checks size and modification time of every file under `storage/` and the linked-file folders. | 1 s |

Latency is measured from the start of the change to the first observation. "Parent only" means only the trashed parent was reported; its child attachments, notes, and annotations were not.

| Change | Notifier | Local API (versions) | Local API (trash list) | Database copy | Storage |
|---|---|---|---|---|---|
| Create item (English; Chinese) | 50; 54 ms | 125; 241 ms | — | 43; 1,033 ms | — |
| Import PDF (English name; Chinese name) | 16; 12 ms | 114; 130 ms | — | 1,029; 913 ms | 956; 824 ms |
| Link PDF (absolute path; inside the base directory) | 12; 11 ms | 236; 86 ms | — | 888; 869 ms | — |
| Edit title | 5 ms | 157 ms | — | 807 ms | — |
| Add note; edit note | 12; 16 ms | 51; 208 ms | — | 830; 860 ms | — |
| Add annotation | 12 ms | 118 ms | — | 898 ms | — |
| Create collection; add item to it; add tag | 23; 13; 19 ms | 40; 188; 78 ms | — | 945; 963; 1,002 ms | — |
| Rename attachment file | 15 ms | 226 ms | — | 1,009 ms | 713 ms |
| Replace an attachment's bytes outside Zotero | not seen | not seen | — | not seen | 689 ms |
| Replace a linked file outside Zotero | not seen | not seen | — | not seen | 724 ms |
| **Trash item with children** | 23 ms, parent only | **not seen** | 891 ms, parent and children | 50 ms, parent only | — |
| Restore item | 36 ms, parent only | 73 ms, parent only | 890 ms, all | 46 ms, parent only | — |
| Trash collection | 50 ms | 203 ms, as removed | — | 34 ms, as removed | — |
| **Erase the trashed collection** | 13 ms | **not seen** | — | 1,030 ms, in `syncDeleteLog` | — |
| Erase item without trash | 2 ms | 140 ms, key missing from list | — | 58 ms, key gone and in `syncDeleteLog` | — |
| **Trash item again** | 15 ms, parent only | **not seen** | 904 ms, all 4 | 88 ms, parent only | — |
| Empty trash (5 objects) | 13 ms, all 5 | 190 ms, all 5 | 911 ms | 97 ms, all 5 | 790 ms, 1 storage folder |
| Create 50 items in one transaction | 55 ms, all 50 | 77 ms, all 50 | — | 130 ms, all 50 | — |

Summary over the 25 changes:
- **Notifier:** saw 23, median 15 ms.
- **Local API version polling:** saw 20, median 135 ms.
- **Database copy:** saw 23, median 860 ms. The latency is mostly the 1-second poll interval: 34–130 ms when a poll fell right after the change.
- **Storage watching:** saw the 6 file changes, median 757 ms.

Only storage watching saw the two file replacements made outside Zotero.

Why the local API missed changes:
- **Trash:** in Zotero 10.0.7, `Zotero.Items.trash()` records the item in `deletedItems` and updates its modification dates, but does not advance `clientVersion`. A version poll therefore answers 304 Not Modified. The trash list shows the change, including children.
- **Permanent deletion:** the local API has no `/deleted` endpoint. An erased item can be found only by diffing the full key list. An erased collection that was already in the trash is not visible at all.

## Hyper-Wiki offline

All pollers were paused. While they were paused, one item was edited, one trashed, one erased, and one added; then the pollers resumed.

| Change while offline | Local API (versions) | Local API (trash list) | Database copy | Storage |
|---|---|---|---|---|
| Edit | caught | — | caught | — |
| Trash | caught, because the edit and add advanced the version | caught | caught | — |
| Erase | caught by diffing the full key list | — | caught | — |
| Add | caught | — | caught | — |

The notifier is not in this table:
- The test plugin writes events to a file, so here nothing was lost.
- A plugin that only holds events in memory loses those raised while its consumer is down.
- Changes made while the plugin is not loaded produce no events at all. Examples are Zotero started without it, the plugin disabled, or Zotero syncing from another computer.

## Safety while Zotero runs

- **Direct read is refused.** Opening the live `zotero.sqlite` read-only with SQLite failed at once with `database is locked`: Zotero holds an exclusive SQLite lock in WAL mode. The attempt created no files.
- **Copying under heavy writes:**
  - In 20 s, the reader copied and read the database 385 times while the driver committed 630 transactions of 20 items each, about 31 per second.
  - No copy was refused (`zotero_database_busy`) and none failed to read. 71 copies (18%) needed a second attempt because a file changed during the copy.
  - Every Zotero write succeeded.
  - Copy time: median 3.0 ms (maximum 24). Reading the copy: median 39 ms as the library grew to 12,653 objects.
- **Nothing left in the data directory.** While Zotero ran, and after it quit, the data directory held only `zotero.sqlite`, `zotero.sqlite-wal`, and `fulltext.sqlite`; the reader created nothing there.
- **After Zotero quits:** it exits in 0.3 s; the copy reader still reads the library, and the local API is unreachable.

## Attachment paths

| Stored as | Zotero resolves | Local API `file/view/url` | Copy reader |
|---|---|---|---|
| `storage:` (imported file, Chinese file name) | yes | yes | available |
| Absolute path (linked file, allowed root) | yes | yes | available |
| `attachments:` (linked file inside the base directory) | yes | yes | `invalid_path`: "linked file path is not an absolute path" |

Zotero stores linked files under the "Linked Attachment Base Directory" relative to that directory. The base directory is the `extensions.zotero.baseAttachmentPath` preference, which lives in Zotero's profile, not in the data directory. The copy reader does not read it yet.

## Real library, read-only, Zotero running

| Measure | Result |
|---|---|
| Database | 3.9 MB; write-ahead log 0 MB |
| Local API, full key and version list | 735 keys in 34 ms; library version 558 |
| Local API, poll answered 304 Not Modified | median 15 ms (59 polls over 60 idle seconds) |
| Local API, trash list | 2 keys in 4 ms |
| Copy reader, copy | median 0.8 ms (5 reads) |
| Copy reader, read the copy | median 16.5 ms; 744 objects; `clientVersion` 558, same as the local API |
| Copy reader, full read including hashing 373 available files | 0.9 s (files probably cached) |
| `syncDeleteLog` entries in this unsynced library | 191 |
| Database file changes during 60 idle seconds | 0 |

The local API lists 9 fewer objects than the database holds:
- All 9 are child notes of computer-program items.
- Each one returns 200 when requested by key.
- None appears in `/items`, in its parent's `/children`, or under `itemType=note`.

A detector that relies on local API listings would never see these notes or their changes. The cause is not known.

## Not measured

- Windows and Linux. The Windows result decides whether a byte copy of the live database is possible there. Zotero's source says it opens the database with an exclusive lock on platforms other than macOS.
- Synced libraries. Zotero clears `syncDeleteLog` after it uploads deletions, so a long-offline reader cannot rely on the log alone.
- Group libraries and feeds.
- Libraries near 5,000 items, which M4 checks.
- Data directories on network or cloud-synced drives.
- CPU and energy cost of polling over hours.
