# HW-007 spike results

Measured 2026-11-03 on the user's own Zotero library: 294 PDFs, 7,692 pages, 1.7 GB. They are 128 conference papers, 100 journal articles, 64 preprints, and 2 books in simplified Chinese. The PDFs and all extracted text stay private and outside Git; these are aggregates from `results/summary.json`, written by `score.py`.

Machine: Apple M4 Pro, macOS, Python 3.12.13; CPU only (Docling with 4 threads, the others single-threaded). Times come from a second run of each extractor alone on an otherwise idle machine.

Versions: pypdfium2 5.14.0, pypdf 6.19.0 with fontTools 4.63.0 (the `fonts` extra), pdfminer.six 20260107, pdfplumber 0.11.10, Docling 2.145.0 with torch 2.13.0.

## Measures

- **Reading order:** each paper's abstract, taken from its Zotero record, should appear intact and in order in the first three extracted pages. The score is the share of the abstract's word 4-grams found there, after normalization: line endings unified; words joined across a line-break hyphen (`-`, soft hyphen, or PDFium's U+FFFE marker); NFKC; casefold.
  - **Intact:** score ≥ 0.9.
  - **Broken:** score < 0.5.
  - **Within 0.05 of best:** compares extractors on the same paper, which cancels differences between the Zotero abstract and the printed one.
  - 275 PDFs have a Zotero abstract of at least 40 words.
- **Defects per 10,000 characters:**
  - U+FFFD replacement characters;
  - `(cid:N)` codes for glyphs the extractor could not map;
  - private-use characters;
  - CJK radicals in place of ideographs;
  - ligatures;
  - C0 control characters;
  - "glued words": runs of 26 or more Latin letters, which mean missing spaces.
- **Empty page:** fewer than 20 non-space characters.
- **Tables:** detected tables compared with distinct "Table N" captions in the same pages, a count-level proxy that does not check cell structure.

## All 294 PDFs

| Extractor | Abstract intact | Abstract broken | Within 0.05 of best | Time per page | Total time | Peak memory | Errors | Empty pages |
|---|---|---|---|---|---|---|---|---|
| pypdfium2 | 96.0% | 0.7% | 100% | 1.8 ms | 14 s | 0.47 GB | 0 | 10 in 3 PDFs |
| pypdf | 89.5% | 1.8% | 89.1% | 24 ms | 184 s | 0.77 GB | 0 | 10 in 3 PDFs |
| pdfminer.six | 91.3% | 5.1% | 94.2% | 46 ms | 350 s | 0.56 GB | 0 | 44 in 6 PDFs (3 mostly empty) |
| pdfplumber, default text | 6.2% | 60.7% | 4.7% | 65 ms | 500 s | 1.35 GB | 0 | 10 in 3 PDFs |

pdfplumber's time includes table detection. All four agree on every page count. The 10 empty pages, in the same 3 PDFs for all but pdfminer.six, have no text layer.

| Defects per 10,000 characters | pypdfium2 | pypdf | pdfminer.six | pdfplumber |
|---|---|---|---|---|
| `(cid:N)` unmapped glyphs | 0 | 0 | 11.7 | 23.9 |
| Glued words | 0.05 | 2.0 | 0.01 | 51.5 |
| C0 control characters | 8.3 | 2.6 | 0.2 | 1.3 |
| Ligatures (cosmetic; NFKC expands them) | 0 | 8.0 | 8.1 | 0.1 |
| Private-use characters | 1.8 | 1.8 | 0.8 | 1.5 |
| U+FFFD | 0.07 | 0.08 | 0.003 | 0.07 |
| PDFs with lone surrogates (invalid text) | 0 | 2 | 0 | 0 |

pypdfium2 ends lines with `\r\n` and marks a hyphen at a line end with U+FFFE. Its control characters come from unmapped math glyphs.

Reading order by item type (mean abstract 4-gram recall):

| Extractor | Conference papers | Journal articles | Preprints |
|---|---|---|---|
| pypdfium2 | 0.982 | 0.980 | 0.952 |
| pypdf | 0.973 | 0.943 | 0.934 |
| pdfminer.six | 0.898 | 0.977 | 0.918 |
| pdfplumber | 0.367 | 0.509 | 0.357 |

## Sample with Docling

47 PDFs, first 40 pages each, 884 pages in all: 15 conference papers, 15 journal articles, and 15 preprints chosen deterministically among those with abstracts, plus both Chinese books. Docling converts only the first 40 pages, so every extractor is scored on the same pages. Reading order uses the 45 English PDFs.

| Extractor | Abstract intact | Abstract broken | Within 0.05 of best | Time per page | Peak memory |
|---|---|---|---|---|---|
| pypdfium2 | 93.3% | 0% | 100% | 3.4 ms | 0.47 GB |
| pdfminer.six | 93.3% | 0% | 100% | 56 ms | 0.56 GB |
| Docling | 91.1% | 0% | 97.8% | 278 ms | 5.5 GB |
| pypdf | 88.9% | 0% | 95.6% | 36 ms | 0.77 GB |
| pdfplumber, default text | 6.7% | 68.9% | 4.4% | 83 ms | 1.35 GB |

Docling's model loading takes about 4 s once the models are cached; downloading them on first use took about 90 s. Its text has 0.7 U+FFFD per 10,000 characters, against 0 for the other extractors on these pages.

Tables in the 45 English PDFs, which have 265 distinct table captions:

| Extractor | Tables detected | Captions matched by count | Detected beyond captions | Median filled cells | PDFs with captions but no table found |
|---|---|---|---|---|---|
| Docling | 286 | 258 | 28 | 30 | 1 |
| pdfplumber | 629 | 201 | 428 | 1 | 5 |

Of the 10,419 numbers in Docling's table cells, 99.8% also occur in the same page's pypdfium2 text. pdfplumber's detections are mostly ruled boxes such as algorithms and figure frames; it misses tables without rules.

## Chinese

There are 2 born-digital books in simplified Chinese, set horizontally; the first 40 pages of each were compared.

- **Defects:** no extractor produced replacement characters, `(cid:N)` codes, private-use characters, or radicals in place of ideographs.
- **Titles:** every extractor found each book's title (character 3-gram recall 1.0).
- **Agreement:**
  - pypdf, pypdfium2, and pdfplumber returned the same 62,915 Han characters (Han bigram Jaccard 1.0 between pypdf and pypdfium2).
  - pdfminer.six returned 60,902 and Docling 60,945; their Jaccard with pypdfium2 is 0.968 and 0.974.
- **Line breaks:** every extractor turns a line break inside a Chinese sentence into whitespace.
- **Spacing:** pypdf adds spaces around Latin words inside Chinese text.
- **Docling rewrites:** Docling turned the Chinese dash —— into `--`, and turned a table of contents into a table.

No Chinese journal paper, scanned page, vertical text, or traditional-script PDF was in the library.

## Page labels

- 85 of the 294 PDFs define printed page labels (`/PageLabels`). pypdf and pypdfium2 return identical labels for 81 of them.
- In the other 4, the label dictionary is malformed, and both return a constant label for every page (`/D` and `D`).
- pdfminer.six, pdfplumber, and Docling do not expose page labels.

## Install size

| Extractor | Download | Installed |
|---|---|---|
| pypdfium2 | wheels: 3.5 MB (macOS arm64), 3.9 MB (Windows x64), 3.8 MB (Linux x86_64); no Python dependencies | 63 MB for all four lightweight extractors together |
| pypdf | 0.4 MB, pure Python; fontTools is about 5 MB | (included above) |
| pdfminer.six | 6.6 MB | (included above) |
| pdfplumber | 0.1 MB, on top of pdfminer.six, pypdfium2, and Pillow | (included above) |
| Docling | torch wheels: 112 MB (macOS), 122 MB (Windows), 527 MB (Linux x86_64, plus CUDA packages); about 0.5 GB of model files fetched on first use | 1.1 GB, plus the model files |

Docling's model files:
- **Layout:** `docling-project/docling-layout-heron`, revision 8f39ad3c0b4c, Apache-2.0.
- **Tables (TableFormer):** `docling-project/docling-models`, revision 2199320848bb, CDLA-Permissive-2.0 and Apache-2.0.

## Reproduce

From the repository root, with `P` a private directory outside the repository:

```sh
D=experiments/hw007-pdf-extraction
uv run --locked python $D/prepare.py --zotero-dir ~/Zotero --private $P
for e in pypdfium2 pypdf pdfminer pdfplumber; do uv run $D/extract.py $e --private $P; done
HF_HOME=/path/to/model-cache uv run $D/extract_docling.py --private $P
python3 $D/score.py --private $P   # add --timing T for an idle rerun in another directory
```
