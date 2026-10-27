# Hyper-Wiki product and research design

Date: 2026-10-26
Status: Proposed design. Benefit and novelty are hypotheses to test.
Inputs: DEVELOPMENT_PLAN.md and RESEARCH_REVIEW.md in the project folder.

## 1. Design decision

Build a research evidence workspace that helps a postgraduate student compare findings, justify research decisions, and maintain those decisions as literature changes. A persistent wiki presents this knowledge; a qualified hypergraph supports retrieval and comparison; Zotero supplies sources.

The first target population is postgraduate researchers in empirical machine learning. Their papers often report datasets, experimental protocols, baselines, metrics, and ablations. Generalizing to other disciplines requires different schemas and user validation.

The first product promise is: turn a collection of papers into an inspectable comparison that tells a researcher which results can be compared, which conditions differ, and which evidence is missing.

Do not promise automatic scientific truth, comprehensive literature coverage, or reliable identification of globally novel research gaps.

## 2. Two success gates

### Gate A: Does it help students?

Measure time to produce a verified research artifact, correctness of the artifact, and the correction burden. Fluent summaries, number of wiki pages, graph size, and user enthusiasm alone are insufficient.

The system is useful if it reduces repetitive evidence gathering while preserving or improving the student's understanding and ability to verify conclusions. Measure understanding with brief explanation/source-location tasks; do not assume faster output means better learning.

### Gate B: Is there a substantive contribution?

Separate three possible contributions:

- Product contribution: researchers complete a recurring task more effectively.
- Engineering contribution: reusable source lifecycle, evidence schema, retrieval interfaces, and evaluation fixtures.
- Research contribution: a new or materially improved method whose advantage survives strong baselines and ablations.

Product value does not require a novel algorithm. A research claim requires more than combining existing wiki, graph, and plugin components. No first-of-its-kind claim is established by the current review.

## 3. Prioritized research workflows

| Priority | Researcher question | Persistent artifact | Success evidence |
|---|---|---|---|
| P0 | Which results are comparable for my task? | Qualified comparison table with supporting passages | Correct values, conditions, citations, and explicit missing fields |
| P0 | What supports this statement in my literature review? | Claim dossier with support, qualifications, and counterevidence | Student can inspect the original evidence quickly |
| P1 | Which experiment should I reproduce or use as a baseline? | Decision record with constraints and rationale | Recommendations trace to reported conditions and user constraints |
| P1 | Does this newly added paper change an earlier conclusion? | Evidence change report and proposed wiki revision | Relevant changes found without unrelated edits |
| P2 | What might be worth investigating next? | Candidate research questions with scoped evidence gaps | Clear distinction between missing local evidence and unstudied problems |

Deep explanation of an individual paper remains available through original evidence and agent interaction. It is not the first differentiation target.

## 4. First end-to-end demonstration

Use a small ECG classification collection as the development domain, then evaluate transfer to a separate empirical ML topic such as stereo depth.

Example task: identify suitable reduced-lead ECG baselines and explain which reported AUROC values are comparable.

1. Ingest the selected Zotero library globally and expose coverage/freshness.
2. Researcher states task, lead configuration, dataset constraints, and comparison objective.
3. Retrieve papers and extract observations with source locations.
4. Produce a table of method, task, leads, data split, preprocessing, training data, metric aggregation, reported value, and evidence.
5. Classify each proposed comparison: comparable under the declared criteria, conditionally comparable, incompatible, or insufficient information.
6. Provide reasons and missing conditions for each classification.
7. Researcher inspects evidence, corrects extraction if necessary, and saves a comparison page.
8. A new paper or source correction triggers a focused change report for that page and linked decisions.

Passing this demonstration requires accurate evidence handling, not simply generation of a plausible table.

## 5. Proposed substantive contribution

### 5.1 Primary hypothesis: condition-aware evidence retrieval

Generic semantic relevance may retrieve papers about the same topic without preserving the conditions needed for a valid comparison. Represent observations as role-qualified relations and use those conditions during retrieval, filtering, and answer construction.

Hypothesis H1: role-qualified retrieval reduces invalid comparisons at a matched evidence budget relative to hybrid source retrieval and agentic wiki traversal.

This is a candidate contribution, not established novelty. Compare with structured database filtering and graphs with explicit observation nodes; they may offer equivalent benefits with less complexity.

### 5.2 Secondary hypothesis: evidence changes propagated to research conclusions

Maintain dependencies from source versions to evidence, claims, comparison rows, wiki statements, and research decisions.

Hypothesis H2: targeted invalidation and revision preserves correctness after source changes with less reprocessing and fewer unrelated edits than full regeneration or ordinary page refresh.

Dependency tracking itself is established engineering. Research value would need a specific method for identifying affected conclusions, preserving unaffected support, and demonstrating measurable gains.

### 5.3 Evaluation contribution

Publish a reproducible benchmark of qualified comparisons and evolving evidence, with licensed fixtures, source-span annotations, uncertainty labels, and update events. Include extraction and retrieval failure analysis.

A well-designed benchmark could be useful even if hypergraph traversal does not win. Its distinctiveness must also be checked against existing scientific evidence and literature-review benchmarks.

## 6. Core knowledge model

Source assertion is the basic scientific unit: a paper reports a finding under stated conditions. It is not automatically a globally true fact.

Entities: paper, method, task, dataset/version, metric, experimental protocol, concept.

Observation relation:

- Stable observation ID and source-version ID.
- Method, task, dataset, split, input configuration.
- Preprocessing, external training data, supervision, and evaluation protocol when relevant.
- Metric name, aggregation, direction, value, units, and uncertainty when reported.
- Evidence references per field, not merely one citation for an entire row.
- Status: reported, extracted, human-corrected, unresolved, or superseded.

Claims reference observations and evidence. Syntheses reference claims. Decisions reference syntheses, user constraints, and alternatives. Personal hypotheses remain distinct.

Preserve disagreements. Separate independent replications from papers that quote the same original finding. A removed source withdraws its support; it does not automatically invalidate a claim supported by other sources.

## 7. Comparison behavior

Comparison criteria are explicit and task-specific. Start with deterministic checks for known mismatches and missing fields; use the LLM for interpretation with cited evidence.

Examples:

- Same dataset name with different splits: flag a protocol mismatch.
- Macro versus micro AUROC: flag a metric mismatch.
- Different input leads: allow a tradeoff comparison but do not present it as a controlled architecture comparison.
- Unknown external training data: mark comparability unresolved.
- Different conclusions under different conditions: do not label a contradiction automatically.

Confidence scores alone do not resolve uncertainty. Show which field is missing or disputed and why it affects the comparison. The system must support abstaining from ranking methods.

## 8. Retrieval and agent contract

Three coordinated representations: original evidence, structured observations/claims, and Markdown synthesis.

Suggested tools:

- search_sources(query, scope): discover original evidence.
- read_evidence(ids): inspect supporting spans and locators.
- search_wiki(query): discover reusable synthesis.
- compare_findings(criteria, scope): return rows, compatibility classifications, and evidence.
- inspect_claim(id): inspect support, counterevidence, and qualifications.
- trace_changes(since, target): show affected claims/pages/decisions.
- propose_revision(target, evidence_ids, expected_revision): create a reviewable update.

Return structured results with evidence IDs, known missing fields, freshness, and scope. Existing agents perform the user-facing reasoning. Retrieval has no implicit write side effects.

Hypergraph expansion starts with relevant entities or observations, preserves participant roles, and respects source scope, degree limits, hop limits, and token budgets. Ordinary lookup does not require traversal.

## 9. Source and wiki lifecycle

Global Zotero ingestion remains the default. Track metadata, evidence, structured knowledge, and wiki freshness separately. Surface unavailable or failed sources.

Maintain field-level evidence links where practical and explicit dependency edges. On update, identify potentially affected outputs, evaluate remaining support, then propose the smallest justified revision. Record unresolved cases rather than silently rewriting conclusions.

Markdown remains portable. Generated pages and personal notes have explicit ownership rules. Preserve edits and use revision checks. Zotero/Obsidian interfaces are clients of the same core services.

## 10. Evaluation design

### 10.1 Product discovery

Conduct a small exploratory study with approximately 5–8 postgraduate researchers. Ask participants to demonstrate recent comparison and literature-review tasks using their own material. Record where time is spent, what they verify, and which mistakes matter. This convenience sample informs design; it is not representative evidence of broad benefit.

### 10.2 Controlled pilot

Use counterbalanced tasks with different but matched paper sets to reduce learning effects. Compare current workflow, a strong agent with source retrieval, and Hyper-Wiki. Record total time including corrections and source verification, artifact correctness, missing evidence, and a short understanding check. Blind artifact scoring where practical.

### 10.3 Technical baselines and ablations

- Hybrid lexical/vector source retrieval with reranking.
- Agent-driven wiki traversal with source access.
- Structured observation records with relational filtering, without hypergraph traversal.
- Equivalent observation-node graph representation.
- Hyper-Wiki with role-qualified hypergraph retrieval.

Ablate qualifiers, expansion, wiki synthesis, and dependency-based updates separately. Keep corpus, parser outputs, extraction budget, answer model, agent tool budget, and context budget comparable. Run both gold-structured-input and end-to-end extraction conditions to isolate algorithm benefit from extraction errors.

Hold out papers and questions used for final evaluation. Test a second domain before making cross-domain claims. Record latency, model calls, token cost, ingestion cost, and update cost.

### 10.4 Evolving-evidence scenarios

Add a paper, correct a table value, replace a manuscript version, remove a source, and add conflicting findings. Include changes that should not affect an existing conclusion. Score affected-output recall, false invalidations, stale unsupported claims, preserved edits, and processing cost.

### 10.5 Provisional decision gates

Targets below are design goals, not measured results or statistical guarantees. Final thresholds and sample size should be set after pilot variance estimates and before the confirmatory evaluation.

- Product: aim for at least 25% less time to a verified comparison without worse critical-error rate or understanding.
- Retrieval: aim for a meaningful reduction in invalid comparisons versus the strongest matched baseline; report effect size and uncertainty rather than wins alone.
- Lifecycle: no silently retained unsupported claims in the controlled update fixtures, with explicit handling of unresolved updates.
- Complexity: retain hypergraph traversal only if its benefit justifies measured maintenance and query cost.

If structured filtering matches the hypergraph system, keep the useful product and narrow the research claim. If verification consumes the time saved, improve extraction or reduce scope before building plugins.

## 11. Implementation sequence

1. Define the observation schema and annotate a small paper set manually.
2. Implement the comparison artifact and evidence inspection using curated records.
3. Test task usefulness with researchers before expensive automatic ingestion.
4. Automate Zotero ingestion and extraction while measuring errors against curated records.
5. Add the wiki and agent tools.
6. Implement hypergraph retrieval behind the same interface and run ablations.
7. Add dependency-based revision and evolving-evidence tests.
8. Broaden the domain and publish reproducible evaluations.
9. Start the Zotero plugin after the usefulness gate; consider Obsidian afterward.

This sequence complements the engineering milestones in DEVELOPMENT_PLAN.md. It moves task validation and baseline evaluation earlier. No implementation or user-study result is claimed by this design document.

## 12. Contribution boundary

Adapters, MCP transport, Markdown rendering, and plugins are enabling infrastructure. The candidate core contribution is preserving experimental conditions and evidence through comparison, synthesis, and change.

Before a publication claim, extend the literature review to scientific claim verification, evidence synthesis, experiment extraction, comparative evaluation, provenance, and incremental knowledge maintenance. The existing wiki/RAG survey is not sufficient to establish novelty in these areas.

Reference context: RESEARCH_REVIEW.md contains the reviewed papers and repositories. This document proposes design choices and experiments; it adds no claim of newly verified literature coverage.
