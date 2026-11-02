# Initial execution backlog

Date: 2026-10-26. All items are TODO. Priority reflects dependencies, not a claim that implementation is complete. Exact task size is revisited after M0.

## M0: first implementation batch

| ID | Task | Depends on | Done when |
|---|---|---|---|
| HW-001 | Python/uv package, lint/test setup, clean-run CI | — | Fresh supported interpreter runs a provider-free check with locked dependencies |
| HW-002 | Snapshot/schema/scope/evidence/wiki contracts | HW-001 | Versioned examples validate and unsupported schema versions fail clearly |
| HW-003 | Synthetic library fixtures | HW-002 | Parent/attachment/note/annotation/collection, missing PDF, duplicates, revisions, and partial snapshots covered |
| HW-004 | SQLite migrations and source repositories | HW-002 | Create/upgrade/reopen works with stable identity and foreign-key integrity |
| HW-005 | Snapshot adapter plus one export mapping | HW-003, HW-004 | Deterministic import/status; invalid paths and malformed records reported |
| HW-006 | Zotero capability spike | Independent; live access required | Target version/endpoints/files/checkpoints/deletions documented with observed or unresolved status |
| HW-007 | Parser bake-off | Licensed PDF subset | Two candidates scored for text/locators/tables, runtime, setup, and license |
| HW-008 | Provider interfaces and cost limits | HW-002 | Fakes work offline; explicit config and hard caps required for live calls |

## M1: first useful vertical slice

| ID | Task | Depends on | Done when |
|---|---|---|---|
| HW-009 | Attachment versions, jobs, leases, fingerprints | HW-004, HW-005 | Reimport/restart deduplicates committed results and resumes unfinished work |
| HW-010 | Evidence extraction and exact locator reading | HW-007, HW-009 | Text and digital-PDF spans resolve to versioned attachments; failure states visible |
| HW-011 | FTS and scoped source search/read | HW-010 | Fixture queries return source IDs/locators and exclude out-of-scope sources |
| HW-012 | Cited paper/index/synthesis compiler | HW-008, HW-010 | Live configured generation produces inspectable citations; fake-only output is labelled contract validation |
| HW-013 | Revision journal, notes, external edit checks | HW-004, HW-012 | Recovery after staged-write interruption; divergent user edits not replaced |
| HW-014 | Dependency invalidation and tombstones | HW-009, HW-013 | Removal/version change marks affected outputs; partial import never implies deletion |
| HW-015 | CLI vertical-slice demonstration | HW-011–HW-014 | Import → evidence → wiki → answer context → changed-source revision demonstrated |

## Subsequent batches

- M2: embeddings/fusion/reranking; source/wiki query paths; scope-safe synthesis; maintenance ledger; E1/E2 baseline runner; live adapter reconciliation after capability checks.
- M3: entities/typed relations/qualifiers; relational and observation-node controls; bounded hyperedge traversal; gold versus automatic extraction; E3 decision report.
- M4: JSON CLI contract, stdio MCP, client configuration examples, workflow skills, protocol tests, actual Codex/Claude Code smoke runs.
- M5: frozen experiment manifests, lifecycle replay, user pilot, backup/restore/rebuild checks, installation docs, license/dependency review, release report.
- M6: target-version Zotero plugin spike, local transport, workflow UI, compatibility and failure tests.
- M7: optional Obsidian gap assessment before implementation.

## External inputs and bounded fallbacks

| Needed input | Needed by | Work that can proceed without it |
|---|---|---|
| Authorized snapshot and research tasks | Real-corpus evaluation | Synthetic fixtures, schemas, ingestion, lifecycle |
| Live Zotero access | HW-006 and live sync | Snapshot adapter and core workflow |
| Generation/embedding provider and budget | Live compiler/hybrid evaluation | Deterministic contracts, FTS, no-cost failure tests |
| Target client versions/access | M4 host gate | Shared services and protocol tests |
| Participants/consent/review | Student benefit claims | Backend benchmarks and engineering preview |
| Project license | Public release | Local development and private evaluation |

When a batch completes, record commands, observed outputs, remaining failures, and the relevant experiment IDs. Do not mark acceptance based on a plan or mock alone.
