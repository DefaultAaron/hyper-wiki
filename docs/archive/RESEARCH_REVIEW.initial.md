# Hyper-Wiki: LLM wiki and RAG research review

Research date: 2026-10-26
Status: Desk research and architecture recommendations; no benchmark reproduction.

## 1. Executive finding

The proposed product remains reasonable: a Zotero-sourced, continuously maintained research wiki for Claude Code and Codex, followed by a Zotero plugin and optionally an Obsidian plugin.

Use a linked wiki to accumulate synthesis, structured claims and hyperedges to preserve relationships, and original document evidence to ground answers. Retain multiple retrieval paths rather than requiring every question to traverse a hypergraph.

The review supports investigating the combination. It does not establish that Hyper-RAG will outperform strong hybrid retrieval or agent-driven wiki traversal on scientific papers.

## 2. Scope and evidence limitations

Reviewed primary papers, official repositories, and official documentation for LLM-wiki approaches, Hyper-RAG, and several related retrieval systems. Some source files were accessible through web retrieval, but cloning the iMoonLab repository failed because the execution environment could not resolve github.com. The implementation was not installed or executed.

Repository documentation describes intended capabilities; it is not proof of runtime correctness. Repository branches are mutable and were not pinned to commit hashes. Before implementation, record commits, dependency versions, configuration, and licenses for each reused component.

The detailed iMoonLab method discussion below draws on its earlier arXiv manuscript. The publication page establishes the later Nature Communications publication; differences between manuscript, final publication, and current code still require verification.

Statements labelled as recommendations or proposed designs are project judgments, not findings demonstrated by the cited work.

## 3. LLM-wiki references

### 3.1 Karpathy's LLM Wiki pattern

Source: https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f

The pattern separates preserved raw sources, a persistent interlinked Markdown wiki, and agent instructions describing structure and workflows. Ingestion updates existing knowledge; querying can produce reusable synthesis; periodic linting checks wiki health. Content indexes and chronological logs serve different navigation needs.

Recommended adoption: separate paper, concept, and comparison pages; maintain indexes and ingestion logs; provide AGENTS.md and CLAUDE.md conventions; implement maintenance checks.

Qualification: this is an architectural idea, not evidence that a wiki alone guarantees factuality, completeness, or scalability. For Hyper-Wiki, source retrieval should remain available when a compiled page omits needed detail.

### 3.2 LLM-Wiki: Retrieval as Reasoning

Paper: https://arxiv.org/abs/2605.25480

Official repository: https://github.com/WeAgentAI/LLM-Wiki

The work compiles linked pages, lets an agent compose search/read operations, and maintains an Error Book for recurring compilation errors. The authors report improvements on multi-hop benchmarks; those results do not directly establish scientific-literature performance.

Recommended adoption: expose small composable tools, support both search-first and browse-first navigation, and persist maintenance findings. Start with deterministic schema/reference checks before adding semantic correction.

Qualification: generated repairs require source checks; a correction loop does not independently establish truth. Human edits and personal hypotheses need protection from automatic repair.

### 3.3 Concrete retriever implementation

Source: https://github.com/WeAgentAI/LLM-Wiki/blob/main/llm_wiki_bench/wiki_retriever.py

The reviewed implementation prioritizes page names, aliases, tags, and descriptions and supplements these signals with BM25. Search returns candidate metadata; reading exposes page content and links.

Implication: agent-friendly wiki search need not rely exclusively on embeddings. Exact scientific identifiers and aliases deserve strong retrieval signals.

### 3.4 WiCER: compilation loss

Paper: https://arxiv.org/abs/2605.07068

This preprint investigates facts lost during wiki compilation and proposes diagnostic probes that guide revisions. Its reported outcomes are specific to its experiments, but the failure mode is directly relevant to summaries of scientific work.

Recommended adoption: evaluate whether important settings, results, exceptions, and negative findings remain recoverable after compilation. Measure wiki consistency separately from answer quality.

## 4. iMoonLab Hyper-RAG

Repository: https://github.com/iMoonLab/Hyper-RAG

Publication: https://www.nature.com/articles/s41467-026-71411-1

Earlier manuscript: https://arxiv.org/html/2504.08758v1

### 4.1 Method

The manuscript describes entity and pairwise/higher-order relation extraction, vector representations of entity/relation descriptions, and hypergraph connectivity. Queries retrieve entities and relations, expand to connected structures, rank within a context budget, and recover original source chunks. It identifies cross-chunk relation extraction as a challenge.

Recommended adoption: retrieve through both entities and relationships; use controlled expansion; ground answers in original evidence. Do not treat generated descriptions as sufficient scientific evidence.

### 4.2 Repository observations

The README documents hyper, hyper-lite, and naive modes and an API/demo workflow. The requirements include hypergraph-db and nano-vectordb. The repository is labelled Apache-2.0.

Sources:

- https://github.com/iMoonLab/Hyper-RAG/blob/main/requirements.txt
- https://github.com/iMoonLab/Hyper-RAG/blob/main/service_api.py
- https://github.com/iMoonLab/Hyper-RAG/blob/main/LICENSE

Its documented efficiency comparison measures local retrieval and prompt construction, not complete user-visible response latency. Treat reported gains as experiment-specific, not an expected improvement for Hyper-Wiki.

### 4.3 Suitability decision

Recommendation: use as a reference and experimental adapter before choosing it as a permanent backend dependency. Validate the following requirements rather than assuming they are present or absent:

| Requirement | Validation task |
|---|---|
| Incremental updates | Test insertion, replacement, deletion, idempotency, and recovery |
| Source traceability | Inspect chunk identities, document versions, and location fidelity |
| Experimental qualifiers | Determine whether roles and protocols survive extraction/retrieval |
| Agent use | Return structured evidence without requiring internal answer generation |
| Global library queries | Apply metadata scope consistently across all retrieval stages |
| Wiki maintenance | Map source changes to affected claims and pages |
| Installation | Verify dependency compatibility and operation on the target machine |

## 5. Broader retrieval references

| Reference | Documented idea | Proposed use in Hyper-Wiki |
|---|---|---|
| HyperGraphRAG | Alternative hypergraph RAG implementation | Compare representation and retrieval choices |
| Microsoft GraphRAG | Basic, local, global, and DRIFT retrieval | Route exact questions and broad corpus surveys differently |
| LightRAG | Graph retrieval, incremental updates, selective deletion, citations | Study lifecycle management and affected-knowledge regeneration |
| HippoRAG / HippoRAG 2 | Associative retrieval using graph connections | Test discovery of indirectly related evidence |
| RAPTOR | Retrieval through recursively constructed summaries | Navigate topic, paper, section, and evidence levels |
| PaperQA2 | Scientific evidence gathering and cited answers | Benchmark the paper QA workflow |
| QMD | Local Markdown hybrid retrieval, reranking, CLI, and MCP | Consider wiki-search integration or use as a baseline |

### 5.1 HyperGraphRAG

Official repository: https://github.com/LHRLAB/HyperGraphRAG

Publication: https://proceedings.neurips.cc/paper_files/paper/2025/hash/df55ee6e59f8ac4a625219e11fe9ddba-Abstract-Conference.html

This is a separate project from iMoonLab Hyper-RAG. Its repository provides construction/query examples and is labelled MIT. Comparing implementations would help distinguish a representation benefit from implementation-specific behavior.

### 5.2 Microsoft GraphRAG

Documentation: https://microsoft.github.io/graphrag/

Global search: https://microsoft.github.io/graphrag/query/global_search/

GraphRAG separates baseline, entity-oriented, and broader retrieval modes. Global search uses community reports. This suggests a dedicated overview path for questions about themes across a collection rather than applying the same retrieval process to every query.

Important distinction: indexing the entire Zotero library is a corpus-scope choice. It does not mean every query must use GraphRAG-style global search.

### 5.3 LightRAG

Repository: https://github.com/HKUDS/LightRAG

The documentation describes selective document deletion with affected graph regeneration, citations, mixed retrieval, and multimodal processing. These are useful lifecycle and ingestion references as well as retrieval features.

Validate behavior against a pinned version; do not infer operational maturity solely from feature listings.

### 5.4 HippoRAG

Repository: https://github.com/OSU-NLP-Group/HippoRAG

The framework emphasizes associative knowledge retrieval; Personalized PageRank is part of its graph-based approach. It offers a useful alternative to explicit hyperedge expansion for questions requiring intermediate connections.

Recommendation: consider as a later evaluation baseline rather than adding another traversal algorithm to the MVP immediately.

### 5.5 RAPTOR

Paper: https://arxiv.org/abs/2401.18059

RAPTOR retrieves across a recursively constructed summary tree. The useful idea is access at different abstraction levels.

Recommendation: borrow hierarchical navigation without assuming that a strict tree is the best representation for overlapping research topics. Summaries should retain evidence links.

### 5.6 PaperQA2

Repository: https://github.com/Future-House/paper-qa

Documentation source: https://github.com/Future-House/paper-qa/blob/main/README.md

PaperQA2 targets scientific documents and evidence-based cited answers. Its documentation notes that text-centred retrieval can miss relevant images or tables when associated text does not retrieve well.

Recommendation: evaluate table-heavy and figure-dependent questions explicitly. Avoid promising comprehensive scientific content support from text extraction alone.

### 5.7 QMD

Repository: https://github.com/tobi/qmd

QMD documents local lexical/vector search, reranking, CLI, and MCP access for Markdown collections.

Recommendation: evaluate it as a wiki-search component or baseline. Keep authoritative paper provenance and structured claims outside any search component whose purpose is indexing readable pages.

## 6. Proposed architecture after the review

Maintain three coordinated representations:

| Representation | Responsibility |
|---|---|
| Original evidence | Extracted passages/tables, document versions, locators |
| Structured knowledge | Qualified claims, observations, entities, hyperedges |
| Markdown wiki | Readable explanations, synthesis, comparisons, questions |

Connect them through stable evidence and claim IDs. A wiki is an editable synthesis layer, not independent confirmation of its sources.

### 6.1 Qualified scientific observations

Use explicit roles rather than an unlabelled set of entities:

```yaml
observation_id: obs-example
method: null
dataset: null
task: null
split: null
input_configuration: null
metric: null
value: null
evaluation_protocol: null
evidence_ids: []
```

This is a proposed schema, not the reference repositories' native format. Unknown fields remain unknown. Multiple pages repeating a finding must not count as independent evidence. Track each supporting source assertion separately before consolidating a claim.

A relational observation table or a graph with explicit observation nodes can encode these roles too. The presence of several participants alone does not demonstrate a need for a specialized hypergraph database.

### 6.2 Query paths

- Lookup: metadata plus lexical/semantic evidence retrieval.
- Relational: qualified observations and bounded hypergraph expansion.
- Overview: wiki/topic navigation followed by supporting evidence.

Expose composable search, read, evidence, comparison, and relation tools through MCP. Let Claude Code/Codex perform their own reasoning. A mandatory second answer-generating agent inside the backend would add cost and obscure evidence access.

### 6.3 Maintenance

Add a persistent maintenance ledger for broken evidence references, stale claims, unresolved entities, missing qualifiers, conflicts, and compilation omissions. Begin with deterministic validation; add model-based checks with evidence and reviewable revisions.

Keep global incremental ingestion. Use source-to-evidence-to-claim-to-page dependencies for refresh and deletion. Separate human notes and hypotheses from paper-supported findings.

## 7. Evaluation additions

Use one corpus snapshot, the same answer model, and comparable evidence budgets. Separate offline ingestion costs from online query costs.

Primary variants:

1. Strong hybrid source retrieval.
2. Agent traversal over the wiki with source access.
3. The same system with hypergraph retrieval enabled.

Add PaperQA2 and a graph-based approach as practical comparative baselines when resources permit. Include the iMoonLab implementation as a reference configuration rather than claiming reproduction from a simplified adaptation.

Evaluate exact settings, table lookup, cross-paper comparisons, multi-hop discovery, conflicting findings, unsupported questions, and source modifications/removals. Measure answer correctness, citation support, evidence recall, preserved experimental conditions, abstention, freshness, latency, tokens, and update cost. Audit core results manually.

Control for additional agent steps: if the hypergraph variant receives more searches or tokens, report that difference. Include failures and cases where simpler retrieval wins.

## 8. Changes recommended for the development plan

1. Keep the agreed phase order: agent wiki, Zotero plugin, optional Obsidian plugin.
2. Establish source provenance and incremental lifecycle before sophisticated traversal.
3. Separate retrieval from answer generation.
4. Implement source search and wiki search as distinct capabilities with a shared evidence interface.
5. Add a maintenance ledger and compilation-preservation checks.
6. Prototype the iMoonLab backend behind an adapter; choose dependencies after testing.
7. Benchmark hypergraph retrieval against strong non-hypergraph alternatives.
8. Pin reviewed references and verify license/dependency requirements before code reuse.

## 9. Open questions for the technical spike

- Which Zotero API capabilities and attachment paths are available on the target installation?
- Which PDF parser preserves useful section/table/page locators on the selected corpus?
- Can the reference backend expose retrieval results independently from generation?
- How are entity aliases, acronym ambiguity, and shared datasets handled across research domains?
- Can source removal retract support without destroying claims supported elsewhere?
- Which wiki edits can be regenerated safely, and which require review?
- What measurable query classes benefit from hypergraph retrieval?

## 10. Positioning

Proposed product identity: a Zotero-backed research memory shared by researchers and coding agents, with inspectable evidence and incremental maintenance.

The reviewed sources do not establish that the project is the first of its kind. Open-source usefulness can come from integration, reliability, and reproducible evaluation. Any academic novelty claim requires a wider literature review and controlled experiments.
