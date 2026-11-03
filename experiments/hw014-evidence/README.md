# HW-014 real-library smoke test

[`smoke.py`](smoke.py) checks HW-014 (evidence extraction with page locators) against a real Zotero library. It:

1. syncs the library from a copy of `zotero.sqlite` into an empty workspace;
2. plans and runs text extraction (`extract.text` jobs on pypdfium2), timed;
3. checks every stored span:
   - its text equals the slice of its stored page text;
   - its locator names its extraction, the attachment version's SHA-256, and its own offsets;
   - its ID is derived from its locator;
   - its bounding box lies within 1 point of its page;
4. reads every span back through `read_evidence` and checks that it is current;
5. plans again, expecting no new work, then forces a full re-extraction and checks that every evidence ID is unchanged;
6. checks that Zotero's database files did not change.

It prints and writes counts and timings only: no titles, paths, keys, or text. The workspace must be an empty directory outside the repository, because it holds a private copy of the library and its text.

```sh
uv run --locked python experiments/hw014-evidence/smoke.py \
    --zotero-dir ~/Zotero --workspace /path/outside/the/repo \
    --summary experiments/hw014-evidence/results/summary.json
```

Results from the user's library are in [`results/summary.json`](results/summary.json) and the [HW-014 plan](../../docs/plans/2026-11-03-hw-014-evidence-extraction.md#results).
