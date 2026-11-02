# ADR 0006: PDF text extraction

Date: 2026-11-03. Status: accepted for M1 (HW-007); to be confirmed in HW-014 on the lifecycle fixtures and on Chinese journal papers. Number 0004 is reserved for the HW-006 change-detection record.

## Context

The [product design](../../PRODUCT_DESIGN.md) requires text extraction from English, Chinese, and mixed-language papers with no AI provider. Extraction failures and unsupported files must be visible. [ADR 0001](0001-core-architecture.md) ties every evidence span to an attachment version and an extraction version. A span carries a page index and, where available, a printed page label, a section, and a bounding box; missing precision stays missing. Tables need a supported representation. Hyper-Wiki runs on researchers' laptops, so install size, speed, and memory matter, and every dependency must pass the [license policy](0002-license.md).

## Evidence

The harness is in [`experiments/hw007-pdf-extraction`](../../experiments/hw007-pdf-extraction); full results are in its [results.md](../../experiments/hw007-pdf-extraction/results.md).

**Corpus:** the user's own Zotero library, read with the Zotero copy reader. It has 294 PDFs and 7,692 pages: 128 conference papers, 100 journal articles, 64 preprints, and 2 born-digital books in simplified Chinese. The PDFs and extracted text stay private; only aggregates are published.

**Reading order** is scored against the user's own metadata: each paper's Zotero abstract should appear intact in the first three extracted pages. 275 PDFs have a Zotero abstract of at least 40 words. Measured on 2026-11-03 on an Apple M4 Pro, CPU only. Times come from a second run of each extractor alone on an idle machine.

**All 294 PDFs:**

| Extractor (license) | Abstract intact | Abstract broken | Within 0.05 of best | Time per page | Main defects |
|---|---|---|---|---|---|
| pypdfium2 5.14.0 (BSD-3-Clause or Apache-2.0; bundles PDFium) | 96.0% | 0.7% | 100% | 1.8 ms | 8.3 control characters per 10,000 characters, from unmapped math glyphs |
| pypdf 6.19.0 with fontTools (BSD-3-Clause, MIT) | 89.5% | 1.8% | 89.1% | 24 ms | invalid text (lone surrogates) in 2 PDFs; 2.0 glued words per 10,000 characters |
| pdfminer.six 20260107 (MIT) | 91.3% | 5.1% | 94.2% | 46 ms | 11.7 `(cid:N)` unmapped glyphs per 10,000 characters; 44 empty pages, 3 PDFs mostly unreadable |
| pdfplumber 0.11.10 (MIT), default text | 6.2% | 60.7% | 4.7% | 65 ms | the two columns of a page interleaved line by line; 51.5 glued words per 10,000 characters |

No extractor raised an error, and all four agree on every page count.

**Sample with Docling.** The sample is 47 PDFs, first 40 pages each. On it, Docling 2.145.0 (MIT) scored 91.1% intact and 0% broken. pypdfium2 scored 93.3% and 0%, and pdfminer.six 93.3% and 0%. Docling took 278 ms per page on 4 CPU threads, about 80 times pypdfium2, and peaked at 5.5 GB of memory. Its install is 1.1 GB plus about 0.5 GB of model files, against 3.5–3.9 MB for a pypdfium2 wheel.

**Tables** (45 English PDFs with 265 distinct table captions):
- **Docling** detected 286 tables, 258 of them matching captions by count.
  - 99.8% of the 10,419 numbers in its cells also occur in the same page's text.
- **pdfplumber** detected 629 "tables", 428 beyond the captions, with a median of one filled cell. It found nothing in 5 PDFs that have captioned tables.

**Chinese** (2 books, first 40 pages):
- No extractor produced broken characters, and all found the titles.
- pypdf, pypdfium2, and pdfplumber returned identical Han text.
- Every extractor turns a line break inside a Chinese sentence into whitespace.
- Docling rewrote the dash —— as `--`, and its text elsewhere contains replacement characters (U+FFFD) that the PDF text layer does not.

**Page labels:**
- 85 PDFs define printed page labels. pypdf and pypdfium2 agree on 81 of them.
- The other 4 have malformed label dictionaries, and both libraries return one constant label for every page.

## Decision

1. **Default extractor: pypdfium2** (PDFium), installed with the core. It is the most accurate on reading order, by far the fastest, needs no Python dependencies, and has small wheels for macOS, Windows, and Linux. Evidence extraction (HW-014) builds on it as follows:
   - **Page index:** the page index is the authoritative locator.
   - **Page labels:** printed page labels are optional hints, used only when the PDF defines them, and never trusted alone.
   - **Bounding boxes:** PDFium's character boxes give bounding boxes for span locators; HW-014 decides how spans map to boxes.
   - **Stored page text** is the extracted text after a cleanup whose version is part of the extractor fingerprint:
     - Convert `\r\n` to `\n`.
     - Treat U+FFFE before a line break as a line-break hyphen, and join the word.
     - Remove C0 control characters other than tab and newline.
     - Join a line break between two Han characters without inserting a space.
     - Nothing else is rewritten. NFKC and casefolding apply only to search keys ([ADR 0005](0005-bilingual-search.md)), so quotes stay faithful to the PDF.
   - **Fingerprint:** the extractor name, the pypdfium2 version, the PDFium build, and the cleanup version, per [HW-012](../plans/2026-11-03-hw-012-jobs-fingerprints.md).
   - **Unsupported pages:** a page with no text layer (fewer than 20 non-space characters) is reported as such in coverage. OCR is not part of v1.
2. **Tables: Docling as an optional "layout and tables" extractor**, not installed by default.
   - **What it adds:** of the candidates, only Docling recovers tables reliably.
   - **Why optional:** it adds PyTorch, about 0.5 GB of models, about 80 times the time per page on a CPU, and several gigabytes of memory.
   - **How its output is used:** Docling's tables become table evidence, each with a page and a bounding box and its own extractor fingerprint. Docling's prose is not used for evidence quotes, because it rewrites characters.
   - **What HW-014 decides:** how Docling tables sit beside pypdfium2 spans for the same attachment version, and whether a user can switch it on later through a Rebuild.
   - **Hardware:** device selection follows the project rule: a setting of auto, CPU, MPS, or CUDA, with CPU fallback.

**Rejected:**
- **pypdf:** 13 times slower than pypdfium2, invalid text in 2 PDFs, missing spaces, and it needs fontTools for the CFF fonts common in LaTeX papers. Being pure Python is its only advantage.
- **pdfminer.six:** fails to map glyphs in some fonts, leaving some PDFs mostly unreadable, and is 25 times slower.
- **pdfplumber:**
  - Its default text mode interleaves the two columns of a page.
  - Its table detection mostly finds non-tables.
  - It is the slowest of the four.
  - Its layout-aware mode would inherit pdfminer.six's font failures.

**Not tested:**
- **PyMuPDF and pymupdf4llm:** AGPL-3.0 or commercial; excluded as a default by the license policy.
- **MinerU 4.1.4:** custom non-OSI license; its models are AGPL-3.0.
- **marker 2.1.0:** the code is Apache-2.0, but the model weights carry a modified OpenRAIL-M license with commercial limits; its surya layout model card says CC-BY-NC-SA-4.0.
- **Nougat:** CC-BY-NC-4.0 weights.
- **GROBID:** Apache-2.0 and strong on scholarly structure and references, but it runs as a Java service, and no Java runtime was installed. Reconsider it for reference parsing.
- **unstructured:** wraps the parsers above.

## Consequences and open items

- **Dependencies:**
  - **pypdfium2:** HW-014 adds it and records it in the dependency table. Its wheels bundle PDFium and its third-party libraries under their own permissive licenses; the M4 license audit lists them in NOTICE.
  - **Docling:** an optional extra.
    - Its layout model (`docling-project/docling-layout-heron`, revision 8f39ad3c0b4c) is Apache-2.0.
    - Its table model (`docling-project/docling-models`, revision 2199320848bb) is CDLA-Permissive-2.0 and Apache-2.0.
    - Both are downloaded at setup and never committed.
- **Untested Chinese material:** Chinese journal papers (for example CNKI downloads, which often use unusual font encodings), scanned pages, vertical text, and traditional script are untested. The library held only two born-digital books. HW-014 needs Chinese papers from the user before Chinese support is called confirmed.
- **Scanned PDFs:** none in this library lacked a text layer. 10 pages in 3 PDFs did. OCR stays out of v1, and such pages are reported, not hidden.
- **Limits of the measures:**
  - Reading order is measured on the abstract region only. Body order around figures, captions, footnotes, headers, and footers is not scored.
  - Tables are measured by count and numeric fidelity, not cell structure.
- **Not measured:**
  - Speed on Windows, on Linux, and on older laptops.
  - Docling on MPS or CUDA.
- **Quote check:** HW-014 must check that every stored quote can be found in the stored page text of its attachment version.

## Reproduce

See [results.md](../../experiments/hw007-pdf-extraction/results.md#reproduce). The PDF list and outputs live in a private directory outside the repository.

## Sources

Accessed 2026-11-03.

- PyPI metadata for [pypdfium2](https://pypi.org/project/pypdfium2/), [pypdf](https://pypi.org/project/pypdf/), [pdfminer.six](https://pypi.org/project/pdfminer.six/), [pdfplumber](https://pypi.org/project/pdfplumber/), [Docling](https://pypi.org/project/docling/), [docling-parse](https://pypi.org/project/docling-parse/), [torch](https://pypi.org/project/torch/), [PyMuPDF](https://pypi.org/project/PyMuPDF/), [marker-pdf](https://pypi.org/project/marker-pdf/), [MinerU](https://pypi.org/project/mineru/), [nougat-ocr](https://pypi.org/project/nougat-ocr/), and [unstructured](https://pypi.org/project/unstructured/) (versions, licenses, wheel sizes).
- Hugging Face model metadata for [docling-layout-heron](https://huggingface.co/docling-project/docling-layout-heron), [docling-models](https://huggingface.co/docling-project/docling-models), [nougat-base](https://huggingface.co/facebook/nougat-base), [surya_layout](https://huggingface.co/datalab-to/surya_layout), and [MinerU2.5](https://huggingface.co/opendatalab/MinerU2.5-2509-1.2B) (licenses, revisions).
- The [marker README](https://github.com/datalab-to/marker#commercial-usage) on model-weight terms, and the [pypdf text extraction guide](https://pypdf.readthedocs.io/en/stable/user/extract-text.html).
