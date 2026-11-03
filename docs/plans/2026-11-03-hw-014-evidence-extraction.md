# HW-014 Evidence Extraction with Page Locators

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn every supported attachment version into evidence spans. Each span has a stable ID, faithful text, and a locator that resolves to one attachment version, one page, and character offsets in that page's stored text. Failures, unsupported files, and pages without a text layer appear in coverage instead of disappearing.

**Architecture:** This implements decision 1 of [ADR 0006](../decisions/0006-pdf-extraction.md) on the HW-012 job queue.
- **Planning:** an `extract.text` job is queued for each current, non-trashed, available attachment version of a supported type.
- **Extraction:** the job reads the file once, checks its SHA-256 against the version, and extracts page text with pypdfium2 (or the plain-text reader). It then cleans the text and cuts each page into spans.
- **Storing:** pages and evidence are written in the job's completion transaction.
- **Coverage:** each attachment's state is derived from the expected fingerprint of its current version, so a settings or extractor change shows up as pending work.

**Tech Stack:** Python 3.12, sqlite3, pydantic 2, pypdfium2 5.14.0 (new: BSD-3-Clause or Apache-2.0, bundles PDFium build 8076), pytest, mypy strict, ruff.

**Spec:** HW-014 in [BACKLOG.md](BACKLOG.md): "English and Chinese spans resolve to versioned attachments and pages; failures and unsupported files visible in coverage". Also ADR 0006 decision 1 and its open items, the evidence section of [ADR 0001](../decisions/0001-core-architecture.md), and the chunk size in [ADR 0005](../decisions/0005-bilingual-search.md). Section 6 of [PRODUCT_DESIGN.md](../../PRODUCT_DESIGN.md) says coverage is always visible.

**Branch:** `m1/hw-014-evidence-extraction`, from `main` (fdbc4fe), whose CI passed.

## Design decisions (builder's calls, recorded for review)

- **Supported types.**
  - **PDF** (`application/pdf`) is read through pypdfium2.
  - **Plain text** (`text/plain`, UTF-8) is read by a built-in reader. A form feed separates pages, as in the synthetic fixtures.
  - **Everything else is unsupported** and counted in coverage by content type. This covers HTML snapshots, EPUB, and images.
  - **Out of HW-014:**
    - OCR (ADR 0006).
    - Section locators. PDF outlines give section starts only at page precision, so section stays missing.
- **Docling tables** (ADR 0006 decision 2: "HW-014 decides how Docling tables sit beside pypdfium2 spans"). They will be a separate operation, `extract.tables`, with its own fingerprint, on the same attachment version.
  - Its rows go into the same evidence table with a different extraction ID. A later migration adds a `kind` column.
  - Switching tables on later queues only table jobs and never re-extracts text.
  - Building it is deferred; BACKLOG gets an M2 line.
- **What is fingerprinted.** The operation is `extract.text`, operation version 1, with schema version 1.
  - **Inputs:** `attachment_version` (the version ID) and `sha256`.
  - **Extractor:** for PDF, `Component(name="pypdfium2", version=<pypdfium2 version>, runtime="pdfium <build>")`; for plain text, `Component(name="hyperwiki.plaintext", version="1")`.
  - **Config:** `cleanup_version: 1`, `chunker_version: 1`, `chunk_chars: 800`, `min_page_chars: 20`.
  - **Effect:** any change re-queues extraction, per ADR 0006.
- **Evidence identity.**
  - An extraction is stored under its fingerprint ID. The locator's `extractor` field holds that ID (`fp_…`), the exact artifact that the offsets belong to (ADR 0001: "chunk offsets belong to a particular extraction artifact").
  - The readable extractor name and version are returned beside the locator.
  - The evidence ID is `make_evidence_id(locator)` from HW-002. The same bytes, extractor, and settings always give the same IDs, so a forced re-run is idempotent. A new PDFium build gives new IDs, as immutability requires.
- **Page text** comes from PDFium's `FPDFText_GetText`, the text HW-007 measured.
  - **Why not per-character lookups:** they disagreed with it on 263 of 354 sampled real pages. PDFium's character list holds markers and unmapped glyphs (U+0002, U+0003, U+0000) that its text output drops, and it returns U+0002 where the text has U+FFFE.
  - **Lone surrogates** are decoded as U+FFFD.
- **Cleanup version 1** follows ADR 0006, with one correction measured here:
  1. Convert `\r\n` to `\n`.
  2. **Remove U+FFFE**, PDFium's marker for a line-end hyphen, together with a line break directly after it, if there is one.
     - In PDFium 8076, `extrac-` at a line end followed by `tion` comes out as `extrac￾tion`, with no line break.
     - The ADR's "U+FFFE before a line break" rule would leave the marker inside the word. ADR 0006 is updated to match.
  3. Remove C0 control characters other than tab and newline. This includes a lone `\r`.
  4. Remove a `\n` between two Han characters (the ADR 0005 ranges).
  - Nothing else is rewritten.
  - Cleanup returns, for each cleaned character, its position in PDFium's text in UTF-16 units. Bounding boxes use this.
- **Pages.**
  - **Locator:** the page index is the locator.
  - **No text layer:** a page whose cleaned text has fewer than 20 non-whitespace characters is stored as having no text layer and gets no evidence.
  - **Page labels:** a page label is stored only when the PDF defines labels (at least one is non-empty) and they are not one constant value across a document of several pages. HW-007 found 4 PDFs with malformed label dictionaries that produce such a constant label.
- **Spans (chunker version 1).**
  - **Page boundaries:** spans never cross pages.
  - **Size:** each span is at most 800 characters, about 300 tokens by ADR 0005.
  - **Where to cut:** the cut is the last paragraph break in the window, else the last line break, else the last sentence end (`. ` `? ` `! ` `。` `！` `？` `；`), else the last whitespace. Only cut points in the second half of the window count. With none there, the cut falls at 800.
  - **Edges:** spans are trimmed of surrounding whitespace, and they do not overlap.
- **Bounding boxes (PDF only).**
  - **Mapping:** a span's first and last characters are mapped to PDFium character indices with `FPDFText_GetCharIndexFromTextIndex`.
  - **The box:** it is the union of PDFium's text rectangles over that range.
  - **Coordinates:** PDF user space in points, origin at the bottom left, as in Zotero's own annotation rectangles. They are not adjusted for page rotation.
  - **Precision:** offsets stay authoritative, so the precision is `span`. The box is a highlight hint and covers both columns when a span does.
  - **Missing box:** when no rectangle is found, the box is left out.
- **Reading files safely.**
  - **Storing the path:** import stores the adapter's resolved path in `attachments.file_path` (migration 0004).
  - **Hash check:** the job reads the bytes once, compares their SHA-256 with the job's version, and parses from memory. Evidence therefore always belongs to the hashed bytes.
  - **File changed or gone:** this fails without retry as `attachment_changed`. The next sync records the new version, and planning queues it.
  - **PDFium cannot open the file** (corrupt or password-protected): this fails without retry as `extraction_failed`, with PDFium's message.
  - **Other exceptions** are retried, as the worker already does.
- **Lifecycle.**
  - **Trashed attachments:** they are not queued and are counted as trashed. Restoring one queues it at the next planning.
  - **Older evidence:** evidence of older attachment versions, or older settings, stays stored. `read_evidence` marks it `current: false`, and HW-015 searches current evidence only. Removal and tombstones belong to HW-013.
- **Coverage, per library.**
  - **States:** each non-trashed attachment has exactly one: `extracted`, `queued` (queued or leased), `failed`, `pending`, `unsupported`, or `no_file`.
  - **Totals:** a `trashed` count, plus `pages`, `pages_without_text`, `attachments_without_text`, and current `evidence` counts.
  - **Detail:** unsupported content types with counts, and a failure list with attachment key, error code, and message.
  - **Where it appears:** `status` includes it.
- **Interfaces.**
  - **CLI:** `hyperwiki extract [--force] [--retry-failed] [--max-jobs N]` plans every library, runs the worker in-process, and prints the plan, the worker report, and coverage.
  - **Reading evidence:** `read_evidence` is a read-only service for HW-016's `read` tool and the smoke test. It has no CLI command yet.

## Global Constraints

- All checks pass after every task: `uv run --locked ruff check`, `uv run --locked ruff format --check`, `uv run --locked mypy`, `uv run --locked pytest`, `uv run --locked python scripts/check_docs.py`.
- mypy strict covers `src`, `tests`, and `scripts`. Tests are fully annotated and flat in `tests/`, with unique file names.
- **Zotero is read-only:** attachment files are opened for reading only.
- **No copyrighted or private PDFs in Git.** Test PDFs are generated by `tests/pdf_factory.py` from synthetic text. Real-library results publish counts and timings only.
- **Tests don't depend on the machine:** tests pass on macOS, Windows, and Linux, use no system fonts, and never sleep.
- **Quotes stay faithful:** stored text is PDFium's text after cleanup version 1 only. NFKC and casefolding belong to search keys (HW-015).

## Review Focus

1. **Offsets must stay aligned when text contains non-BMP characters**, such as math alphanumerics, which take two UTF-16 units in PDFium and one Python character. Otherwise boxes drift. Pinned by `test_offsets_and_boxes_survive_non_bmp_text` (Task 2).
2. **A file that changed between import and extraction must not yield evidence labelled with the old version.** Pinned by `test_changed_file_fails_without_evidence` (Task 4).
3. **Malformed page labels must not be shown as printed page numbers.** These are labels that are one constant value on every page. Pinned by `test_constant_page_labels_are_dropped` (Task 2).
4. **A corrupt PDF must fail once, visibly, without blocking other attachments.** Pinned by `test_corrupt_pdf_is_reported_in_coverage` (Task 5).
5. **Repeated or forced extraction must not duplicate evidence or change its IDs.** Pinned by `test_forced_rerun_keeps_evidence_ids` (Task 4).

---

### Task 1: Cleanup and chunking (pure functions)

**Files:** create `src/hyperwiki/processing/text_cleanup.py` and `src/hyperwiki/processing/chunking.py`; tests `tests/test_text_cleanup.py` and `tests/test_chunking.py`.

**Produces:**
- `CleanText(text: str, sources: tuple[int, ...])`, where `sources[i]` is the UTF-16 unit index of the cleaned character `i` in the raw text.
- `clean_page_text(raw: str) -> CleanText`.
- `CLEANUP_VERSION = 1`.
- `chunk_spans(text: str, *, max_chars: int) -> list[tuple[int, int]]`.
- `CHUNKER_VERSION = 1`.

- [x] Tests:
  - `test_crlf_becomes_lf`;
  - `test_hyphen_marker_is_removed_with_following_break` (both `ab￾cd` and `ab￾\r\ncd` give `abcd`);
  - `test_c0_controls_removed_except_tab_and_newline`;
  - `test_line_break_between_han_is_joined` (`中文\r\n论文` gives `中文论文`; `中文\r\nPDF` keeps the break);
  - `test_sources_count_utf16_units` (raw `x 𝐀 y`: the source of `y` is 4);
  - `test_spans_never_exceed_max_and_cover_all_text`;
  - `test_spans_prefer_paragraph_then_line_then_sentence_breaks`;
  - `test_chinese_sentence_end_is_a_break`;
  - `test_spans_are_trimmed_and_skip_blank_text`;
  - `test_hard_cut_without_break`.
- [x] Run; expect import errors. Implement. All checks. Commit `feat(processing): clean page text and cut it into spans`.

### Task 2: Extractors (PDF and plain text) and the test PDF factory

**Files:** add `pypdfium2>=5.14.0` to `pyproject.toml` and `uv.lock`; create `src/hyperwiki/processing/extractors.py` and `tests/pdf_factory.py`; test `tests/test_extractors.py`. Add `EXTRACTION_FAILED` and `ATTACHMENT_CHANGED` to `errors.py`.

**Produces:**
- `ExtractedSpan(char_start, char_end, bbox: BoundingBox | None)`.
- `ExtractedPage(index, label: str | None, width: float | None, height: float | None, text: str, has_text: bool, spans: list[ExtractedSpan])`.
- `ExtractedDocument(pages: list[ExtractedPage])`.
- `ExtractionSettings(chunk_chars=800, min_page_chars=20)`.
- `extract_pdf(data: bytes, settings, *, on_page: Callable[[int], None]) -> ExtractedDocument`.
- `extract_plaintext(data: bytes, settings) -> ExtractedDocument`.
- `extractor_component(content_type) -> Component | None`, which is None when the type is unsupported.
- Unreadable input raises `HyperWikiError` with `extraction_failed`.
- `tests/pdf_factory.make_pdf(pages: Sequence[Sequence[str]], *, page_labels: str | None = None) -> bytes`. It writes one Type0 font with Identity-H encoding and a ToUnicode map, so English, Chinese, and non-BMP text extract without any font file.

- [x] Tests:
  - `test_extracts_english_and_chinese_pages_with_labels`;
  - `test_hyphenated_line_end_is_joined`;
  - `test_page_without_text_layer_has_no_spans`;
  - `test_constant_page_labels_are_dropped`;
  - `test_pdf_without_labels_has_none`;
  - `test_spans_have_boxes_inside_the_page`;
  - `test_offsets_and_boxes_survive_non_bmp_text`;
  - `test_corrupt_pdf_raises_extraction_failed`;
  - `test_plaintext_pages_split_on_form_feed`;
  - `test_non_utf8_text_raises_extraction_failed`;
  - `test_on_page_called_per_page`.
- [x] Run; expect failures. Implement. All checks. Commit `feat(processing): extract PDF and plain-text pages with pypdfium2`.

### Task 3: Migration 0004 and the evidence repository

**Files:** create `storage/migrations/0004_evidence.sql` and `storage/evidence_repo.py`; modify `storage/library_repo.py` (`upsert_attachment(..., file_path=None)`) and `services/library_import.py` (pass `file.resolved_path`). Tests go in `tests/test_storage.py` and `tests/test_evidence_repo.py`.

**Migration 0004:**
- `attachments.file_path TEXT`.
- `extractions(id = fingerprint PRIMARY KEY, attachment_version_id REFERENCES attachment_versions, fingerprint_json, page_count, created_at)`, indexed by version.
- `extraction_pages(extraction_id, page_index, page_label, width, height, text, has_text, PRIMARY KEY (extraction_id, page_index))`.
- `evidence(id PRIMARY KEY, extraction_id, attachment_id, page_index, ordinal, char_start, char_end, text, locator_json, UNIQUE (extraction_id, ordinal), FOREIGN KEY (extraction_id, page_index) REFERENCES extraction_pages)`, indexed by attachment.

**Produces:**
- `replace_extraction(conn, *, fingerprint: str, spec: WorkFingerprint, version: AttachmentVersionRow, document: ExtractedDocument, now) -> ExtractionSummary`. It deletes rows of the same fingerprint, then inserts pages and evidence. Before writing, it checks that every span's text equals its page slice.
- `get_evidence(conn, evidence_id) -> EvidenceRecord | None`.
- `EvidenceRecord` goes in `domain/evidence.py`, with fields `id`, `locator`, `text`, `library_id`, `item_key`, `attachment_key`, `attachment_version_id`, `extractor: Component`, `current: bool`.

- [x] Tests:
  - `test_migration_0004_adds_evidence_tables` (version 4; earlier tests updated);
  - `test_import_stores_resolved_file_path`;
  - `test_replace_extraction_round_trips_evidence`;
  - `test_replace_extraction_is_idempotent`;
  - `test_span_outside_page_text_is_rejected`.
- [x] Run; expect failures. Implement. All checks. Commit `feat(storage): store extracted pages and evidence (migration 0004)`.

### Task 4: Extraction jobs and service

**Files:** create `processing/evidence_jobs.py` and `services/evidence.py`; test `tests/test_evidence_service.py`.

**Produces:**
- `EXTRACT_TEXT = "extract.text"`.
- `extraction_spec(version_id: str, sha256: str, content_type: str, settings) -> WorkFingerprint | None`.
- `TextExtractionHandler(settings)`, which implements `Handler`. It heartbeats every 25 pages.
- `ExtractionPlan(queued, already_queued, already_done, already_failed, unsupported, no_file, trashed)`.
- `plan_extraction(conn, *, now, force=False, retry_failed=False, settings=DEFAULT) -> dict[str, ExtractionPlan]`, keyed by library ID. An extraction whose last job failed is not queued again unless `retry_failed` or `force` is set.
- `extract_evidence(conn, *, owner, now=utc_now, max_jobs=None, should_stop=…) -> WorkerReport`.
- `read_evidence(conn, evidence_id) -> EvidenceRecord`, which raises `not_found` for an unknown ID.

- [x] Tests, using the bilingual fixture plus generated PDFs in a writable snapshot copy:
  - `test_english_and_chinese_spans_resolve_to_version_and_page`;
  - `test_replanning_queues_nothing_new`;
  - `test_forced_rerun_keeps_evidence_ids`;
  - `test_replaced_pdf_is_extracted_and_old_evidence_not_current`;
  - `test_changed_file_fails_without_evidence`;
  - `test_trashed_attachment_is_not_queued`;
  - `test_settings_change_requeues`.
- [x] Run; expect failures. Implement. All checks. Commit `feat(evidence): queue, run, and read text extraction jobs`.

### Task 5: Coverage, CLI, and status

**Files:** `domain/evidence.py` (`EvidenceCoverage`, `CoverageFailure`), `storage/evidence_repo.py` (queries), `services/evidence.py` (`evidence_coverage(conn, library_id, settings) -> EvidenceCoverage`), `services/library_import.py` (`LibraryStatus.evidence`), and `interfaces/cli.py` (`extract`). Tests go in `tests/test_evidence_service.py` and `tests/test_cli.py`.

- [x] Tests:
  - `test_coverage_counts_every_state` (fixture: text attachments extracted, the missing PDF counted as `no_file`, the linked URL counted as `no_file`, an HTML file counted as `unsupported` with its type, plus a pending one);
  - `test_corrupt_pdf_is_reported_in_coverage` (failed once with `extraction_failed`; the others are still extracted);
  - `test_textless_pages_are_counted_not_hidden`;
  - `test_cli_extract_reports_plan_worker_and_coverage`;
  - `test_cli_status_includes_evidence_coverage`.
- [x] Run; expect failures. Implement. All checks. Commit `feat(cli): extract evidence and show coverage in status`.

### Task 6: Real-library smoke and documentation

**Files:** `experiments/hw014-evidence/smoke.py`, `README.md`, and `results/summary.json`; ADR 0006 (cleanup correction and status), BACKLOG, DEVELOPMENT_PLAN, the README usage section, and this plan's Results.

- [x] The smoke works in a private workspace outside the repository:
  1. Sync the user's library with the copy reader.
  2. Plan and extract, timed.
  3. Check every quote: each evidence text equals its page slice, and every box lies inside its page.
  4. Plan again and expect nothing queued.
  5. Report counts: pages, pages without text, spans, spans with Han characters, failures by code, unsupported types, time per page, and database size.
  6. Publish aggregates only.
- [x] Self-review the branch (no subagent), record the rulings and deferred minors, and run all checks. Commit `docs: record HW-014 plan and results`.

## Results

Measured on 2026-11-03 on an Apple M4 Pro (macOS 26.6.2), Python 3.12.13, pypdfium2 5.14.0 with PDFium 8076, CPU only.

**Checks:** all CI checks pass, with 319 tests passing and the opt-in live provider test skipped. Key tests were checked by breaking the code they cover and watching them fail:
- non-BMP offsets;
- constant page labels;
- span validation;
- idempotent storage;
- the hash check;
- the stale-job check;
- the trash skip.

**The user's library**, read with the copy reader ([`experiments/hw014-evidence`](../../experiments/hw014-evidence/README.md); aggregates in [`results/summary.json`](../../experiments/hw014-evidence/results/summary.json)):

| Measure | Result |
|---|---|
| Attachments | 373 (1 trashed): 294 PDFs extracted, 78 HTML snapshots unsupported, none failed |
| Pages | 7,692, the same as HW-007; 10 without a text layer, matching the 10 pages HW-007 found in 3 PDFs |
| Evidence spans | 34,740; median 751 characters, 95th percentile 797, maximum 800 |
| Chinese | the 2 Chinese books are mostly Han text; 1,897 spans and 830 pages contain Han characters |
| Page labels | kept for 81 PDFs. HW-007 found 85 PDFs defining labels, 4 of them malformed with one constant label; those 4 are dropped |
| Quote check | every span's text equals its page slice; every locator names its extraction, version hash, and offsets; every ID matches its locator (0 mismatches) |
| Reading back | all 34,740 spans read back as current through `read_evidence` in 1.1 s |
| Bounding boxes | every span has one. 59 spans in 17 PDFs extend beyond the page by a median of 133 points, because those PDFs place text outside the visible page. Boxes are reported as PDFium gives them, not clamped |
| Time | sync 0.9 s; planning 0.02 s; extraction 20.6 s, or 2.7 ms per page including reading, hashing, and storing |
| Repeat | planning again queued nothing. A forced re-extraction of all 294 took 21.2 s and gave identical evidence IDs |
| Workspace database | 91.7 MB |
| Zotero | database files unchanged |

**HTML snapshots:** each of the 78 belongs to an item that also has a PDF, so no item in this library lacks evidence because of them.

**Not tested:**
- Chinese journal papers, such as CNKI downloads, which often use unusual font encodings. ADR 0006 needs them before Chinese support is called confirmed.
- Scanned PDFs, vertical text, and traditional script.
- Windows and Linux speed. CI runs the synthetic tests on all three systems.
- PDFs that hang PDFium. There is no per-file timeout yet.

## Rulings made during execution

- **U+FFFE cleanup** (ADR 0006 corrected): the marker is removed together with a following line break, if any. PDFium 8076 emits `extrac\uFFFEtion` with no break. *Cost if wrong:* a real hyphen at a line end (`state-of-the-` + `art`) loses its hyphen.
- **Page text from `FPDFText_GetText`**, not PDFium's per-character list, which differed on 263 of 354 sampled real pages. Boxes map through `FPDFText_GetCharIndexFromTextIndex`. *Cost if wrong:* none for text.
- **Lint:** RUF001 (ambiguous characters) is ignored in `tests/` only, for bilingual test data; source code uses escapes. *Cost if wrong:* an ambiguous character slips into a test string.
- **Error type:** extraction failures raise `HyperWikiError(extraction_failed)` rather than a new exception class. *Cost if wrong:* none.
- **Typing:** mypy ignores missing imports for pypdfium2, which ships no type information. Only `processing/extractors.py` imports it. *Cost if wrong:* untyped calls are confined to that module.
- **Layering:** `ExtractedSpan`, `ExtractedPage`, and `ExtractedDocument` live in `domain/extraction.py`, so storage does not import processing. *Cost if wrong:* none.
- **Box rounding:** boxes are rounded to 0.01 point before hashing, so float noise between processors cannot change evidence IDs. *Cost if wrong:* precision below 0.01 point is lost.
- **Where `current` is decided:** the repository returns stored evidence, and the service decides `current` (current version and current settings). *Cost if wrong:* none.
- **Stale jobs:** the handler takes its settings from the job's fingerprint. It refuses, without retry, a job whose extractor or settings differ from the running code. *Cost if wrong:* a stale queued job fails visibly, and planning queues the right one.
- **Failed extractions:** planning skips a fingerprint whose last job failed, counting it as `already_failed`. `--retry-failed` or `--force` queues it again. The same bytes, extractor, and settings fail the same way, and HW-013 will plan on every sync. *Cost if wrong:* a transient failure that used up its retries needs `--retry-failed`.
- **Empty workspace:** `extract` on a workspace with no database returns `not_found` and creates nothing. *Cost if wrong:* none.

## Self-review

Final review: self-review (no subagent tool used, as the user asked). No Critical or Important findings.

**Deferred minors:**
- **No per-file timeout.** A PDF that hangs PDFium blocks the worker. A subprocess with a time limit is the fix, when needed.
- **Old extractions grow the database.** Evidence from older settings or extractor versions stays stored. Every settings change adds a full copy (about 90 MB for this library). Pruning belongs with HW-013 tombstones or M4 maintenance.
- **Trashed attachments** keep their evidence, and `read_evidence` still reports it as current. HW-015 scope must exclude trashed attachments, and trashed collections, from search.
- **Large files** are read whole into memory to hash and parse them. This is fine for papers. A size cap may be needed for very large books.
- **Interrupted extraction:** Ctrl-C during `extract` prints a traceback. The lease expires after 5 minutes and the next run resumes.
- **Unsupported types:** HTML snapshots and EPUB are counted as unsupported, not extracted.
- **No section locators;** section stays missing.
