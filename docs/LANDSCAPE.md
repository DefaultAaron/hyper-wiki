# Landscape survey: existing tools Hyper-Wiki would compete with

Date: 2026-11-02. Status: desk survey of public READMEs, project pages, forum posts, and secondary listings. Nothing was installed or run. Star counts and features are snapshots and change quickly; verify a project directly before depending on, integrating, or positioning against it.

Purpose: Hyper-Wiki must be valuable new software for researchers, not a duplicate of an existing tool and not just several open-source projects glued together. This survey records what already exists so the plan builds only what is missing. It complements [RESEARCH_REVIEW.md](../RESEARCH_REVIEW.md), which compared RAG and wiki *methods*; this document compares *products and projects*.

## Summary

- **Agent access to Zotero is already solved.** The most popular Zotero MCP server has about 5.7k GitHub stars, 40+ tools, metadata/full-text/semantic search, annotations, and write operations through the Zotero 10 local API [1]. Several smaller servers add passage-level search [3][4][5].
- **In-Zotero chat with page-linked citations is already solved.** Plugins answer questions about one or several papers, link citations to PDF pages or passages, run agents, and save answers as notes or Markdown [6][7][8][9].
- **LLM wikis are spreading quickly but are generic or early.** The leading open-source LLM wiki (Apache-2.0, about 1.7k stars) writes cited pages via MCP and updates nightly, but has no Zotero or research-paper model [10]. Zotero-sourced wikis exist only as personal projects or agent skills with manual ingestion [11][12].
- **Graph RAG over Zotero exists in early form.** One project extracts papers, methods, datasets, claims, limitations, and evidence into a graph served over MCP with optional Zotero auto-sync [13]; another feeds Zotero PDFs into LightRAG [14]. Neither maintains a readable wiki, and neither documents how source changes or deletions propagate.
- **Hyper-RAG is a method reference, not a maintainable backend.** The iMoonLab repository documents batch construction, a FastAPI service, and no incremental update, standalone retrieval API, or MCP interface [15].
- **No surveyed project documents the combination Hyper-Wiki targets:** a wiki that updates automatically from Zotero changes, keeps every generated statement linked to page-level source evidence, tracks which statements depend on which sources, flags or revises stale content when sources change or disappear, preserves human edits, and exposes the same knowledge to agents and to the user.

## What exists, by category

### Zotero MCP servers (agent access)

| Project | What it does | Update behaviour | Wiki / structure | License, maturity |
|---|---|---|---|---|
| 54yyyu/zotero-mcp [1][2] | 41 default / 53 total tools: search (title, tag, full text, semantic), metadata, BibTeX, full text, page ranges, page images, annotations; adds papers, edits tags/collections, merges duplicates (Zotero 10 local API or Web API) | Semantic index rebuilt manually, on server start, or on a daily/N-day schedule; metadata-only by default, `--fulltext` optional; passages carry section labels, page numbers not documented | None | MIT (bundled agent plugin AGPL-3.0); ~5.7k stars |
| xunhe730/ZotPilot [3] | 20–32 tools: section-chunked passage search, notes/annotations, tagging, collections, academic database search, tutoring, review-outline skill | Manual incremental `index` (skips indexed items, resumable); deletion handling undocumented | None | MIT; ~74 stars; v0.5.3 |
| nealcaren/mcp-zotero [4], ccam80/zotero-chunk-mcp [5] | Optional RAG/OCR extra; passage-level Gemini + ChromaDB search reading `zotero.sqlite` read-only | Not documented | None | Small projects |

### Zotero AI plugins (in-app reading assistants)

| Project | What it does | Persistence | License, maturity |
|---|---|---|---|
| yilewang/llm-for-zotero [6] | Reader chat, multi-paper context, agent mode with confirmation/undo, skills (evidence-based QA, compare papers, literature review), MinerU parsing; citations jump to source passages, page labels kept conservative until verified | Zotero notes, Markdown folders (Obsidian/Logseq), chat history; "agent memory" on roadmap; no wiki | AGPL-3.0; ~3.5k stars |
| dralkh/seerai [7] | Chat, "Papers Tables" structured extraction, systematic-review workflow, agents, separate MCP server, local API | Workspace files with optional Git; passage-level references listed as future work | MIT; ~95 stars |
| Grounded Q&A [8] | Single- and multi-paper answers with clickable `[Paper N, Page M]` citations | Answers only (persistence not described) | Posted June 2026 |
| PapersGPT [9] | Chat with PDFs or collections; local embeddings, vector store, reranking, BM25 over metadata/notes/annotations | Not described | Mixed open/commercial |

Zotero itself shipped no first-party AI assistant as of October 2026, according to secondary sources [16].

### LLM-wiki implementations

| Project | What it does | Update behaviour | Research/Zotero fit |
|---|---|---|---|
| lucasastorian/llmwiki [10] | Claude writes pages through MCP (`search`, `read`, `create`, `edit`, `lint`, …) with footnote citations to sources; lint checks citation resolution, dangling links, orphans; web UI and graph view | File watcher re-indexes; nightly routine processes new sources | Generic documents; no Zotero or paper metadata. Apache-2.0; ~1.7k stars |
| chris-santiago/ml-wiki [11] | Claude Code commands render paper, synthesis, idea, and topic pages from a Better BibTeX Zotero export; fragments linked to source entries | Manual `/ingest --sync`; re-render adds staleness banners to dependent syntheses; removal handling undocumented; fragment IDs unstable | Personal project, one commit |
| zotero-keeper `llm-wiki-builder` skill [12] | Agent skill that builds Foam-compatible wikis from Zotero, PubMed, documents, and notes | Manual skill invocation | Details unverified |
| WeAgentAI/LLM-Wiki [17] | Research code for "retrieval as reasoning": compile wiki, agent searches/reads with a tool budget, Error Book | Not documented | Benchmarked on HotpotQA; no source citations described |

Many other Karpathy-pattern agent skills and plugins appeared after the April 2026 gist [18]; most are prompt/skill packages rather than maintained backends.

### Graph and hypergraph RAG aimed at papers

| Project | What it does | Update / provenance | Maturity |
|---|---|---|---|
| takapi-s/LiteratureGraphContext [13] | Papers → Kuzu graph of Paper, Method, Dataset, Claim, Limitation, Evidence; MCP tools `search_papers`, `find_limitations`, `explore_paper_graph`, `compare_papers`; deliberately leaves narrative conclusions to the agent | Claims carry paper, page, section, and evidence text; optional Zotero auto-sync daemon (triggers, updates, deletions undocumented) | MIT; no stars or releases |
| local-rag-stack [14] | Zotero PDFs and Obsidian notes → LightRAG (graph + vectors, Ollama) with MCP tools to query, insert, delete | Manual ingest or watch daemon; answer citations not described | MIT; v0.1.0, March 2026 |
| iMoonLab/Hyper-RAG [15] | Hypergraph RAG (`hyper`, `hyper-lite`, `naive`), Hypergraph-DB, FastAPI service, web UI | Batch build; incremental update, standalone retrieval, and answer provenance not documented; no MCP | Apache-2.0; ~330 stars; Nature Communications 2026 |

### Commercial research assistants

NotebookLM, Elicit, and SciSpace are upload-based cloud services. Secondary comparisons disagree about their Zotero support but agree it is import/export rather than a live connection to the user's library [19]. They do not maintain an editable local wiki.

## Where Hyper-Wiki can add new value

These are inferences from the survey, not verified market facts.

1. **A living wiki driven by Zotero events.** Existing Zotero tools rebuild search indexes manually, at startup, or on a schedule; existing wikis process generic sources nightly or manually. None documents automatic, incremental wiki maintenance triggered by Zotero library changes with visible coverage.
2. **Change propagation with dependency tracking.** Only one personal project marks dependent pages stale after re-rendering. None documents what happens to wiki statements when a paper is replaced, corrected, or removed, or when a new paper conflicts with an existing synthesis.
3. **Evidence that survives compilation.** Plugins link *answers* to pages; one graph project stores page-level evidence for *claims*. None documents page-level evidence carried through *generated wiki pages* and checked by lint.
4. **One consistent knowledge base in three forms.** Raw evidence, structured knowledge (graph or hypergraph), and readable Markdown exist separately in different projects. Keeping them consistent under change is the unoccupied engineering problem.
5. **A maintainable advanced-RAG backend.** Hyper-RAG demonstrates the method but not incremental updates, deletions, provenance, or agent tools. Making hypergraph retrieval maintainable over a changing library is engineering work nobody has published.
6. **Human edits as first-class.** Existing wikis either re-index whatever is on disk or preserve only a marked notes section. Ownership rules and conflict-aware revisions remain open.

## What not to rebuild

- General Zotero management through MCP (tagging, collections, adding papers, duplicate merging): the established server already covers it [1]. Hyper-Wiki should interoperate with, not duplicate, these tools, and keep its own tools focused on the wiki and evidence.
- In-reader chat: mature plugins exist [6][7]. The Zotero plugin milestone should focus on what only Hyper-Wiki has (wiki navigation, freshness, evidence, update status), not another chat panel.
- PDF parsing and generic embedding search: reuse proven components under the [license policy](decisions/0002-license.md).

## Limitations of this survey

Feature descriptions come from READMEs and listings summarised on 2026-11-02, partly through automated page summaries. Absence of a feature in documentation is not proof of absence in code. Some repositories, release dates, and licenses could not be confirmed. Before final positioning, install and test the closest projects ([1], [6], [10], [13]) against the same small library.

## Sources

1. 54yyyu/zotero-mcp — <https://github.com/54yyyu/zotero-mcp> (README; primary)
2. zotero-mcp semantic search guide — <https://github.com/54yyyu/zotero-mcp/blob/main/docs/semantic-search.md> (primary)
3. xunhe730/ZotPilot — <https://github.com/xunhe730/ZotPilot>; announcement <https://forums.zotero.org/discussion/130483> (primary)
4. nealcaren/mcp-zotero — <https://pypi.org/project/mcp-zotero/> (package page)
5. ccam80/zotero-chunk-mcp — <https://lobehub.com/es/mcp/ccam80-zotero-chunk-mcp> (listing; secondary)
6. yilewang/llm-for-zotero — <https://github.com/yilewang/llm-for-zotero> (README; primary)
7. dralkh/seerai — <https://github.com/dralkh/seerai> (README; primary)
8. Grounded Q&A plugin announcement — <https://forums.zotero.org/discussion/132311> (author post)
9. PapersGPT — <https://www.sourcepulse.org/projects/1834154> (listing; secondary)
10. lucasastorian/llmwiki — <https://github.com/lucasastorian/llmwiki> (README; primary)
11. chris-santiago/ml-wiki — <https://github.com/chris-santiago/ml-wiki> (README; primary)
12. zotero-keeper `llm-wiki-builder` skill — <https://skillsmp.com/skills/u9401066-zotero-keeper-codex-skills-llm-wiki-builder-skill-md> (skill listing; secondary)
13. takapi-s/LiteratureGraphContext — <https://github.com/takapi-s/LiteratureGraphContext> (README; primary)
14. local-rag-stack — <https://pypi.org/project/local-rag-stack/> (package page; primary)
15. iMoonLab/Hyper-RAG — <https://github.com/iMoonLab/Hyper-RAG> (README; primary)
16. Zotero AI plugin roundups — <https://coursiv.io/blog/zotero-ai>, <https://casrai.org/compare/best-zotero-ai-plugins> (secondary; Zotero release notes not checked)
17. WeAgentAI/LLM-Wiki — <https://github.com/WeAgentAI/LLM-Wiki> (README; primary)
18. Karpathy LLM-wiki ecosystem coverage — <https://implicator.ai/karpathys-llm-wiki-got-16-million-views-the-code-isnt-the-point-2/> (news analysis; secondary)
19. Research assistant comparisons — <https://scientist-in-progress.pages.dev/blog/ai-literature-review-tools-scientists/>, <https://aicomparison.ai/notebooklm-vs-elicit/> (blogs; conflicting, low confidence)
