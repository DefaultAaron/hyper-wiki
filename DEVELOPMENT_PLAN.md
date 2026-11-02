# Hyper-Wiki development plan

Date: 2026-10-26. Status: executable planning baseline; implementation has not started.
Authority: the user's clarification and PRODUCT_DESIGN.md supersede the original proposal. Milestone estimates assume one experienced full-time developer; they are not deadlines.

## 1. Release shape

Deliver a local research wiki and retrieval backend, then useful agent access, then a Zotero plugin. Keep Obsidian optional. The first demonstration is domain-independent:

1. Import a Zotero-shaped snapshot with papers, attachments, collections, tags, notes, and annotations.
2. Extract versioned evidence and report unavailable content.
3. Generate a cited paper page and one linked synthesis page.
4. Retrieve both wiki knowledge and original evidence for a real research question.
5. Change or remove a source, flag affected content, and propose a minimal reviewable revision.
6. Preserve a researcher note and detect an external page edit throughout the update.

The first working slice uses lexical retrieval; hybrid search and hypergraph experiments follow. Do not wait for every schema, model, or plugin to demonstrate this chain.

## 2. Technical baseline and boundaries

Provisional stack: Python core, uv-managed environment/lockfile, SQLite/FTS5, Markdown/YAML, official MCP Python SDK, and TypeScript for later plugins. M0 selects and verifies an exact Python/dependency set; the installed Python 3.14 is not presumed compatible with all parsing/model packages. Prefer a supported, dependency-compatible 3.12+ interpreter unless the spike supports a different choice.

Start with a replaceable embedding provider and exact vector scoring on a small corpus. Add a persisted ANN index only after measuring size and latency. Lexical retrieval stays usable if embeddings are unavailable; surface degraded mode. Reranking is separately switchable.

No separate graph database, service cluster, or internal answering agent is required. CLI and MCP call shared application services. A worker performs bounded resumable processing. Answer synthesis belongs to the client agent; wiki compilation uses a separate configurable generation provider. Existing agent subscriptions are not assumed to supply backend model API access.

See [architecture decisions](docs/decisions/0001-core-architecture.md) for source identity, recovery, and tool contracts.

## 3. Milestones

### M0 — Contracts, fixtures, and technical spike (1–2 weeks)

Tasks:
- Create Python package/test/lint configuration, lock dependencies, and add CI for a clean provider-free run.
- Define versioned library manifest, evidence locator, wiki frontmatter, scope, retrieval result, provider, and error schemas.
- Implement tiny synthetic fixtures and a CLI import/status skeleton.
- Map one explicit Zotero export shape into the normalized manifest; do not claim all export formats are supported.
- Inspect target Zotero capabilities when execution permits; record metadata, hierarchy, notes, annotations, file resolution, pagination, updates, deletions, and local/Web differences.
- Compare two parser candidates on representative licensed digital PDFs; record page/section/table fidelity, runtime, installation, and license tradeoffs.
- Run a short embedding/generation smoke check only when credentials and budget are configured; retain deterministic fakes for offline tests.

Exit: chosen stack with pinned versions; versioned fixture/import contract; source identity and locator examples; written capability/parser decisions; a clean offline setup. Any untested live capability is marked unresolved. If blocked, proceed with snapshots, not assumed live behavior.

### M1 — Reliable source-to-wiki slice (2–3 weeks)

Tasks:
- Build normalized sources, attachment versions, evidence spans, jobs, dependencies, migrations, and explicit coverage states.
- Implement metadata-first snapshot import, content hashing, text/PDF extraction, FTS search, source reading, and one configurable wiki compiler.
- Produce paper pages, index, linked notes, and one evidence-backed synthesis page.
- Implement stable IDs, citation resolution, generation fingerprints, and write/revision journal.
- Reject missing evidence references; distinguish cited source reports from model interpretation.

Exit: the six-step demonstration above works on synthetic fixtures and a permitted small corpus; repeated import creates no duplicates; interrupted processing resumes; source links resolve; external edits survive. Missing/failed attachments remain visible. Without a configured generation provider, only the deterministic contract path is validated; the generated-wiki exit remains pending.

### M2 — Hybrid retrieval and maintained wiki (2–3 weeks)

Tasks:
- Add embeddings, lexical/vector fusion, optional reranking, source/wiki search, deduplication, scope enforcement, and evidence budgets.
- Add concept/topic/synthesis pages using evidence-backed claims and links; record compilation dependencies.
- Implement tombstones, change reconciliation, freshness by layer, stale propagation, and reviewable revision application.
- Add a read-only live Zotero adapter after the capability spike, with adapter-specific checkpoints, file resolution, and complete-scope reconciliation. If live access remains unavailable, label the release snapshot-only and keep live compatibility pending.
- Add maintenance checks for broken links, unsupported claims, stale citations, unresolved aliases, and compilation omissions.
- Run experiments E1–E3's initial offline subsets to select defaults.

Exit: hybrid retrieval runs against a frozen corpus; scope holds at every stage; known update/deletion fixtures produce no silently active unsupported citations; user edits are preserved. Report quality/cost versus lexical and source-only baselines. Default compiler and retrieval settings are justified by measurements, not intuition. Claim live Zotero support only after successful target-version import/update/removal tests; snapshot support can pass independently.

### M3 — Structured and hypergraph retrieval experiment (3–4 weeks)

Tasks:
- Add entities, aliases, claims, typed role-labelled relations, qualifiers, and evidence per assertion; support optional domain observation fields.
- Implement bounded relation retrieval and expansion behind the retrieval interface.
- Compare relational filtering and an equivalent observation-node graph using identical extracted records.
- Evaluate gold structures before automatic extraction, then run both end to end.
- Spike a pinned iMoonLab adapter only if its retrieval/lifecycle contract is suitable; any simplified native method is labelled an adaptation.
- Run E3 and relevant lifecycle scenarios; report ingest/update costs separately from query costs.

Exit: graph path preserves evidence and scope, obeys traversal/token budgets, and handles removal. A documented retain/experimental-only/defer decision follows the predefined experiment gate. A negative graph result does not block the core release.

### M4 — Agent beta: MCP, CLI, and skills (2–3 weeks)

Tasks:
- Stabilize minimal JSON CLI commands and stdio MCP tools over shared services.
- Return evidence IDs, versions, scope, freshness, missing coverage, truncation, and structured errors.
- Package workflow instructions for question answering, synthesis, and reviewed wiki maintenance; adapt discovery/configuration per client.
- Ship sample configurations, operation help, and a tiny redistributable demo workspace.
- Run protocol tests plus actual Codex and Claude Code smoke workflows, including an unavailable provider and stale source.

Exit: both clients complete source search → evidence inspection → cited response → proposed revision using the same backend. Read tools have no write effects. Applied changes require correct expected revision. Client versions, configuration, traces, and limitations are recorded. A protocol test alone cannot pass the host-integration gate.

### M5 — Reproducible core release and pilot (2–3 weeks)

Tasks:
- Complete E1–E5 at pilot scale, publish aggregate results/fixtures allowed by license, and separate failures by pipeline stage.
- Run a small user pilot, calibrate targets, and document installation, provider costs, supported content, and known limitations.
- Exercise backup/restore, migrations, index rebuild, cold start, and recovery from interrupted writes.
- Choose project license and audit reused dependencies before public distribution.

Exit: clean-machine demo, reproducible experiment commands/manifests, lifecycle regression report, documented defaults, cost ledger, and either a pilot report or an explicit pending user-validation status. An engineering preview may be released before recruitment completes but must not claim demonstrated student benefit.

### M6 — Zotero plugin (3–5 additional weeks)

Only after core/agent contracts stabilize. Spike the installed target version before committing to a plugin API surface. Provide processing status, selected/global questions, wiki navigation, and reliable evidence opening. Keep ingestion and retrieval in the backend. Add local authenticated IPC/HTTP only where plugin needs justify it; do not expose a public service by default.

Exit: paper selection → backend evidence → cited wiki/answer → source navigation works in Zotero. Plugin/backend version compatibility and failure behavior are tested. Backend remains independently usable.

### M7 — Optional Obsidian plugin (separate decision)

First open the wiki as plain Markdown and collect missing workflows. Proceed only for a concrete gap such as provenance/freshness inspection or revision review. Scope and estimate follow that decision; no plugin is required to read or preserve the wiki.

## 4. Immediate implementation queue

Detailed tasks and dependencies: [BACKLOG.md](docs/plans/BACKLOG.md).

First work session: establish the package and offline schemas; create a tiny manifest; import it and display coverage. Next: extract/read evidence, add FTS, compile one cited page, and prove idempotent refresh. Live Zotero probing and parser selection are bounded spikes rather than prerequisites for all core work.

Do not start with plugin UI, a complete ontology, a graph database deployment, or a broad leaderboard reproduction.

## 5. Release checks and operating constraints

- Store no real library or secrets in Git. Redistributable fixtures have explicit provenance and rights.
- Include parser/model/schema/config versions in processing fingerprints. Document what content is sent to providers.
- Never trust instructions embedded in papers or notes. Validate paths and configured attachment roots.
- Use explicit state mutations, revision checks, and recoverable writes. Removal withdraws support, not automatically every multiply supported claim.
- Inventory the entire library; report queued, available, unsupported, failed, and stale states separately.
- Keep strict engineering gates separate from provisional quality/productivity targets.

## 6. Status and blockers

All milestones are planned. Current repository artifacts are documentation and a documentation checker. Local SQLite/FTS5 was checked, and installed Zotero metadata was read. Live API access was sandbox-blocked; dependency compatibility and model/parser/client behavior remain untested. Git history was initialized outside the sandbox and pushed to the GitHub remote on 2026-11-02.

Need before real-corpus/model experiments: authorized corpus and representative tasks, supported languages, provider credentials/budget, and target client versions. Synthetic and offline implementation can proceed immediately. Original plan preserved in [archive](docs/archive/DEVELOPMENT_PLAN.initial.md).
