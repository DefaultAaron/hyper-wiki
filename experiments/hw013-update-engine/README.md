# HW-013 update engine smoke test

[`smoke.py`](smoke.py) checks HW-013 (the update engine) against a real, running Zotero, in two parts.

**Throwaway library.** It starts Zotero 10 on a throwaway profile and data directory with the [HW-006 harness](../hw006-change-detection/README.md) and its test-only driver plugin, on port 23124, never the user's 23119. It runs `watch_updates` in a thread with its default timings (checks every 2 s, a 3 s quiet period, a 60 s maximum wait) while Zotero applies changes. It measures how long each change takes to reach the evidence store:
1. a PDF imported into an item becomes current evidence;
2. trashing the item makes the evidence `trashed`, and restoring it makes it `current`;
3. excluding the item's collection makes the evidence `excluded`. A PDF added while excluded is synced but not extracted, and including the collection again extracts it;
4. while processing is paused, a new PDF is synced and queued but not extracted. After resuming, it is extracted;
5. emptying the trash tombstones the item and its attachments, and their evidence reads `deleted`.

It writes [`results/summary.json`](results/summary.json), which holds timings and counts from synthetic data only.

**Real library, read-only** (`--zotero-dir`). In an empty private workspace it runs:
- update all (sync and extract);
- an automatic update with nothing changed;
- a full check that rehashes every file;
- planning on its own;
- an idle `watch`.

It prints counts and timings only, with no titles, paths, keys, or text, and checks that Zotero's database files did not change.

```sh
uv run --locked python experiments/hw013-update-engine/smoke.py \
    --private /path/outside/the/repo --zotero-dir ~/Zotero
```

`--private` must be outside the repository, because it holds the throwaway profile and a private copy of the library. Results are in the [HW-013 plan](../../docs/plans/2026-11-04-hw-013-update-engine.md#results).
