# Hyper-Wiki experiment plan

Date: 2026-10-26. Status: prospective protocol; no results yet. Targets are provisional until a pilot, then frozen before held-out evaluation. Product release and academic contribution are separate decisions.

## 1. Questions and hypotheses

- H1, wiki value: persistent cited synthesis improves multi-paper research tasks beyond strong source retrieval at comparable online evidence budgets.
- H2, structure value: qualified relation retrieval improves connected/qualified questions beyond the same wiki/source system without traversal.
- H3, lifecycle value: dependency-driven invalidation and targeted revision retain correctness with less reprocessing than full regeneration and fewer stale outputs than ordinary page refresh.
- H4, product value: students complete verified research tasks faster without materially worse artifact correctness or understanding.

H2 does not assume a specialized hypergraph database is needed. A benefit shared with relational filtering or an observation-node graph is a structured-knowledge benefit unless a distinct traversal advantage is demonstrated.

## 2. Corpus and annotation

Start with approximately 8–12 licensed/synthetic documents for parser/lifecycle debugging, then a pilot of 30–50 authorized papers from a researcher-selected topic. Do not reuse the retired example domain as a requirement. Add a second distinct topic before generalization claims.

Include born-digital prose, tables, multi-column layout, repeated acronyms, papers citing the same original result, conflicting reports, revisions, missing attachments, notes, and annotations. Include scanned/figure-dependent material to measure unsupported coverage; do not silently remove it from the denominator. Record languages and supported-document policy.

Use a small development set to tune chunking, prompts, schemas, thresholds, and retrieval. Split paper families/versions across development and held-out sets so near duplicates do not leak. Build each held-out retrieval index from its allowed source set; lock questions and labels away from tuning. Report whether evaluation is isolated or uses a full realistic library with distractors. No gold answer or question text may enter wiki compilation or graph extraction.

Pilot question pool: about 60 questions, approximately ten each for lookup, paper explanation, cross-paper synthesis, connected discovery, qualified comparison/conflict, and insufficient-evidence/coverage cases. This is an exploratory size, not a power calculation. Reserve a held-out subset before tuning. Use matched numbers of tasks per variant and label which question types actually require relations.

Annotation records: query ID/type, scope, answerability, required answer elements, acceptable alternative evidence, source spans/locators, qualifications, prohibited overclaims, and relevant update dependencies. For wiki compilation, annotate a smaller set of facts/conditions that must remain recoverable. For relations, annotate role participants and evidence on a manageable subset.

Have a second knowledgeable reviewer independently label at least 20% plus all disputed/critical cases; report agreement and adjudication. A single-annotator development run is allowed but labelled accordingly. Gold means adjudicated reference, not scientific truth.

## 3. Experiment ladder

| Experiment | Runs when | Variants and purpose | Output |
|---|---|---|---|
| E0: ingestion/parser | M0–M1 | Two parser candidates plus manually checked spans | Capability, extraction fidelity, failure/coverage report |
| E1: source retrieval | M2 | FTS only; vector only; fusion; fusion + reranker | Strong default and evidence recall/cost curves |
| E2: wiki contribution | M2/M5 | Strong source baseline; wiki only; wiki + source | Incremental value and compilation loss |
| E3: structure contribution | M3/M5 | Wiki + source; relational filtering; equivalent observation-node graph; hyperedge traversal | Gold-input and end-to-end graph decision |
| E4: evolving evidence | M1 onward | Stale/no refresh control; ordinary page refresh; full regeneration; dependency-targeted revision | Correctness, edit preservation, changed outputs, costs |
| E5: agent/product | M4–M5 | Client protocol/smoke checks; then current workflow, strong source agent, full system | Host usability and exploratory student benefit |

Optional external baselines: pinned PaperQA2 and iMoonLab Hyper-RAG if installation, corpus access, and matched configurations permit. Do not describe an adapter or modified method as a reproduction. Defer broad leaderboard reproduction until the core questions have useful answers.

## 4. Fair comparisons and ablations

Hold corpus bytes, extraction artifacts, answer model/version, system instructions, query scopes, output limits, maximum tool calls, and evidence-token limits fixed within each matched experiment. Pin compiler/extractor/embedding/reranker versions and seeds where supported. A wiki/graph variant may have extra offline processing; account for that separately rather than calling it free.

Run retrieval-only evaluation before full agent evaluation. Use one neutral answer harness with matched tools for primary backend comparisons; run each real client separately as a deployment experiment because client prompts and models may differ. Do not pool client effects with backend effects.

For E2 use identical source extraction. Compare source-only and wiki-assisted performance while recording total offline compilation cost. For E3 every structured variant consumes identical records. First use adjudicated records to isolate retrieval, then automatically extracted records to measure attainable benefit. Compare role/qualifier removal and traversal-off variants. A pairwise projection that loses information is not the only graph baseline: include explicit observation nodes that preserve the full relation.

For E4 keep prompts/models/inputs matched and replay exactly the same ordered events. Full regeneration receives the same ownership protections; user edits lost by a deliberately unsafe baseline do not establish an algorithmic advantage.

Use a fixed primary budget chosen in the pilot, for example 8,000 evidence tokens and eight read/search calls; these are starting configurations, not universal recommendations. Repeat selected conditions over a small budget curve and at least three stochastic answer runs when affordable. Report actual usage and truncated results.

## 5. Measurements

| Layer | Primary measurements | Important denominator/detail |
|---|---|---|
| Import/extraction | Metadata/hierarchy preservation; span text/locator accuracy; coverage and failures | All inventoried sources, including unsupported/missing files |
| Retrieval | Annotated evidence recall@budget; ranking quality where labels allow; scope violations | Acceptable alternative evidence adjudicated, not string matches alone |
| Answers | Required-element correctness; supported factual-claim fraction; critical errors; abstention precision/recall | Separate source citations from citations that actually support a claim |
| Wiki | Retained required facts/conditions; citation validity; duplicate support; stale assertions | Wiki is evaluated independently from final answer fluency |
| Structure | Role/qualifier precision/recall; relation evidence correctness; connected-task performance | Gold and automatic structures reported separately |
| Updates | Affected-output recall; false invalidation; active unsupported claims; preserved edits; revision scope | Report no-op events as well as real changes |
| Cost | Ingestion/compile/update/query time, calls, tokens, memory, disk; p50/p95 query latency | Cold/warm cache, hardware, failures, actual provider rates/date |
| Student task | Total time through verification/correction; artifact score; source-location/explanation check | Include corrections and verification, not draft time alone |

Use a defined scoring rubric with critical-error examples before rating outputs. Blind variant labels during human review where practical. Automated judges may assist triage but cannot be the sole basis for core conclusions.

## 6. Lifecycle fixtures

Replay: add a paper; edit metadata/tags only; add an annotation; replace attachment bytes; correct one reported value; add a conflicting claim; remove a source with no other support; remove one of multiple supporting sources; restore a source; change extraction/model schema; edit a generated page externally; interrupt a job/file write; rebuild indexes; import a partial snapshot; change an unrelated source.

For each event, specify expected dirty evidence/claims/pages, allowable revisions, surviving support, retained notes, and job counts. Absence from a partial snapshot is not deletion. Only an explicitly complete reconciled scope or authoritative deletion event can tombstone missing items.

Strict engineering gates: zero out-of-scope evidence in controlled fixtures; zero overwritten human edits; no silently current citation to withdrawn evidence; repeated imports/no-op updates produce no duplicate committed outputs; recovery converges after injected failures. These are fixture checks, not claims that all real-world errors are impossible.

## 7. Pilot and decision rules

Provisional engineering/product decisions, frozen after pilot and before held-out runs:

- Source/wiki default: no increase in critical-error rate on the held-out paired task set; report uncertainty and per-task failures. If the sample cannot rule out harm, retain an experimental label.
- Hypergraph promotion: initially seek at least 5 percentage points improvement in evidence recall at the fixed budget on relation-requiring tasks, without worse critical errors, and within a predeclared cost envelope such as at most 2× query latency and total processing spend. Compare with the strongest structured baseline, not just source-only. These thresholds express a product tradeoff, not statistical significance.
- If the gain is equivalent to relational filtering, use the simpler implementation by default. If gold structures help but automatic structures do not, improve extraction or restrict the feature rather than claim an end-to-end win.
- Lifecycle: pass strict fixture gates; targeted revision should show lower changed-output/reprocessing cost than full regeneration at equivalent correctness. If it only flags stale content safely, release that limited behavior explicitly.
- Student pilot: aim for approximately 20% lower median verified-task time without worse critical errors or understanding. This is a design target, not a validated noninferiority margin. A confirmatory study needs a justified margin, power analysis, and a new frozen protocol.

E5 discovery: interview approximately 5–8 postgraduate students using recent real tasks. Controlled pilot: approximately 8–12 participants if available, counterbalanced condition order, different matched paper/task sets, and blinded artifact grading. Obtain appropriate consent and applicable institutional review before recruitment. Pilot estimates guide task design and sample-size planning; a convenience sample does not support broad population claims.

For backend results use paired differences and confidence intervals, resampling at a paper/task-family level where observations share sources. For user results analyze within-participant differences and report order effects. Small samples may yield wide intervals; report them. Select one primary metric per hypothesis, mark exploratory analyses, and publish negative cases.

## 8. Reproducibility and budget

Every run manifest records Git revision, corpus hashes/rights, split and question versions, schema/parser/model/provider versions, prompts, parameters, environment/hardware, budgets, seeds, and cache policy. Each result records query/scope, returned IDs, answer, annotations, calls/tokens, timings, costs, and failures. Store private traces outside tracked files; publish redacted aggregates and redistributable fixtures.

Before paid runs, estimate extraction, embedding, compilation, reranking, and answer spend separately. Configure a hard monetary/token cap, maximum documents/jobs, retries, and cancellation. Spend uncertainty must stop a run rather than produce unbounded work. No paid experiment is authorized merely by writing this plan.

Deliver command-based runners and machine-readable results, an analysis report with confidence intervals, per-task errors, and a default-backend decision. No results are filled in until runs exist. Cross-domain or novelty claims require additional evidence beyond this pilot and the earlier literature review.
