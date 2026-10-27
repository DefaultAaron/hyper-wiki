# Hyper-Wiki product scope

Date: 2026-10-26. Status: working scope following the user's clarification.

## Purpose and delivery order

Build an RAG-based LLM wiki that assists postgraduate students with research. Turn an evolving literature library into persistent, navigable knowledge while retaining inspectable evidence.

1. Core wiki and (hyper-)RAG backend ingesting a Zotero-like library.
2. MCP, CLI, and workflow skills for Codex and Claude Code.
3. Zotero plugin reusing that backend.
4. Optional Obsidian plugin if ordinary Markdown use leaves a demonstrated gap.

A minimal CLI is also the core's development/evaluation harness. Polished agent packaging follows the core. No previous example research domain is an implementation requirement.

## Workflows

- Find papers, passages, definitions, methods, and results.
- Explain a paper with citations and explicit limitations.
- Build and browse paper, concept, topic, and synthesis pages.
- Discover connections across papers and inspect their evidence.
- Draft literature syntheses and comparisons while retaining qualifications and disagreement.
- Maintain knowledge as sources and researcher understanding change.
- Save personal notes and hypotheses separately from published findings.

Comparison tables are one workflow within the broader wiki, not the defining product scope.

## Initial product contract

One local user and one library namespace. Preserve item hierarchy, attachments, notes, annotations, collections, and tags. Entire-library discovery/import is the default; selected papers and collections are retrieval scopes. Schedule costly processing in visible bounded batches with coverage reporting.

Maintain three coordinated representations: versioned source evidence, structured entities/claims/relations, and portable Markdown synthesis. Generated factual statements should resolve to evidence or be explicitly marked as unsupported interpretation. Missing PDFs, unknown conditions, extraction failures, stale pages, and conflicts remain visible.

Source assertions, synthesis, and personal hypotheses are distinct. Generated wiki prose never becomes independent scientific evidence. The system must be able to report insufficient evidence and does not certify scientific truth or global research novelty.

Generated content and personal notes have separate ownership. External edits trigger conflict handling. Retrieval is read-only; proposing or applying changes is explicit.

## MVP boundary

Include normalized snapshots, supported text/digital-PDF extraction, incremental lifecycle, hybrid retrieval, cited wiki pages, bounded relation retrieval, and agent operations. Lexical retrieval can ship internally before vectors, but the hybrid milestone requires both.

Domain-specific observation fields are extensible. Defer multi-user accounts, cloud hosting, Zotero write-back, a custom editor, unrestricted bidirectional merging, and comprehensive figure/equation/OCR interpretation. Retain metadata for unsupported attachments and expose failures.

## Success

Product: faster verified research work without materially worse correctness or understanding. Engineering: reproducible ingestion, traceable evidence, safe updates, portable wiki, and usable agent contracts. Research: a specific hypothesis supported against matched baselines with uncertainty and failure analysis.

Hypergraph retrieval is planned and evaluated behind a replaceable interface. If simpler methods match it, retain the useful wiki and narrow the research claim. No measured benefit or novelty is established.

See [development](DEVELOPMENT_PLAN.md), [feasibility](docs/FEASIBILITY.md), and [experiments](docs/plans/EXPERIMENT_PLAN.md). The [original proposal](docs/archive/PRODUCT_DESIGN.initial.md) is historical.
