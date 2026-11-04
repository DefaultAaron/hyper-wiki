# Hyper-Wiki

A research wiki backed by retrieval-augmented generation, built to help postgraduate students read, connect, and reuse research literature.

The core ingests a Zotero-like library, retains inspectable source evidence, and maintains a linked Markdown wiki. Codex and Claude Code access shared services through MCP, CLI, and workflow skills. A Zotero plugin follows the core; an Obsidian plugin is optional.

## Current status

Product redesigned and roadmap revised 2026-11-02; M0 in progress. M0 build tasks (HW-001–HW-005, HW-009, HW-010) are done: versioned contracts, bilingual synthetic fixtures, SQLite storage, a read-only snapshot importer with a JSON `import`/`status` CLI, and the AI-provider contract ([ADR 0003](docs/decisions/0003-provider-contract.md)). CI passes on macOS, Windows, and Linux. The bilingual search spike (HW-008) chose the Chinese keyword analyzer, local embedding model, and fusion from synthetic smoke results ([ADR 0005](docs/decisions/0005-bilingual-search.md)). M1 has started: HW-012 added resumable, deduplicated jobs keyed by fingerprints, permanent attachment versions, and an importer that reads a Zotero library from a copy of its database without touching Zotero. HW-011 turned it into a live sync that reports what changed, including trash, restore, and deletion. The PDF extraction spike (HW-007) compared five extractors on the user's own library and chose pypdfium2, with Docling optional for tables ([ADR 0006](docs/decisions/0006-pdf-extraction.md)). The Zotero change-detection spike (HW-006) chose to watch and copy Zotero's database, measured on macOS ([ADR 0004](docs/decisions/0004-zotero-change-detection.md)); the Windows run is pending. HW-014 added evidence extraction: `extract` turns PDFs into evidence spans with stable IDs, page numbers, and character offsets, and `status` shows what is extracted, failed, or unsupported. HW-013 added the update engine: `sync` updates everything or selected items now, `watch` keeps the workspace and its evidence in step with Zotero automatically and on a schedule, deleted records are tombstoned, and processing can be paused and collections excluded. There is no search, wiki, or agent integration yet. Hypergraph retrieval is planned as an interchangeable backend and must earn its default position through experiments. No previous example research domain is required.

## Read first

- [Product design](PRODUCT_DESIGN.md)
- [Feasibility assessment](docs/FEASIBILITY.md)
- [Development plan](DEVELOPMENT_PLAN.md)
- [Evaluation plan](docs/plans/EXPERIMENT_PLAN.md)
- [Architecture decisions](docs/decisions/0001-core-architecture.md) and [license policy](docs/decisions/0002-license.md)
- [Execution backlog](docs/plans/BACKLOG.md) and [M0 implementation plan](docs/plans/2026-11-02-m0-contracts-implementation.md)
- [Earlier literature review](RESEARCH_REVIEW.md)
- [Landscape survey of existing tools](docs/LANDSCAPE.md)

The current product and development/experiment plans supersede archived proposals. The literature review is background, not a verified dependency list or evidence that this implementation works.

## Repository setup

Git history is tracked on `main` and pushed to [DefaultAaron/hyper-wiki](https://github.com/DefaultAaron/hyper-wiki) on GitHub. Use the SSH remote:

```sh
git clone git@github.com:DefaultAaron/hyper-wiki.git
```

Keep private library material and runtime workspaces outside tracked source files.

## Development

Requires [uv](https://docs.astral.sh/uv/) 0.11.14 or later. The project targets Python 3.12 (pinned in `.python-version`); uv downloads it if needed. Install the locked environment and run the same provider-free checks as CI:

```sh
uv sync --locked
uv run ruff check
uv run ruff format --check
uv run mypy
uv run pytest
uv run python scripts/check_docs.py
```

The `hyperwiki` command imports a Zotero-shaped snapshot directory or keeps a workspace database in step with a Zotero data directory, and reports status, including job counts and update state. Each command prints one JSON document (`{"ok": true, "result": …}` or `{"ok": false, "error": …}`) to stdout. Try it on the synthetic bilingual fixture:

```sh
uv run hyperwiki --workspace /tmp/hw-demo import tests/fixtures/libraries/bilingual
uv run hyperwiki --workspace /tmp/hw-demo status
```

To update from your own Zotero library, run `sync`. It finds the Zotero data directory from Zotero's settings, or takes it as an argument (shown in Zotero under Settings → Advanced → Files and Folders). It reads the library, applies what changed, and extracts evidence from new or changed attachments:

```sh
uv run hyperwiki --workspace /tmp/hw-demo sync                     # or: sync ~/Zotero
uv run hyperwiki --workspace /tmp/hw-demo sync --item ABCD2345     # update selected items now
uv run hyperwiki --workspace /tmp/hw-demo watch                    # keep updating as Zotero changes
```

`sync` reports what changed since the last update: added or edited, trashed, restored, or deleted records, by kind. Add `--changes` to list each change.
- **Update all:** `sync` without a selection is a full check. It also rehashes every attachment file.
- **Update selected:** with `--item` or `--collection` (which includes subcollections), it processes those items ahead of anything queued. `--force` extracts them again.

`watch` keeps running and writes one JSON line per update:
- **Automatic mode:** it checks Zotero's database every 2 seconds and syncs once Zotero has been quiet for 3 seconds, or at the latest 60 seconds after the first change. Every 10 minutes it also syncs, to catch PDFs replaced outside Zotero.
- **Scheduled check:** a full check runs daily by default, including when `watch` starts after a day or more away.
- **Settings:** `settings` shows and changes them:
  - `--automatic on|off`;
  - `--schedule daily|weekly|fortnightly|monthly|off`;
  - `--exclude-collection KEY` and `--include-collection KEY`. Items only in excluded collections, or their subcollections, are skipped; unfiled items are kept.
- **Pause:** `pause` holds processing while syncing continues, so the workspace still mirrors Zotero, and `status --pending` lists what is waiting. `resume` runs it.

Records deleted from Zotero are tombstoned: kept for history and no longer counted or processed. Their evidence reads as `deleted`. Trashed records stay restorable. A partial snapshot never deletes anything.

It never opens, locks, or changes Zotero's database:
- **Reading:** it copies `zotero.sqlite` to a temporary directory and reads the copy, so it works whether or not Zotero is running.
- **Attachments:** files are only read for hashing, and only when their size or modification time changed, except in a full check.
- **Base-directory files:** linked files stored relative to Zotero's Linked Attachment Base Directory are found through Zotero's settings, or `--base-dir`.

Only the user library is synced for now.

`sync` and `watch` extract evidence as they go; for a snapshot, run `extract` after importing. Extraction reads each PDF (with pypdfium2) or UTF-8 text file once, checks it against the imported version, and stores each page's text and spans of up to 800 characters. Each span has a stable evidence ID, its page index and printed page label, character offsets, and a box on the page. Work already done is skipped; `extract --force` redoes it and `extract --retry-failed` retries failures. `status` then shows each library's evidence coverage: extracted, queued, failed (with the reason), pending, unsupported (such as HTML snapshots), attachments without a file, and trashed or excluded ones, plus pages without a text layer, such as scans. OCR is not supported.

```sh
uv run hyperwiki --workspace /tmp/hw-demo extract
```

Without `--workspace`, the workspace is `$HYPERWIKI_WORKSPACE`, else `~/.hyperwiki`. Hyper-Wiki only reads the snapshot or Zotero data; linked files are read only from directories passed with `--linked-root`.

## License

Hyper-Wiki is licensed under the [Apache License 2.0](LICENSE); see [NOTICE](NOTICE). Third-party integrations follow the license policy in [ADR 0002](docs/decisions/0002-license.md).
