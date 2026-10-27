# Feasibility assessment

Date: 2026-10-26. Scope: assess the earlier proposals against the clarified, domain-independent wiki/RAG product. This is a design assessment with limited local checks, not a working-system benchmark.

## Verdict

The local, single-user wiki/RAG core is feasible using conventional components. The earlier plan is directionally sound but too broad for one implementation pass. The difficult parts are evidence fidelity, incremental lifecycle, wiki ownership, and establishing whether structured retrieval is worth its extraction cost. Neither a specialized graph database nor a plugin is required to demonstrate the core.

Proceed with a snapshot-driven vertical slice, then live integration and richer retrieval. Build evaluation fixtures alongside each feature. Academic novelty is not a core release requirement.

## Evidence actually collected

| Check | Result | Interpretation |
|---|---|---|
| Folder inventory | Three proposals; no code or Git repository | Implementation starts from zero |
| Python | 3.14.6 | Available; dependency compatibility untested |
| SQLite | 3.53.3; FTS5 table creation passed | Local lexical indexing available |
| Zotero application metadata | 10.0.6 at standard macOS path | Installed version only |
| Local API root request | Operation not permitted | Sandbox blocked probe; API availability unknown |
| Git initialization | `.git: Operation not permitted` | Session cannot create Git metadata |
| Parsers, models, retrieval, agent clients | Not executed | No runtime compatibility, quality, cost, or latency evidence |

No private library was imported or external-model request made. The local API failure is not evidence of a Zotero defect or disabled setting.

## Component assessment

| Component | Feasibility | Main risk | Resolution |
|---|---|---|---|
| Zotero-like snapshots | High | Export variants, paths | Versioned manifest and explicit export mapping |
| Live Zotero | High, capability-dependent | Versions, files, deletions, annotations | Target-version matrix and reconciliation tests |
| PDF/text evidence | Medium | Reading order, tables, scans, locators | Representative parser bake-off |
| Hybrid retrieval | High | Identifiers, languages, library size | Lexical/vector/reranking benchmark |
| Cited wiki | Medium | Compilation loss, edit conflicts | Source probes, revisions, separate notes |
| Hypergraph retrieval | Implementable; benefit unknown | Extraction errors, aliases, fan-out | Gold-structure and end-to-end ablations |
| Incremental maintenance | Medium | Support withdrawal, partial failures | Dependencies, tombstones, replay fixtures |
| MCP/CLI/skills | High | Tool choice and host behavior | Shared services, protocol and client tests |
| Zotero plugin | Medium after core | Version-specific UI/lifecycle | Compatibility spike after stable contracts |
| Obsidian plugin | Need unproven | Duplicate functionality | Test ordinary Markdown use first |

## Changes needed to the original plan

1. Restore the broad research-wiki purpose. Retire the previous example domain and comparison-first sequencing; observations are extensible domain data.
2. Separate Zotero-shaped input from live connectivity. A snapshot permits reproducible development without desktop access.
3. Separate whole-library inventory from expensive processing. Queue supported content in bounded batches; expose coverage and provider costs.
4. Deliver source → evidence → wiki → retrieval early, before comprehensive schemas or sophisticated traversal.
5. Implement support tracking and ownership before automatic refresh. Initially mark affected content stale; do not silently rewrite it.
6. Keep hypergraph retrieval replaceable. N-ary data does not prove a benefit over relational filtering or observation-node graphs.
7. Define DB/filesystem recovery: an atomic Markdown rename cannot join a SQLite transaction atomically.
8. Separate strict engineering checks from pilot-calibrated productivity/quality targets.
9. Use staged experiments to isolate wiki value, graph value, extraction errors, and update cost.

## Technical grounding

The [Zotero local API documentation](https://www.zotero.org/support/dev/web_api/v3/local_api) documents reads, local attachment locators, and distinct local instance/version semantics. Keep sync checkpoints adapter-specific and test local file handling explicitly.

[SQLite FTS5](https://www.sqlite.org/fts5.html) supplies lexical indexing. [WAL documentation](https://www.sqlite.org/wal.html) describes concurrent access constraints. Start with a single job writer, short transactions, recovery handling, and local runtime storage.

[pypdf's guide](https://pypdf.readthedocs.io/en/stable/user/extract-text.html) describes PDF reconstruction and OCR limitations. Compare a simple parser with a layout-aware option; classify unsupported content and do not invent precise locators.

The [MCP Python SDK](https://py.sdk.modelcontextprotocol.io/), [Codex MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli), and [Claude Code MCP](https://code.claude.com/docs/en/mcp) support the proposed local server integration. Actual host behavior remains an execution test.

[iMoonLab Hyper-RAG](https://github.com/iMoonLab/Hyper-RAG) is an experimental reference, not a verified lifecycle backend. Pin code, inspect retrieval-only access, updates/deletion, dependencies, and license obligations before reuse. It has not been reproduced locally.

## Effort and decision gates

Allowance for one experienced full-time developer: 10–15 developer-weeks through agent beta and an informative hypergraph experiment (M0–M4), 12–18 through the core release/pilot work (M0–M5), plus approximately 3–5 for an initial Zotero plugin. These are effort estimates, not delivery promises. Annotation, recruitment, provider limits, and extraction problems can extend elapsed time. Publication-grade evaluation is separate.

Use the simpler default if graph gains do not justify costs. Narrow supported document types if extraction dominates errors. Stop automatic revisions if citations or edits cannot be preserved. Defer plugins until their workflow works outside them.

External inputs still needed: an authorized representative library snapshot, supported languages, provider/budget choices, target client versions, and license selection. Synthetic fixtures and provider-free contract work can proceed independently.
