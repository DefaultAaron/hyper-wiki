# Hyper-Wiki development plan

Date: 2026-11-02. Status: roadmap for the approved [product design](PRODUCT_DESIGN.md); M0 in progress (build tasks done; spikes open). Supersedes the 2026-10-26 plan (see Git history); the [original plan](docs/archive/DEVELOPMENT_PLAN.initial.md) is historical.
Estimates assume one experienced full-time developer; they are effort allowances, not deadlines.

## 1. Release shape

Deliver, in order: a living evidence base with agent search; a living wiki; a maintainable hypergraph; a beta release with a head-to-head comparison; then the Zotero plugin. Obsidian stays optional. Each milestone ends with something the user can run on their own library.

The end-to-end demonstration the beta must pass, in every provider mode:

1. Install, run setup, and point Hyper-Wiki at a real Zotero library with English and Chinese papers.
2. Add a paper in Zotero. Within minutes its evidence is searchable and, depending on the provider mode, its paper page exists or is queued for an agent.
3. Ask Claude Code or Codex a cross-paper question; the answer cites pages in specific papers, drawn from both wiki and original evidence.
4. Replace or remove a paper. Dependent statements are flagged; unedited pages are revised; an edited page receives a proposal instead of being overwritten; a personal note is untouched.
5. Run the scheduled check, update selected, update all, pause, and rebuild (with its cost estimate) and observe consistent status and coverage.

## 2. Technical baseline and boundaries

Python 3.12 core managed by uv; SQLite (FTS5 or a successor chosen in M0) for records, jobs, and keyword search; Markdown with YAML frontmatter for the wiki; the official MCP Python SDK; TypeScript for plugins. CI runs on macOS, Windows, and Linux.

Hyper-Wiki only reads Zotero and never writes to its database or storage. Library management for agents is left to tools such as zotero-mcp ([landscape](docs/LANDSCAPE.md)). No chat interface, graph database server, service cluster, or internal answering agent is built: client agents answer; Hyper-Wiki retrieves, maintains, and validates.

Non-AI stages (inventory, extraction, evidence, keyword index, local embeddings, invalidation) never require a provider. AI stages (relation extraction, page writing and revision) run through one provider interface with three kinds: API key, local model, and a subscription queue processed by the user's agent. Every generated artifact records provider, model, and prompt version.

See [ADR 0001](docs/decisions/0001-core-architecture.md) for identity, evidence, recovery, and tool contracts, [ADR 0002](docs/decisions/0002-license.md) for the integration license policy, and [ADR 0003](docs/decisions/0003-provider-contract.md) for the provider contract.

## 3. Milestones

### M0 — Contracts, fixtures, and spikes (1–2 weeks)

Settle the contracts every later milestone builds on, and answer the technical questions the design deliberately left open.

- Package, locked toolchain, provider-free CI (HW-001, done 2026-11-02).
- Contracts, fixtures, storage, snapshot import CLI, and provider contract (HW-002–HW-005, HW-009, done 2026-11-02; [ADR 0003](docs/decisions/0003-provider-contract.md)).
- Versioned contracts: Zotero-shaped library manifest, update events, evidence locator, scope, retrieval result, wiki frontmatter, provider configuration, and structured errors.
- Synthetic English and Chinese fixtures covering lifecycle events; SQLite migrations; snapshot adapter with an import/status CLI skeleton.
- Spikes, each ending in an ADR: Zotero change detection while Zotero runs (macOS and Windows); PDF extraction including Chinese and mixed-language papers; bilingual keyword search and local multilingual embeddings; provider contract including the subscription queue and vendor terms for scheduled agent runs.
- CI on macOS, Windows, and Linux (HW-010, done 2026-11-02).

Exit: contracts validate and reject unsupported versions; fixtures import deterministically; ADRs record change detection, extractor, search/embedding stack, and provider contract with observed evidence. Untested live behaviour is marked unresolved, never assumed.

### M1 — Living evidence base and agent search (2–3 weeks)

The first release a researcher can use daily, with no AI provider required.

- Live read-only Zotero adapter per the change-detection ADR.
- Update engine: automatic mode with debounce; scheduled check (daily, weekly, fortnightly, monthly, off); update selected with force option; update all; pause with pending list; collection exclusion; trash, restore, and deletion with tombstones; resumable jobs; coverage status.
- Versioned evidence extraction with page locators for English and Chinese papers; visible failure states.
- Bilingual keyword search, local embeddings, hybrid fusion, and scope enforcement at every stage.
- JSON CLI (`setup`, `status`, `sync`, `search`, `read`) and MCP read tools (`search_sources`, `read_evidence`, `get_status`).

Exit: on a private library of about 500 items, a paper added in Zotero becomes searchable within minutes without a provider; every non-AI lifecycle gate passes; nothing writes to Zotero; Claude Code and Codex can search and cite evidence through MCP.

### M2 — Living wiki (3–4 weeks)

- Provider runtime for API, local, and subscription kinds; per-task overrides; spending cap required at setup; MCP tools for the subscription queue (list pending tasks, submit drafts) and a workflow skill that processes it.
- Wiki compiler: automatic paper, concept, and topic pages; synthesis pages on request; configurable wiki language with original terms kept on first use; Obsidian-compatible Markdown.
- Citation validation for every draft, from the backend or an agent; lint for unresolved or withdrawn citations, broken links, and orphans.
- Dependency tracking from statements to evidence; immediate stale flags; automatic revision of unedited pages with history and undo; proposals for edited pages; personal notes; Zotero notes and annotations labelled as the researcher's.
- Rebuild with cost and time estimate.
- MCP wiki tools (`search_wiki`, `read_wiki_page`, `propose_revision`, `apply_revision`, `record_note`) and workflow skills for Claude Code and Codex.

Exit: the release demonstration (section 1) passes in all three provider modes with fixtures and on the private library; zero overwritten edits and no current citation to withdrawn evidence across the lifecycle suite.

### M3 — Maintainable hypergraph (3–4 weeks)

- Entities and cross-language aliases, claims, and role-labelled n-ary relations with qualifiers and evidence per relation; extraction runs through the provider interface, including the subscription queue.
- Incremental index: add, change, and remove papers without a rebuild; removal withdraws support; provenance survives every update.
- Bounded, scope-safe relation retrieval (hops, fan-out, token budget) and MCP tools `explore_relations` and `inspect_claim`.
- Comparison points: pinned Hyper-RAG reference configuration and a relational-filter baseline over the same records.

Exit: relations pass the lifecycle suite and budgets; an ADR records whether hypergraph retrieval becomes the default, using the decision rule in the [evaluation plan](docs/plans/EXPERIMENT_PLAN.md).

### M4 — Beta release and head-to-head (2–3 weeks)

- One-command install and a setup step that detects Zotero, chooses provider, language, and spending cap, and registers the MCP server with Claude Code and Codex.
- Backup and restore, migrations, index rebuild, cold start, and recovery from interrupted writes.
- Scale check on a synthetic library of about 5,000 items and 2,000 PDFs on a laptop: initial sync time, update latency after a Zotero change, search latency, memory, and disk.
- Head-to-head comparison against the closest tools and real use by the user and labmates, per the evaluation plan.
- Dependency, model, and fixture license audit; NOTICE updated.

Exit: clean-machine install on macOS and Windows; published aggregate head-to-head results with failures; documented limitations and provider costs; beta tagged.

### M5 — Zotero plugin (3–5 weeks)

- Spike Zotero 10 plugin APIs, Notifier events, local authenticated transport, and how the plugin installs or launches the backend without a terminal. Check plugin template licenses against ADR 0002 and keep the plugin in its own directory.
- Instant change detection through the plugin.
- Update controls: update selected items or collections from the context menu, update all, pause, pending queue, spending and status, rebuild with estimate.
- Wiki navigation, opening cited evidence at the right page in the Zotero reader, and freshness indicators. No chat panel.

Exit: in Zotero, adding a paper updates the wiki; a citation opens the PDF at the cited page; it works without a terminal; the backend remains independently usable.

### M6 — Optional Obsidian plugin (separate decision)

Use the wiki in Obsidian as plain Markdown first and record missing workflows. Build only for a concrete gap such as provenance/freshness inspection or revision review.

## 4. Immediate queue

Detailed tasks and dependencies: [BACKLOG.md](docs/plans/BACKLOG.md). Next: the HW-006–HW-008 spikes (Zotero change detection, PDF extraction, bilingual search), which need a live Zotero 10 library and licensed English and Chinese PDFs. Do not start with plugin UI, a full ontology, or a graph database deployment.

## 5. Release checks and operating constraints

- No real library, credentials, copyrighted PDFs, databases, or private traces in Git; redistributable fixtures have explicit provenance.
- Treat paper text, notes, and retrieved content as untrusted data.
- Read operations never mutate content; changes are explicit, revision-checked, and recoverable.
- Never write to Zotero. Never handle subscription credentials.
- Report queued, done, failed, unsupported, stale, and pending-AI states separately; spending is always capped and visible.
- Keep planned targets separate from measured results.

## 6. Status and external inputs

HW-001–HW-005, HW-009, and HW-010 are done (2026-11-02): the provider-free checks pass in CI on Linux, macOS, and Windows; the opt-in live provider smoke test has not run. HW-006–HW-008 remain open. Local SQLite/FTS5 and an installed Zotero 10.0.6 were observed on 2026-10-26; live Zotero access, extraction, embeddings, real providers, and agent hosts remain untested.

Inputs needed: a private library of about 30–50 papers in English and Chinese with real questions (M1 smoke tests, M4 comparison); at least one API key or local model for M2; Claude Code and Codex access for host tests; a Windows machine or CI runner for Zotero spikes.
