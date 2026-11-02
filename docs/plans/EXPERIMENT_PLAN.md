# Hyper-Wiki evaluation plan

Date: 2026-11-02. Status: plan; no results yet. Implements section 12 of the [product design](../../PRODUCT_DESIGN.md). Supersedes the 2026-10-26 experiment protocol (see Git history), whose academic-study elements (hypotheses for publication, ethics review, confirmatory statistics) no longer apply.

Hyper-Wiki is a development project. Evaluation answers practical questions: does it work reliably, is it better than what researchers already use, and does each advanced component earn its place? Report results honestly, including failures and cases where simpler tools win. Never fill in results before runs exist.

## 1. Reliability gates (strict, automated)

Run in CI with synthetic fixtures from M1 onward, extended as features land. Each event specifies expected dirty evidence, claims, relations, and pages; allowed revisions; surviving support; untouched notes; and job counts.

Events: add a paper; add its PDF shortly after its metadata; edit metadata or tags only; add a note or annotation; replace attachment bytes; correct one reported value; add a conflicting paper; trash then restore; permanently delete a paper with no other support; delete one of several supporting papers; exclude and re-include a collection; change extractor, model, or schema (rebuild); edit a generated page externally; edit a personal note; interrupt a job and a file write; rebuild indexes; import a partial snapshot; change an unrelated paper; switch provider kind; process the subscription queue with a valid and an invalid draft.

Pass means all of the following on every fixture:

- Zero overwritten human edits or personal notes.
- No current citation to withdrawn, replaced, or non-resolving evidence.
- No duplicates after repeated or no-op updates.
- Convergence after injected interruptions.
- No out-of-scope evidence at any retrieval stage.
- Absence from a partial snapshot never deletes anything.
- Nothing is written to Zotero.

These are fixture checks, not claims that real-world errors are impossible.

## 2. Component checks that choose defaults

Run on a small licensed or synthetic set during development, then confirm on the private library.

| ID | Milestone | Question | Variants | Decides |
|---|---|---|---|---|
| C1 extraction | M0 | Which extractor keeps reading order, page locators, tables, and Chinese text best? | Two or more extractors | Default extractor (ADR) |
| C2 search | M0–M1 | Which search setup finds the right evidence in English, Chinese, and across languages? | Keyword only; embeddings only; hybrid; hybrid plus reranker | Default search stack and tokenizer (ADR) |
| C3 providers | M2 | How much worse are pages from local models than from API models? | One API model; two local models; agent-written drafts via the queue | Which tasks local mode supports fully or labels experimental |
| C4 updates | M2 | Do targeted updates stay correct while doing less work than regenerating everything? | Targeted revision; full regeneration with the same ownership protections | Default update strategy; reported cost savings |
| C5 hypergraph | M3 | Does hypergraph retrieval beat hybrid retrieval on relation-heavy questions? | Hybrid; relational filtering over the same records; hypergraph traversal; pinned Hyper-RAG reference | Whether hypergraph becomes the default (ADR) |

Measures: evidence found within a fixed budget, whether cited pages actually support each claim, critical errors, time, tokens, and cost. Hold corpus, extraction outputs, answer model, instructions, and budgets fixed within each comparison; record versions.

Decision rules (starting points, adjustable before the confirming run):

- Hypergraph becomes the default if it finds at least 5 percentage points more of the required evidence on relation-heavy questions at the same budget, with no increase in critical errors and at most 2× query latency and cost. If relational filtering does as well, use the simpler method. If it only helps with hand-corrected relations, improve extraction or keep it optional.
- A local model is "fully supported" for a task if its page citation validity and required-fact coverage are within 10 percentage points of the API model; otherwise that task is labelled experimental in local mode.
- Targeted updates are kept only if they pass every reliability gate. Report reprocessing saved compared with full regeneration.

## 3. Head-to-head comparison (M4)

**Library:** about 30–50 papers in English and Chinese chosen by the user, kept private and out of Git. Include tables, multi-column layouts, repeated acronyms, papers that cite the same result, conflicting findings, a missing PDF, notes, and annotations.

**Questions:** about 40–60 real questions from the user's reading, roughly evenly spread across: finding a passage or result; explaining one paper; cross-paper synthesis; finding connections; comparing methods or conflicting results; questions the library cannot answer; and questions asked again after a library change (paper added, replaced, or removed). Write questions and reference answers with source pages before running any tool. Keep a few aside and do not tune on them.

**Contenders:** Hyper-Wiki; zotero-mcp with the same agent; llm-for-zotero; llmwiki fed the same papers; LiteratureGraphContext. Record versions and configurations; give every tool the same answer model where it allows one.

**Measures:**

- Answer correctness against the reference.
- Citation support: does the cited page actually support each claim?
- Critical errors: wrong values, fabricated citations, missed conflicts.
- Appropriate "not enough evidence" answers.
- Behaviour after library changes: stale answers, correct updates.
- Time to a checked answer, and cost.

Score manually against a rubric written beforehand, hiding which tool produced each answer where practical. Automated judges may help triage but never decide results alone.

**Success target:** Hyper-Wiki matches or beats the best contender on answer correctness and citation support, clearly beats all contenders on behaviour after library changes, and makes no more critical errors. These are product targets, not statistical claims; report per-question results and failures.

## 4. Real use (M2 onward)

The user, then a few labmates, use Hyper-Wiki for real reading. Keep a simple log: task, what worked, what failed, what was missing, time spent checking sources. Review it at each milestone and turn recurring problems into backlog items. Ask labmates for permission before logging their usage and keep logs private.

## 5. Reproducibility and budget

Every run records the Git revision, library and question versions (hashes, not contents), extractor, model, provider and prompt versions, parameters, hardware, budgets, and cache policy. Results record questions, returned evidence IDs, answers, scores, calls, tokens, time, cost, and failures. Keep private traces and library content outside Git; publish only aggregates and redistributable fixtures.

Before any paid run, estimate extraction, embedding, compilation, and answer costs separately and set a hard spending cap. A run that would exceed its cap stops rather than continuing. Writing this plan authorizes no spending.
