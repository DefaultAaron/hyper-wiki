# Execution backlog

Date: 2026-11-02. Status: HW-001–HW-005 and HW-009 are done; HW-010 awaits its first green CI run on all three operating systems; HW-006–HW-008 and later items are TODO. Follows the [development plan](../../DEVELOPMENT_PLAN.md) and [product design](../../PRODUCT_DESIGN.md). IDs from the 2026-10-26 backlog after HW-001 were reassigned (see Git history). Priority reflects dependencies; sizes are revisited after M0.

## M0: contracts, fixtures, and spikes

| ID | Task | Depends on | Done when |
|---|---|---|---|
| HW-001 | Python/uv package, lint/test setup, clean-run CI | — | **Done 2026-11-02:** Python 3.12, `uv.lock`, ruff/mypy/pytest, GitHub Actions CI on Linux |
| HW-002 | Versioned contracts: library manifest (Zotero-shaped: items, attachments, notes, annotations, collections, tags, trash, versions), update events, evidence locator, scope, retrieval result, wiki frontmatter, provider configuration, structured errors | HW-001 | Example documents validate; unsupported schema versions and malformed records fail with stable error codes. **Done 2026-11-02:** `hyperwiki.domain` (manifest, change events, evidence locator and IDs, scope, retrieval, wiki frontmatter, provider settings) and `hyperwiki.errors`; tests in `tests/test_errors_and_common.py`, `test_contracts_*.py` |
| HW-003 | Synthetic English and Chinese fixtures | HW-002 | Cover parent/attachment/note/annotation/collection hierarchy, mixed-language items, missing PDF, duplicates, metadata edit, PDF replacement, trash, restore, permanent deletion, and partial snapshot. **Done 2026-11-02:** `tests/fixtures/libraries/bilingual` with checksums and `.gitattributes`; lifecycle helpers in `tests/snapshot_mutations.py` (edit, replace, trash/restore, delete, partial) |
| HW-004 | SQLite migrations and source repositories | HW-002 | Create/upgrade/reopen works with stable identity, foreign-key integrity, and no duplicates on re-import. **Done 2026-11-02:** migration `0001_initial.sql` shipped in the wheel; newer databases rejected; failed migrations roll back |
| HW-005 | Snapshot adapter and `import`/`status` CLI skeleton | HW-003, HW-004 | Deterministic import and JSON status; invalid paths and malformed records reported, never silently skipped. **Done 2026-11-02:** `hyperwiki import`/`status`/`version`; path escapes, symlinks, and linked files outside allowed roots reported as `invalid_path` and never read; failed imports leave no partial state |
| HW-006 | Zotero change-detection spike (macOS and Windows) | Live Zotero 10 | ADR 0004 compares local API version polling, read-only database access, and plugin notifications on detection latency, trash/deletion visibility, attachment path resolution, and safety while Zotero runs; unobserved behaviour marked unresolved |
| HW-007 | PDF extraction comparison | Licensed English, Chinese, and mixed PDFs | ADR scores two or more extractors on text order, page locators, tables, Chinese text, runtime, install, and license |
| HW-008 | Bilingual search spike | HW-003 | ADR selects a Chinese-capable keyword tokenizer and a CPU-friendly multilingual embedding model with cross-language retrieval smoke results |
| HW-009 | Provider contract | HW-002 | Interfaces and deterministic fakes for API, local, and subscription-queue kinds; OpenAI-compatible smoke test when configured; spending cap mandatory for API kind; vendor terms for scheduled agent runs recorded. **Done 2026-11-02:** [ADR 0003](../decisions/0003-provider-contract.md); OpenAI-compatible client tested with mocked HTTP only (live smoke test not yet run: no local server available) |
| HW-010 | CI on macOS, Windows, and Linux | HW-001 | All checks pass on all three runners. **Matrix configured 2026-11-02; awaiting the first green run on all three runners** |

## M1: living evidence base and agent search

| ID | Task | Depends on | Done when |
|---|---|---|---|
| HW-011 | Live read-only Zotero adapter | HW-005, HW-006 | Reads current library state and changes per ADR 0004 without writing to or locking Zotero |
| HW-012 | Jobs, leases, fingerprints, attachment versions | HW-004 | Restart resumes unfinished work; committed results deduplicate; fingerprints include extractor, model, schema, and configuration |
| HW-013 | Update engine and modes | HW-011, HW-012 | Automatic with debounce; scheduled daily/weekly/fortnightly/monthly/off; update selected (with force); update all; pause and pending list; collection exclusion; trash/restore/delete with tombstones; partial snapshots never delete |
| HW-014 | Evidence extraction with page locators | HW-007, HW-012 | English and Chinese spans resolve to versioned attachments and pages; failures and unsupported files visible in coverage |
| HW-015 | Bilingual hybrid search with scope | HW-008, HW-014 | Keyword, local embedding, and fused search return evidence IDs and locators; queries in either language find both; out-of-scope evidence never returned |
| HW-016 | JSON CLI and MCP read tools | HW-013, HW-015 | `setup`, `status`, `sync`, `search`, `read` and MCP `search_sources`, `read_evidence`, `get_status` share services; read tools have no write effects |
| HW-017 | Non-AI lifecycle gates and private-library smoke | HW-013–HW-016 | Lifecycle fixture suite passes; about 500-item private library syncs, updates within minutes of a Zotero change, and is searchable from Claude Code and Codex |

## Subsequent batches

- **M2 living wiki:** provider runtime (API, local, subscription queue and skill); spending cap; compiler for paper/concept/topic pages and on-request synthesis; configurable language; citation validation and lint; dependency tracking, stale flags, automatic revision of unedited pages, proposals for edited pages, history and undo; personal notes; rebuild estimate; MCP wiki tools and workflow skills.
- **M3 maintainable hypergraph:** entities and cross-language aliases; claims; role-labelled relations with evidence; incremental index with support withdrawal; bounded scope-safe traversal; `explore_relations` and `inspect_claim`; Hyper-RAG reference and relational baseline; default decision ADR.
- **M4 beta:** installer and setup with MCP registration; backup/restore/migration/rebuild/recovery; 5,000-item scale check; head-to-head comparison and real-use log; license audit; beta tag.
- **M5 Zotero plugin:** API and launch spike; Notifier-based detection; update controls; wiki navigation; evidence opening; freshness; no-terminal install.
- **M6 Obsidian:** gap assessment before any build.

## External inputs and fallbacks

| Needed input | Needed by | Work that can proceed without it |
|---|---|---|
| Live Zotero 10 on macOS and Windows | HW-006, HW-011 | Contracts, fixtures, snapshot adapter |
| Licensed English and Chinese PDFs | HW-007 | Synthetic text fixtures |
| Private 30–50 paper library with real questions | HW-017, M4 comparison | Synthetic fixtures and lifecycle gates |
| API key or local model | M2 live compiler, M3 extraction | Deterministic fakes, subscription-queue contract |
| Claude Code and Codex access | HW-017, M2 host tests | Protocol tests |

When a task completes, record commands, observed outputs, and remaining failures. Do not mark acceptance based on a plan or mock alone.
