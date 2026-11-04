# HW-011 live adapter smoke test

[`smoke.py`](smoke.py) checks the live Zotero adapter (HW-011, [ADR 0004](../../docs/decisions/0004-zotero-change-detection.md)) against a real, running Zotero.

- **Throwaway library:** it starts Zotero on a throwaway profile and data directory with the [HW-006 harness](../hw006-change-detection/README.md) and its test-only driver plugin. It runs the watch loop (`watch_zotero` then; since HW-013, `watch_updates`) with the default 2-second interval and 3-second quiet period, applies 14 changes through Zotero, and checks that each appears as the expected change events. The changes cover create, attach, link under the base directory, edit, note, annotation, collection, trash, restore, and erase, plus a PDF replaced outside Zotero and 50 items at once. It writes `results/summary.json`, which holds timings and counts from synthetic data only.
- **Real library, read-only** (`--zotero-dir`): it discovers the data directory from Zotero's settings, syncs into a private workspace twice, and then watches for `--idle` seconds. It prints counts and timings only and never writes to Zotero.

```sh
uv run --locked python experiments/hw011-live-adapter/smoke.py \
    --private /path/outside/the/repo --zotero-dir ~/Zotero
```

The safety rules of the HW-006 harness apply:
- **Separate instance:** the throwaway Zotero uses its own port (23124) and its own data directory.
- **No side installs:** Word and LibreOffice plugin installs are turned off.
- **Private output:** everything goes under `--private`, outside the repository.

Results are in the [HW-011 plan](../../docs/plans/2026-11-03-hw-011-live-zotero-adapter.md#results).
