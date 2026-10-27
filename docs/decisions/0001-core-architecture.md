# ADR 0001: Local, evidence-backed wiki with interchangeable retrieval

Date: 2026-10-26. Status: accepted as the implementation baseline; exact dependencies remain subject to M0.

## Context and decision

Build a single-user core independently of desktop plugins and particular research domains. A normalized source adapter feeds versioned evidence, structured knowledge, and Markdown synthesis. Shared application services expose retrieval and controlled mutations to CLI, MCP, workers, and later plugins.

Use Python, SQLite/FTS5, and Markdown initially. Keep embedding, parsing, compilation, reranking, and graph implementations replaceable. Begin with an exact vector scan for small corpora, then measure before selecting ANN infrastructure. A specialized graph database is not required to represent hyperedges.

## Proposed module boundaries

```text
src/hyperwiki/
  domain/          identities, evidence, claims, scope, result contracts
  adapters/        normalized snapshots, export mapper, later live Zotero
  storage/         migrations, transactions, repository implementations
  processing/      extraction, chunking, jobs, fingerprints
  retrieval/       lexical, vectors, fusion, relations, budgets
  wiki/            compiler, references, maintenance, revision journal
  providers/       model/embedding/parser interfaces and implementations
  services/        application use cases shared by all entry points
  interfaces/      CLI, MCP, later local plugin transport
tests/             unit, integration, lifecycle and protocol fixtures
experiments/       manifests, runners, analysis, redistributable fixtures
integrations/      client examples and later packaged workflow skills
```

These paths are proposed, not implemented modules. Avoid speculative class hierarchies; add boundaries as the vertical slice needs them.

## Source and evidence contracts

The versioned snapshot manifest has `schema_version`, adapter/namespace identity, snapshot identity, completion/scope metadata, collections, and items. Items preserve source key, type, parent, bibliographic fields, tags, collection membership, notes/annotations, and attachment descriptors. A fixture uses synthetic content; a user snapshot is local/private.

Use `(namespace, library identity, item key)` as external identity and an internal stable UUID for references. DOI, title, aliases, and citation keys aid matching but are not primary IDs. Preserve parent and attachment identities separately. Duplicate detection must not silently merge different source versions.

Track adapter instance, sync checkpoint, and source revision separately from attachment byte hash. Do not compare local and Web API versions directly. The [Zotero local API](https://www.zotero.org/support/dev/web_api/v3/local_api) documents separate local version semantics and file locators; map each adapter into the normalized model.

Evidence spans have immutable IDs tied to attachment/extraction version, text or supported table representation, page index, optional printed page label/section/bounding box, extractor identity, and locator precision. Chunk offsets belong to a particular extraction artifact, not an unversioned PDF. Missing precision stays missing. Retain old versions for historical inspection but exclude them from current retrieval unless requested.

## Structured knowledge

Store sources, attachments, evidence, entities/aliases, source assertions, claims, typed relations, role-labelled participants, qualifications, dependencies, jobs, and revisions. Claim consolidation preserves individual support records and opposing/qualifying evidence. Multiple summaries of the same source are not independent support.

A hyperedge record has a relation type, participants with explicit roles, qualifiers, evidence, extraction/review status, and revision. Domain-specific observations extend this structure. Relational filtering and an equivalent graph with explicit relation nodes consume the same records for fair comparisons.

## Retrieval contract

`RetrievalRequest`: query, source scope, mode, freshness policy, maximum candidates, hops, fan-out, evidence tokens, and timeout.

`RetrievalResult`: ranked hits with evidence/claim/wiki IDs, source and extraction versions, locators, matched scope, ranking signals, known missing fields, freshness, coverage, truncation, and warnings. A wiki hit includes underlying source dependencies; source evidence remains independently retrievable.

Modes: `source`, `wiki`, `hybrid`, and experimental `relations`. Apply scope before search, expansion, reranking, and final evidence access. A generated synthesis containing out-of-scope dependencies must not leak through scoped search: exclude it, or return only verified in-scope material with an explicit partial-result marker. Do not treat filtering its citation list as sufficient.

No retrieval call implicitly compiles pages, saves answers, or modifies notes. Index caches may be operationally maintained, but a read cannot mutate authoritative content.

## Wiki ownership and recovery

Initial page types: paper, concept/topic, synthesis, index, and personal note. Frontmatter includes schema version, stable ID, page type, source/claim/evidence IDs, expected revision, generation fingerprint, and last verified state. Status in the database is authoritative for current freshness; offline Markdown shows its last verified snapshot.

Keep generated pages separate from personal notes. Detect a content-hash change before replacing a page. Preserve the changed file, create a proposal, and require revision-aware application. Do not introduce unrestricted text merging in the MVP.

Database commits and file renames are not one transaction. Stage content, validate references, record a pending revision/job, atomically replace the file, then finalize the DB revision. A journal records previous/new hashes and states. Startup reconciliation completes or reports interrupted operations without overwriting divergent human edits. Test crashes at each boundary.

On changes, invalidate dependencies first. Recompute remaining support and propose revisions later. Removed sources are tombstoned. A claim with valid independent support can survive; pages citing removed evidence become stale. Unchanged notes and unaffected claims retain their revisions.

## Jobs and concurrency

One worker holds leased jobs initially. Idempotency keys include input versions, operation, schema, provider/model, and configuration. Expired leases can be retried; committed outputs deduplicate. Use short DB transactions, bounded retries, cancellation, and explicit failure states. A process launching the MCP server does not silently launch a competing full-library worker.

Maintain separate metadata, evidence, structured-knowledge, and wiki coverage/freshness. A failed extraction must not prevent metadata discovery. Index rebuild uses committed evidence and does not repeat paid model extraction unnecessarily.

## Agent surface

First read tools: `search_sources`, `read_evidence`, `search_wiki`, `read_wiki_page`, `get_status`; then `inspect_claim`, `explore_relations`, and optional domain comparisons. Mutation tools: `sync`, `propose_revision`, `apply_revision`, and `record_note`. Applying a revision requires its ID and expected current revision.

Expose the same operation semantics through JSON CLI and stdio MCP. Send logs to stderr; reserve stdout for the selected protocol. Return bounded structured results and stable error codes. Package skills to teach search → read → verify → cite and propose → inspect → apply workflows; client configuration and discovery differ by host.

[Official OpenAI skill guidance](https://learn.chatgpt.com/docs/build-skills) and [Claude Code skill guidance](https://code.claude.com/docs/en/skills) are integration references. They do not replace backend authorization, evidence checking, or revision rules. Validate both hosts before claiming support.

## Consequences and deferred choices

Benefits: portable wiki, offline development, one implementation of lifecycle rules, and controlled retrieval experiments. Costs: maintaining alignment among DB, indexes, and editable files; extraction/compilation can dominate runtime and spend.

M0 chooses parser, exact provider versions, export mapping, and packaging. Later measurements choose ANN storage, hypergraph default, and plugin transport. Provider credentials remain local configuration. A generation provider is required for actual LLM wiki compilation; deterministic fakes establish contracts only.
