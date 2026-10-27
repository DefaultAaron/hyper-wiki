# Hyper-Wiki development plan

Date: 2026-10-26
Status: Proposed implementation plan

## 1. Goal and priorities

Build a continuously maintained, evidence-backed research wiki sourced from Zotero. Claude Code and Codex are the first clients. Hypergraph retrieval supports cross-paper knowledge discovery and qualified comparisons.

Delivery order:

1. Research wiki with Hyper-RAG, MCP, and CLI for AI agents.
2. Zotero plugin using the same backend.
3. Optional Obsidian plugin, after the core goals are reached.

Index the entire Zotero library by default, update incrementally, and retrieve selectively. Selected papers, collections, tags, and dates are optional query scopes, not prerequisites for ingestion.

## 2. Initial scope

The first release targets one local user and one Zotero library. It includes metadata, collections, tags, notes, annotations, accessible attachments, source evidence, cited Markdown pages, structured claims, and agent tools.

Defer multi-user collaboration, hosted accounts, Zotero write-back, a custom graphical editor, unrestricted bidirectional merging, and comprehensive figure/equation interpretation. Report unsupported content and missing PDFs explicitly.

Keep the internal source model independent of Zotero while preserving its metadata and organizational structure.

## 3. Architecture

| Component | Responsibility |
|---|---|
| Zotero adapter | Import library records, detect changes, resolve attachments |
| Background worker | Resumable ingestion, extraction, indexing, and wiki refresh |
| Evidence store | Versioned extracted content and source locations |
| Knowledge store | Entities, qualified claims, observations, hyperedges, dependencies |
| Retrieval engine | Lexical/semantic search, filtering, reranking, bounded graph expansion |
| Wiki compiler | Cited Markdown pages and reviewable revisions |
| MCP server and CLI | Shared application interfaces for agents and automation |
| Later plugins | Zotero workflow integration; optional Obsidian interface |

Suggested starting stack, subject to a technical spike:

- Python core for ingestion, extraction, retrieval, and evaluation.
- SQLite for source metadata, evidence, claims, jobs, dependencies, and hypergraph incidence records.
- SQLite FTS5 for lexical retrieval and a replaceable local vector-index interface.
- Markdown with YAML frontmatter for persistent wiki content.
- Configurable embedding, extraction, reranking, and generation providers.
- TypeScript for later plugins.

Avoid requiring a separate graph database until traversal or operational needs justify it. Represent hyperedges as records with role-labelled participants initially. Coordinate database access between the worker and MCP processes.

## 4. Source identity and ownership

Identify Zotero sources using library identity plus item key. Distinguish parent paper items from attachment items. Treat DOI, title, and citation key as metadata rather than primary identifiers.

| Information | Authority |
|---|---|
| Bibliographic metadata, collections, tags, Zotero notes and annotations | Zotero |
| Document bytes | Original attachments, optionally configured immutable snapshots |
| Extracted evidence and locators | Versioned evidence store |
| Structured claims and hyperedges | Knowledge service |
| Wiki prose and personal notes | Markdown files |
| Embeddings and search indexes | Rebuildable derived data |

Validate the target Zotero version's local API first, and evaluate a Web API fallback. Document differences in attachment access, annotations, change detection, and supported operations. Avoid direct mutation of Zotero's database.

## 5. Core data model

- SourceItem: source identity, bibliographic metadata, collections, tags, source version, synchronization state.
- Attachment: parent source, attachment key, media type, availability, content hash, document version.
- EvidenceSpan: attachment version, extracted text/table content, section, page, available precise locator, extraction method.
- Entity: stable identity, type, aliases, resolution history.
- Claim: statement, supporting/opposing evidence, type, extraction status, review status.
- Observation: method, dataset, task, split, input configuration, training/evaluation protocol, metric, value, units, evidence.
- Hyperedge: relation type, role-labelled participants, qualifiers, provenance, version.
- WikiPage: stable ID, page type, file path, referenced claim/evidence IDs, revision, freshness.
- Dependency: relationships used to invalidate derived knowledge.
- Job: operation, input fingerprint, state, retry count, errors, output revision.

Keep unknown experimental conditions explicitly unknown. Do not assume results are comparable because dataset and metric names match.

Distinguish paper-supported findings, generated synthesis, and personal hypotheses. A generated answer or wiki page must not become independent scientific evidence.

## 6. Global ingestion and freshness

Initial import:

1. Discover all items and import metadata.
2. Inventory notes, annotations, and attachments.
3. Make metadata searchable immediately.
4. Extract accessible documents in resumable background jobs.
5. Index evidence.
6. Extract claims, observations, and hyperedges.
7. Generate paper pages and refresh affected concept/comparison pages.

| Change | Processing |
|---|---|
| New paper | Import metadata and queue derived processing |
| Metadata, tags, collections | Update fields and navigation without repeating PDF extraction |
| Notes or annotations | Update corresponding records and dependent pages |
| Attachment contents | Create a content version and invalidate affected knowledge |
| Source removal | Tombstone source, exclude from active retrieval, flag dependent claims/pages |
| Extraction schema/model change | Controlled reprocessing using updated processing fingerprints |

Use hashes and fingerprints for idempotency. Fingerprints include relevant input versions, schema, extractor, and configuration. Retry jobs without duplicate records. Periodic reconciliation should catch missed changes, including local attachment edits.

Track metadata, evidence, structured-knowledge, and wiki freshness separately. Newly indexed evidence can answer questions before wiki generation finishes. Show missing attachments, failed extraction, pending jobs, and relevant source versions. Strict freshness mode may wait within a timeout; normal queries use available evidence and report limitations.

Maintain source → evidence → claim/hyperedge → wiki dependencies. Refresh affected pages rather than rebuilding the entire library.

## 7. Retrieval design

Baseline path: apply scope filters, run lexical and semantic retrieval, merge/deduplicate, rerank, and select supporting evidence within a context budget.

Hypergraph path:

1. Identify candidate entities, claims, and observations.
2. Retrieve qualified hyperedges.
3. Expand selectively to connected evidence.
4. Preserve experimental qualifiers and source provenance.
5. Rerank, deduplicate, and stop within configured budgets.

Bound traversal depth, fan-out, candidate count, latency, and evidence tokens. Popular entities must not trigger unlimited expansion.

Hyperedges represent qualified observations or relations, not just co-occurrence. Example: a result connects method, dataset, task, split, lead configuration, metric, value, and protocol when reported, with links to the original table or passage.

Query modes:

- Entire library, the default.
- Selected papers, collections, tags, or dates.
- Selected papers plus library context, with outside evidence identified.

Scope restrictions must apply to all retrieval stages. Hypergraph retrieval is a planned capability, but its benefit must be measured against the baseline.

## 8. Markdown wiki and editing

Page types: paper, concept, method, dataset, comparison, research question.

Use stable IDs, documented frontmatter, predictable links, source/claim references, revision information, and freshness indicators. Keep Markdown portable so Obsidian can open it without a plugin.

For the MVP, keep generated paper summaries and personal notes in separate linked files. Detect external changes before replacing generated pages. Preserve human edits and produce reviewable revisions when conflicts arise. Write updates atomically and keep revision history.

Markdown links provide readable navigation; the structured knowledge store retains authoritative hyperedge roles and qualifiers.

## 9. Agent interfaces

| Read operation | Purpose |
|---|---|
| search_library | Search source metadata, evidence, and wiki with filters |
| read_wiki_page | Read content and freshness state |
| read_evidence | Inspect original extracted evidence and locator |
| get_claim_evidence | Inspect support, qualifiers, and versions |
| compare_observations | Compare structured results and missing conditions |
| explore_relations | Traverse bounded relationships with provenance |
| get_sync_status | Inspect coverage, pending jobs, and errors |

Update operations include sync/retry commands, propose_wiki_update, record_personal_note, and explicit revision application. Query tools remain read-only. Generated refreshes follow configured ownership rules; edits use revision preconditions to avoid lost updates.

Provide MCP and CLI interfaces over shared services. Also support ordinary filesystem reading. Agent instructions explain evidence verification, citation conventions, freshness, and editing. Validate workflows in both Claude Code and Codex.

## 10. Phase 1 milestones

### M0: Technical spike

Validate Zotero integration, attachment access, change detection, annotations, extraction locators, storage, and MCP dependencies. Create fixtures covering missing PDFs, duplicate records, notes, malformed documents, changed attachments, and removals.

Exit: documented capability matrix, selected stack, initial schemas, and representative evaluation corpus.

### M1: Reliable ingestion

Implement normalized records, resumable jobs, incremental sync, tombstones, reconciliation, and coverage reporting.

Exit: repeated sync is idempotent, restart resumes work, and modifications/removals propagate correctly.

### M2: Evidence retrieval and agent access

Implement extraction, locators, hybrid retrieval, scope filters, MCP read tools, and CLI.

Exit: both target agents answer fixture questions with traceable evidence and obey scope boundaries.

### M3: Persistent wiki

Generate cited pages, track dependencies/freshness, retain revisions, and preserve human edits.

Exit: source changes flag or refresh affected pages; external edits survive; pages remain readable offline.

### M4: Hypergraph knowledge and retrieval

Implement qualified claims/observations, entity resolution, role-labelled hyperedges, bounded expansion, and comparisons.

Exit: retrieved relations link to evidence, unknown conditions remain visible, and traversal respects budgets.

### M5: Evaluation and release

Compare retrieval variants, document limitations, package installation/configuration, and provide reproducible examples.

Exit: a report shows quality, provenance, latency, token cost, update behavior, and when hypergraph retrieval helps or does not help.

## 11. Evaluation

Begin with approximately 30–50 papers from one research area and manually checked questions drawn from actual reading.

Cover exact settings, table lookup, concept explanations, cross-paper synthesis, qualified comparisons, conflicting findings, insufficient evidence, source updates/removals, and discovery outside the initially selected collection.

Compare:

1. Hybrid source retrieval.
2. Wiki plus source retrieval.
3. Wiki plus source retrieval and hypergraph expansion.

Use the same corpus snapshot, answer model, and comparable evidence budgets. Report ingestion costs separately. Measure correctness, citation support, annotated evidence recall, preservation of conditions, appropriate abstention, freshness, latency, context tokens, and update cost. Evaluate wiki consistency separately. Human review should substantiate core results rather than relying exclusively on LLM judging.

## 12. Zotero plugin

Reuse the backend. Add selection/collection ingestion controls, processing status, scoped/global questions, wiki navigation, and source-evidence navigation. Support PDF navigation only where locators are reliable.

Exit: a complete reading workflow works inside Zotero without duplicating extraction or retrieval logic in the plugin.

## 13. Optional Obsidian plugin

Develop only after a demonstrated gap remains beyond Markdown compatibility. Potential features: Hyper-RAG sidebar, verified citation insertion, provenance/freshness inspection, structured relation navigation, and proposed wiki updates.

The backend remains usable without Obsidian.

## 14. Reliability and open-source release

- Keep credentials outside wiki content and exports.
- Document content sent to external providers.
- Treat paper text and notes as untrusted content, not executable agent instructions.
- Use transactions and revision checks for concurrent updates.
- Provide schema migrations, backup, restore, and index rebuild procedures.
- Report uncertain extraction and avoid fabricated locator precision.
- Verify dependency/reused-code licenses and choose a project license before release.
- Publish redistributable fixtures; do not bundle copyrighted library PDFs without permission.

## 15. Immediate next steps

1. Confirm the installed Zotero version and integration capabilities.
2. Select a representative collection and real evaluation questions.
3. Define stable source IDs, evidence locators, observation schema, and page frontmatter.
4. Complete the ingestion technical spike.
5. Deliver one vertical workflow: Zotero paper → evidence → cited page → agent answer → incremental refresh.
6. Add and evaluate hypergraph retrieval before starting the Zotero plugin.
