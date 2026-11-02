# Hyper-Wiki product design

Date: 2026-11-02. Status: design for approval. Supersedes the 2026-10-26 product scope (see Git history); the [original proposal](docs/archive/PRODUCT_DESIGN.initial.md) is historical. Items marked *(design call)* were proposed during design and not separately confirmed by the user; everything else was decided by the user.

## 1. Intent

Hyper-Wiki is a paper-reading and research assistant for postgraduate students. It turns a researcher's Zotero library into a living, evidence-linked wiki that the researcher and their AI agents (Claude Code, Codex, and other MCP clients) share.

**Origin.** The idea came from a meeting about Hyper-RAG: build an MCP server for AI agents on an advanced RAG backend combined with the LLM-wiki pattern, to manage the papers in a Zotero library. An earlier planning session with Codex agreed that the data model follows Zotero's own structure: items with metadata, attachments in per-item storage folders, notes, annotations, collections, and tags.

**Contribution.** The contribution is engineering and product value, not an academic method or paper. Hyper-Wiki must be valuable new software: not a duplicate of an existing tool, and not an integration of several open-source projects. The [landscape survey](docs/LANDSCAPE.md) found that agent access to Zotero, in-reader chat with page citations, and generic LLM wikis already exist. Hyper-Wiki's value lies where those tools stop (section 3).

## 2. Users and workflows

Users are postgraduate students who keep papers in Zotero and read English and Chinese literature. Some use Claude Code or Codex; some use Obsidian. One local user and one Zotero library per installation.

Workflows:

- Find papers, passages, definitions, methods, and results.
- Explain a paper with citations and explicit limitations.
- Build and browse paper, concept, topic, and synthesis pages.
- Discover connections across papers and inspect their evidence.
- Draft literature syntheses and comparisons that keep qualifications and disagreement.
- Keep knowledge current as the library changes.
- Keep personal notes and hypotheses separate from generated content.

## 3. What only Hyper-Wiki does

1. **A wiki that stays in sync with Zotero.** Adding, changing, trashing, or deleting papers updates the wiki automatically (section 6).
2. **Evidence all the way through.** Every generated factual statement links to a passage or page in a specific attachment version, or is explicitly marked as unsupported interpretation. A built-in check finds citations that no longer resolve or point to withdrawn evidence. Generated prose is never itself evidence.
3. **Targeted updates.** Hyper-Wiki records which statements depend on which evidence and sources. A changed or removed paper flags or revises exactly the dependent statements; a statement still supported by other papers survives.
4. **A maintainable hypergraph.** Following Hyper-RAG, relations can connect several participants at once (for example method, dataset, task, metric, result, and conditions), each with an explicit role and evidence. Unlike the reference implementation, the index is updated incrementally (add, change, remove papers without a full rebuild), keeps provenance for every relation, and is queryable by agents. It becomes the default retrieval path only where it measurably beats plain hybrid retrieval (section 12).

Together these keep three representations consistent as the library changes: versioned source evidence, structured knowledge (entities, claims, role-labelled relations), and portable Markdown pages.

## 4. Build versus reuse *(design call)*

| Concern | Decision |
|---|---|
| Zotero library management for agents (tags, collections, adding papers, duplicates) | Do not rebuild. Recommend running [zotero-mcp](https://github.com/54yyyu/zotero-mcp) alongside. Hyper-Wiki only reads Zotero. |
| PDF text extraction, embeddings, keyword and vector search primitives | Reuse proven libraries, chosen in M0 under the [license policy](docs/decisions/0002-license.md). |
| Hyper-RAG | Reuse its method and, where useful, its Apache-2.0 code with attribution. Build Hyper-Wiki's own incremental, provenance-preserving index. Keep the original implementation as a comparison point. |
| Chat interface | Not built. Claude Code, Codex, and later the Zotero plugin's navigation provide the conversation surface. |
| Wiki lifecycle, evidence linking, dependency tracking, update engine, ownership rules | Built here; this is the product's core. |

## 5. Deliverables and order

1. **Core backend and agent access.** Library sync, evidence, structured knowledge, hypergraph and hybrid retrieval, the wiki, a JSON CLI, an MCP server, and workflow skills for Claude Code and Codex. It reads the live local Zotero library from the first usable release, because automatic updates are the default.
2. **Zotero plugin.** Instant change detection; update controls (update selected items or collections from the context menu, update all, pause, pending queue, spending and status); wiki navigation; opening cited evidence in the Zotero reader; freshness indicators. No chat panel. *(design call: plugin scope)*
3. **Optional Obsidian plugin.** Only if plain Markdown in Obsidian leaves a demonstrated gap, such as provenance/freshness inspection or revision review.

## 6. Updates

### 6.1 Modes

| Mode | Trigger | Default |
|---|---|---|
| Automatic | Items are added, changed, trashed, or deleted in Zotero. Detected within about a minute by the backend, or instantly once the Zotero plugin is installed. Waits briefly so metadata and a PDF that arrives shortly after are processed together. | On |
| Scheduled check | Full reconciliation against the library to catch anything automatic missed (Zotero or Hyper-Wiki closed, PDF replaced on disk). The only automatic path when automatic mode is off. Interval: daily, weekly, fortnightly, or monthly. | Daily |
| Update selected | Manual: chosen items or collections are processed now, ahead of the queue. Option to force re-processing even if nothing changed, for example after fixing a bad PDF. | Manual |
| Update all now | Manual: the scheduled full check, run immediately. | Manual |
| Rebuild | Manual: re-process with a new PDF extractor, model, schema, or settings. Shows an estimated cost and duration first. Never automatic. | Manual |

### 6.2 Controls

- **Pause automatic:** changes accumulate in a visible pending list until resumed.
- **Spending cap:** automatic AI work stops at a daily limit in API mode; remaining work waits in the queue. Setup asks for a cap and never defaults to unlimited. *(design call)*
- **Exclude collections:** items whose collections are all excluded are skipped entirely. Unfiled items are included. *(design call: membership rule)*

### 6.3 What an update does

Processing has stages. Stages that need no AI always run automatically in every provider mode: inventory and metadata, attachment text extraction, evidence spans, keyword index, local embeddings, and dependency invalidation (flagging stale statements). AI stages (structure and relation extraction, page writing and revision) run according to the provider mode (section 7).

Effects on the wiki:

- **New paper:** its paper page and evidence are added automatically. Concept and topic pages it touches are created or revised. Synthesis pages are created on request by the user or an agent, then maintained automatically. *(design call: which page types are automatic)*
- **Existing pages affected:** a page Hyper-Wiki generated and nobody has edited since is revised automatically, with revision history and undo. A page is "edited" if its file differs from the last version Hyper-Wiki wrote. Edited pages receive a proposed revision instead of being overwritten.
- **Paper changed or removed:** statements depending on it are flagged immediately. A statement with remaining independent support stays current.
- **Trash versus deletion:** an item in Zotero's trash is hidden from retrieval and the wiki but restorable. Emptying the trash removes it: evidence is tombstoned, kept only for history, and dependent content is flagged.

Guarantees carried from the earlier plans: absence from a partial snapshot is never deletion; repeated updates create no duplicates; interrupted work resumes; coverage is always visible (queued, done, failed, unsupported, stale, pending AI work).

## 7. AI providers

One provider setting with three kinds, all supported:

| Kind | Background AI work | Notes |
|---|---|---|
| API key (OpenAI, Anthropic, DeepSeek, Gemini, any OpenAI-compatible service) | Runs automatically | Spending cap applies. Paper text is sent to the provider. |
| Local model (Ollama, LM Studio, any OpenAI-compatible local server) | Runs automatically | No cost or data leaving the machine; slower; smaller models may lower page and relation quality, which is measured, not assumed. |
| Subscription only (Claude Code or Codex) | Queued until an agent processes it | Non-AI stages still run automatically. AI work waits in a visible queue and is processed when the user next opens their agent (a workflow skill prompts it) or by a scheduled agent run the user configures. Pending pages show metadata, abstract, and a pending marker. |

Rules:

- Hyper-Wiki never handles subscription credentials; vendors restrict third-party use of subscription logins. Before relying on scheduled agent runs, check each vendor's current terms. *(design call)*
- Agent-submitted page drafts pass the same validation as backend-generated pages: every citation must resolve to current evidence IDs before a draft is applied.
- Per-task override: for example a cheaper model for extraction and a stronger one for synthesis.
- Search works in every mode: keyword search always; meaning-based search uses a local multilingual embedding model by default, with API embeddings optional.
- Every generated page and record stores which provider, model, and prompt version produced it.
- Setup and documentation state what content leaves the machine in each mode.

## 8. Languages

- Papers in English and Chinese, including mixed libraries.
- The wiki language is chosen at setup; default English. Changing it later is a Rebuild of generated pages. *(design call)*
- The PDF extractor must handle Chinese and mixed-language papers; the M0 extractor comparison includes Chinese PDFs.
- Queries in either language find evidence in both languages.
- Quoted evidence stays in its original language; wiki prose uses the configured language. On first use, technical terms keep the original-language term alongside the translation. *(design call)*
- Keyword search must segment Chinese text correctly, and embeddings must be multilingual.
- Entity names and aliases across languages (for example 注意力机制 and attention mechanism) resolve to one entity when evidence supports it.

## 9. Library model, wiki, and agent interface

**Library model.** Mirror Zotero's structure: library, parent items, attachments in per-item storage folders, notes, annotations, collections, tags, trash state, and versions. External identity is (library, item key); internal references use stable UUIDs. A normalized snapshot adapter serves tests, fixtures, and reproducible runs; a live read-only adapter serves real use. Hyper-Wiki must never lock, write to, or corrupt Zotero's database while Zotero is running. Zotero notes and annotations are the researcher's own material: they are imported, searchable, and linked from paper pages, but are labelled as the researcher's and never treated as evidence of what a paper reports. *(design call)*

**Query scope.** The whole library is the default; queries can be limited to selected papers, collections, tags, or dates. Scope applies at every retrieval stage, including wiki pages whose sources fall partly outside it.

**Wiki.** Markdown with YAML frontmatter and Obsidian-compatible links, in a folder the user chooses (it can sit inside an Obsidian vault). Page types: paper, concept/topic, synthesis, index, and personal note. Generated pages and personal notes have separate ownership; personal notes are never rewritten. Frontmatter carries stable ID, page type, evidence/claim references, revision, generator fingerprint, and last-verified state.

**Agent interface.** MCP server and JSON CLI over the same application services. Read tools never modify content. Changes are explicit and use revision preconditions. The starting tool set is in [ADR 0001](docs/decisions/0001-core-architecture.md#agent-surface), plus tools for the subscription queue: list pending AI tasks and submit validated drafts. Workflow skills teach agents to search → read evidence → cite, to process the update queue, and to review proposed revisions. Retrieved paper text and notes are untrusted data, never instructions.

## 10. Platforms and scale *(design call)*

- macOS, Windows, and Linux; CI runs on all three.
- Current Zotero (10.x) is the supported target for the first release; older versions are best-effort.
- Design for libraries up to about 5,000 items and 2,000 PDFs on a laptop; test the first release at about 500 items.
- Agent users install the backend with one command, then a setup step detects Zotero, chooses the provider and wiki language, and registers the MCP server with Claude Code and Codex. Zotero-plugin users should not need a terminal; how the plugin installs or launches the backend is decided in the plugin milestone.

## 11. Goals, non-goals, anti-goals

**Goals:** the four capabilities in section 3; the update modes in section 6; all three provider kinds; English and Chinese; agent access first, then the Zotero plugin.

**Non-goals for this version:** multi-user or cloud hosting; writing to Zotero; a custom editor; unrestricted bidirectional text merging; full figure, equation, and scanned-document understanding (unsupported content is reported, not hidden); academic novelty claims.

**Anti-goals (failure even if it technically works):**

- Becoming another chat-with-your-PDFs tool.
- Duplicating zotero-mcp's library management.
- Silently overwriting a researcher's edits or notes.
- A current citation pointing to removed, replaced, or non-supporting evidence.
- Requiring a paid API to be useful.
- Hidden or unbounded spending.
- Locking knowledge in a proprietary format.
- Claiming the hypergraph is better without measurement.

## 12. How we know it is valuable *(design call)*

This is a development project, so evaluation is practical rather than an academic study (no ethics approval or confirmatory statistics). Results are reported honestly, including failures.

1. **Reliability gates (strict, automated):** lifecycle fixtures such as add, edit metadata, replace PDF, remove a sole or one of several supporting sources, restore, external page edit, interrupted job, and partial snapshot. Pass means zero overwritten human edits, no current citation to withdrawn evidence, no duplicates after re-import, convergence after interruption, and no out-of-scope evidence.
2. **Head-to-head comparison:** the same small library (about 30–50 papers, English and Chinese, kept private and out of Git) and real questions from the user's reading. Compare Hyper-Wiki with the closest tools in the [survey](docs/LANDSCAPE.md) (zotero-mcp with an agent, llm-for-zotero, llmwiki, LiteratureGraphContext). Measure answer correctness, whether cited pages actually support each claim, behaviour after library changes, time, and cost. Check outputs manually, blinded where practical.
3. **Advanced retrieval earns its place:** compare hypergraph retrieval with hybrid retrieval on the same tasks. Hypergraph becomes the default only if it is better on relation-heavy questions within an agreed cost envelope; otherwise it stays optional.
4. **Real use:** the user and a few labmates use it for real reading; record failures and missing workflows.

## 13. Constraints

Apache-2.0 with the [license policy](docs/decisions/0002-license.md) for every integration. Local-first, single user. No real libraries, credentials, copyrighted PDFs, databases, or private traces in Git. Python 3.12 backend managed by uv. Read operations never mutate content. Planned targets and measured results stay clearly distinguished.

## 14. Decisions and open items

**Decided by the user (2026-11-02):** engineering/product contribution rather than academic; differentiation from existing tools rather than integration; Zotero-shaped data model; delivery order (agent wiki MCP, then Zotero plugin, then optional Obsidian plugin); automatic updates by default with scheduled checks (daily, weekly, fortnightly, monthly), update selected, update all, and rebuild; automatic revision of unedited generated pages with proposals for edited pages; support for API key, local model, and subscription providers; English and Chinese with a configurable wiki language.

**Design calls to confirm while reading:** sections 4, 5 (plugin scope), 6.2 (spending cap, exclusion rule), 6.3 (automatic page types), 7 (subscription credential rule), 8 (default language, term handling), 10, and 12.

**Left to the builder:** Zotero change-detection mechanism (local API, read-only database access, or plugin notifications) after the M0 spike; debounce timing; PDF extractor; embedding model; Chinese tokenizer; storage layout; hypergraph schema details and traversal limits; exact MCP tool names; page templates; setup implementation.

**Consequences for existing plans** (to be applied when the development plan is revised): live Zotero change detection moves into the first usable slice; Chinese support and the provider abstraction start early; the maintainable hypergraph is a core deliverable whose default status depends on measurement; the experiment plan becomes the practical evaluation in section 12; the Zotero plugin scope follows section 5; [RESEARCH_REVIEW.md](RESEARCH_REVIEW.md) and [FEASIBILITY.md](docs/FEASIBILITY.md) wording that treats integration as sufficient is updated.
