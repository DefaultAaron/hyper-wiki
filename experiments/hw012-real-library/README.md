# HW-012 real-library smoke test

[`smoke.py`](smoke.py) checks HW-012 (jobs, leases, fingerprints, attachment versions) and the Zotero copy reader against a real Zotero library. It:

1. reads the library from a copy of `zotero.sqlite` and imports it, twice, checking that counts and internal IDs do not change;
2. queues one job per current PDF version; each job re-reads the file and compares its SHA-256 with the recorded attachment version, pausing 20 ms to stand in for slower real processing;
3. runs the worker in a separate process, kills it after about a third of the jobs, waits for the 2-second lease to expire, and runs a worker again;
4. checks that every job finished exactly once, nothing is left queued or leased, every file verified, queuing everything again finds no new work, and Zotero's database files are unchanged.

It prints counts and timings only. The workspace must be an empty directory outside the repository, because it holds a private copy of the library's metadata.

```sh
uv run --locked python experiments/hw012-real-library/smoke.py \
    --zotero-dir ~/Zotero --workspace /path/outside/the/repo
```

The job is a stand-in: real processing (text extraction, embeddings) arrives with HW-014 and HW-015. Results from the user's library are recorded in the [HW-012 plan](../../docs/plans/2026-11-03-hw-012-jobs-fingerprints.md#results).
